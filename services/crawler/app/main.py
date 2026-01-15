import json
import logging
from pathlib import Path

import yaml

from app.capture import capture_and_discover
from app.discovery import append_candidates, is_allowed, load_allow_block, load_crawler_config

CONFIG_PATH = Path("/app/config/allow_block.yml")
CANDIDATE_PATH = Path("/app/data/candidates/candidates.jsonl")
PROCESSED_PATH = Path("/app/data/candidates/processed.json")


def load_seeds():
    config = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8")) or {}
    return config.get("seed_urls", [])


def _load_processed() -> set:
    if not PROCESSED_PATH.exists():
        return set()
    try:
        data = json.loads(PROCESSED_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return set()
    return set(data or [])


def _save_processed(processed: set) -> None:
    PROCESSED_PATH.parent.mkdir(parents=True, exist_ok=True)
    PROCESSED_PATH.write_text(json.dumps(sorted(processed)), encoding="utf-8")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    config = load_allow_block()
    crawler_config = load_crawler_config()
    max_depth = crawler_config.get("max_depth", 0)
    seeds = load_seeds()
    append_candidates(seeds, "seed", 0)
    if not CANDIDATE_PATH.exists():
        return
    processed = _load_processed()
    for line in CANDIDATE_PATH.read_text(encoding="utf-8").splitlines():
        entry = json.loads(line)
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
        if not is_allowed(url, config):
            processed.add(url)
            _save_processed(processed)
            continue
        try:
            capture_and_discover(url, depth)
        except Exception as exc:
            logging.error("Error capturing %s: %s", url, exc)
        processed.add(url)
        _save_processed(processed)


if __name__ == "__main__":
    main()
