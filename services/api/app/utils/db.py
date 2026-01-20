import json
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

DB_PATH = Path("/app/data/conversations/conversations.db")
DB_PATH.parent.mkdir(parents=True, exist_ok=True)


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS conversations (
                id TEXT PRIMARY KEY,
                created_at TEXT,
                updated_at TEXT,
                title TEXT,
                summary TEXT,
                auto_titled INTEGER DEFAULT 0
            );
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS messages (
                id TEXT PRIMARY KEY,
                conversation_id TEXT,
                timestamp TEXT,
                role TEXT,
                content TEXT,
                FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
            );
            """
        )
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(conversations)").fetchall()}
        if "auto_titled" not in columns:
            conn.execute("ALTER TABLE conversations ADD COLUMN auto_titled INTEGER DEFAULT 0")


def create_conversation() -> str:
    conversation_id = str(uuid.uuid4())
    now = datetime.utcnow().isoformat()
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO conversations (id, created_at, updated_at, title, summary, auto_titled)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (conversation_id, now, now, "New Conversation", "", 0),
        )
    return conversation_id


def list_conversations() -> List[Dict[str, Any]]:
    with _connect() as conn:
        rows = conn.execute("SELECT * FROM conversations ORDER BY updated_at DESC").fetchall()
    return [dict(row) for row in rows]


def get_conversation(conversation_id: str) -> Optional[Dict[str, Any]]:
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM conversations WHERE id = ?", (conversation_id,)
        ).fetchone()
    return dict(row) if row else None


def update_conversation(conversation_id: str, title: str, auto_titled: Optional[bool] = None) -> None:
    now = datetime.utcnow().isoformat()
    with _connect() as conn:
        if auto_titled is None:
            conn.execute(
                "UPDATE conversations SET title = ?, updated_at = ? WHERE id = ?",
                (title, now, conversation_id),
            )
        else:
            conn.execute(
                "UPDATE conversations SET title = ?, updated_at = ?, auto_titled = ? WHERE id = ?",
                (title, now, int(auto_titled), conversation_id),
            )


def delete_conversation(conversation_id: str) -> None:
    with _connect() as conn:
        conn.execute("DELETE FROM conversations WHERE id = ?", (conversation_id,))


def add_message(conversation_id: str, role: str, content: Dict[str, Any]) -> None:
    message_id = str(uuid.uuid4())
    timestamp = datetime.utcnow().isoformat()
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO messages (id, conversation_id, timestamp, role, content)
            VALUES (?, ?, ?, ?, ?)
            """,
            (message_id, conversation_id, timestamp, role, json.dumps(content)),
        )
        conn.execute(
            "UPDATE conversations SET updated_at = ? WHERE id = ?",
            (timestamp, conversation_id),
        )


def list_messages(conversation_id: str) -> List[Dict[str, Any]]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM messages WHERE conversation_id = ? ORDER BY timestamp ASC",
            (conversation_id,),
        ).fetchall()
    return [dict(row) for row in rows]
