"""SQLite-backed session/message persistence (Phase 1 of docs/SPARK_V2_SPEC.md).

Only ever call these functions from spark_whisper_mic.py's main() thread —
sqlite3 connections aren't safe to share across threads, and nothing here
needs to run from mic_listener or the WebSocket server thread.
"""

import os
import sqlite3
import time

DEFAULT_DB_PATH = os.path.expanduser("~/.spark/spark.db")


def init_db(db_path: str = DEFAULT_DB_PATH) -> sqlite3.Connection:
    """Open (creating if needed) the database and ensure the schema exists."""
    if db_path != ":memory:":
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            started_at REAL NOT NULL,
            ended_at REAL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id INTEGER NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at REAL NOT NULL
        )
        """
    )
    # Schema only for now — populated starting in Phase 5's semantic memory work.
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS memories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            content TEXT NOT NULL,
            source_session_id INTEGER,
            created_at REAL NOT NULL,
            embedding BLOB
        )
        """
    )
    conn.commit()
    return conn


def start_session(conn: sqlite3.Connection) -> int:
    cur = conn.execute("INSERT INTO sessions (started_at) VALUES (?)", (time.time(),))
    conn.commit()
    return cur.lastrowid


def end_session(conn: sqlite3.Connection, session_id: int) -> None:
    conn.execute("UPDATE sessions SET ended_at = ? WHERE id = ?", (time.time(), session_id))
    conn.commit()


def add_message(conn: sqlite3.Connection, session_id: int, role: str, content: str) -> None:
    conn.execute(
        "INSERT INTO messages (session_id, role, content, created_at) VALUES (?, ?, ?, ?)",
        (session_id, role, content, time.time()),
    )
    conn.commit()


def load_recent_messages(conn: sqlite3.Connection, limit: int = 20) -> list:
    """Last `limit` conversational turns across recent sessions, chronological,
    safe to feed straight to Claude as chat_history.

    Two things this deliberately guards against, since Claude's API requires
    strict user/assistant alternation (see the duplicate-message bug fixed in
    PR #14 — this is the same class of bug):
    - 'tool' rows (tool-call summaries, logged for history but not a real
      conversational turn) are excluded entirely.
    - the LIMIT window can land on an odd boundary — e.g. starting mid-pair,
      or ending on a user message with no assistant reply yet persisted
      (crash, or the reload just happened to land there). Both ends are
      trimmed until the sequence starts on 'user' and ends on 'assistant'.
    """
    rows = conn.execute(
        "SELECT role, content FROM messages WHERE role IN ('user', 'assistant') "
        "ORDER BY created_at DESC LIMIT ?",
        (limit,),
    ).fetchall()
    rows.reverse()

    while rows and rows[0][0] != "user":
        rows.pop(0)
    while rows and rows[-1][0] != "assistant":
        rows.pop()

    return [{"role": role, "content": content} for role, content in rows]


def clear_history(conn: sqlite3.Connection) -> None:
    conn.execute("DELETE FROM messages")
    conn.commit()
