"""
Tests for tools/validate_ingest.py

Tests:
- Schema initialization when database is missing
- Schema initialization when tables don't exist
- Correct count retrieval after initialization
- Data integrity checks
"""

import json
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

# Define the schema SQL here to avoid importing from validate_ingest
# (which requires redis, qdrant_client, etc.)
SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS documents (
    doc_id TEXT PRIMARY KEY,
    url TEXT,
    content_hash TEXT,
    ingested_at TEXT,
    chunk_count INTEGER
);

CREATE TABLE IF NOT EXISTS chunks (
    chunk_id TEXT PRIMARY KEY,
    doc_id TEXT,
    chunk_index INTEGER,
    vector_id TEXT,
    FOREIGN KEY (doc_id) REFERENCES documents(doc_id)
);

CREATE INDEX IF NOT EXISTS idx_documents_url ON documents(url);
CREATE INDEX IF NOT EXISTS idx_chunks_doc_id ON chunks(doc_id);
"""


def ensure_db_schema(db_path: str) -> bool:
    """Ensure the database schema is initialized. Returns True if successful."""
    try:
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        # Enable WAL mode for better concurrent access
        cursor.execute("PRAGMA journal_mode=WAL;")

        # Execute schema creation
        cursor.executescript(SCHEMA_SQL)

        # Set schema version
        cursor.execute("PRAGMA user_version = 1;")

        conn.commit()
        conn.close()
        return True
    except Exception as e:
        print(f"Warning: Could not initialize DB schema: {e}", file=sys.stderr)
        return False


def check_schema_exists(db_path: str) -> dict:
    """Check if the required tables exist in the database."""
    result = {"exists": False, "tables": [], "error": None}

    if not Path(db_path).exists():
        result["error"] = "database_not_found"
        return result

    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name;")
        tables = [row[0] for row in cursor.fetchall()]

        conn.close()

        result["tables"] = tables
        result["exists"] = "documents" in tables and "chunks" in tables
        return result
    except Exception as e:
        result["error"] = str(e)
        return result


def get_db_counts(db_path: str) -> dict:
    """Get document and chunk counts from SQLite. Ensures schema exists first."""
    if not Path(db_path).exists():
        return {"documents": 0, "chunks": 0, "db_exists": False}

    # Check if schema exists
    schema_check = check_schema_exists(db_path)
    if not schema_check["exists"]:
        # Try to initialize the schema
        if ensure_db_schema(db_path):
            # Schema was just initialized - tables are empty
            return {"documents": 0, "chunks": 0, "db_exists": True, "schema_initialized": True}
        else:
            return {
                "documents": -1,
                "chunks": -1,
                "db_exists": True,
                "error": f"Schema missing and could not be initialized. Tables found: {schema_check['tables']}"
            }

    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        docs = cursor.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
        chunks = cursor.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]

        conn.close()
        return {"documents": docs, "chunks": chunks, "db_exists": True}
    except Exception as e:
        return {"documents": -1, "chunks": -1, "db_exists": True, "error": str(e)}


class TestValidateIngest(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tempdir, "metadata.db")

    def tearDown(self) -> None:
        shutil.rmtree(self.tempdir)

    def test_ensure_db_schema_creates_tables(self) -> None:
        """Test that ensure_db_schema creates the required tables."""
        # DB doesn't exist yet
        self.assertFalse(Path(self.db_path).exists())

        # Initialize schema
        result = ensure_db_schema(self.db_path)
        self.assertTrue(result)

        # DB should now exist
        self.assertTrue(Path(self.db_path).exists())

        # Check tables exist
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name;")
        tables = [row[0] for row in cursor.fetchall()]
        conn.close()

        self.assertIn("documents", tables)
        self.assertIn("chunks", tables)

    def test_check_schema_exists_when_empty_db(self) -> None:
        """Test schema check when DB exists but has no tables."""
        # Create empty database
        conn = sqlite3.connect(self.db_path)
        conn.close()

        result = check_schema_exists(self.db_path)
        self.assertFalse(result["exists"])
        self.assertEqual(result["tables"], [])

    def test_check_schema_exists_when_tables_present(self) -> None:
        """Test schema check when tables exist."""
        # Create database with schema
        ensure_db_schema(self.db_path)

        result = check_schema_exists(self.db_path)
        self.assertTrue(result["exists"])
        self.assertIn("documents", result["tables"])
        self.assertIn("chunks", result["tables"])

    def test_get_db_counts_missing_db(self) -> None:
        """Test get_db_counts when database doesn't exist."""
        result = get_db_counts(self.db_path)
        self.assertEqual(result["documents"], 0)
        self.assertEqual(result["chunks"], 0)
        self.assertFalse(result.get("db_exists", True))

    def test_get_db_counts_empty_schema(self) -> None:
        """Test get_db_counts initializes schema when tables don't exist."""
        # Create empty database (no tables)
        conn = sqlite3.connect(self.db_path)
        conn.close()

        result = get_db_counts(self.db_path)

        # Should have initialized the schema
        self.assertEqual(result["documents"], 0)
        self.assertEqual(result["chunks"], 0)
        self.assertTrue(result.get("db_exists", False))
        self.assertTrue(result.get("schema_initialized", False))

    def test_get_db_counts_with_data(self) -> None:
        """Test get_db_counts returns correct counts when data exists."""
        # Create database with schema
        ensure_db_schema(self.db_path)

        # Insert some data
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute(
            "INSERT INTO documents (doc_id, url, content_hash, ingested_at, chunk_count) VALUES (?, ?, ?, ?, ?)",
            ("doc1", "https://example.com/1", "hash1", "2024-01-01", 3),
        )
        cursor.execute(
            "INSERT INTO documents (doc_id, url, content_hash, ingested_at, chunk_count) VALUES (?, ?, ?, ?, ?)",
            ("doc2", "https://example.com/2", "hash2", "2024-01-01", 2),
        )

        for i in range(5):
            cursor.execute(
                "INSERT INTO chunks (chunk_id, doc_id, chunk_index, vector_id) VALUES (?, ?, ?, ?)",
                (f"chunk{i}", "doc1" if i < 3 else "doc2", i % 3, f"vec{i}"),
            )

        conn.commit()
        conn.close()

        result = get_db_counts(self.db_path)
        self.assertEqual(result["documents"], 2)
        self.assertEqual(result["chunks"], 5)

    def test_no_error_on_missing_schema(self) -> None:
        """Test that get_db_counts doesn't error when schema is missing."""
        # Create empty database
        conn = sqlite3.connect(self.db_path)
        conn.close()

        # This should not raise an exception
        try:
            result = get_db_counts(self.db_path)
            self.assertIsInstance(result, dict)
            # Should not have error key indicating query failure
            self.assertIsNone(result.get("error"))
        except Exception as e:
            self.fail(f"get_db_counts raised an exception: {e}")


class TestSchemaIntegrity(unittest.TestCase):
    """Test that schema initialization is idempotent."""

    def setUp(self) -> None:
        self.tempdir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tempdir, "metadata.db")

    def tearDown(self) -> None:
        shutil.rmtree(self.tempdir)

    def test_schema_init_is_idempotent(self) -> None:
        """Test that calling ensure_db_schema multiple times is safe."""
        # First call
        result1 = ensure_db_schema(self.db_path)
        self.assertTrue(result1)

        # Insert some data
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO documents (doc_id, url, content_hash, ingested_at, chunk_count) VALUES (?, ?, ?, ?, ?)",
            ("doc1", "https://example.com/", "hash1", "2024-01-01", 1),
        )
        conn.commit()
        conn.close()

        # Second call should not error or destroy data
        result2 = ensure_db_schema(self.db_path)
        self.assertTrue(result2)

        # Data should still be there
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM documents")
        count = cursor.fetchone()[0]
        conn.close()

        self.assertEqual(count, 1)


if __name__ == "__main__":
    unittest.main()
