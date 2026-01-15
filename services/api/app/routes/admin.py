import asyncio
from pathlib import Path
from typing import Any, AsyncGenerator, Dict, List

import yaml
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from app.utils.config import refresh_config
from app.utils.jobs import delete_job, get_job, list_jobs, start_job

router = APIRouter(prefix="/api/admin", tags=["admin"])

SECRETS_PATH = Path("/app/secrets/admin_tokens")
CONFIG_DIR = Path("/app/config")


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


def _job_worker(job_type: str):
    def run(log):
        log(f"Starting {job_type} job")
        log("Job running...")
        log(f"{job_type} job complete")

    return run


@router.post("/crawl")
async def trigger_crawl() -> Dict[str, str]:
    job = start_job("crawl", _job_worker("crawl"))
    return {"job_id": job.job_id}


@router.post("/ingest")
async def trigger_ingest() -> Dict[str, str]:
    job = start_job("ingest", _job_worker("ingest"))
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


@router.delete("/jobs/{job_id}")
async def remove_job(job_id: str) -> Dict[str, str]:
    delete_job(job_id)
    return {"status": "ok"}
