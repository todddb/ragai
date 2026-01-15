import sqlite3
from pathlib import Path

DB_PATH = Path("/app/data/ingest/metadata.db")
DB_PATH.parent.mkdir(parents=True, exist_ok=True)


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with connect() as conn:
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
