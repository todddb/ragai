import signal
import threading
from pathlib import Path
from typing import Any, Dict

import yaml

CONFIG_DIR = Path("/app/config")

_cache: Dict[str, Any] = {}


def _load_yaml(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def load_config(name: str) -> Dict[str, Any]:
    if name not in _cache:
        _cache[name] = _load_yaml(CONFIG_DIR / f"{name}.yml")
    return _cache[name]


def refresh_config(name: str) -> Dict[str, Any]:
    _cache[name] = _load_yaml(CONFIG_DIR / f"{name}.yml")
    return _cache[name]


def load_agents_config() -> Dict[str, Any]:
    return load_config("agents")


def load_system_config() -> Dict[str, Any]:
    return load_config("system")


def reload_all(_: int, __: Any) -> None:
    for name in ("agents", "system", "allow_block", "crawler", "ingest"):
        refresh_config(name)


# ✅ Only register SIGHUP when we're in the main thread and SIGHUP exists
try:
    if threading.current_thread() is threading.main_thread() and hasattr(signal, "SIGHUP"):
        signal.signal(signal.SIGHUP, reload_all)
except Exception:
    # Don't crash the API if signal handlers can't be installed
    pass
