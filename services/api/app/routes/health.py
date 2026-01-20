from typing import Dict, Optional

import httpx
from fastapi import APIRouter

from app.utils.config import load_system_config

router = APIRouter(prefix="/api", tags=["health"])


@router.get("/health")
async def health() -> Dict[str, str]:
    config = load_system_config()
    ollama_host = config["ollama"]["host"]
    model = config["ollama"]["model"]
    embedding_model = config["ollama"].get("embedding_model", "")
    qdrant_host = config.get("qdrant", {}).get("host", "")

    ollama_status = "down"
    qdrant_status = "down"
    model_available: Optional[bool] = None
    embedding_available: Optional[bool] = None

    # Check Ollama connectivity
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{ollama_host}/api/tags", timeout=1.0)
            response.raise_for_status()
            payload = response.json()
            model_list = [item.get("name", "") for item in payload.get("models", [])]
            model_available = model in model_list
            embedding_available = embedding_model in model_list if embedding_model else None
        ollama_status = "ok"
    except Exception:
        ollama_status = "down"

    # Check Qdrant connectivity
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{qdrant_host}/collections", timeout=1.0)
            response.raise_for_status()
        qdrant_status = "ok"
    except Exception:
        qdrant_status = "down"

    return {
        "api": "ok",
        "ollama": ollama_status,
        "qdrant": qdrant_status,
        "ollama_url": ollama_host,
        "qdrant_url": qdrant_host,
        "model": model,
        "embedding_model": embedding_model,
        "model_available": str(model_available) if model_available is not None else "unknown",
        "embedding_model_available": (
            str(embedding_available) if embedding_available is not None else "unknown"
        ),
    }
