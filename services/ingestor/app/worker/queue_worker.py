# services/ingestor/app/worker/queue_worker.py
import asyncio
import json
import os
import sys
import uuid
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Set, Tuple

import redis.asyncio as aioredis
import yaml
from qdrant_client import QdrantClient

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from app.utils.db import connect
from app.utils.ollama_embed import embed_text
from app.utils.qdrant import delete_by_doc_id, ensure_collection, upsert_vectors

REDIS_URL = os.getenv("REDIS_HOST", "redis://redis:6379/0")
ARTIFACT_DIR = Path("/app/data/artifacts")
CONFIG_PATH = Path("/app/config/system.yml")

# Boilerplate filters (same as ingest.py)
BOILERPLATE_KEYWORDS = {
    "skip to main content",
    "burger menu",
    "close menu",
    "sign in to view",
    "sign in",
    "log in",
    "loading",
}
PDF_MARKER = "%pdf-"
MIN_CHUNK_LENGTH = 40


def _load_config(path: Path) -> Dict:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _filter_chunks(chunks: List[Dict]) -> Tuple[List[Dict], Dict[str, int]]:
    """Filter out boilerplate, PDF markers, and too-short chunks."""
    kept: List[Dict] = []
    skipped_counts = {"boilerplate": 0, "pdf": 0, "too_short": 0}
    for chunk in chunks:
        text = chunk.get("text", "")
        normalized = text.lower()
        if PDF_MARKER in normalized:
            skipped_counts["pdf"] += 1
            continue
        if any(keyword in normalized for keyword in BOILERPLATE_KEYWORDS):
            skipped_counts["boilerplate"] += 1
            continue
        if len(text.strip()) < MIN_CHUNK_LENGTH:
            skipped_counts["too_short"] += 1
            continue
        kept.append(chunk)
    return kept, skipped_counts


def _doc_ids_on_disk() -> Set[str]:
    """Get all doc_ids present in artifact directories."""
    return {path.parent.name for path in ARTIFACT_DIR.glob("*/artifact.json")}


async def publish_event(redis: aioredis.Redis, job_id: str, event: dict):
    """Publish an event to the job's Redis pubsub channel."""
    await redis.publish(f"job:{job_id}:events", json.dumps(event))


async def publish_log(redis: aioredis.Redis, job_id: str, message: str, level: str = "info"):
    """Publish a log message."""
    await publish_event(
        redis,
        job_id,
        {
            "type": "log",
            "level": level,
            "message": message,
            "ts": datetime.utcnow().isoformat(),
        },
    )


async def process_job(redis: aioredis.Redis, job: dict):
    """Process a single ingest job."""
    job_id = job["job_id"]
    job_key = f"job:{job_id}"

    try:
        # Mark job as running
        await redis.hset(
            job_key,
            mapping={
                "status": "running",
                "started_at": datetime.utcnow().isoformat(),
            },
        )
        await publish_log(redis, job_id, f"Starting ingest job {job_id}")

        # Load configuration
        system_config = _load_config(CONFIG_PATH)
        qdrant_host = system_config["qdrant"]["host"]
        collection = system_config["qdrant"]["collection"]
        embedding_model = system_config["ollama"]["embedding_model"]
        ollama_host = system_config["ollama"]["host"]

        # Initialize Qdrant client
        client = QdrantClient(url=qdrant_host)
        vector_size = len(embed_text(ollama_host, embedding_model, "dimension probe"))
        ensure_collection(client, collection, vector_size=vector_size)

        # Connect to SQLite database
        with connect() as conn:
            # Clean up deleted documents
            disk_doc_ids = _doc_ids_on_disk()
            stored_doc_ids = {
                row["doc_id"]
                for row in conn.execute("SELECT doc_id FROM documents").fetchall()
            }
            missing_doc_ids = stored_doc_ids - disk_doc_ids
            for doc_id in missing_doc_ids:
                delete_by_doc_id(client, collection, doc_id)
                conn.execute("DELETE FROM chunks WHERE doc_id = ?", (doc_id,))
                conn.execute("DELETE FROM documents WHERE doc_id = ?", (doc_id,))
                await publish_log(redis, job_id, f"Deleted vectors for missing doc_id {doc_id}")

            # Get artifact paths to process
            artifact_paths = job.get("artifact_paths", [])
            if not artifact_paths:
                # Process all artifacts
                artifact_paths = list(ARTIFACT_DIR.glob("*/artifact.json"))
            else:
                # Convert string paths to Path objects
                artifact_paths = [Path(p) for p in artifact_paths]

            total_artifacts = len(artifact_paths)

            # Update total in job state
            await redis.hset(job_key, "total", total_artifacts)
            await publish_log(
                redis, job_id, f"Found {total_artifacts} artifacts to process"
            )

            # Process each artifact
            done = 0
            for idx, artifact_path in enumerate(artifact_paths, start=1):
                try:
                    # Check for cancellation
                    status = await redis.hget(job_key, "status")
                    if status == "cancelling":
                        await publish_log(
                            redis, job_id, "Job cancelled by user", level="warning"
                        )
                        await redis.hset(job_key, "status", "cancelled")
                        return

                    # Load artifact
                    artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
                    doc_id = artifact["doc_id"]
                    content_hash = artifact["content_hash"]
                    url = artifact.get("url", "")

                    # Check if document needs updating
                    row = conn.execute(
                        "SELECT content_hash FROM documents WHERE doc_id = ?", (doc_id,)
                    ).fetchone()
                    if row and row["content_hash"] == content_hash:
                        # Skip unchanged
                        await publish_log(
                            redis,
                            job_id,
                            f"Skipped unchanged artifact {idx}/{total_artifacts}: {url}",
                        )
                        done += 1
                        await redis.hset(job_key, "done", done)
                        await publish_event(
                            redis,
                            job_id,
                            {
                                "type": "progress",
                                "done": done,
                                "total": total_artifacts,
                                "status": "running",
                                "ts": datetime.utcnow().isoformat(),
                            },
                        )
                        continue

                    # If updating, clear previous data
                    if row:
                        delete_by_doc_id(client, collection, doc_id)
                        conn.execute("DELETE FROM chunks WHERE doc_id = ?", (doc_id,))
                        conn.execute("DELETE FROM documents WHERE doc_id = ?", (doc_id,))

                    # Load and filter chunks
                    chunks_path = artifact_path.parent / "chunks.jsonl"
                    chunks = [
                        json.loads(line)
                        for line in chunks_path.read_text(encoding="utf-8").splitlines()
                    ]

                    total_chunks = len(chunks)
                    filtered_chunks, skipped_counts = _filter_chunks(chunks)
                    kept_chunks = len(filtered_chunks)
                    skipped_total = total_chunks - kept_chunks

                    await publish_log(
                        redis,
                        job_id,
                        f"Processing artifact {idx}/{total_artifacts}: {url} "
                        f"({kept_chunks} chunks kept, {skipped_total} skipped)",
                    )

                    if kept_chunks == 0:
                        # No chunks to insert
                        conn.execute(
                            "INSERT INTO documents (doc_id, url, content_hash, ingested_at, chunk_count) VALUES (?, ?, ?, ?, ?)",
                            (
                                doc_id,
                                artifact["url"],
                                content_hash,
                                datetime.utcnow().isoformat(),
                                0,
                            ),
                        )
                        conn.commit()
                        done += 1
                        await redis.hset(job_key, "done", done)
                        await publish_event(
                            redis,
                            job_id,
                            {
                                "type": "progress",
                                "done": done,
                                "total": total_artifacts,
                                "status": "running",
                                "ts": datetime.utcnow().isoformat(),
                            },
                        )
                        continue

                    # Generate embeddings
                    texts = [chunk["text"] for chunk in filtered_chunks]
                    await publish_log(
                        redis, job_id, f"Generating embeddings for {kept_chunks} chunks..."
                    )
                    vectors = [
                        embed_text(ollama_host, embedding_model, text) for text in texts
                    ]

                    # Generate deterministic UUIDs
                    ids = [
                        str(uuid.uuid5(uuid.NAMESPACE_URL, chunk["chunk_id"]))
                        for chunk in filtered_chunks
                    ]

                    payloads = [
                        {
                            "doc_id": doc_id,
                            "chunk_id": chunk["chunk_id"],
                            "url": artifact["url"],
                            "title": artifact.get("title", ""),
                            "text": chunk["text"],
                        }
                        for chunk in filtered_chunks
                    ]

                    # Upsert to Qdrant
                    await publish_log(
                        redis, job_id, f"Upserting {kept_chunks} vectors to Qdrant..."
                    )
                    upsert_vectors(client, collection, ids, vectors, payloads)

                    # Save metadata
                    conn.execute(
                        "INSERT INTO documents (doc_id, url, content_hash, ingested_at, chunk_count) VALUES (?, ?, ?, ?, ?)",
                        (
                            doc_id,
                            artifact["url"],
                            content_hash,
                            datetime.utcnow().isoformat(),
                            kept_chunks,
                        ),
                    )
                    conn.executemany(
                        "INSERT INTO chunks (chunk_id, doc_id, chunk_index, vector_id) VALUES (?, ?, ?, ?)",
                        [
                            (chunk["chunk_id"], doc_id, chunk["chunk_index"], ids[i])
                            for i, chunk in enumerate(filtered_chunks)
                        ],
                    )
                    conn.commit()

                    await publish_log(
                        redis,
                        job_id,
                        f"Completed artifact {idx}/{total_artifacts}: {url} "
                        f"({kept_chunks} chunks indexed)",
                    )

                    # Update progress
                    done += 1
                    await redis.hset(job_key, "done", done)
                    await publish_event(
                        redis,
                        job_id,
                        {
                            "type": "progress",
                            "done": done,
                            "total": total_artifacts,
                            "status": "running",
                            "ts": datetime.utcnow().isoformat(),
                        },
                    )

                except Exception as e:
                    await publish_log(
                        redis,
                        job_id,
                        f"Error processing artifact {idx}/{total_artifacts}: {repr(e)}",
                        level="error",
                    )
                    # Continue to next artifact
                    continue

            # Job complete
            try:
                total_docs = conn.execute("SELECT count(*) AS c FROM documents").fetchone()[
                    "c"
                ]
                total_chunks_db = conn.execute("SELECT count(*) AS c FROM chunks").fetchone()[
                    "c"
                ]
            except Exception:
                total_docs = "?"
                total_chunks_db = "?"

            await redis.hset(
                job_key,
                mapping={
                    "status": "done",
                    "finished_at": datetime.utcnow().isoformat(),
                },
            )
            await publish_log(
                redis,
                job_id,
                f"Ingest complete: {total_artifacts} artifacts processed, "
                f"{total_docs} documents, {total_chunks_db} chunks indexed",
            )
            await publish_event(
                redis,
                job_id,
                {
                    "type": "complete",
                    "msg": "Ingest complete",
                    "total_artifacts": total_artifacts,
                    "total_documents": total_docs,
                    "total_chunks": total_chunks_db,
                    "ts": datetime.utcnow().isoformat(),
                },
            )

    except Exception as e:
        # Job failed
        await redis.hincrby(job_key, "attempts", 1)
        await redis.hset(
            job_key,
            mapping={
                "status": "error",
                "error": str(e),
                "finished_at": datetime.utcnow().isoformat(),
            },
        )
        await publish_log(redis, job_id, f"Job failed: {repr(e)}", level="error")
        await publish_event(
            redis, job_id, {"type": "error", "msg": str(e), "ts": datetime.utcnow().isoformat()}
        )

        # Move to DLQ
        await redis.lpush("jobs:dlq", json.dumps(job))


async def worker_loop():
    """Main worker loop: poll Redis queue and process jobs."""
    redis = aioredis.from_url(REDIS_URL, decode_responses=True)
    print(f"Worker started, polling {REDIS_URL} for jobs...")
    sys.stdout.flush()

    while True:
        try:
            # Block waiting for a job (timeout 1 second to allow for graceful shutdown)
            result = await redis.brpop("jobs:queue", timeout=1)
            if result is None:
                continue

            _, raw = result
            job = json.loads(raw)
            print(f"Processing job {job.get('job_id', '?')}")
            sys.stdout.flush()

            await process_job(redis, job)

        except KeyboardInterrupt:
            print("Worker shutting down...")
            break
        except Exception as e:
            print(f"Worker error: {repr(e)}")
            sys.stdout.flush()
            # Continue processing
            await asyncio.sleep(1)


if __name__ == "__main__":
    asyncio.run(worker_loop())
