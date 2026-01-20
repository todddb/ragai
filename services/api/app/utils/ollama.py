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
        "No trailing commentary. No extra keys. Use double quotes only.\n"
        f"The JSON object MUST include exactly these keys: {schema_keys}.\n"
        f"User prompt:\n{prompt}"
    )

    last_error: Optional[str] = None
    last_raw: Optional[str] = None

    def _iter_json_candidates(text: str) -> list[str]:
        candidates: list[str] = []
        stripped = text.strip()
        if stripped.startswith("{") and stripped.endswith("}"):
            candidates.append(stripped)

        in_string = False
        escape_next = False
        depth = 0
        start: Optional[int] = None
        for index, char in enumerate(text):
            if escape_next:
                escape_next = False
                continue
            if char == "\\" and in_string:
                escape_next = True
                continue
            if char == '"':
                in_string = not in_string
                continue
            if in_string:
                continue
            if char == "{":
                if depth == 0:
                    start = index
                depth += 1
            elif char == "}":
                if depth > 0:
                    depth -= 1
                    if depth == 0 and start is not None:
                        candidates.append(text[start : index + 1])
                        start = None
        return candidates

    async with httpx.AsyncClient() as client:
        for _ in range(3):
            response = await client.post(
                f"{host}/api/generate",
                json={
                    "model": model,
                    "prompt": wrapped_prompt,
                    "stream": False,
                    "format": "json",
                    "options": {
                        "temperature": 0,
                        "num_predict": 512,
                    },
                },
                timeout=120.0,
            )
            response.raise_for_status()
            payload: Dict[str, Any] = response.json()
            raw = str(payload.get("response", "")).strip()
            last_raw = raw
            candidates = _iter_json_candidates(raw)
            if not candidates:
                last_error = "json_extract_error: no_json_object_found"
                continue
            for candidate in candidates:
                try:
                    output_json = json.loads(candidate)
                except json.JSONDecodeError as exc:
                    last_error = f"json_decode_error: {exc}"
                    continue
                try:
                    return schema(**output_json)
                except ValidationError as exc:
                    last_error = f"validation_error: {exc}"
                    continue
        snippet = (last_raw or "")[:500]
        prompt_snippet = (prompt or "")[:500]
        raise ValueError(
            "Unable to validate response from model. "
            f"last_error={last_error}, model={model}, "
            f"response_snippet={snippet}, prompt_snippet={prompt_snippet}"
        )
