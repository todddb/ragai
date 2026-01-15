import json
from datetime import datetime
from pathlib import Path
from typing import Dict, List

import httpx
import yaml
from qdrant_client import QdrantClient

from app.utils.db import connect
from app.utils.qdrant import delete_by_doc_id, ensure_collection, upsert_vectors

ARTIFACT_DIR = Path("/app/data/artifacts")
CONFIG_PATH = Path("/app/config/system.yml")
INGEST_CONFIG = Path("/app/config/ingest.yml")


def _load_config(path: Path) -> Dict:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _load_embeddings(texts: List[str], host: str, model: str) -> List[List[float]]:
    vectors = []
    for text in texts:
        response = httpx.post(
            f"{host}/api/embeddings",
            json={"model": model, "prompt": text},
            timeout=60.0,
        )
        response.raise_for_status()
        vectors.append(response.json()["embedding"])
    return vectors


def ingest() -> None:
    system_config = _load_config(CONFIG_PATH)
    ingest_config = _load_config(INGEST_CONFIG)
    qdrant_host = system_config["qdrant"]["host"]
    collection = system_config["qdrant"]["collection"]
    embedding_model = system_config["ollama"]["embedding_model"]
    ollama_host = system_config["ollama"]["host"]

    client = QdrantClient(url=qdrant_host)
    ensure_collection(client, collection, vector_size=1024)

    with connect() as conn:
        for artifact_path in ARTIFACT_DIR.glob("*/artifact.json"):
            artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
            doc_id = artifact["doc_id"]
            content_hash = artifact["content_hash"]
            row = conn.execute(
                "SELECT content_hash FROM documents WHERE doc_id = ?", (doc_id,)
            ).fetchone()
            if row and row["content_hash"] == content_hash:
                continue
            if row:
                delete_by_doc_id(client, collection, doc_id)
                conn.execute("DELETE FROM chunks WHERE doc_id = ?", (doc_id,))
                conn.execute("DELETE FROM documents WHERE doc_id = ?", (doc_id,))

            chunks_path = artifact_path.parent / "chunks.jsonl"
            chunks = []
            for line in chunks_path.read_text(encoding="utf-8").splitlines():
                chunks.append(json.loads(line))

            texts = [chunk["text"] for chunk in chunks]
            vectors = _load_embeddings(texts, ollama_host, embedding_model)
            ids = [chunk["chunk_id"] for chunk in chunks]
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
            upsert_vectors(client, collection, ids, vectors, payloads)

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
                        chunk["chunk_id"],
                    )
                    for chunk in chunks
                ],
            )
        conn.commit()
