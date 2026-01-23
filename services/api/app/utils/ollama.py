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


async def _maybe_async_validate(fn: Callable[[str], Awaitable[T] | T], raw: str) -> T:
    """
    Call `fn(raw)`. If `fn` is async, await it; otherwise call it directly.
    Returns the result (an instance of schema).
    """
    if asyncio.iscoroutinefunction(fn):
        return await fn(raw)  # type: ignore[return-value]
    else:
        return fn(raw)  # type: ignore[return-value]


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
            resp = await client.post(OLLAMA_URL, json={"model": OLLAMA_MODEL, "prompt": prompt})
            resp.raise_for_status()
        except Exception as e:
            logger.exception("Error calling Ollama generate endpoint")
            raise

        # extract text response (assumes response body contains the model text; adjust if your API differs)
        try:
            raw_body = None

            # Helpful: check content-type for NDJSON (streaming)
            content_type = resp.headers.get("content-type", "").lower()
            if "application/x-ndjson" in content_type or "ndjson" in content_type:
                # resp.text may include many newline-separated JSON objects.
                # Parse each line and join "response" fields (this matches Ollama's streaming structure).
                pieces = []
                for line in resp.text.splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                        # Ollama streaming uses "response" field for text fragments
                        if isinstance(obj, dict) and "response" in obj and obj["response"] is not None:
                            pieces.append(str(obj["response"]))
                        else:
                            # If object contains nested content fields, try common variants
                            if "text" in obj and isinstance(obj["text"], str):
                                pieces.append(obj["text"])
                            elif "output" in obj and isinstance(obj["output"], str):
                                pieces.append(obj["output"])
                            elif "choices" in obj and isinstance(obj["choices"], list):
                                # try to extract content from choices
                                for c in obj["choices"]:
                                    if isinstance(c, dict):
                                        if "message" in c and isinstance(c["message"], dict) and "content" in c["message"]:
                                            pieces.append(c["message"]["content"])
                                            break
                                        if "text" in c and isinstance(c["text"], str):
                                            pieces.append(c["text"])
                                            break
                    except Exception:
                        # ignore parsing errors for individual lines; include raw line in fallback
                        logger.debug("Failed to parse NDJSON line from Ollama: %r", line)
                        pieces.append(line)
                raw_body = "".join(pieces)

            else:
                # Try to parse response as JSON dict first (non-streaming)
                body_json = None
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
                        # try to extract content from choices
                        for c in body_json["choices"]:
                            if isinstance(c, dict):
                                if "message" in c and isinstance(c["message"], dict) and "content" in c["message"]:
                                    raw_body = c["message"]["content"]
                                    break
                                if "text" in c and isinstance(c["text"], str):
                                    raw_body = c["text"]
                                    break
                    else:
                        # fallback to entire JSON text
                        raw_body = json.dumps(body_json)
                else:
                    # If not JSON payload, treat response text as raw model output
                    # However, if resp.text includes multiple JSON objects (ndjson) join 'response' fields
                    text = resp.text
                    # detect ndjson-ish text with multiple JSON objects separated by newline
                    if "\n" in text and text.strip().startswith("{") and "response" in text:
                        pieces = []
                        for line in text.splitlines():
                            line = line.strip()
                            if not line:
                                continue
                            try:
                                obj = json.loads(line)
                                if isinstance(obj, dict) and "response" in obj and obj["response"] is not None:
                                    pieces.append(str(obj["response"]))
                                else:
                                    pieces.append(line)
                            except Exception:
                                pieces.append(line)
                        raw_body = "".join(pieces)
                    else:
                        raw_body = text

            # final fallback
            if raw_body is None:
                raw_body = resp.text

        except Exception as e:
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
                resp2 = await client.post(OLLAMA_URL, json={"model": OLLAMA_MODEL, "prompt": repair_prompt})
                resp2.raise_for_status()
                # reuse the same NDJSON-aware extraction logic for the repair response
                raw_body2 = None
                content_type2 = resp2.headers.get("content-type", "").lower()
                if "application/x-ndjson" in content_type2 or "ndjson" in content_type2:
                    pieces = []
                    for line in resp2.text.splitlines():
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            obj = json.loads(line)
                            if isinstance(obj, dict) and "response" in obj and obj["response"] is not None:
                                pieces.append(str(obj["response"]))
                            else:
                                if "text" in obj and isinstance(obj["text"], str):
                                    pieces.append(obj["text"])
                                elif "output" in obj and isinstance(obj["output"], str):
                                    pieces.append(obj["output"])
                                elif "choices" in obj and isinstance(obj["choices"], list):
                                    for c in obj["choices"]:
                                        if isinstance(c, dict):
                                            if "message" in c and isinstance(c["message"], dict) and "content" in c["message"]:
                                                pieces.append(c["message"]["content"])
                                                break
                                            if "text" in c and isinstance(c["text"], str):
                                                pieces.append(c["text"])
                                                break
                        except Exception:
                            logger.debug("Failed to parse NDJSON line (repair): %r", line)
                            pieces.append(line)
                    raw_body2 = "".join(pieces)
                else:
                    # non-ndjson fallback
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
                        text2 = resp2.text
                        if "\n" in text2 and text2.strip().startswith("{") and "response" in text2:
                            pieces = []
                            for line in text2.splitlines():
                                line = line.strip()
                                if not line:
                                    continue
                                try:
                                    obj = json.loads(line)
                                    if isinstance(obj, dict) and "response" in obj and obj["response"] is not None:
                                        pieces.append(str(obj["response"]))
                                    else:
                                        pieces.append(line)
                                except Exception:
                                    pieces.append(line)
                            raw_body2 = "".join(pieces)
                        else:
                            raw_body2 = text2

                if raw_body2 is None:
                    raw_body2 = resp2.text

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

