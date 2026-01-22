#!/usr/bin/env python3
"""
tools/validate_ingest.py

Validate ingest job results and data integrity.

Checks:
- Redis job states (queued, running, done, error, cancelled)
- Job metrics (done vs total)
- Qdrant vector counts
- SQLite metadata consistency
- Artifact-to-document mapping

Outputs:
- Job log: data/logs/jobs/{job_id}.log
- Summary JSON: data/logs/summaries/{job_id}.json
"""

import argparse
import json
import os
import sys
import uuid
import sqlite3
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Any

import redis
import yaml
from qdrant_client import QdrantClient


# -----------------------------
# Config
# -----------------------------

DEFAULT_REDIS_HOST = "localhost"
DEFAULT_REDIS_PORT = 6379
DEFAULT_QDRANT_HOST = "http://localhost:6333"
DEFAULT_CONFIG_PATH = "config/system.yml"
DEFAULT_DB_PATH = "data/ingest/metadata.db"
DEFAULT_ARTIFACTS_DIR = "data/artifacts"


@dataclass
class Finding:
    severity: str
    code: str
    message: str
    job_id: Optional[str] = None
    evidence: Optional[str] = None


# -----------------------------
# Helpers
# -----------------------------


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def load_config(path: Path) -> Dict[str, Any]:
    """Load YAML config file."""
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def count_artifacts() -> int:
    """Count artifact directories."""
    artifacts_dir = Path(DEFAULT_ARTIFACTS_DIR)
    if not artifacts_dir.exists():
        return 0
    return len(list(artifacts_dir.glob("*/artifact.json")))


def get_db_counts(db_path: str) -> Dict[str, int]:
    """Get document and chunk counts from SQLite."""
    if not Path(db_path).exists():
        return {"documents": 0, "chunks": 0}

    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        docs = cursor.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
        chunks = cursor.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]

        conn.close()
        return {"documents": docs, "chunks": chunks}
    except Exception as e:
        return {"documents": -1, "chunks": -1, "error": str(e)}


def get_qdrant_count(qdrant_host: str, collection: str) -> int:
    """Get vector count from Qdrant collection."""
    try:
        client = QdrantClient(url=qdrant_host)
        info = client.get_collection(collection)
        return info.points_count
    except Exception as e:
        return -1


# -----------------------------
# Validation logic
# -----------------------------


def validate_job(redis_client: redis.Redis, job_id: str) -> List[Finding]:
    """Validate a single job's state and metrics."""
    findings: List[Finding] = []
    job_key = f"job:{job_id}"

    # Get job data
    info = redis_client.hgetall(job_key)
    if not info:
        findings.append(
            Finding(
                severity="high",
                code="JOB_NOT_FOUND",
                message=f"Job {job_id} not found in Redis",
                job_id=job_id,
            )
        )
        return findings

    # Decode bytes to strings
    info = {k.decode(): v.decode() for k, v in info.items()}

    status = info.get("status", "unknown")
    total = int(info.get("total", 0))
    done = int(info.get("done", 0))
    attempts = int(info.get("attempts", 0))

    # Check for inconsistencies
    if status == "done" and done != total and total > 0:
        findings.append(
            Finding(
                severity="medium",
                code="INCOMPLETE_JOB",
                message=f"Job marked done but processed {done}/{total} items",
                job_id=job_id,
                evidence=f"status={status}, done={done}, total={total}",
            )
        )

    if status == "error":
        error_msg = info.get("error", "unknown error")
        findings.append(
            Finding(
                severity="high",
                code="JOB_FAILED",
                message=f"Job failed with error: {error_msg}",
                job_id=job_id,
                evidence=error_msg,
            )
        )

    if attempts > 1:
        findings.append(
            Finding(
                severity="low",
                code="MULTIPLE_ATTEMPTS",
                message=f"Job required {attempts} attempts",
                job_id=job_id,
            )
        )

    if status == "running":
        # Check if job might be stuck
        started_at = info.get("started_at")
        if started_at:
            try:
                start_time = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
                now = datetime.now(timezone.utc)
                duration = (now - start_time).total_seconds()
                if duration > 3600:  # 1 hour
                    findings.append(
                        Finding(
                            severity="medium",
                            code="LONG_RUNNING_JOB",
                            message=f"Job has been running for {int(duration/60)} minutes",
                            job_id=job_id,
                            evidence=f"started_at={started_at}",
                        )
                    )
            except Exception:
                pass

    return findings


def validate_data_integrity(
    qdrant_host: str, collection: str, db_path: str
) -> List[Finding]:
    """Validate consistency between Qdrant and SQLite."""
    findings: List[Finding] = []

    # Get counts
    db_counts = get_db_counts(db_path)
    qdrant_count = get_qdrant_count(qdrant_host, collection)

    if db_counts.get("documents", 0) < 0:
        findings.append(
            Finding(
                severity="high",
                code="DB_ERROR",
                message=f"Failed to query SQLite database: {db_counts.get('error', 'unknown')}",
                evidence=db_path,
            )
        )
        return findings

    if qdrant_count < 0:
        findings.append(
            Finding(
                severity="high",
                code="QDRANT_ERROR",
                message=f"Failed to query Qdrant collection: {collection}",
                evidence=qdrant_host,
            )
        )
        return findings

    # Check if chunk count matches vector count (approximately)
    chunk_count = db_counts.get("chunks", 0)
    if abs(chunk_count - qdrant_count) > max(chunk_count * 0.01, 10):
        findings.append(
            Finding(
                severity="high",
                code="VECTOR_MISMATCH",
                message=f"Qdrant has {qdrant_count} vectors but metadata DB has {chunk_count} chunks",
                evidence=f"qdrant={qdrant_count}, db={chunk_count}, diff={abs(chunk_count - qdrant_count)}",
            )
        )

    # Check if artifact count matches document count (approximately)
    artifact_count = count_artifacts()
    doc_count = db_counts.get("documents", 0)
    if abs(artifact_count - doc_count) > max(artifact_count * 0.05, 5):
        findings.append(
            Finding(
                severity="medium",
                code="ARTIFACT_MISMATCH",
                message=f"Found {artifact_count} artifacts but {doc_count} documents in DB",
                evidence=f"artifacts={artifact_count}, documents={doc_count}",
            )
        )

    return findings


# -----------------------------
# Main
# -----------------------------


def main() -> int:
    ap = argparse.ArgumentParser(description="Validate ingest job results and data integrity")
    ap.add_argument("--job", help="Validate specific job ID")
    ap.add_argument("--all-jobs", action="store_true", help="Validate all jobs in Redis")
    ap.add_argument(
        "--data-integrity", action="store_true", help="Check data integrity (Qdrant vs SQLite)"
    )
    ap.add_argument("--redis-host", default=DEFAULT_REDIS_HOST)
    ap.add_argument("--redis-port", type=int, default=DEFAULT_REDIS_PORT)
    ap.add_argument("--config", default=DEFAULT_CONFIG_PATH)
    ap.add_argument("--db", default=DEFAULT_DB_PATH)
    ap.add_argument("--fail-on", choices=["low", "medium", "high"], default="high")
    args = ap.parse_args()

    validation_id = f"validate_ingest_{uuid.uuid4()}"
    started = now_utc()

    # Setup logging
    jobs_dir = Path("data/logs/jobs")
    summary_dir = Path("data/logs/summaries")
    jobs_dir.mkdir(parents=True, exist_ok=True)
    summary_dir.mkdir(parents=True, exist_ok=True)

    job_log_path = jobs_dir / f"{validation_id}.log"
    summary_path = summary_dir / f"{validation_id}.json"

    def log(msg: str):
        line = f"[{now_utc()}] {msg}"
        print(line)
        with job_log_path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")

    log(f"Validation started ({validation_id})")

    # Connect to Redis
    try:
        r = redis.Redis(
            host=args.redis_host,
            port=args.redis_port,
            decode_responses=False,  # We'll decode manually
        )
        r.ping()
        log(f"Connected to Redis at {args.redis_host}:{args.redis_port}")
    except Exception as e:
        log(f"ERROR: Failed to connect to Redis: {e}")
        return 2

    findings: List[Finding] = []

    # Validate specific job
    if args.job:
        log(f"Validating job: {args.job}")
        findings.extend(validate_job(r, args.job))

    # Validate all jobs
    if args.all_jobs:
        log("Scanning all jobs in Redis...")
        job_keys = r.keys("job:*")
        # Filter out event channels
        job_keys = [k for k in job_keys if not k.decode().endswith(":events")]
        log(f"Found {len(job_keys)} jobs")

        for key in job_keys:
            job_id = key.decode().split(":", 1)[1]
            findings.extend(validate_job(r, job_id))

    # Validate data integrity
    if args.data_integrity or (not args.job and not args.all_jobs):
        log("Checking data integrity...")

        # Load config for Qdrant settings
        config = load_config(Path(args.config))
        qdrant_host = config.get("qdrant", {}).get("host", DEFAULT_QDRANT_HOST)
        collection = config.get("qdrant", {}).get("collection", "ragai")

        log(f"Qdrant: {qdrant_host}, collection: {collection}")
        log(f"Database: {args.db}")

        findings.extend(validate_data_integrity(qdrant_host, collection, args.db))

    # Summarize findings
    counts = {"low": 0, "medium": 0, "high": 0}
    for f in findings:
        counts[f.severity] += 1

    log(f"Validation complete: {counts['low']} low, {counts['medium']} medium, {counts['high']} high")

    summary = {
        "validation_id": validation_id,
        "started_at": started,
        "finished_at": now_utc(),
        "finding_counts": counts,
        "findings": [asdict(f) for f in findings],
    }

    # Write summary
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    log(f"Summary written to {summary_path}")

    # Determine exit code
    severity_order = {"low": 1, "medium": 2, "high": 3}
    fail_threshold = severity_order.get(args.fail_on, 999)
    fail = any(severity_order.get(f.severity, 0) >= fail_threshold for f in findings)

    log(f"Validation exit: {'1' if fail else '0'}")
    return 1 if fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
