import hashlib
import io
import json
import logging
import sqlite3
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Tuple
from urllib.parse import urlparse

import tiktoken
import yaml

from app.discovery import append_candidates
from app.fetch import fetch_html_playwright, fetch_resource_httpx
from app.parse import parse_by_type
from app.utils.url import canonicalize_url, doc_id_for_url

CONFIG_PATH = Path("/app/config/crawler.yml")
INGEST_CONFIG_PATH = Path("/app/config/ingest.yml")

ARTIFACT_DIR = Path("/app/data/artifacts")
STRUCTURED_DB_PATH = Path("/app/data/structured/structured.db")


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


def _init_structured_db(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS xlsx_sheets (
            doc_id TEXT,
            sheet_name TEXT,
            sheet_index INTEGER,
            source_url TEXT,
            ingested_at TEXT,
            PRIMARY KEY (doc_id, sheet_name)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS xlsx_cells (
            doc_id TEXT,
            sheet_name TEXT,
            row INTEGER,
            column INTEGER,
            value TEXT
        )
        """
    )


def _maybe_ingest_xlsx(
    doc_id: str,
    content_bytes: bytes,
    source_url: str,
    parser_name: str,
    db_path: Path | None = None,
) -> None:
    if parser_name != "xlsx":
        return
    try:
        import openpyxl  # type: ignore
    except Exception as exc:
        raise RuntimeError("Missing dependency: openpyxl (pip install openpyxl)") from exc
    target_path = db_path or STRUCTURED_DB_PATH
    target_path.parent.mkdir(parents=True, exist_ok=True)
    workbook = openpyxl.load_workbook(filename=io.BytesIO(content_bytes), data_only=True)
    with sqlite3.connect(target_path) as conn:
        _init_structured_db(conn)
        ingested_at = datetime.utcnow().isoformat() + "Z"
        for index, sheet in enumerate(workbook.worksheets):
            conn.execute(
                """
                INSERT OR REPLACE INTO xlsx_sheets
                (doc_id, sheet_name, sheet_index, source_url, ingested_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (doc_id, sheet.title, index, source_url, ingested_at),
            )
            for row_index, row in enumerate(sheet.iter_rows(values_only=True), start=1):
                for col_index, value in enumerate(row, start=1):
                    if value is None:
                        continue
                    conn.execute(
                        """
                        INSERT INTO xlsx_cells (doc_id, sheet_name, row, column, value)
                        VALUES (?, ?, ?, ?, ?)
                        """,
                        (doc_id, sheet.title, row_index, col_index, str(value)),
                    )
        conn.commit()


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
    logger = logging.getLogger(__name__)
    logger.info("FETCH=httpx url=%s", canonical)
    content_bytes, content_type, final_url, status_code = fetch_resource_httpx(
        canonical, headers=headers, timeout=timeout
    )
    fetch_url = final_url or canonical
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

    doc_id = doc_id_for_url(fetch_url)
    content_hash = _content_hash(text)
    artifact_path = ARTIFACT_DIR / doc_id
    artifact_path.mkdir(parents=True, exist_ok=True)

    markdown_path = artifact_path / "content.md"
    markdown_path.write_text(parsed_doc.markdown, encoding="utf-8")

    artifact = {
        "doc_id": doc_id,
        "url": canonical,
        "canonical_url": canonical,
        "final_url": fetch_url,
        "content_type": content_type,
        "parser": parser_name,
        "status_code": status_code,
        "markdown_path": "content.md",
        "meta": parsed_doc.meta,
        "content_hash": content_hash,
        "fetched_at": datetime.utcnow().isoformat() + "Z",
        "title": title,
        "text": text,
    }
    (artifact_path / "artifact.json").write_text(
        json.dumps(artifact, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    chunking = ingest_config.get("chunking", {})
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

    _maybe_ingest_xlsx(doc_id, content_bytes, fetch_url, parser_name)

    return canonical, links


def capture_and_discover(url: str, source_depth: int) -> None:
    canonical, links = capture_url(url)
    append_candidates(links, canonical, source_depth + 1)
