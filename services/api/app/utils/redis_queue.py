# services/api/app/utils/redis_queue.py
import json
import os
from typing import Any, Dict, Optional
import redis.asyncio as aioredis
from datetime import datetime

REDIS_URL = os.getenv("REDIS_HOST", "redis://redis:6379/0")

redis_client = aioredis.from_url(REDIS_URL, decode_responses=True)


async def push_job(job: Dict[str, Any]):
    """Push job (JSON) to queue and init job state hash."""
    job_id = job["job_id"]
    await redis_client.lpush("jobs:queue", json.dumps(job))
    job_key = f"job:{job_id}"
    await redis_client.hset(
        job_key,
        mapping={
            "status": "queued",
            "total": job.get("chunks_estimate", 0),
            "done": 0,
            "total_artifacts": 0,
            "done_artifacts": 0,
            "attempts": 0,
            "created_at": datetime.utcnow().isoformat(),
            "job_type": job.get("type", "ingest"),
        },
    )
    return job_id


async def set_job_status(job_id: str, status: str, **extra):
    """Update job status and optional extra fields."""
    key = f"job:{job_id}"
    mapping = {"status": status}
    mapping.update({k: str(v) for k, v in extra.items()})
    await redis_client.hset(key, mapping=mapping)


async def increment_done(job_id: str, inc: int = 1):
    """Increment done counter and publish progress event."""
    key = f"job:{job_id}"
    await redis_client.hincrby(key, "done", inc)
    # Optionally publish progress event
    info = await redis_client.hgetall(key)
    await redis_client.publish(
        f"job:{job_id}:events",
        json.dumps(
            {
                "type": "progress",
                "done": int(info.get("done", 0)),
                "total": int(info.get("total", 0)),
                "status": info.get("status", "running"),
                "ts": datetime.utcnow().isoformat(),
            }
        ),
    )


async def get_job(job_id: str) -> Optional[Dict[str, str]]:
    """Get job state from Redis hash."""
    key = f"job:{job_id}"
    data = await redis_client.hgetall(key)
    return data or None


async def publish_log(job_id: str, message: str, level: str = "info"):
    """Publish a log message to the job's event stream."""
    await redis_client.publish(
        f"job:{job_id}:events",
        json.dumps(
            {
                "type": "log",
                "level": level,
                "message": message,
                "ts": datetime.utcnow().isoformat(),
            }
        ),
    )


async def publish_event(job_id: str, event_type: str, data: Dict[str, Any]):
    """Publish a custom event to the job's event stream."""
    event = {"type": event_type, "ts": datetime.utcnow().isoformat()}
    event.update(data)
    await redis_client.publish(f"job:{job_id}:events", json.dumps(event))
