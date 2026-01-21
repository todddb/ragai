import asyncio
import json
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
async def clear_vectors() -> Dict[str, str]:
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

    vector_size = None
    if any(col.name == collection for col in collections):
        try:
            info = client.get_collection(collection)
            vector_size = info.config.params.vectors.size
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
    if vector_size is None:
        if not embedding_model or not ollama_host:
            raise HTTPException(status_code=400, detail="Missing embedding configuration")
        vector_size = len(embed_text(ollama_host, embedding_model, "dimension probe"))
    client.create_collection(
        collection_name=collection,
        vectors_config=rest.VectorParams(size=vector_size, distance=rest.Distance.COSINE),
    )
    client.create_payload_index(collection_name=collection, field_name="doc_id", field_schema="keyword")
    if DB_PATH.exists():
        DB_PATH.unlink()
    return {"status": "ok"}
