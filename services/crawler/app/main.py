import json
from pathlib import Path

import yaml

from app.capture import capture_and_discover
from app.discovery import append_candidates, is_allowed, load_allow_block

CONFIG_PATH = Path("/app/config/allow_block.yml")
CANDIDATE_PATH = Path("/app/data/candidates/candidates.jsonl")


def load_seeds():
    config = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8")) or {}
    return config.get("seed_urls", [])


def main() -> None:
    config = load_allow_block()
    seeds = load_seeds()
    append_candidates(seeds, "seed")
    if not CANDIDATE_PATH.exists():
        return
    for line in CANDIDATE_PATH.read_text(encoding="utf-8").splitlines():
        entry = json.loads(line)
        url = entry.get("url")
        if not url:
            continue
        if not is_allowed(url, config):
            continue
        capture_and_discover(url)


if __name__ == "__main__":
    main()
