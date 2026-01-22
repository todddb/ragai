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


def _extract_embeddings(payload: dict) -> Optional[List[List[float]]]:
    if "embeddings" in payload and payload["embeddings"]:
        return payload["embeddings"]
    if "data" in payload and payload["data"]:
        return [item.get("embedding") for item in payload["data"] if item.get("embedding") is not None]
    if "embedding" in payload:
        return [payload["embedding"]]
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


async def embed_texts_async(
    client: httpx.AsyncClient, host: str, model: str, texts: List[str]
) -> List[List[float]]:
    global _EMBED_ENDPOINT_CACHE
    if not texts:
        return []
    for endpoint in _iter_endpoints():
        payload_key = endpoint["payload_key"]
        if endpoint["path"] == "/api/embeddings" and len(texts) > 1:
            embeddings: List[List[float]] = []
            for text in texts:
                response = await client.post(
                    f"{host}{endpoint['path']}",
                    json={"model": model, payload_key: text},
                    timeout=60.0,
                )
                if response.status_code == 404:
                    if _EMBED_ENDPOINT_CACHE == endpoint["path"]:
                        _EMBED_ENDPOINT_CACHE = None
                    embeddings = []
                    break
                response.raise_for_status()
                payload = response.json()
                embedding = _extract_embedding(payload)
                if embedding is None:
                    raise ValueError(
                        f"Missing embedding in response from {endpoint['path']}"
                    )
                embeddings.append(embedding)
            if embeddings:
                _EMBED_ENDPOINT_CACHE = endpoint["path"]
                return embeddings
            continue
        response = await client.post(
            f"{host}{endpoint['path']}",
            json={"model": model, payload_key: texts if len(texts) > 1 else texts[0]},
            timeout=60.0,
        )
        if response.status_code == 404:
            if _EMBED_ENDPOINT_CACHE == endpoint["path"]:
                _EMBED_ENDPOINT_CACHE = None
            continue
        response.raise_for_status()
        payload = response.json()
        embeddings = _extract_embeddings(payload)
        if embeddings is None:
            raise ValueError(f"Missing embeddings in response from {endpoint['path']}")
        _EMBED_ENDPOINT_CACHE = endpoint["path"]
        return embeddings
    raise RuntimeError(
        f"Ollama embedding endpoint not found at {host}. Tried "
        f"{', '.join(ep['path'] for ep in _ENDPOINTS)}. "
        "Ensure the Ollama server supports embeddings."
    )
