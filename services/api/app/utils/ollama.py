import json
from typing import Any, Dict, Type, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from app.utils.config import load_system_config

T = TypeVar("T", bound=BaseModel)


async def call_ollama_json(prompt: str, schema: Type[T]) -> T:
    config = load_system_config()
    host = config["ollama"]["host"]
    model = config["ollama"]["model"]

    async with httpx.AsyncClient() as client:
        for _ in range(3):
            response = await client.post(
                f"{host}/api/generate",
                json={
                    "model": model,
                    "prompt": prompt,
                    "stream": False,
                    "format": "json",
                },
                timeout=60.0,
            )
            response.raise_for_status()
            payload: Dict[str, Any] = response.json()
            try:
                output_json = json.loads(payload["response"])
                return schema(**output_json)
            except (json.JSONDecodeError, ValidationError):
                continue
        raise ValueError("Unable to validate response from model")
