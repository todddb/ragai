import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Set, Tuple
import yaml
from qdrant_client import QdrantClient

from app.utils.db import connect
from app.utils.ollama_embed import embed_text
from app.utils.qdrant import delete_by_doc_id, ensure_collection, upsert_vectors

ARTIFACT_DIR = Path("/app/data/artifacts")
CONFIG_PATH = Path("/app/config/system.yml")
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


def _load_embeddings(texts: List[str], host: str, model: str) -> List[List[float]]:
    return [embed_text(host, model, text) for text in texts]


def _doc_ids_on_disk() -> Set[str]:
    return {path.parent.name for path in ARTIFACT_DIR.glob("*/artifact.json")}


def _filter_chunks(chunks: List[Dict]) -> Tuple[List[Dict], Dict[str, int]]:
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


def ingest() -> None:
    system_config = _load_config(CONFIG_PATH)
    qdrant_host = system_config["qdrant"]["host"]
    collection = system_config["qdrant"]["collection"]
    embedding_model = system_config["ollama"]["embedding_model"]
    ollama_host = system_config["ollama"]["host"]

    client = QdrantClient(url=qdrant_host)
    vector_size = len(embed_text(ollama_host, embedding_model, "dimension probe"))
    ensure_collection(client, collection, vector_size=vector_size)

    with connect() as conn:
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
            print(f"Deleted vectors for missing doc_id {doc_id}")
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

            total_chunks = len(chunks)
            filtered_chunks, skipped_counts = _filter_chunks(chunks)
            kept_chunks = len(filtered_chunks)
            skipped_total = total_chunks - kept_chunks
            print(
                "Doc "
                f"{doc_id}: total_chunks={total_chunks} kept_chunks={kept_chunks} "
                f"skipped_chunks={skipped_total} skipped_by_reason={skipped_counts}"
            )
            if kept_chunks == 0:
                continue

            texts = [chunk["text"] for chunk in filtered_chunks]
            vectors = _load_embeddings(texts, ollama_host, embedding_model)
            # ids = [chunk["chunk_id"] for chunk in filtered_chunks]   <-- This was breaking
            ids = [str(uuid.uuid5(uuid.NAMESPACE_URL, chunk["chunk_id"])) for chunk in filtered_chunks]
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
            upsert_vectors(client, collection, ids, vectors, payloads)

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
                    (
                         chunk["chunk_id"],
                         doc_id,
                         chunk["chunk_index"],
                         ids[i],
                    )
                    for i,chunk in enumerate(filtered_chunks)
                 ],
            )

        conn.commit()
