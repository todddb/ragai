import asyncio
import json
import uuid
from pathlib import Path
from typing import Any, AsyncGenerator, Dict, List
from urllib.parse import urlparse

import yaml
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from qdrant_client import QdrantClient
from qdrant_client.http import models as rest

from app.utils.config import refresh_config
from app.utils.jobs import delete_job, get_job, list_jobs, start_job
from app.utils.ollama_embed import embed_text
from app.workers.ingest_worker import DB_PATH, run_ingest_job

router = APIRouter(prefix="/api/admin", tags=["admin"])

SECRETS_PATH = Path("/app/secrets/admin_tokens")
CONFIG_DIR = Path("/app/config")
CANDIDATES_PATH = Path("/app/data/candidates/candidates.jsonl")
PROCESSED_PATH = Path("/app/data/candidates/processed.json")
AUTH_HINTS_PATH = Path("/app/data/logs/auth_hints.json")


def _load_tokens() -> List[str]:
    if not SECRETS_PATH.exists():
        return []
    return [line.strip() for line in SECRETS_PATH.read_text(encoding="utf-8").splitlines() if line.strip()]


@router.post("/unlock")
async def unlock(payload: Dict[str, str]) -> Dict[str, str]:
    token = payload.get("token")
    if not token:
        raise HTTPException(status_code=400, detail="Missing token")
    if token not in _load_tokens():
        raise HTTPException(status_code=403, detail="Invalid token")
    return {"status": "ok"}


@router.get("/config/{name}")
async def get_config(name: str) -> Dict[str, Any]:
    path = CONFIG_DIR / f"{name}.yml"
    if not path.exists():
        raise HTTPException(status_code=404, detail="Config not found")
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


@router.put("/config/{name}")
async def update_config(name: str, payload: Dict[str, Any]) -> Dict[str, str]:
    path = CONFIG_DIR / f"{name}.yml"
    try:
        yaml.safe_dump(payload)
    except yaml.YAMLError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    refresh_config(name)
    return {"status": "ok"}


def _derive_allowed_domains(allow_rules: List[Dict[str, Any]]) -> List[str]:
    """Derive allowed_domains from allow_rules patterns."""
    domains = set()
    for rule in allow_rules:
        pattern = rule.get("pattern", "")
        try:
            parsed = urlparse(pattern)
            if parsed.netloc:
                domains.add(parsed.netloc)
        except Exception:
            continue
    return sorted(domains)


def _ensure_rule_id(rule: Dict[str, Any]) -> str:
    """Ensure a rule has an ID, generating one if missing."""
    if "id" not in rule or not rule["id"]:
        rule["id"] = str(uuid.uuid4())
    return rule["id"]


@router.post("/allowed-urls")
async def create_allowed_url(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Create a new allowed URL rule."""
    # Validate required fields
    if "pattern" not in payload or not payload["pattern"]:
        raise HTTPException(status_code=400, detail="Missing required field: pattern")

    # Set defaults
    rule = {
        "id": str(uuid.uuid4()),
        "pattern": payload["pattern"],
        "match": payload.get("match", "prefix"),
        "types": payload.get("types", {
            "web": True,
            "pdf": False,
            "docx": False,
            "xlsx": False,
            "pptx": False,
        }),
        "playwright": payload.get("playwright", False),
        "allow_http": payload.get("allow_http", False),
        "auth_profile": payload.get("auth_profile"),
    }

    # Validate match type
    if rule["match"] not in ["prefix", "exact"]:
        raise HTTPException(status_code=400, detail="Invalid match type (must be 'prefix' or 'exact')")

    # Load current config
    path = CONFIG_DIR / "allow_block.yml"
    config = yaml.safe_load(path.read_text(encoding="utf-8")) or {}

    # Ensure allow_rules exists
    if "allow_rules" not in config:
        config["allow_rules"] = []

    # Ensure existing rules have IDs
    for existing_rule in config["allow_rules"]:
        _ensure_rule_id(existing_rule)

    # Add new rule
    config["allow_rules"].append(rule)

    # Update allowed_domains
    config["allowed_domains"] = _derive_allowed_domains(config["allow_rules"])

    # Save config
    path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    refresh_config("allow_block")

    return rule


@router.put("/allowed-urls/{rule_id}")
async def update_allowed_url(rule_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """Update an existing allowed URL rule."""
    # Load current config
    path = CONFIG_DIR / "allow_block.yml"
    config = yaml.safe_load(path.read_text(encoding="utf-8")) or {}

    if "allow_rules" not in config:
        raise HTTPException(status_code=404, detail="No allow rules found")

    # Ensure existing rules have IDs
    for existing_rule in config["allow_rules"]:
        _ensure_rule_id(existing_rule)

    # Find the rule to update
    rule_index = None
    for i, rule in enumerate(config["allow_rules"]):
        if rule.get("id") == rule_id:
            rule_index = i
            break

    if rule_index is None:
        raise HTTPException(status_code=404, detail="Rule not found")

    # Update the rule
    updated_rule = {
        "id": rule_id,
        "pattern": payload.get("pattern", config["allow_rules"][rule_index].get("pattern")),
        "match": payload.get("match", config["allow_rules"][rule_index].get("match", "prefix")),
        "types": payload.get("types", config["allow_rules"][rule_index].get("types", {})),
        "playwright": payload.get("playwright", config["allow_rules"][rule_index].get("playwright", False)),
        "allow_http": payload.get("allow_http", config["allow_rules"][rule_index].get("allow_http", False)),
        "auth_profile": payload.get("auth_profile", config["allow_rules"][rule_index].get("auth_profile")),
    }

    # Validate match type
    if updated_rule["match"] not in ["prefix", "exact"]:
        raise HTTPException(status_code=400, detail="Invalid match type (must be 'prefix' or 'exact')")

    # Validate pattern
    if not updated_rule["pattern"]:
        raise HTTPException(status_code=400, detail="Pattern cannot be empty")

    # Update the rule in config
    config["allow_rules"][rule_index] = updated_rule

    # Update allowed_domains
    config["allowed_domains"] = _derive_allowed_domains(config["allow_rules"])

    # Save config
    path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    refresh_config("allow_block")

    return updated_rule


@router.delete("/allowed-urls/{rule_id}")
async def delete_allowed_url(rule_id: str) -> Dict[str, str]:
    """Delete an allowed URL rule."""
    # Load current config
    path = CONFIG_DIR / "allow_block.yml"
    config = yaml.safe_load(path.read_text(encoding="utf-8")) or {}

    if "allow_rules" not in config:
        raise HTTPException(status_code=404, detail="No allow rules found")

    # Ensure existing rules have IDs
    for existing_rule in config["allow_rules"]:
        _ensure_rule_id(existing_rule)

    # Find and remove the rule
    original_count = len(config["allow_rules"])
    config["allow_rules"] = [rule for rule in config["allow_rules"] if rule.get("id") != rule_id]

    if len(config["allow_rules"]) == original_count:
        raise HTTPException(status_code=404, detail="Rule not found")

    # Update allowed_domains
    config["allowed_domains"] = _derive_allowed_domains(config["allow_rules"])

    # Save config
    path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    refresh_config("allow_block")

    return {"status": "ok"}


@router.put("/playwright-settings")
async def update_playwright_settings(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Update Playwright settings (enabled flag and auth profiles)."""
    # Load current crawler config
    path = CONFIG_DIR / "crawler.yml"
    config = yaml.safe_load(path.read_text(encoding="utf-8")) or {}

    # Ensure playwright section exists
    if "playwright" not in config:
        config["playwright"] = {}

    # Update enabled flag if provided
    if "enabled" in payload:
        config["playwright"]["enabled"] = bool(payload["enabled"])

    # Update auth_profiles if provided
    if "auth_profiles" in payload:
        config["playwright"]["auth_profiles"] = payload["auth_profiles"]

    # Preserve other playwright settings
    for key in ["headless", "navigation_timeout_ms"]:
        if key not in config["playwright"] and key in payload:
            config["playwright"][key] = payload[key]

    # Save config
    path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    refresh_config("crawler")

    return config["playwright"]


@router.get("/candidates/recommendations")
async def candidate_recommendations() -> Dict[str, List[Dict[str, Any]]]:
    if not CANDIDATES_PATH.exists():
        return {"items": []}
    counts: Dict[str, Dict[str, Any]] = {}
    for line in CANDIDATES_PATH.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        url = entry.get("url")
        if not url:
            continue
        parsed = urlparse(url)
        if not parsed.scheme or not parsed.netloc:
            continue
        path_parts = [part for part in parsed.path.split("/") if part]
        if path_parts:
            suggested_url = f"{parsed.scheme}://{parsed.netloc}/{path_parts[0]}/"
        else:
            suggested_url = f"{parsed.scheme}://{parsed.netloc}/"
        seen_types = {
            "web": True,
            "pdf": False,
            "docx": False,
            "xlsx": False,
            "pptx": False,
        }
        lower_url = url.lower()
        if lower_url.endswith(".pdf"):
            seen_types = {**seen_types, "web": False, "pdf": True}
        elif lower_url.endswith(".docx"):
            seen_types = {**seen_types, "web": False, "docx": True}
        elif lower_url.endswith(".xlsx"):
            seen_types = {**seen_types, "web": False, "xlsx": True}
        elif lower_url.endswith(".pptx"):
            seen_types = {**seen_types, "web": False, "pptx": True}
        entry_key = suggested_url
        if entry_key not in counts:
            counts[entry_key] = {
                "suggested_url": suggested_url,
                "host": parsed.netloc,
                "count": 0,
                "seen_types": {
                    "web": False,
                    "pdf": False,
                    "docx": False,
                    "xlsx": False,
                    "pptx": False,
                },
            }
        counts[entry_key]["count"] += 1
        for key, value in seen_types.items():
            if value:
                counts[entry_key]["seen_types"][key] = True
    items = sorted(counts.values(), key=lambda item: item["count"], reverse=True)[:50]
    return {"items": items}


@router.post("/candidates/purge")
async def purge_candidates() -> Dict[str, str]:
    try:
        if CANDIDATES_PATH.exists():
            CANDIDATES_PATH.unlink()
        if PROCESSED_PATH.exists():
            PROCESSED_PATH.unlink()
        return {"status": "ok"}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/reset_crawl")
async def reset_crawl() -> Dict[str, Any]:
    """Reset crawl state by deleting artifacts, candidates, and job logs."""
    import shutil
    deleted_items = []

    # Delete artifacts
    artifacts_path = Path("/app/data/artifacts")
    if artifacts_path.exists():
        artifact_count = len(list(artifacts_path.glob("*/artifact.json")))
        shutil.rmtree(artifacts_path)
        artifacts_path.mkdir(parents=True, exist_ok=True)
        deleted_items.append(f"{artifact_count} artifacts")

    # Delete candidates
    if CANDIDATES_PATH.exists():
        CANDIDATES_PATH.unlink()
        deleted_items.append("candidates.jsonl")

    if PROCESSED_PATH.exists():
        PROCESSED_PATH.unlink()
        deleted_items.append("processed.json")

    # Delete job logs
    job_logs_path = Path("/app/data/logs/jobs")
    if job_logs_path.exists():
        log_count = len(list(job_logs_path.glob("*.log")))
        shutil.rmtree(job_logs_path)
        job_logs_path.mkdir(parents=True, exist_ok=True)
        deleted_items.append(f"{log_count} job logs")

    # Delete summaries
    summaries_path = Path("/app/data/logs/summaries")
    if summaries_path.exists():
        summary_count = len(list(summaries_path.glob("*.json")))
        shutil.rmtree(summaries_path)
        summaries_path.mkdir(parents=True, exist_ok=True)
        deleted_items.append(f"{summary_count} summaries")

    return {"status": "ok", "deleted": deleted_items}


@router.post("/reset_ingest")
async def reset_ingest() -> Dict[str, Any]:
    """Reset ingest state by deleting metadata database."""
    import sqlite3
    deleted_items = []

    if DB_PATH.exists():
        # Count records before deleting
        try:
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            doc_count = cursor.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
            chunk_count = cursor.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
            conn.close()
            deleted_items.append(f"{doc_count} documents")
            deleted_items.append(f"{chunk_count} chunks")
        except Exception:
            deleted_items.append("metadata.db (corrupted or empty)")

        DB_PATH.unlink()

    return {"status": "ok", "deleted": deleted_items}


@router.get("/crawl/auth_hints")
async def get_auth_hints() -> Dict[str, Any]:
    if not AUTH_HINTS_PATH.exists():
        return {"by_domain": {}, "recent": []}
    try:
        return json.loads(AUTH_HINTS_PATH.read_text(encoding="utf-8")) or {"by_domain": {}, "recent": []}
    except json.JSONDecodeError:
        return {"by_domain": {}, "recent": []}


@router.post("/crawl")
async def trigger_crawl() -> Dict[str, str]:
    from app.workers.crawl_worker import run_crawl_job

    job = start_job("crawl", run_crawl_job)
    return {"job_id": job.job_id}


@router.post("/ingest")
async def trigger_ingest() -> Dict[str, str]:
    job = start_job("ingest", run_ingest_job)
    return {"job_id": job.job_id}


@router.get("/jobs")
async def get_jobs() -> List[Dict[str, Any]]:
    return [job.__dict__ for job in list_jobs().values()]


@router.get("/jobs/{job_id}")
async def get_job_detail(job_id: str) -> Dict[str, Any]:
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job.__dict__


async def _tail_log(job_id: str) -> AsyncGenerator[str, None]:
    log_path = Path("/app/data/logs/jobs") / f"{job_id}.log"
    if not log_path.exists():
        yield f"data: {job_id} not found\n\n"
        return
    with log_path.open("r", encoding="utf-8") as handle:
        while True:
            line = handle.readline()
            if line:
                yield f"data: {line.strip()}\n\n"
            else:
                await asyncio.sleep(1.0)


@router.get("/jobs/{job_id}/log")
async def stream_log(job_id: str) -> StreamingResponse:
    return StreamingResponse(_tail_log(job_id), media_type="text/event-stream")


@router.get("/jobs/{job_id}/log/export")
async def export_log(job_id: str) -> FileResponse:
    log_path = Path("/app/data/logs/jobs") / f"{job_id}.log"
    if not log_path.exists():
        raise HTTPException(status_code=404, detail="Log not found")
    return FileResponse(log_path, filename=f"{job_id}.log", media_type="text/plain")


@router.get("/jobs/{job_id}/summary")
async def get_job_summary(job_id: str) -> Dict[str, Any]:
    summary_path = Path("/app/data/logs/summaries") / f"{job_id}.json"
    if not summary_path.exists():
        raise HTTPException(status_code=404, detail="Summary not found")
    return json.loads(summary_path.read_text(encoding="utf-8"))


@router.delete("/jobs/{job_id}")
async def remove_job(job_id: str) -> Dict[str, str]:
    delete_job(job_id)
    return {"status": "ok"}


@router.post("/clear_vectors")
async def clear_vectors() -> Dict[str, Any]:
    system_config = yaml.safe_load((CONFIG_DIR / "system.yml").read_text(encoding="utf-8")) or {}
    qdrant_config = system_config.get("qdrant", {})
    ollama_config = system_config.get("ollama", {})
    collection = qdrant_config.get("collection")
    qdrant_host = qdrant_config.get("host")
    embedding_model = ollama_config.get("embedding_model")
    ollama_host = ollama_config.get("host")
    if not collection or not qdrant_host:
        raise HTTPException(status_code=400, detail="Missing qdrant configuration")
    client = QdrantClient(url=qdrant_host)
    try:
        collections = client.get_collections().collections
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error connecting to Qdrant: {e}")

    deleted_items = []
    points_count = 0

    vector_size = None
    if any(col.name == collection for col in collections):
        try:
            info = client.get_collection(collection)
            vector_size = info.config.params.vectors.size
            points_count = info.points_count
        except AttributeError:
            # Handle case where config structure doesn't match expected schema
            print(f"Warning: Could not get vector size for collection '{collection}' due to schema mismatch")
        except Exception as e:
            # Handle pydantic validation errors
            if "validation" in str(e).lower() or "extra" in str(e).lower():
                print(f"Warning: Qdrant config validation error (server schema mismatch): {e}")
            else:
                raise HTTPException(status_code=500, detail=f"Error getting collection info: {e}")
        client.delete_collection(collection_name=collection)
        deleted_items.append(f"{points_count} vectors from collection '{collection}'")
    if vector_size is None:
        if not embedding_model or not ollama_host:
            raise HTTPException(status_code=400, detail="Missing embedding configuration")
        vector_size = len(embed_text(ollama_host, embedding_model, "dimension probe"))
    client.create_collection(
        collection_name=collection,
        vectors_config=rest.VectorParams(size=vector_size, distance=rest.Distance.COSINE),
    )
    client.create_payload_index(collection_name=collection, field_name="doc_id", field_schema="keyword")

    # Also delete ingest metadata
    if DB_PATH.exists():
        DB_PATH.unlink()
        deleted_items.append("ingest metadata.db")

    return {"status": "ok", "deleted": deleted_items}
