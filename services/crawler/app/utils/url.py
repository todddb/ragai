import hashlib
from typing import Dict, List
from urllib.parse import parse_qsl, urlparse, urlunparse


def canonicalize_url(url: str, config: Dict[str, List[str]]) -> str:
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
    normalized = urlunparse((scheme, host, path, "", query, ""))
    return normalized


def doc_id_for_url(canonical_url: str) -> str:
    return hashlib.sha256(canonical_url.encode("utf-8")).hexdigest()
