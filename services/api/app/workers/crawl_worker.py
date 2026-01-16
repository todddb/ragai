import json
import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, List, Set
from urllib.parse import parse_qsl, urljoin, urlparse, urlunparse

import httpx
import yaml

try:
    import tiktoken  # type: ignore
except Exception:
    tiktoken = None  # type: ignore

try:
    from bs4 import BeautifulSoup  # type: ignore
except Exception:
    BeautifulSoup = None  # type: ignore

ALLOW_BLOCK_PATH = Path("/app/config/allow_block.yml")
CRAWLER_CONFIG_PATH = Path("/app/config/crawler.yml")
INGEST_CONFIG_PATH = Path("/app/config/ingest.yml")
ARTIFACT_DIR = Path("/app/data/artifacts")
CANDIDATE_PATH = Path("/app/data/candidates/candidates.jsonl")
PROCESSED_PATH = Path("/app/data/candidates/processed.json")


def _require_tiktoken() -> None:
    if tiktoken is None:
        raise RuntimeError("Missing dependency: tiktoken (pip install tiktoken)")


def _require_bs4() -> None:
    if BeautifulSoup is None:
        raise RuntimeError("Missing dependency: beautifulsoup4 (pip install beautifulsoup4)")


def _load_config(path: Path) -> Dict:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _load_allow_block() -> Dict[str, List[str]]:
    return _load_config(ALLOW_BLOCK_PATH)


def _load_crawler_config() -> Dict:
    return _load_config(CRAWLER_CONFIG_PATH)


def _canonicalize_url(url: str, config: Dict[str, List[str]]) -> str:
    parsed = urlparse(url)
    scheme = parsed.scheme.lower()
    host = parsed.netloc.lower()
    path = parsed.path or "/"
    if path != "/" and path.endswith("/"):
        path = path[:-1]
    query_params = parse_qsl(parsed.query, keep_blank_values=True)
    preserve = set(config.get("preserve_query_params", []))
    blocked = set(config.get("blocked_params", []))
    filtered_params = []
    for key, value in query_params:
        if key in blocked or key.startswith("utm_"):
            continue
        if preserve:
            if key in preserve:
                filtered_params.append((key, value))
        else:
            continue
    query = "&".join([f"{k}={v}" for k, v in filtered_params])
    return urlunparse((scheme, host, path, "", query, ""))


def _doc_id_for_url(canonical_url: str) -> str:
    import hashlib

    return hashlib.sha256(canonical_url.encode("utf-8")).hexdigest()


def _is_allowed(url: str, config: Dict[str, List[str]]) -> bool:
    parsed = urlparse(url)
    host = parsed.netloc
    path = parsed.path or "/"
    if host in config.get("blocked_domains", []):
        return False
    for blocked in config.get("blocked_paths", []):
        if path.startswith(blocked):
            return False
    allowed_domains = config.get("allowed_domains", [])
    if allowed_domains and host not in allowed_domains:
        return False
    return True


def _load_processed() -> Set[str]:
    if not PROCESSED_PATH.exists():
        return set()
    try:
        return set(json.loads(PROCESSED_PATH.read_text(encoding="utf-8")) or [])
    except json.JSONDecodeError:
        return set()


def _save_processed(processed: Set[str]) -> None:
    PROCESSED_PATH.parent.mkdir(parents=True, exist_ok=True)
    PROCESSED_PATH.write_text(json.dumps(sorted(processed)), encoding="utf-8")


def _append_candidates(urls: Iterable[str], source: str, depth: int, max_depth: int) -> None:
    if depth > max_depth:
        return
    crawler_config = _load_crawler_config()
    url_config = crawler_config.get("url_canonicalization", {})
    seen = set()
    if CANDIDATE_PATH.exists():
        for line in CANDIDATE_PATH.read_text(encoding="utf-8").splitlines():
            try:
                entry = json.loads(line)
                seen.add(entry.get("url"))
            except json.JSONDecodeError:
                continue
    CANDIDATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with CANDIDATE_PATH.open("a", encoding="utf-8") as handle:
        for url in urls:
            canonical = _canonicalize_url(url, url_config)
            if canonical in seen:
                continue
            record = {
                "url": canonical,
                "discovered_at": datetime.utcnow().isoformat() + "Z",
                "source": source,
                "depth": depth,
            }
            handle.write(json.dumps(record) + "\n")
            seen.add(canonical)


def _chunk_text(text: str, size: int, overlap: int) -> List[str]:
    _require_tiktoken()
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
    import hashlib

    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _capture_url(url: str) -> List[str]:
    _require_bs4()
    crawler_config = _load_crawler_config()
    ingest_config = _load_config(INGEST_CONFIG_PATH)
    url_config = crawler_config.get("url_canonicalization", {})
    canonical = _canonicalize_url(url, url_config)
    headers = {"User-Agent": crawler_config.get("user_agent", "RagAI-Crawler/1.0")}
    delay = crawler_config.get("request_delay", 1.0)
    timeout = crawler_config.get("timeout", 30)
    time.sleep(delay)
    response = httpx.get(canonical, headers=headers, timeout=timeout)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")
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
    doc_id = _doc_id_for_url(canonical)
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
    return links


def run_crawl_job(log) -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    allow_block = _load_allow_block()
    crawler_config = _load_crawler_config()
    max_depth = crawler_config.get("max_depth", 0)
    seeds = allow_block.get("seed_urls", [])
    log(f"Starting crawl job with {len(seeds)} seed(s)")
    _append_candidates(seeds, "seed", 0, max_depth)
    if not CANDIDATE_PATH.exists():
        log("No candidates to process.")
        return
    processed = _load_processed()
    candidates = CANDIDATE_PATH.read_text(encoding="utf-8").splitlines()
    log(f"Loaded {len(candidates)} candidate(s)")
    for line in candidates:
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        url = entry.get("url")
        depth = entry.get("depth", 0)
        if not url:
            continue
        if url in processed:
            continue
        if depth > max_depth:
            processed.add(url)
            _save_processed(processed)
            continue
        if not _is_allowed(url, allow_block):
            processed.add(url)
            _save_processed(processed)
            continue
        log(f"Crawling {url} (depth {depth})")
        try:
            links = _capture_url(url)
            _append_candidates(links, url, depth + 1, max_depth)
            log(f"Captured {url} with {len(links)} link(s)")
        except Exception as exc:
            log(f"Error capturing {url}: {exc}")
        processed.add(url)
        _save_processed(processed)
    log("Crawl job complete")
