import json
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Set

import yaml
from qdrant_client import QdrantClient
from qdrant_client.http import models as rest

from app.utils.ollama_embed import embed_text

ARTIFACT_DIR = Path("/app/data/artifacts")
CONFIG_PATH = Path("/app/config/system.yml")
DB_PATH = Path("/app/data/ingest/metadata.db")


def _load_config(path: Path) -> Dict:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _init_db(conn: sqlite3.Connection) -> None:
    """Initialize the ingest metadata database schema.

    Creates tables and indexes if they don't exist. Safe to call multiple times (idempotent).
    """
    # Enable WAL mode for better concurrent access
    conn.execute("PRAGMA journal_mode=WAL;")

    # Create documents table
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS documents (
            doc_id TEXT PRIMARY KEY,
            url TEXT,
            content_hash TEXT,
            ingested_at TEXT,
            chunk_count INTEGER
        );
        """
    )

    # Create chunks table
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS chunks (
            chunk_id TEXT PRIMARY KEY,
            doc_id TEXT,
            chunk_index INTEGER,
            vector_id TEXT,
            FOREIGN KEY (doc_id) REFERENCES documents(doc_id)
        );
        """
    )

    # Create indexes for better query performance
    conn.execute("CREATE INDEX IF NOT EXISTS idx_documents_url ON documents(url);")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_chunks_doc_id ON chunks(doc_id);")

    # Set schema version for tracking
    conn.execute("PRAGMA user_version = 1;")

    # Commit changes
    conn.commit()


def ensure_metadata_db_initialized() -> None:
    """Ensure the ingest metadata database is initialized.

    Safe to call multiple times (idempotent).
    """
    with _connect() as conn:
        _init_db(conn)


def _ensure_collection(client: QdrantClient, collection: str, vector_size: int) -> None:
    try:
        collections = client.get_collections().collections
    except Exception as e:
        # Handle potential validation errors when getting collections
        print(f"Warning: Error getting collections (possibly validation error): {e}")
        # Try to create the collection anyway
        try:
            client.create_collection(
                collection_name=collection,
                vectors_config=rest.VectorParams(size=vector_size, distance=rest.Distance.COSINE),
            )
            client.create_payload_index(collection_name=collection, field_name="doc_id", field_schema="keyword")
        except Exception:
            pass  # Collection might already exist
        return

    if any(col.name == collection for col in collections):
        try:
            info = client.get_collection(collection)
            existing_size = info.config.params.vectors.size
            if existing_size != vector_size:
                raise ValueError(
                    f"Qdrant collection '{collection}' has vector size {existing_size}, "
                    f"expected {vector_size}. Clear vectors or use a matching embedding model."
                )
        except AttributeError:
            # Handle case where config structure doesn't match expected schema
            # This can happen with different Qdrant server versions
            print(f"Warning: Could not verify vector size for collection '{collection}' due to schema mismatch")
            print("Continuing with ingest - ensure your embedding model matches the collection configuration")
        except Exception as e:
            # Handle pydantic validation errors or other exceptions
            if "validation" in str(e).lower() or "extra" in str(e).lower():
                print(f"Warning: Qdrant config validation error (server schema mismatch): {e}")
                print("This is typically harmless - continuing with ingest")
            else:
                raise
        return

    client.create_collection(
        collection_name=collection,
        vectors_config=rest.VectorParams(size=vector_size, distance=rest.Distance.COSINE),
    )
    client.create_payload_index(collection_name=collection, field_name="doc_id", field_schema="keyword")


def _delete_by_doc_id(client: QdrantClient, collection: str, doc_id: str) -> None:
    client.delete(
        collection_name=collection,
        points_selector=rest.Filter(
            must=[rest.FieldCondition(key="doc_id", match=rest.MatchValue(value=doc_id))]
        ),
    )


def _upsert_vectors(
    client,
    collection: str,
    ids: list,
    vectors: list,
    payloads: list,
    batch_size: int = 50,
) -> None:
    """
    Upsert points into Qdrant in smaller batches to avoid large JSON payloads.
    This replaces a previous single-call implementation that used rest.Batch(ids=..., vectors=..., payloads=...).
    """
    if not (len(ids) == len(vectors) == len(payloads)):
        raise ValueError("ids, vectors and payloads must have equal length")

    # Send in batches
    for i in range(0, len(ids), batch_size):
        batch_ids = ids[i : i + batch_size]
        batch_vectors = vectors[i : i + batch_size]
        batch_payloads = payloads[i : i + batch_size]

        # Optional debug: print approximate JSON size of this batch
        try:
            sample_body = {
                "points": [
                    {"id": _id, "vector": vec, "payload": pl}
                    for _id, vec, pl in zip(batch_ids, batch_vectors, batch_payloads)
                ]
            }
            print(f"DEBUG: upsert batch bytes={len(json.dumps(sample_body).encode('utf-8'))}", file=sys.stderr)
        except Exception:
            # never fail on debug measurement
            pass

        # Build point structs and upsert this batch
        client.upsert(
            collection_name=collection,
            points=[
                rest.PointStruct(id=_id, vector=vec, payload=pl)
                for _id, vec, pl in zip(batch_ids, batch_vectors, batch_payloads)
            ],
        )

def _load_embeddings(texts: List[str], host: str, model: str) -> List[List[float]]:
    return [embed_text(host, model, text) for text in texts]


def _doc_ids_on_disk() -> Set[str]:
    return {path.parent.name for path in ARTIFACT_DIR.glob("*/artifact.json")}


def _qdrant_has_points(
    client: QdrantClient, collection: str, doc_id: str, url: str
) -> bool:
    conditions = []
    if doc_id:
        conditions.append(
            rest.FieldCondition(key="doc_id", match=rest.MatchValue(value=doc_id))
        )
    if url:
        conditions.append(rest.FieldCondition(key="url", match=rest.MatchValue(value=url)))
    if not conditions:
        return False
    if len(conditions) == 1:
        point_filter = rest.Filter(must=conditions)
    else:
        point_filter = rest.Filter(should=conditions)
    try:
        count_result = client.count(
            collection_name=collection,
            count_filter=point_filter,
            exact=True,
        )
        return (count_result.count or 0) > 0
    except Exception:
        try:
            points, _ = client.scroll(
                collection_name=collection,
                scroll_filter=point_filter,
                limit=1,
            )
            return len(points) > 0
        except Exception:
            return False


def run_ingest_job(log, job_id: str = None) -> None:
    try:
        system_config = _load_config(CONFIG_PATH)
        qdrant_host = system_config["qdrant"]["host"]
        collection = system_config["qdrant"]["collection"]
        embedding_model = system_config["ollama"]["embedding_model"]
        ollama_host = system_config["ollama"]["host"]
        log(f"Connecting to Qdrant at {qdrant_host}")
        client = QdrantClient(url=qdrant_host)
        log(f"Probing embedding model {embedding_model}")
        vector_size = len(embed_text(ollama_host, embedding_model, "dimension probe"))
        log(f"Vector size: {vector_size}")
        log(f"Ensuring collection '{collection}' exists")
        _ensure_collection(client, collection, vector_size=vector_size)
        log("Starting ingest job")
    except Exception as e:
        log(f"Error during ingest setup: {e}")
        raise
    with _connect() as conn:
        _init_db(conn)
        disk_doc_ids = _doc_ids_on_disk()
        stored_doc_ids = {
            row["doc_id"]
            for row in conn.execute("SELECT doc_id FROM documents").fetchall()
        }
        missing_doc_ids = stored_doc_ids - disk_doc_ids
        for doc_id in missing_doc_ids:
            _delete_by_doc_id(client, collection, doc_id)
            conn.execute("DELETE FROM chunks WHERE doc_id = ?", (doc_id,))
            conn.execute("DELETE FROM documents WHERE doc_id = ?", (doc_id,))
            log(f"Deleted vectors for missing doc_id {doc_id}")
        artifact_files = list(ARTIFACT_DIR.glob("*/artifact.json"))
        log(f"Found {len(artifact_files)} artifact(s)")
        for artifact_path in artifact_files:
            artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
            doc_id = artifact["doc_id"]
            content_hash = artifact["content_hash"]
            row = conn.execute(
                "SELECT content_hash, chunk_count FROM documents WHERE doc_id = ?",
                (doc_id,),
            ).fetchone()
            if row and row["content_hash"] == content_hash:
                qdrant_has_points = _qdrant_has_points(
                    client, collection, doc_id, artifact.get("url", "")
                )
                if row["chunk_count"] and row["chunk_count"] > 0 and qdrant_has_points:
                    continue
                _delete_by_doc_id(client, collection, doc_id)
                conn.execute("DELETE FROM chunks WHERE doc_id = ?", (doc_id,))
                conn.execute("DELETE FROM documents WHERE doc_id = ?", (doc_id,))
                log(
                    f"Repairing partial ingest for {doc_id} "
                    f"chunk_count={row['chunk_count']} qdrant_points={qdrant_has_points}"
                )
                row = None
            if row:
                _delete_by_doc_id(client, collection, doc_id)
                conn.execute("DELETE FROM chunks WHERE doc_id = ?", (doc_id,))
                conn.execute("DELETE FROM documents WHERE doc_id = ?", (doc_id,))
                log(f"Refreshing vectors for {doc_id}")
            chunks_path = artifact_path.parent / "chunks.jsonl"
            chunks = []
            for line in chunks_path.read_text(encoding="utf-8").splitlines():
                chunks.append(json.loads(line))
            if not chunks:
                log(f"Skipping {doc_id} (no chunks found)")
                continue
            texts = [chunk["text"] for chunk in chunks]
            vectors = _load_embeddings(texts, ollama_host, embedding_model)
            # Generate deterministic UUID for Qdrant point IDs (not chunk_id strings)
            ids = [str(uuid.uuid5(uuid.NAMESPACE_URL, chunk["chunk_id"])) for chunk in chunks]
            payloads = [
                {
                    "doc_id": doc_id,
                    "chunk_id": chunk["chunk_id"],
                    "url": artifact["url"],
                    "title": artifact.get("title", ""),
                    "text": chunk["text"],
                }
                for chunk in chunks
            ]
            assert len(ids) == len(vectors) == len(payloads), (len(ids), len(vectors), len(payloads))
            _upsert_vectors(client, collection, ids, vectors, payloads)
            conn.execute(
                "INSERT INTO documents (doc_id, url, content_hash, ingested_at, chunk_count) VALUES (?, ?, ?, ?, ?)",
                (
                    doc_id,
                    artifact["url"],
                    content_hash,
                    datetime.utcnow().isoformat(),
                    len(chunks),
                ),
            )
            conn.executemany(
                "INSERT INTO chunks (chunk_id, doc_id, chunk_index, vector_id) VALUES (?, ?, ?, ?)",
                [
                    (
                        chunk["chunk_id"],
                        doc_id,
                        chunk["chunk_index"],
                        ids[i],
                    )
                    for i, chunk in enumerate(chunks)
                ],
            )
        conn.commit()
    log("Ingest job complete")
