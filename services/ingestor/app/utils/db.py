import sqlite3
from pathlib import Path

DB_PATH = Path("/app/data/ingest/metadata.db")
DB_PATH.parent.mkdir(parents=True, exist_ok=True)


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    """Initialize the ingest metadata database schema.

    Creates tables and indexes if they don't exist. Safe to call multiple times (idempotent).
    """
    with connect() as conn:
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
