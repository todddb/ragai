import hashlib
import json
import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Tuple
from urllib.parse import urljoin, urlparse

import tiktoken
import yaml
from bs4 import BeautifulSoup

from app.discovery import append_candidates
from app.fetch import fetch_html_httpx, fetch_html_playwright
from app.utils.url import canonicalize_url, doc_id_for_url

CONFIG_PATH = Path("/app/config/crawler.yml")
INGEST_CONFIG_PATH = Path("/app/config/ingest.yml")

ARTIFACT_DIR = Path("/app/data/artifacts")


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
    if use_playwright:
        storage_state_path = playwright_config.get("storage_state_path")
        if not storage_state_path:
            raise ValueError("Playwright storage_state_path is not configured for this crawl.")
        logger.info("FETCH=playwright url=%s", canonical)
        html = fetch_html_playwright(
            canonical,
            storage_state_path=storage_state_path,
            headless=playwright_config.get("headless", True),
            timeout_ms=playwright_config.get("navigation_timeout_ms", 60000),
        )
    else:
        logger.info("FETCH=httpx url=%s", canonical)
        html = fetch_html_httpx(canonical, headers=headers, timeout=timeout)

    soup = BeautifulSoup(html, "html.parser")
    title = soup.title.string.strip() if soup.title and soup.title.string else ""
    if not title:
        h1 = soup.find("h1")
        if h1:
            title = h1.get_text(strip=True)
    text = " ".join(soup.get_text(separator=" ").split())

    links = []
    for tag in soup.find_all("a"):
        href = tag.get("href")
        if not href:
            continue
        links.append(urljoin(canonical, href))

    doc_id = doc_id_for_url(canonical)
    content_hash = _content_hash(text)
    artifact_path = ARTIFACT_DIR / doc_id
    artifact_path.mkdir(parents=True, exist_ok=True)

    artifact = {
        "doc_id": doc_id,
        "url": canonical,
        "canonical_url": canonical,
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

    return canonical, links


def capture_and_discover(url: str, source_depth: int) -> None:
    canonical, links = capture_url(url)
    append_candidates(links, canonical, source_depth + 1)
