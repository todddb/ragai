# services/api/app/utils/ollama.py
import asyncio
import json
import logging
import os
import time
from pathlib import Path
from typing import Callable, Awaitable, Type, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

T = TypeVar("T", bound=BaseModel)
logger = logging.getLogger(__name__)

# Config via environment (docker-compose already sets these)
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "qwen2.5:latest")
OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://ollama:11434")
OLLAMA_URL = f"{OLLAMA_HOST.rstrip('/')}/api/generate"


async def _maybe_async_validate(fn: Callable[[str], Awaitable[T] | T], raw: str) -> T:
    """
    Call `fn(raw)`. If `fn` is async, await it; otherwise call it directly.
    Returns the result (an instance of schema).
    """
    if asyncio.iscoroutinefunction(fn):
        return await fn(raw)  # type: ignore[return-value]
    else:
        return fn(raw)  # type: ignore[return-value]


def _dump_raw(raw_text: str, prompt: str, schema_name: str, tag: str = "json_fail") -> None:
    """
    Persist raw model outputs plus a small metadata file for easier debugging.
    """
    try:
        debug_dir = Path("data/logs/ollama_raw")
        debug_dir.mkdir(parents=True, exist_ok=True)
        ts = int(time.time())
        fname = debug_dir / f"{tag}_{ts}.txt"
        fname.write_text(raw_text, encoding="utf-8")
        meta = {
            "timestamp": ts,
            "schema": schema_name,
            "prompt_snippet": (prompt or "")[:1000],
            "tag": tag,
        }
        meta_fname = debug_dir / f"{tag}_{ts}.meta.json"
        meta_fname.write_text(json.dumps(meta, indent=2), encoding="utf-8")
        logger.error("Wrote raw Ollama response to %s (meta %s)", str(fname), str(meta_fname))
    except Exception:
        logger.exception("Failed to write ollama raw response to disk")


async def call_ollama_json(prompt: str, schema: Type[T]) -> T:
    """
    Call Ollama (async) and return an instance of `schema` validated from the model's JSON response.

    Behavior:
      - Call the model once and try to parse+validate its JSON.
      - If parsing/validation fails, re-prompt once with a short "repair JSON" instruction.
      - If still failing, save the raw response to data/logs/ollama_raw/ for debugging and raise ValueError.
    """
    async def _parse_and_validate(raw_text: str) -> T:
        # First parse JSON
        try:
            parsed = json.loads(raw_text)
        except Exception as e:
            raise ValueError(f"Failed to parse JSON from model response: {e}\nraw_snippet={raw_text[:1000]!r}")

        # Then validate into the schema (support both pydantic v1 & v2 patterns)
        try:
            # prefer v2-style model_validate if available
            if hasattr(schema, "model_validate"):
                return schema.model_validate(parsed)  # type: ignore[call-arg]
            # prefer parse_obj (v1)
            if hasattr(schema, "parse_obj"):
                return schema.parse_obj(parsed)  # type: ignore[call-arg]
            # fallback to constructor
            return schema(**parsed)  # type: ignore[call-arg]
        except ValidationError as ve:
            # re-raise so caller can decide; include the validation error text
            raise ve

    # make the HTTP call once
    async with httpx.AsyncClient(timeout=60.0) as client:
        try:
            logger.info("Calling Ollama model=%s endpoint=%s", OLLAMA_MODEL, OLLAMA_URL)
            resp = await client.post(OLLAMA_URL, json={"model": OLLAMA_MODEL, "prompt": prompt})
            try:
                resp.raise_for_status()
            except Exception:
                # log body to make 404/400 easier to debug
                logger.error("Ollama responded with status=%s body=%s", resp.status_code, resp.text[:2000])
                resp.raise_for_status()
        except Exception:
            logger.exception("Error calling Ollama generate endpoint")
            raise

        # extract text response (assumes response body contains the model text; adjust if your API differs)
        try:
            raw_body = None
            try:
                body_json = resp.json()
            except Exception:
                body_json = None

            if isinstance(body_json, dict):
                # Try common places for generated text
                if "text" in body_json and isinstance(body_json["text"], str):
                    raw_body = body_json["text"]
                elif "output" in body_json and isinstance(body_json["output"], str):
                    raw_body = body_json["output"]
                elif "choices" in body_json and isinstance(body_json["choices"], list):
                    for c in body_json["choices"]:
                        if isinstance(c, dict):
                            if "message" in c and isinstance(c["message"], dict) and "content" in c["message"]:
                                raw_body = c["message"]["content"]
                                break
                            if "text" in c and isinstance(c["text"], str):
                                raw_body = c["text"]
                                break
                else:
                    raw_body = json.dumps(body_json)
            else:
                raw_body = resp.text

            if raw_body is None:
                raw_body = resp.text
        except Exception:
            logger.exception("Failed to extract body from Ollama response")
            raise

    # First try to parse + validate
    try:
        return await _maybe_async_validate(_parse_and_validate, raw_body)
    except Exception as first_error:
        # Save raw and attempt one repair re-prompt
        _dump_raw(raw_body, prompt, schema.__name__ if hasattr(schema, "__name__") else str(schema), tag="first_fail")

        logger.warning(
            "Initial validation failed: %s. Attempting one repair prompt to enforce JSON.",
            getattr(first_error, "errors", repr(first_error)),
        )

        # Construct a short repair prompt instructing model to return only JSON matching the schema
        repair_prompt = (
            prompt
            + "\n\n"
            + "IMPORTANT: Your previous response was invalid. Return ONLY a JSON object that matches the schema below.\n"
            + "If a field is not applicable, use an empty list or null where appropriate.\n"
            + "Schema: "
        )

        # Try to include schema example if possible
        try:
            if hasattr(schema, "schema_json"):
                repair_prompt += schema.schema_json()
            elif hasattr(schema, "schema"):
                repair_prompt += json.dumps(schema.schema(), indent=2)
            else:
                repair_prompt += f"{schema}"
        except Exception:
            repair_prompt += f"{getattr(schema, '__name__', str(schema))}"

        # call Ollama one more time with the repair prompt
        async with httpx.AsyncClient(timeout=60.0) as client:
            try:
                logger.info("Calling Ollama (repair) model=%s endpoint=%s", OLLAMA_MODEL, OLLAMA_URL)
                resp2 = await client.post(OLLAMA_URL, json={"model": OLLAMA_MODEL, "prompt": repair_prompt})
                try:
                    resp2.raise_for_status()
                except Exception:
                    logger.error("Ollama (repair) responded with status=%s body=%s", resp2.status_code, resp2.text[:2000])
                    resp2.raise_for_status()

                try:
                    content_type = resp2.headers.get("content-type", "").lower()
                    raw_body2 = resp2.json() if content_type.startswith("application/json") else resp2.text
                    if isinstance(raw_body2, dict):
                        raw_body2 = json.dumps(raw_body2)
                    else:
                        raw_body2 = str(raw_body2)
                except Exception:
                    raw_body2 = resp2.text
            except Exception:
                logger.exception("Repair request to Ollama failed")
                raise

        # Try parse + validate again
        try:
            return await _maybe_async_validate(_parse_and_validate, raw_body2)
        except Exception as second_error:
            # dump second raw for debugging and raise a helpful error
            _dump_raw(raw_body2, repair_prompt, schema.__name__ if hasattr(schema, "__name__") else str(schema), tag="second_fail")
            logger.exception("Second validation attempt failed")
            raise ValueError(
                "Model response could not be parsed into the expected schema after a repair attempt. "
                f"First error: {first_error}; Second error: {second_error}\n"
                f"Snippets:\nfirst_raw={raw_body[:1000]!r}\nsecond_raw={raw_body2[:1000]!r}"
            )

