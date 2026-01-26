# services/ingestor/app/worker/queue_worker.py
import asyncio
import json
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Set, Tuple

import httpx
import redis.asyncio as aioredis
import yaml
from qdrant_client import QdrantClient

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from app.utils.db import connect, init_db
from app.utils.ollama_embed import embed_text, embed_texts_async
from app.utils.qdrant import delete_by_doc_id, ensure_collection, upsert_vectors

REDIS_URL = os.getenv("REDIS_HOST", "redis://redis:6379/0")
ARTIFACT_DIR = Path("/app/data/artifacts")
CONFIG_PATH = Path("/app/config/system.yml")
INGEST_CONFIG_PATH = Path("/app/config/ingest.yml")
HEARTBEAT_KEY = "ingest_worker:heartbeat"
WORKER_INFO_KEY = "ingest_worker:info"
WORKER_VERSION = "2"  # Bumped for Qdrant client fix

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
    if not path.exists():
        return {}
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _get_ingest_settings(config: Dict) -> Dict[str, int]:
    ingest_config = config.get("ingest", {}) if config else {}
    return {
        "embed_concurrency": int(ingest_config.get("embed_concurrency", 4)),
        "upsert_batch_size": int(ingest_config.get("upsert_batch_size", 64)),
        "chunk_batch_size": int(ingest_config.get("chunk_batch_size", 16)),
        "max_inflight_chunks": int(ingest_config.get("max_inflight_chunks", 256)),
    }


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
            "ts": _utcnow(),
        },
    )


async def process_job(redis: aioredis.Redis, job: dict):
    """Process a single ingest job."""
    job_id = job["job_id"]
    job_key = f"job:{job_id}"

    try:
        # Mark job as running
        started_at = _utcnow()
        await redis.hset(
            job_key,
            mapping={
                "status": "running",
                "started_at": started_at,
                "updated_at": started_at,
                "errors": 0,
            },
        )
        await publish_log(redis, job_id, f"Starting ingest job {job_id}")

        # Load configuration
        system_config = _load_config(CONFIG_PATH)
        ingest_config = _get_ingest_settings(_load_config(INGEST_CONFIG_PATH))
        qdrant_host = system_config["qdrant"]["host"]
        collection = system_config["qdrant"]["collection"]
        embedding_model = system_config["ollama"]["embedding_model"]
        ollama_host = system_config["ollama"]["host"]

        # Initialize Qdrant client (named distinctly to avoid confusion with HTTP client)
        qdrant_client = QdrantClient(url=qdrant_host)

        # Startup diagnostics: log Qdrant client info
        print(f"[QDRANT] URL: {qdrant_host}")
        print(f"[QDRANT] Client class: {type(qdrant_client).__name__}")
        print(f"[QDRANT] hasattr(client, 'upsert'): {hasattr(qdrant_client, 'upsert')}")
        sys.stdout.flush()
        await publish_log(redis, job_id, f"Qdrant client initialized: url={qdrant_host}, class={type(qdrant_client).__name__}")

        # Fail-fast: verify Qdrant is reachable before processing
        try:
            collections_check = qdrant_client.get_collections()
            await publish_log(redis, job_id, f"Qdrant reachable: {len(collections_check.collections)} collection(s) found")
        except Exception as qdrant_err:
            error_msg = f"Qdrant unreachable at {qdrant_host}: {repr(qdrant_err)}"
            print(f"[QDRANT] FAIL-FAST: {error_msg}")
            sys.stdout.flush()
            await publish_log(redis, job_id, error_msg, level="error")
            raise RuntimeError(error_msg) from qdrant_err

        vector_size = len(embed_text(ollama_host, embedding_model, "dimension probe"))
        ensure_collection(qdrant_client, collection, vector_size=vector_size)

        # Ensure ingest metadata database schema is initialized
        init_db()
        await publish_log(redis, job_id, "Initialized ingest metadata schema")

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
                delete_by_doc_id(qdrant_client, collection, doc_id)
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
            await redis.hset(
                job_key,
                mapping={
                    "total": total_artifacts,
                    "total_artifacts": total_artifacts,
                    "done_artifacts": 0,
                },
            )
            await publish_log(
                redis, job_id, f"Found {total_artifacts} artifacts to process"
            )
            await publish_event(
                redis,
                job_id,
                {
                    "type": "start",
                    "total_artifacts": total_artifacts,
                    "started_at": started_at,
                },
            )

            # Process each artifact
            done = 0
            errors = 0
            async with httpx.AsyncClient(timeout=60.0) as http_client:
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
                            await redis.hset(
                                job_key,
                                mapping={
                                    "done": done,
                                    "done_artifacts": done,
                                    "current_artifact": url or str(artifact_path),
                                    "updated_at": _utcnow(),
                                },
                            )
                            await publish_event(
                                redis,
                                job_id,
                                {
                                    "type": "artifact_progress",
                                    "done_artifacts": done,
                                    "total_artifacts": total_artifacts,
                                    "current_artifact": url or str(artifact_path),
                                    "errors": errors,
                                    "status": "running",
                                },
                            )
                            continue

                        # If updating, clear previous data
                        if row:
                            delete_by_doc_id(qdrant_client, collection, doc_id)
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
                                    _utcnow(),
                                    0,
                                ),
                            )
                            conn.commit()
                            done += 1
                            await redis.hset(
                                job_key,
                                mapping={
                                    "done": done,
                                    "done_artifacts": done,
                                    "current_artifact": url or str(artifact_path),
                                    "updated_at": _utcnow(),
                                },
                            )
                            await publish_event(
                                redis,
                                job_id,
                                {
                                    "type": "artifact_progress",
                                    "done_artifacts": done,
                                    "total_artifacts": total_artifacts,
                                    "current_artifact": url or str(artifact_path),
                                    "errors": errors,
                                    "status": "running",
                                },
                            )
                            continue

                        # Generate embeddings
                        texts = [chunk["text"] for chunk in filtered_chunks]
                        await publish_log(
                            redis,
                            job_id,
                            f"Generating embeddings for {kept_chunks} chunks "
                            f"(concurrency={ingest_config['embed_concurrency']}, "
                            f"batch={ingest_config['chunk_batch_size']})...",
                        )

                        chunk_batch_size = max(1, ingest_config["chunk_batch_size"])
                        max_inflight_chunks = max(
                            chunk_batch_size, ingest_config["max_inflight_chunks"]
                        )
                        max_inflight_batches = max(
                            1, max_inflight_chunks // chunk_batch_size
                        )
                        semaphore = asyncio.Semaphore(
                            max(1, ingest_config["embed_concurrency"])
                        )

                        async def run_embed_batch(
                            batch_index: int, batch_texts: List[str]
                        ) -> Tuple[int, List[List[float]]]:
                            async with semaphore:
                                embeddings = await embed_texts_async(
                                    http_client, ollama_host, embedding_model, batch_texts
                                )
                            return batch_index, embeddings

                        batches = [
                            texts[i : i + chunk_batch_size]
                            for i in range(0, len(texts), chunk_batch_size)
                        ]
                        batch_results: List[List[List[float]]] = [
                            [] for _ in range(len(batches))
                        ]
                        tasks: List[asyncio.Task] = []
                        for batch_index, batch_texts in enumerate(batches):
                            tasks.append(
                                asyncio.create_task(
                                    run_embed_batch(batch_index, batch_texts)
                                )
                            )
                            if len(tasks) >= max_inflight_batches:
                                done_tasks, pending = await asyncio.wait(
                                    tasks, return_when=asyncio.FIRST_COMPLETED
                                )
                                tasks = list(pending)
                                for task in done_tasks:
                                    idx, embeddings = task.result()
                                    batch_results[idx] = embeddings
                        if tasks:
                            for idx, embeddings in await asyncio.gather(*tasks):
                                batch_results[idx] = embeddings

                        vectors = [
                            embedding
                            for batch in batch_results
                            for embedding in batch
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
                            redis,
                            job_id,
                            f"Upserting {kept_chunks} vectors to Qdrant "
                            f"(batch_size={ingest_config['upsert_batch_size']})...",
                        )
                        upsert_vectors(
                            qdrant_client,
                            collection,
                            ids,
                            vectors,
                            payloads,
                            batch_size=ingest_config["upsert_batch_size"],
                        )

                        # Save metadata
                        conn.execute(
                            "INSERT INTO documents (doc_id, url, content_hash, ingested_at, chunk_count) VALUES (?, ?, ?, ?, ?)",
                            (
                                doc_id,
                                artifact["url"],
                                content_hash,
                                _utcnow(),
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
                        await redis.hset(
                            job_key,
                            mapping={
                                "done": done,
                                "done_artifacts": done,
                                "current_artifact": url or str(artifact_path),
                                "updated_at": _utcnow(),
                            },
                        )
                        await publish_event(
                            redis,
                            job_id,
                            {
                                "type": "artifact_progress",
                                "done_artifacts": done,
                                "total_artifacts": total_artifacts,
                                "current_artifact": url or str(artifact_path),
                                "errors": errors,
                                "status": "running",
                            },
                        )

                    except Exception as e:
                        errors += 1
                        await redis.hset(
                            job_key,
                            mapping={"errors": errors, "updated_at": _utcnow()},
                        )
                        await publish_log(
                            redis,
                            job_id,
                            f"Error processing artifact {idx}/{total_artifacts}: {repr(e)}",
                            level="error",
                        )
                        await publish_event(
                            redis,
                            job_id,
                            {
                                "type": "error",
                                "msg": str(e),
                                "current_artifact": url or str(artifact_path),
                            },
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
                    "finished_at": _utcnow(),
                    "updated_at": _utcnow(),
                    "current_artifact": "",
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
                    "errors": errors,
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
                "finished_at": _utcnow(),
                "updated_at": _utcnow(),
            },
        )
        await publish_log(redis, job_id, f"Job failed: {repr(e)}", level="error")
        await publish_event(
            redis, job_id, {"type": "error", "msg": str(e)}
        )

        # Move to DLQ
        await redis.lpush("jobs:dlq", json.dumps(job))


async def worker_loop():
    """Main worker loop: poll Redis queue and process jobs."""
    redis = aioredis.from_url(REDIS_URL, decode_responses=True)
    print(f"Worker started, polling {REDIS_URL} for jobs...")
    sys.stdout.flush()
    await redis.hset(
        WORKER_INFO_KEY,
        mapping={"pid": os.getpid(), "version": WORKER_VERSION},
    )

    async def heartbeat_loop() -> None:
        while True:
            await redis.set(HEARTBEAT_KEY, _utcnow())
            await asyncio.sleep(5)

    heartbeat_task = asyncio.create_task(heartbeat_loop())

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
            heartbeat_task.cancel()
            break
        except Exception as e:
            print(f"Worker error: {repr(e)}")
            sys.stdout.flush()
            # Continue processing
            await asyncio.sleep(1)


if __name__ == "__main__":
    asyncio.run(worker_loop())
