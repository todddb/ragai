import json
import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, List, Set
from urllib.parse import parse_qsl, urljoin, urlparse, urlunparse

import httpx
import yaml

from app.utils.auth_hints import record_auth_hint
from app.utils.auth_validation import collect_required_profiles, run_auth_checks
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


class AuthRequiredError(RuntimeError):
    def __init__(self, auth_info: Dict[str, str]):
        super().__init__("Auth required")
        self.auth_info = auth_info


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


def _canonicalize_url(url: str, config: Dict[str, List[str]], allow_http: bool = False) -> str:
    parsed = urlparse(url)
    scheme = parsed.scheme.lower()
    host = parsed.netloc.lower()

    # Normalize HTTP to HTTPS if allow_http is False
    if not allow_http and scheme == "http":
        scheme = "https"

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

    # Check allow_rules if present
    allow_rules = config.get("allow_rules", [])
    if allow_rules:
        for rule in allow_rules:
            if isinstance(rule, str):
                pattern = rule
                match_type = "prefix"
            else:
                pattern = rule.get("pattern", "")
                match_type = rule.get("match", "prefix")
            if match_type == "exact" and url == pattern:
                return True
            if match_type != "exact" and pattern and url.startswith(pattern):
                return True
        return False

    # Fallback to allowed_domains if no allow_rules
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


def _append_candidates(urls: Iterable[str], source: str, depth: int, max_depth: int, allow_http: bool = False) -> None:
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
            canonical = _canonicalize_url(url, url_config, allow_http)
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


def _match_auth_redirect(target: str) -> str | None:
    parsed = urlparse(target)
    host = (parsed.hostname or "").lower()
    path = (parsed.path or "").lower()
    query = (parsed.query or "").lower()
    if host == "cas.byu.edu" and "/cas/login" in path:
        return "cas.byu.edu/cas/login"
    if host == "auth.brightspot.byu.edu" and "/authenticate/login" in path:
        return "auth.brightspot.byu.edu/authenticate/login"
    if "login.byu.edu" in host:
        return "login.byu.edu"
    if "/sso/login" in path:
        return "/SSO/login"
    if "service=" in query or "returnpath=" in query:
        return "sso_query"
    return None


def _capture_url(url: str, allow_http: bool = False) -> List[str]:
    _require_bs4()
    crawler_config = _load_crawler_config()
    ingest_config = _load_config(INGEST_CONFIG_PATH)
    url_config = crawler_config.get("url_canonicalization", {})
    canonical = _canonicalize_url(url, url_config, allow_http)
    headers = {"User-Agent": crawler_config.get("user_agent", "RagAI-Crawler/1.0")}
    delay = crawler_config.get("request_delay", 1.0)
    timeout = crawler_config.get("timeout", 30)
    time.sleep(delay)
    response = httpx.get(canonical, headers=headers, timeout=timeout, follow_redirects=False)
    if response.status_code in {301, 302, 303, 307, 308}:
        location = response.headers.get("location")
        if location:
            target = urljoin(str(response.url), location)
            matched_auth = _match_auth_redirect(target)
            if matched_auth:
                parsed_target = urlparse(target)
                raise AuthRequiredError(
                    {
                        "original_url": canonical,
                        "redirect_location": target,
                        "redirect_host": (parsed_target.hostname or ""),
                        "matched_auth_pattern": matched_auth,
                    }
                )
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


def _get_allow_http_for_url(url: str, config: Dict) -> bool:
    """Determine if HTTP is allowed for a given URL based on allow_rules or seed_urls config."""
    # Check seed_urls first
    seed_urls = config.get("seed_urls", [])
    for seed in seed_urls:
        if isinstance(seed, dict):
            seed_url = seed.get("url", "")
            if url == seed_url or url.startswith(seed_url):
                return seed.get("allow_http", False)
        elif isinstance(seed, str) and (url == seed or url.startswith(seed)):
            return False  # Default to not allowing HTTP for string seeds

    # Check allow_rules
    allow_rules = config.get("allow_rules", [])
    for rule in allow_rules:
        if isinstance(rule, dict):
            pattern = rule.get("pattern", "")
            match_type = rule.get("match", "prefix")
            if match_type == "exact" and url == pattern:
                return rule.get("allow_http", False)
            elif match_type != "exact" and pattern and url.startswith(pattern):
                return rule.get("allow_http", False)

    # Default to not allowing HTTP
    return False


def run_crawl_job(log, job_id: str = None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    allow_block = _load_allow_block()
    crawler_config = _load_crawler_config()
    max_depth = crawler_config.get("max_depth", 0)
    required_profiles = collect_required_profiles(crawler_config, allow_block)
    if required_profiles:
        log(f"Validating {len(required_profiles)} auth profile(s) before crawl")
        results = run_auth_checks(required_profiles.keys())
        invalid_profiles = [
            result for result in results.values() if not result.get("ok")
        ]
        if invalid_profiles:
            for result in invalid_profiles:
                log(
                    f"Auth profile '{result.get('profile_name')}' invalid: {result.get('error_reason')}"
                )
            first_failure = invalid_profiles[0]
            profile_name = first_failure.get("profile_name")
            reason = first_failure.get("error_reason") or "auth validation failed"
            raise RuntimeError(
                f"Auth profile '{profile_name}' is invalid ({reason}). Refresh token and retry."
            )

    # Extract seeds and normalize to URL strings
    raw_seeds = allow_block.get("seed_urls", [])
    seeds = []
    for seed in raw_seeds:
        if isinstance(seed, dict):
            seeds.append(seed.get("url", ""))
        else:
            seeds.append(seed)
    seeds = [s for s in seeds if s]  # Filter out empty strings

    # Initialize metrics tracking
    metrics = {
        "total_seeds": len(seeds),
        "total_candidates": 0,
        "crawled": 0,
        "captured": 0,
        "artifacts_written": 0,
        "errors": 0,
        "skipped": {
            "already_processed": 0,
            "depth_exceeded": 0,
            "not_allowed": 0,
            "auth_required": 0,
            "non_html": 0
        },
        "errors_by_class": {
            "4xx": 0,
            "5xx": 0,
            "network_timeout": 0,
            "other": 0
        },
        "error_details": []
    }

    log(f"Starting crawl job with {len(seeds)} seed(s)")
    log("Using per-row HTTP/HTTPS configuration")

    # Append candidates for each seed with its individual allow_http setting
    for seed_url in seeds:
        allow_http = _get_allow_http_for_url(seed_url, allow_block)
        _append_candidates([seed_url], "seed", 0, max_depth, allow_http)
    if not CANDIDATE_PATH.exists():
        log("No candidates to process.")
        _save_job_summary(job_id, metrics)
        return
    processed = _load_processed()
    candidates = CANDIDATE_PATH.read_text(encoding="utf-8").splitlines()
    metrics["total_candidates"] = len(candidates)
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
            metrics["skipped"]["already_processed"] += 1
            continue
        if depth > max_depth:
            metrics["skipped"]["depth_exceeded"] += 1
            processed.add(url)
            _save_processed(processed)
            continue
        if not _is_allowed(url, allow_block):
            metrics["skipped"]["not_allowed"] += 1
            processed.add(url)
            _save_processed(processed)
            continue
        log(f"Crawling {url} (depth {depth})")
        metrics["crawled"] += 1
        try:
            # Determine allow_http flag for this specific URL
            url_allow_http = _get_allow_http_for_url(url, allow_block)
            links = _capture_url(url, url_allow_http)
            _append_candidates(links, url, depth + 1, max_depth, url_allow_http)
            metrics["captured"] += 1
            metrics["artifacts_written"] += 1
            log(f"Captured {url} with {len(links)} link(s)")
        except AuthRequiredError as exc:
            metrics["skipped"]["auth_required"] += 1
            record_auth_hint(exc.auth_info)
            auth_location = exc.auth_info.get("redirect_location", "")
            log(f"Auth required for {url} (redirect to {auth_location})")
        except httpx.HTTPStatusError as exc:
            metrics["errors"] += 1
            status_code = exc.response.status_code

            # Check if this is an auth redirect
            if status_code in [301, 302, 303, 307, 308]:
                location = exc.response.headers.get("location", "")
                auth_hosts = ["auth.brightspot.byu.edu", "y.byu.edu/logout", "cas.byu.edu", "login.byu.edu"]
                if any(host in location.lower() for host in auth_hosts):
                    metrics["skipped"]["auth_required"] += 1
                    error_msg = f"{url}: Auth required (redirect to {location})"
                else:
                    error_msg = f"{url}: HTTP {status_code} redirect"
            elif 400 <= status_code < 500:
                metrics["errors_by_class"]["4xx"] += 1
                error_msg = f"{url}: HTTP {status_code}"
            elif 500 <= status_code < 600:
                metrics["errors_by_class"]["5xx"] += 1
                error_msg = f"{url}: HTTP {status_code}"
            else:
                metrics["errors_by_class"]["other"] += 1
                error_msg = f"{url}: HTTP {status_code}"

            if len(metrics["error_details"]) < 10:
                metrics["error_details"].append(error_msg)
            log(f"Error capturing {url}: {exc}")
        except (httpx.TimeoutException, httpx.ConnectTimeout, httpx.ReadTimeout, httpx.WriteTimeout, httpx.PoolTimeout) as exc:
            metrics["errors"] += 1
            metrics["errors_by_class"]["network_timeout"] += 1
            error_msg = f"{url}: Timeout"
            if len(metrics["error_details"]) < 10:
                metrics["error_details"].append(error_msg)
            log(f"Error capturing {url}: {exc}")
        except Exception as exc:
            metrics["errors"] += 1
            # Check if it's a non-HTML content type error
            error_str = str(exc).lower()
            if "content-type" in error_str or "not html" in error_str:
                metrics["skipped"]["non_html"] += 1
                error_msg = f"{url}: Non-HTML content"
            else:
                metrics["errors_by_class"]["other"] += 1
                error_msg = f"{url}: {str(exc)}"
            if len(metrics["error_details"]) < 10:
                metrics["error_details"].append(error_msg)
            log(f"Error capturing {url}: {exc}")
        processed.add(url)
        _save_processed(processed)

    # Save summary at the end
    _save_job_summary(job_id, metrics)

    # Log summary
    log("=" * 60)
    log("Crawl Summary:")
    log(f"  Total seeds: {metrics['total_seeds']}")
    log(f"  Candidates loaded: {metrics['total_candidates']}")
    log(f"  URLs crawled: {metrics['crawled']}")
    log(f"  Successfully captured: {metrics['captured']}")
    log(f"  Artifacts written: {metrics['artifacts_written']}")
    log("")
    log("  Skipped:")
    log(f"    Already processed: {metrics['skipped']['already_processed']}")
    log(f"    Depth exceeded: {metrics['skipped']['depth_exceeded']}")
    log(f"    Not allowed: {metrics['skipped']['not_allowed']}")
    log(f"    Auth required: {metrics['skipped']['auth_required']}")
    log(f"    Non-HTML: {metrics['skipped']['non_html']}")
    log("")
    log(f"  Total errors: {metrics['errors']}")
    log("  Errors by class:")
    log(f"    4xx: {metrics['errors_by_class']['4xx']}")
    log(f"    5xx: {metrics['errors_by_class']['5xx']}")
    log(f"    Network/Timeout: {metrics['errors_by_class']['network_timeout']}")
    log(f"    Other: {metrics['errors_by_class']['other']}")
    log("=" * 60)
    log("Crawl job complete")


def _save_job_summary(job_id: str, metrics: Dict) -> None:
    if not job_id:
        return
    summary_dir = Path("/app/data/logs/summaries")
    summary_dir.mkdir(parents=True, exist_ok=True)
    summary_path = summary_dir / f"{job_id}.json"
    summary_path.write_text(json.dumps(metrics, indent=2), encoding="utf-8")
