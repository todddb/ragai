import json
from datetime import datetime
from pathlib import Path
from typing import Dict
from urllib.parse import urlparse

AUTH_HINTS_PATH = Path("/app/data/logs/auth_hints.json")
MAX_RECENT_HINTS = 50


def _load_auth_hints() -> Dict:
    if not AUTH_HINTS_PATH.exists():
        return {"by_domain": {}, "recent": []}
    try:
        return json.loads(AUTH_HINTS_PATH.read_text(encoding="utf-8")) or {"by_domain": {}, "recent": []}
    except json.JSONDecodeError:
        return {"by_domain": {}, "recent": []}


def record_auth_hint(auth_info: Dict[str, str]) -> None:
    if not auth_info:
        return
    original_url = auth_info.get("original_url")
    if not original_url:
        return
    parsed = urlparse(original_url)
    domain = parsed.hostname or ""
    if not domain:
        return
    data = _load_auth_hints()
    by_domain = data.get("by_domain", {})
    now = datetime.utcnow().isoformat() + "Z"
    entry = by_domain.get(domain, {"count": 0})
    entry["count"] = entry.get("count", 0) + 1
    entry["last_seen"] = now
    entry["redirect_host"] = auth_info.get("redirect_host", "")
    entry["matched_auth_pattern"] = auth_info.get("matched_auth_pattern", "")
    by_domain[domain] = entry
    recent = data.get("recent", [])
    recent.insert(
        0,
        {
            "original_url": original_url,
            "redirect_location": auth_info.get("redirect_location", ""),
            "redirect_host": auth_info.get("redirect_host", ""),
            "matched_auth_pattern": auth_info.get("matched_auth_pattern", ""),
            "last_seen": now,
        },
    )
    data["by_domain"] = by_domain
    data["recent"] = recent[:MAX_RECENT_HINTS]
    data["updated_at"] = now
    AUTH_HINTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    AUTH_HINTS_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")
