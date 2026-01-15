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
    ollama_status = "error"

    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{ollama_host}/api/tags", timeout=5.0)
            response.raise_for_status()
        ollama_status = "ok"
    except Exception:
        ollama_status = "error"

    return {"api": "ok", "ollama": ollama_status, "model": model}
