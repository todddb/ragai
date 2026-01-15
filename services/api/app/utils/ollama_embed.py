from typing import List, Optional

import httpx

_EMBED_ENDPOINT_CACHE: Optional[str] = None

_ENDPOINTS = [
    {"path": "/api/embed", "payload_key": "input"},
    {"path": "/api/embeddings", "payload_key": "prompt"},
]


def _extract_embedding(payload: dict) -> Optional[List[float]]:
    if "embedding" in payload:
        return payload["embedding"]
    if "embeddings" in payload and payload["embeddings"]:
        return payload["embeddings"][0]
    if "data" in payload and payload["data"]:
        return payload["data"][0].get("embedding")
    return None


def _iter_endpoints() -> List[dict]:
    if _EMBED_ENDPOINT_CACHE:
        cached = next((ep for ep in _ENDPOINTS if ep["path"] == _EMBED_ENDPOINT_CACHE), None)
        if cached:
            return [cached] + [ep for ep in _ENDPOINTS if ep["path"] != _EMBED_ENDPOINT_CACHE]
    return list(_ENDPOINTS)


def embed_text(host: str, model: str, text: str) -> List[float]:
    global _EMBED_ENDPOINT_CACHE
    with httpx.Client() as client:
        for endpoint in _iter_endpoints():
            response = client.post(
                f"{host}{endpoint['path']}",
                json={"model": model, endpoint["payload_key"]: text},
                timeout=60.0,
            )
            if response.status_code == 404:
                if _EMBED_ENDPOINT_CACHE == endpoint["path"]:
                    _EMBED_ENDPOINT_CACHE = None
                continue
            response.raise_for_status()
            payload = response.json()
            embedding = _extract_embedding(payload)
            if embedding is None:
                raise ValueError(f"Missing embedding in response from {endpoint['path']}")
            _EMBED_ENDPOINT_CACHE = endpoint["path"]
            return embedding
    raise RuntimeError(
        f"Ollama embedding endpoint not found at {host}. Tried "
        f"{', '.join(ep['path'] for ep in _ENDPOINTS)}. "
        "Ensure the Ollama server supports embeddings."
    )


async def embed_text_async(host: str, model: str, text: str) -> List[float]:
    global _EMBED_ENDPOINT_CACHE
    async with httpx.AsyncClient() as client:
        for endpoint in _iter_endpoints():
            response = await client.post(
                f"{host}{endpoint['path']}",
                json={"model": model, endpoint["payload_key"]: text},
                timeout=60.0,
            )
            if response.status_code == 404:
                if _EMBED_ENDPOINT_CACHE == endpoint["path"]:
                    _EMBED_ENDPOINT_CACHE = None
                continue
            response.raise_for_status()
            payload = response.json()
            embedding = _extract_embedding(payload)
            if embedding is None:
                raise ValueError(f"Missing embedding in response from {endpoint['path']}")
            _EMBED_ENDPOINT_CACHE = endpoint["path"]
            return embedding
    raise RuntimeError(
        f"Ollama embedding endpoint not found at {host}. Tried "
        f"{', '.join(ep['path'] for ep in _ENDPOINTS)}. "
        "Ensure the Ollama server supports embeddings."
    )
