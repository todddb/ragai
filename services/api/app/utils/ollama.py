import json
from typing import Any, Dict, Optional, Type, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from app.utils.config import load_system_config

T = TypeVar("T", bound=BaseModel)


async def call_ollama_json(prompt: str, schema: Type[T]) -> T:
    config = load_system_config()
    host = config["ollama"]["host"]
    model = config["ollama"]["model"]
    schema_keys = ", ".join(schema.model_fields.keys())
    wrapped_prompt = (
        "You are a strict JSON generator. Return ONLY a single JSON object with double quotes.\n"
        "Do not include markdown, code fences, or any extra commentary.\n"
        f"The JSON object MUST include exactly these keys: {schema_keys}.\n"
        f"User prompt:\n{prompt}"
    )

    last_error: Optional[str] = None
    last_raw: Optional[str] = None

    def _extract_json(candidate: str) -> Dict[str, Any]:
        return json.loads(candidate)

    def _extract_first_json_object(text: str) -> Optional[Dict[str, Any]]:
        start = text.find("{")
        if start == -1:
            return None
        depth = 0
        for index in range(start, len(text)):
            char = text[index]
            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    return json.loads(text[start : index + 1])
        return None

    async with httpx.AsyncClient() as client:
        for _ in range(3):
            response = await client.post(
                f"{host}/api/generate",
                json={
                    "model": model,
                    "prompt": wrapped_prompt,
                    "stream": False,
                    "format": "json",
                    "options": {"temperature": 0},
                },
                timeout=120.0,
            )
            response.raise_for_status()
            payload: Dict[str, Any] = response.json()
            raw = str(payload.get("response", "")).strip()
            try:
                output_json = _extract_json(raw)
            except json.JSONDecodeError as exc:
                last_error = f"json_decode_error: {exc}"
                last_raw = raw
                try:
                    extracted = _extract_first_json_object(raw)
                    if extracted is None:
                        continue
                    output_json = extracted
                except json.JSONDecodeError as inner_exc:
                    last_error = f"json_extract_error: {inner_exc}"
                    last_raw = raw
                    continue
            try:
                return schema(**output_json)
            except ValidationError as exc:
                last_error = f"validation_error: {exc}"
                last_raw = raw
                continue
        snippet = (last_raw or "")[:500]
        raise ValueError(
            "Unable to validate response from model. "
            f"last_error={last_error}, response_snippet={snippet}"
        )
