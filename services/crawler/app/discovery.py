import json
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, List
from urllib.parse import urlparse

import yaml

from app.utils.url import canonicalize_url

CONFIG_PATH = Path("/app/config/allow_block.yml")
CRAWLER_CONFIG = Path("/app/config/crawler.yml")
CANDIDATE_PATH = Path("/app/data/candidates/candidates.jsonl")


def load_allow_block() -> Dict[str, List[str]]:
    return yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8")) or {}


def load_crawler_config() -> Dict[str, Dict[str, List[str]]]:
    return yaml.safe_load(CRAWLER_CONFIG.read_text(encoding="utf-8")) or {}


def is_allowed(url: str, config: Dict[str, List[str]]) -> bool:
    parsed = urlparse(url)
    host = parsed.netloc
    path = parsed.path or "/"
    if host in config.get("blocked_domains", []):
        return False
    for blocked in config.get("blocked_paths", []):
        if path.startswith(blocked):
            return False
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
    allowed_domains = config.get("allowed_domains", [])
    if allowed_domains and host not in allowed_domains:
        return False
    return True


def append_candidates(urls: Iterable[str], source: str, depth: int) -> None:
    crawler_config = load_crawler_config()
    max_depth = crawler_config.get("max_depth", 0)
    if depth > max_depth:
        return
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
            canonical = canonicalize_url(url, url_config)
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
