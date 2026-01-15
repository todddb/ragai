import threading
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, Optional

JOB_LOG_DIR = Path("/app/data/logs/jobs")
JOB_LOG_DIR.mkdir(parents=True, exist_ok=True)


@dataclass
class JobRecord:
    job_id: str
    job_type: str
    status: str
    started_at: str
    ended_at: Optional[str]


_jobs: Dict[str, JobRecord] = {}


def _write_log(job_id: str, message: str) -> None:
    log_path = JOB_LOG_DIR / f"{job_id}.log"
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(message + "\n")


def start_job(job_type: str, worker: Callable[[Callable[[str], None]], None]) -> JobRecord:
    job_id = str(uuid.uuid4())
    record = JobRecord(
        job_id=job_id,
        job_type=job_type,
        status="running",
        started_at=datetime.utcnow().isoformat(),
        ended_at=None,
    )
    _jobs[job_id] = record

    def run() -> None:
        try:
            worker(lambda message: _write_log(job_id, message))
            record.status = "completed"
        except Exception as exc:  # pragma: no cover - log errors
            record.status = "failed"
            _write_log(job_id, f"ERROR: {exc}")
            raise
        finally:
            record.ended_at = datetime.utcnow().isoformat()

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return record


def list_jobs() -> Dict[str, JobRecord]:
    return _jobs


def get_job(job_id: str) -> Optional[JobRecord]:
    return _jobs.get(job_id)


def delete_job(job_id: str) -> None:
    _jobs.pop(job_id, None)
    log_path = JOB_LOG_DIR / f"{job_id}.log"
    if log_path.exists():
        log_path.unlink()
