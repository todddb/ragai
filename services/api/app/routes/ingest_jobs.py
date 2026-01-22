# services/api/app/routes/ingest_jobs.py
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from uuid import uuid4
import time
import json
from datetime import datetime, timezone
from app.utils.redis_queue import push_job, get_job, set_job_status, redis_client

router = APIRouter(prefix="/api/ingest", tags=["ingest"])


@router.post("")
async def start_ingest(payload: dict):
    """
    Start a new ingest job.

    Expected payload:
    {
        "artifact_paths": [...],  # optional: specific paths to process
        "chunks_estimate": 0,     # optional: estimated total chunks
        "meta": {}                # optional: metadata
    }

    Returns:
    {
        "job_id": "job_...",
        "status": "queued"
    }
    """
    job_id = f"job_{int(time.time())}_{uuid4().hex[:6]}"
    job = {
        "job_id": job_id,
        "type": "ingest",
        "artifact_paths": payload.get("artifact_paths", []),
        "chunks_estimate": payload.get("chunks_estimate", 0),
        "meta": payload.get("meta", {}),
    }
    await push_job(job)
    return {"job_id": job_id, "status": "queued"}


@router.get("/{job_id}")
async def get_job_status(job_id: str):
    """
    Get the current status of an ingest job.

    Returns:
    {
        "status": "queued|running|done|error|cancelled",
        "done": 0,
        "total": 0,
        "attempts": 0,
        "created_at": "...",
        "started_at": "...",  # if started
        "finished_at": "...", # if finished
        ...
    }
    """
    info = await get_job(job_id)
    if not info:
        raise HTTPException(404, "job not found")
    return info


@router.get("/{job_id}/events")
async def job_events(request: Request, job_id: str):
    """
    Server-Sent Events stream for live job progress and logs.

    Event types:
    - start: {"type": "start", "total_artifacts": N, "started_at": "..."}
    - artifact_progress: {"type": "artifact_progress", "done_artifacts": N, "total_artifacts": M, "current_artifact": "..."}
    - log: {"type": "log", "level": "info|error", "message": "...", "ts": "..."}
    - complete: {"type": "complete", "msg": "...", "ts": "..."}
    - error: {"type": "error", "msg": "...", "ts": "..."}
    - control: {"type": "control", "action": "cancelling", "ts": "..."}
    """

    async def event_generator():
        pubsub = redis_client.pubsub()
        await pubsub.subscribe(f"job:{job_id}:events")
        try:
            # Send initial connection confirmation
            yield f"data: {json.dumps({'type': 'connected', 'job_id': job_id})}\n\n"

            async for message in pubsub.listen():
                if message is None:
                    continue
                if message["type"] != "message":
                    continue
                data = message["data"]
                # message["data"] is already a str because decode_responses=True
                yield f"data: {data}\n\n"

                # Check if client disconnected
                if await request.is_disconnected():
                    break

                # Check if job is complete
                try:
                    event = json.loads(data)
                    if event.get("type") in ["complete", "error"]:
                        # Give client time to receive the message before closing
                        break
                except:
                    pass
        finally:
            await pubsub.unsubscribe(f"job:{job_id}:events")
            await pubsub.close()

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@router.get("/worker/status")
async def get_worker_status():
    heartbeat = await redis_client.get("ingest_worker:heartbeat")
    info = await redis_client.hgetall("ingest_worker:info")
    queue_depth = await redis_client.llen("jobs:queue")
    age_seconds = None
    if heartbeat:
        try:
            heartbeat_dt = datetime.fromisoformat(heartbeat.replace("Z", "+00:00"))
            now = datetime.now(timezone.utc)
            age_seconds = (now - heartbeat_dt).total_seconds()
        except Exception:
            age_seconds = None
    return {
        "heartbeat": heartbeat,
        "age_seconds": age_seconds,
        "worker": info or {},
        "queue_depth": queue_depth,
    }


@router.post("/{job_id}/cancel")
async def cancel_job(job_id: str):
    """
    Request cancellation of a running job.

    The worker will check the status and stop processing.

    Returns:
    {
        "job_id": "...",
        "status": "cancelling"
    }
    """
    # Check if job exists
    job = await get_job(job_id)
    if not job:
        raise HTTPException(404, "job not found")

    # Set status to cancelling
    await set_job_status(job_id, "cancelling")
    await redis_client.publish(
        f"job:{job_id}:events",
        json.dumps({"type": "control", "action": "cancelling"}),
    )
    return {"job_id": job_id, "status": "cancelling"}
