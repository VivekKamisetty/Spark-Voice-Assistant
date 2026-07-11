import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import store


def make_db():
    # In-memory DB, isolated per test — never touches the user's real
    # ~/.spark/spark.db.
    return store.init_db(":memory:")


def test_init_db_creates_tables():
    conn = make_db()
    tables = {
        row[0]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    assert {"sessions", "messages", "memories"} <= tables


def test_session_lifecycle():
    conn = make_db()
    session_id = store.start_session(conn)
    row = conn.execute("SELECT started_at, ended_at FROM sessions WHERE id = ?", (session_id,)).fetchone()
    assert row[0] is not None
    assert row[1] is None

    store.end_session(conn, session_id)
    row = conn.execute("SELECT ended_at FROM sessions WHERE id = ?", (session_id,)).fetchone()
    assert row[0] is not None


def test_add_and_load_recent_messages():
    conn = make_db()
    session_id = store.start_session(conn)
    store.add_message(conn, session_id, "user", "hello")
    store.add_message(conn, session_id, "assistant", "hi there")
    store.add_message(conn, session_id, "user", "what time is it")
    store.add_message(conn, session_id, "assistant", "3pm")

    history = store.load_recent_messages(conn, limit=20)
    assert history == [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hi there"},
        {"role": "user", "content": "what time is it"},
        {"role": "assistant", "content": "3pm"},
    ]


def test_load_recent_messages_excludes_tool_rows():
    conn = make_db()
    session_id = store.start_session(conn)
    store.add_message(conn, session_id, "user", "check my calendar")
    store.add_message(conn, session_id, "tool", "Used tools: execute_shell_command")
    store.add_message(conn, session_id, "assistant", "nothing today")

    history = store.load_recent_messages(conn, limit=20)
    assert history == [
        {"role": "user", "content": "check my calendar"},
        {"role": "assistant", "content": "nothing today"},
    ]


def test_load_recent_messages_trims_dangling_leading_assistant():
    conn = make_db()
    session_id = store.start_session(conn)
    # Simulate a window (limit=1) landing mid-pair: only the assistant half
    # of the first exchange would be visible without trimming.
    store.add_message(conn, session_id, "user", "first question")
    store.add_message(conn, session_id, "assistant", "first answer")
    store.add_message(conn, session_id, "user", "second question")
    store.add_message(conn, session_id, "assistant", "second answer")

    history = store.load_recent_messages(conn, limit=3)
    # Limit=3 grabs [first answer, second question, second answer]; the
    # leading assistant-only row must get trimmed so it starts on 'user'.
    assert history == [
        {"role": "user", "content": "second question"},
        {"role": "assistant", "content": "second answer"},
    ]


def test_load_recent_messages_trims_dangling_trailing_user():
    conn = make_db()
    session_id = store.start_session(conn)
    store.add_message(conn, session_id, "user", "answered question")
    store.add_message(conn, session_id, "assistant", "an answer")
    store.add_message(conn, session_id, "user", "unanswered question")

    history = store.load_recent_messages(conn, limit=20)
    assert history == [
        {"role": "user", "content": "answered question"},
        {"role": "assistant", "content": "an answer"},
    ]


def test_add_list_delete_memory():
    conn = make_db()
    session_id = store.start_session(conn)
    memory_id = store.add_memory(conn, "likes tea", session_id, b"\x00\x01")

    rows = store.list_memories(conn)
    assert len(rows) == 1
    assert rows[0]["id"] == memory_id
    assert rows[0]["content"] == "likes tea"
    assert rows[0]["embedding"] == b"\x00\x01"

    store.delete_memory(conn, memory_id)
    assert store.list_memories(conn) == []


def test_clear_history():
    conn = make_db()
    session_id = store.start_session(conn)
    store.add_message(conn, session_id, "user", "hello")
    store.add_message(conn, session_id, "assistant", "hi")

    store.clear_history(conn)

    assert store.load_recent_messages(conn) == []
