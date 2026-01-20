import hashlib
import json
import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Tuple
from urllib.parse import urlparse

import tiktoken
import yaml

from app.discovery import append_candidates, is_allowed, load_allow_block
from app.fetch import fetch_html_playwright
from app.fetch_redirect import fetch_resource_httpx_redirect_safe
from app.parsers.router import parse_by_type
from app.structured_store.sqlite_store import SQLiteStructuredStore
from app.utils.url import canonicalize_url, doc_id_for_url

CONFIG_PATH = Path("/app/config/crawler.yml")
INGEST_CONFIG_PATH = Path("/app/config/ingest.yml")

ARTIFACT_DIR = Path("/app/data/artifacts")
STRUCTURED_DB_PATH = Path("/app/data/sqlite/structured.db")


def _load_config(path: Path) -> Dict:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _chunk_text(text: str, size: int, overlap: int) -> List[str]:
    encoder = tiktoken.get_encoding("cl100k_base")
    tokens = encoder.encode(text)
    chunks = []
    start = 0
    while start < len(tokens):
        end = min(start + size, len(tokens))
        chunk_tokens = tokens[start:end]
        chunks.append(encoder.decode(chunk_tokens))
        start = end - overlap
        if start < 0:
            start = 0
        if end == len(tokens):
            break
    return chunks


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _is_html_content_type(content_type: str) -> bool:
    normalized = (content_type or "").split(";")[0].strip().lower()
    return normalized in {"text/html", "application/xhtml+xml"}


def _write_chunks(artifact_path: Path, doc_id: str, text: str, chunking: Dict) -> None:
    chunks = _chunk_text(
        text,
        size=chunking.get("chunk_size", 512),
        overlap=chunking.get("chunk_overlap", 128),
    )
    chunks_path = artifact_path / "chunks.jsonl"
    with chunks_path.open("w", encoding="utf-8") as handle:
        for index, chunk_text in enumerate(chunks):
            record = {
                "chunk_id": f"{doc_id}_{index}",
                "doc_id": doc_id,
                "chunk_index": index,
                "text": chunk_text,
            }
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def capture_url(url: str) -> Tuple[str, List[str]]:
    crawler_config = _load_config(CONFIG_PATH)
    ingest_config = _load_config(INGEST_CONFIG_PATH)
    url_config = crawler_config.get("url_canonicalization", {})
    canonical = canonicalize_url(url, url_config)

    headers = {"User-Agent": crawler_config.get("user_agent", "RagAI-Crawler/1.0")}
    delay = crawler_config.get("request_delay", 1.0)
    timeout = crawler_config.get("timeout", 30)

    time.sleep(delay)
    playwright_config = crawler_config.get("playwright", {})
    hostname = (urlparse(canonical).hostname or "").lower()
    use_domains = [domain.lower() for domain in playwright_config.get("use_for_domains", [])]
    use_playwright = bool(playwright_config.get("enabled")) and hostname in use_domains
    allow_block_cfg = load_allow_block()
    logger = logging.getLogger(__name__)
    logger.info("FETCH=httpx url=%s", canonical)
    fetch_result = fetch_resource_httpx_redirect_safe(
        canonical,
        headers=headers,
        timeout=timeout,
        allow_block_cfg=allow_block_cfg,
        is_allowed_fn=is_allowed,
    )
    fetch_url = fetch_result.final_url or canonical
    doc_id = doc_id_for_url(fetch_url)
    artifact_path = ARTIFACT_DIR / doc_id
    artifact_path.mkdir(parents=True, exist_ok=True)
    markdown_path = artifact_path / "content.md"
    ingest_chunking = ingest_config.get("chunking", {})

    if not fetch_result.ok:
        if fetch_result.status == "blocked_redirect":
            logger.info(
                "SKIP_BLOCKED_REDIRECT url=%s blocked_to=%s", canonical, fetch_result.blocked_to
            )
            markdown = f"Skipped redirect to blocked URL: {fetch_result.blocked_to}"
        elif fetch_result.status == "not_found":
            logger.info("SKIP_NOT_FOUND url=%s", canonical)
            markdown = "Skipped: not found"
        else:
            logger.info("SKIP_HTTP_ERROR url=%s status_code=%s", canonical, fetch_result.status_code)
            markdown = "Skipped: http error"
        markdown_path.write_text(markdown, encoding="utf-8")
        artifact = {
            "doc_id": doc_id,
            "url": canonical,
            "canonical_url": canonical,
            "final_url": fetch_url,
            "content_type": fetch_result.content_type or "",
            "parser": "",
            "status": fetch_result.status,
            "status_code": fetch_result.status_code,
            "markdown_path": "content.md",
            "meta": {"redirect_chain": fetch_result.redirect_chain},
            "content_hash": _content_hash(markdown),
            "fetched_at": datetime.utcnow().isoformat() + "Z",
            "title": "",
            "text": "",
        }
        (artifact_path / "artifact.json").write_text(
            json.dumps(artifact, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        _write_chunks(artifact_path, doc_id, "", ingest_chunking)
        return canonical, []

    content_bytes = fetch_result.content_bytes
    content_type = fetch_result.content_type
    status_code = fetch_result.status_code
    if use_playwright and _is_html_content_type(content_type):
        storage_state_path = playwright_config.get("storage_state_path")
        if not storage_state_path:
            raise ValueError("Playwright storage_state_path is not configured for this crawl.")
        logger.info("FETCH=playwright url=%s", fetch_url)
        html = fetch_html_playwright(
            fetch_url,
            storage_state_path=storage_state_path,
            headless=playwright_config.get("headless", True),
            timeout_ms=playwright_config.get("navigation_timeout_ms", 60000),
        )
        content_bytes = html.encode("utf-8")
        content_type = "text/html"
        status_code = 200

    parsed_doc, parser_name = parse_by_type(content_bytes, content_type, fetch_url)
    title = parsed_doc.title
    text = parsed_doc.text_for_chunking
    links = parsed_doc.links if parser_name == "html" else []

    content_hash = _content_hash(text)
    markdown_path.write_text(parsed_doc.markdown, encoding="utf-8")

    artifact = {
        "doc_id": doc_id,
        "url": canonical,
        "canonical_url": canonical,
        "final_url": fetch_url,
        "content_type": content_type,
        "parser": parser_name,
        "status": "captured",
        "status_code": status_code,
        "markdown_path": "content.md",
        "meta": parsed_doc.meta,
        "content_hash": content_hash,
        "fetched_at": datetime.utcnow().isoformat() + "Z",
        "title": title,
        "text": text,
    }
    structured_cfg = crawler_config.get("structured_store", {})
    if parser_name == "xlsx" and structured_cfg.get("enabled", False):
        sqlite_path = Path(structured_cfg.get("sqlite_path", str(STRUCTURED_DB_PATH)))
        xlsx_ingest_cfg = structured_cfg.get("xlsx_ingest", {})
        store = SQLiteStructuredStore(sqlite_path)
        ingest_meta = store.ingest_xlsx_to_meta(
            doc_id=doc_id,
            source_url=fetch_url,
            content_bytes=content_bytes,
            max_cells=xlsx_ingest_cfg.get("max_cells", 50000),
            batch_size=xlsx_ingest_cfg.get("batch_size", 2000),
        )
        artifact["meta"] = {**artifact["meta"], "xlsx_ingest": ingest_meta}

    (artifact_path / "artifact.json").write_text(
        json.dumps(artifact, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    _write_chunks(artifact_path, doc_id, text, ingest_chunking)

    return canonical, links


def capture_and_discover(url: str, source_depth: int) -> None:
    canonical, links = capture_url(url)
    append_candidates(links, canonical, source_depth + 1)
