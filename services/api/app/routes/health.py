from typing import Dict

import httpx
from fastapi import APIRouter

from app.utils.config import load_system_config

router = APIRouter(prefix="/api", tags=["health"])


@router.get("/health")
async def health() -> Dict[str, str]:
    config = load_system_config()
    ollama_host = config["ollama"]["host"]
    model = config["ollama"]["model"]
    qdrant_host = config.get("qdrant", {}).get("host", "")

    ollama_status = "down"
    qdrant_status = "down"

    # Check Ollama connectivity
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{ollama_host}/api/tags", timeout=1.0)
            response.raise_for_status()
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
    }
