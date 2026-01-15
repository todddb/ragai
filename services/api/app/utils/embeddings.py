from typing import List

import httpx

from app.utils.config import load_system_config


async def embed_text(text: str) -> List[float]:
    config = load_system_config()
    host = config["ollama"]["host"]
    model = config["ollama"]["embedding_model"]
    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{host}/api/embeddings",
            json={"model": model, "prompt": text},
            timeout=60.0,
        )
        response.raise_for_status()
        payload = response.json()
    return payload["embedding"]
