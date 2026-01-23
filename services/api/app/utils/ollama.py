# services/api/app/utils/ollama.py
import json
import logging
import time
import httpx
import os
from pathlib import Path
from typing import Any, Dict, Optional, Type, TypeVar
from pydantic import BaseModel, ValidationError

T = TypeVar("T", bound=BaseModel)
logger = logging.getLogger(__name__)

import asyncio
from typing import Callable, Awaitable

OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL") or "qwen2.5:latest"
logger.info("Using Ollama model: %s (from OLLAMA_MODEL env or default)", OLLAMA_MODEL)
OLLAMA_SEED = os.environ.get("OLLAMA_SEED")
OLLAMA_OPTIONS: Dict[str, Any] = {
    "temperature": float(os.environ.get("OLLAMA_TEMPERATURE", "0")),
    "top_p": float(os.environ.get("OLLAMA_TOP_P", "0.1")),
    "repeat_penalty": float(os.environ.get("OLLAMA_REPEAT_PENALTY", "1.05")),
}
if OLLAMA_SEED is not None:
    try:
        OLLAMA_OPTIONS["seed"] = int(OLLAMA_SEED)
    except ValueError:
        logger.warning("Invalid OLLAMA_SEED value %r; ignoring seed setting.", OLLAMA_SEED)
else:
    OLLAMA_OPTIONS["seed"] = 42


async def _maybe_async_validate(fn: Callable[[str], Awaitable[T] | T], raw: str) -> T:
    """
    Call `fn(raw)`. If `fn` is async, await it; otherwise call it directly.
    Returns the result (an instance of schema).
    """
    if asyncio.iscoroutinefunction(fn):
        return await fn(raw)  # type: ignore[return-value]
    else:
        return fn(raw)  # type: ignore[return-value]


def _parse_resp_text_and_join(resp) -> str:
    """Return stitched string from resp (NDJSON-aware). Useful for testing."""
    resp_text = resp.text or ""
    content_type = (resp.headers.get("content-type") or "").lower()

    # Heuristic: if content-type is NDJSON or response contains newline-delimited JSON lines,
    # parse each line and stitch together any "response" fields (Ollama streaming fragments).
    if "application/x-ndjson" in content_type or "\n{" in resp_text:
        lines = [L for L in resp_text.splitlines() if L.strip()]
        parts: list[str] = []
        for L in lines:
            try:
                obj = json.loads(L)
            except Exception:
                # not a JSON line — keep the raw line
                parts.append(L)
                continue

            # Prefer the streaming "response" token if present (common Ollama streaming format)
            if isinstance(obj, dict) and "response" in obj:
                parts.append(obj["response"])
            else:
                # fallback: stringify the object so we don't lose info
                parts.append(json.dumps(obj))

        raw_body = "".join(parts).strip()
        logger.debug("Detected NDJSON from Ollama: lines=%d joined_len=%d", len(lines), len(raw_body))
        return raw_body
    else:
        # Non-streaming path: try to parse full JSON first, else use resp.text
        body_json = None
        try:
            body_json = json.loads(resp_text)
        except Exception:
            body_json = None

        if isinstance(body_json, dict):
            if "text" in body_json and isinstance(body_json["text"], str):
                return body_json["text"]
            elif "output" in body_json and isinstance(body_json["output"], str):
                return body_json["output"]
            elif "choices" in body_json and isinstance(body_json["choices"], list):
                # pick first choice content (common LLM API shape)
                for c in body_json["choices"]:
                    if isinstance(c, dict):
                        if "message" in c and isinstance(c["message"], dict) and "content" in c["message"]:
                            return c["message"]["content"]
                        if "text" in c and isinstance(c["text"], str):
                            return c["text"]
            return json.dumps(body_json)
        else:
            return resp_text or ""


async def call_ollama_json(prompt: str, schema: Type[T]) -> T:
    """
    Call Ollama (async) and return an instance of `schema` validated from the model's JSON response.

    Behavior:
      - Call the model once and try to parse+validate its JSON.
      - If parsing/validation fails, re-prompt once with a short "repair JSON" instruction.
      - If still failing, save the raw response to data/logs/ollama_raw/ for debugging and raise ValueError.
    """
    # endpoint - keep consistent with your environment
    OLLAMA_URL = "http://ollama:11434/api/generate"

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

    # helper to persist raw model text for debugging
    def _dump_raw(raw_text: str, tag: str = "json_fail"):
        try:
            debug_dir = Path("data/logs/ollama_raw")
            debug_dir.mkdir(parents=True, exist_ok=True)
            fname = debug_dir / f"{tag}_{int(time.time())}.txt"
            fname.write_text(raw_text, encoding="utf-8")
            logger.error("Wrote raw Ollama response to %s", str(fname))
        except Exception:
            logger.exception("Failed to write ollama raw response to disk")

    # make the HTTP call once
    async with httpx.AsyncClient(timeout=60.0) as client:
        # The exact request body / headers depend on your Ollama usage.
        # This mirrors a typical generate call and ensures prompt is passed through.
        try:
            logger.info("Calling Ollama model=%s endpoint=%s", OLLAMA_MODEL, OLLAMA_URL)
            resp = await client.post(
                OLLAMA_URL,
                json={"model": OLLAMA_MODEL, "prompt": prompt, "options": OLLAMA_OPTIONS},
            )
            resp.raise_for_status()
        except Exception as e:
            logger.exception("Error calling Ollama generate endpoint")
            raise

        # extract text response (assumes response body contains the model text; adjust if your API differs)
        try:
            raw_body = None

            # full response text from httpx (Ollama streams NDJSON as plain text)
            resp_text = resp.text or ""
            content_type = (resp.headers.get("content-type") or "").lower()

            # Heuristic: if content-type is NDJSON or response contains newline-delimited JSON lines,
            # parse each line and stitch together any "response" fields (Ollama streaming fragments).
            if "application/x-ndjson" in content_type or "\n{" in resp_text:
                lines = [L for L in resp_text.splitlines() if L.strip()]
                parts: list[str] = []
                for L in lines:
                    try:
                        obj = json.loads(L)
                    except Exception:
                        # not a JSON line — keep the raw line
                        parts.append(L)
                        continue

                    # Prefer the streaming "response" token if present (common Ollama streaming format)
                    if isinstance(obj, dict) and "response" in obj:
                        parts.append(obj["response"])
                    else:
                        # fallback: stringify the object so we don't lose info
                        parts.append(json.dumps(obj))

                raw_body = "".join(parts).strip()
                logger.debug("Detected NDJSON from Ollama: lines=%d joined_len=%d", len(lines), len(raw_body))
            else:
                # Non-streaming path: try to parse full JSON first, else use resp.text
                body_json = None
                try:
                    body_json = resp.json()
                except Exception:
                    body_json = None

                if isinstance(body_json, dict):
                    if "text" in body_json and isinstance(body_json["text"], str):
                        raw_body = body_json["text"]
                    elif "output" in body_json and isinstance(body_json["output"], str):
                        raw_body = body_json["output"]
                    elif "choices" in body_json and isinstance(body_json["choices"], list):
                        # pick first choice content (common LLM API shape)
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
                    raw_body = resp_text

            if raw_body is None:
                raw_body = resp_text or ""
        except Exception:
            logger.exception("Failed to extract body from Ollama response")
            raise

    # First try to parse + validate
    try:
        return await _maybe_async_validate(_parse_and_validate, raw_body)
    except Exception as first_error:
        # Save raw and attempt one repair re-prompt
        _dump_raw(raw_body, tag="first_fail")

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
            # ignore schema formatting errors
            repair_prompt += f"{schema.__name__}"

        # call Ollama one more time with the repair prompt
        async with httpx.AsyncClient(timeout=60.0) as client:
            try:
                logger.info("Calling Ollama (repair) model=%s endpoint=%s", OLLAMA_MODEL, OLLAMA_URL)
                resp2 = await client.post(
                    OLLAMA_URL,
                    json={"model": OLLAMA_MODEL, "prompt": repair_prompt, "options": OLLAMA_OPTIONS},
                )
                resp2.raise_for_status()
                # reuse the same NDJSON-aware extraction logic for the repair response
                raw_body2 = None
                resp_text2 = resp2.text or ""
                content_type2 = (resp2.headers.get("content-type") or "").lower()

                # Same NDJSON detection heuristic as initial response
                if "application/x-ndjson" in content_type2 or "\n{" in resp_text2:
                    lines2 = [L for L in resp_text2.splitlines() if L.strip()]
                    parts2: list[str] = []
                    for L in lines2:
                        try:
                            obj = json.loads(L)
                        except Exception:
                            parts2.append(L)
                            continue

                        if isinstance(obj, dict) and "response" in obj:
                            parts2.append(obj["response"])
                        else:
                            parts2.append(json.dumps(obj))

                    raw_body2 = "".join(parts2).strip()
                    logger.debug("Detected NDJSON from Ollama (repair): lines=%d joined_len=%d", len(lines2), len(raw_body2))
                else:
                    # Non-streaming path
                    body_json2 = None
                    try:
                        body_json2 = resp2.json()
                    except Exception:
                        body_json2 = None

                    if isinstance(body_json2, dict):
                        if "text" in body_json2 and isinstance(body_json2["text"], str):
                            raw_body2 = body_json2["text"]
                        elif "output" in body_json2 and isinstance(body_json2["output"], str):
                            raw_body2 = body_json2["output"]
                        elif "choices" in body_json2 and isinstance(body_json2["choices"], list):
                            for c in body_json2["choices"]:
                                if isinstance(c, dict):
                                    if "message" in c and isinstance(c["message"], dict) and "content" in c["message"]:
                                        raw_body2 = c["message"]["content"]
                                        break
                                    if "text" in c and isinstance(c["text"], str):
                                        raw_body2 = c["text"]
                                        break
                        else:
                            raw_body2 = json.dumps(body_json2)
                    else:
                        raw_body2 = resp_text2

                if raw_body2 is None:
                    raw_body2 = resp_text2 or ""

            except Exception:
                logger.exception("Repair request to Ollama failed")
                raise

        # Try parse + validate again
        try:
            return await _maybe_async_validate(_parse_and_validate, raw_body2)
        except Exception as second_error:
            # dump second raw for debugging and raise a helpful error
            _dump_raw(raw_body2, tag="second_fail")
            logger.exception("Second validation attempt failed")
            raise ValueError(
                "Model response could not be parsed into the expected schema after a repair attempt. "
                f"First error: {first_error}; Second error: {second_error}\n"
                f"Snippets:\nfirst_raw={raw_body[:1000]!r}\nsecond_raw={raw_body2[:1000]!r}"
            )
