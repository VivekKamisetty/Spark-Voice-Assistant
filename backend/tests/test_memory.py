import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import numpy as np
import pytest

import store
import memory


def make_db():
    return store.init_db(":memory:")


def _fake_embed(vectors_by_text):
    def _embed(text):
        return np.array(vectors_by_text[text], dtype=np.float32)
    return _embed


# --- cosine_similarity / blob roundtrip -------------------------------------


def test_cosine_similarity_identical_vectors():
    v = np.array([1.0, 2.0, 3.0], dtype=np.float32)
    assert memory.cosine_similarity(v, v) == pytest.approx(1.0)


def test_cosine_similarity_orthogonal_vectors():
    a = np.array([1.0, 0.0], dtype=np.float32)
    b = np.array([0.0, 1.0], dtype=np.float32)
    assert memory.cosine_similarity(a, b) == pytest.approx(0.0)


def test_cosine_similarity_opposite_vectors():
    a = np.array([1.0, 0.0], dtype=np.float32)
    b = np.array([-1.0, 0.0], dtype=np.float32)
    assert memory.cosine_similarity(a, b) == pytest.approx(-1.0)


def test_cosine_similarity_zero_vector_is_safe():
    a = np.zeros(3, dtype=np.float32)
    b = np.array([1.0, 2.0, 3.0], dtype=np.float32)
    assert memory.cosine_similarity(a, b) == 0.0


def test_blob_roundtrip_preserves_vector():
    v = np.array([1.5, -2.25, 3.0], dtype=np.float32)
    restored = memory._from_blob(memory._to_blob(v))
    assert np.allclose(v, restored)


# --- save_memory / dedup -----------------------------------------------------


def test_save_memory_stores_new_fact(monkeypatch):
    conn = make_db()
    monkeypatch.setattr(memory, "embed", _fake_embed({"likes tea": [1.0, 0.0]}))

    memory_id = memory.save_memory(conn, "likes tea", session_id=1)

    assert memory_id is not None
    rows = store.list_memories(conn)
    assert len(rows) == 1
    assert rows[0]["content"] == "likes tea"


def test_save_memory_skips_near_duplicate(monkeypatch):
    conn = make_db()
    vectors = {"likes tea": [1.0, 0.0], "enjoys tea": [0.999, 0.001]}
    monkeypatch.setattr(memory, "embed", _fake_embed(vectors))

    memory.save_memory(conn, "likes tea", session_id=1)
    second = memory.save_memory(conn, "enjoys tea", session_id=1)

    assert second is None
    assert len(store.list_memories(conn)) == 1


def test_save_memory_keeps_dissimilar_fact(monkeypatch):
    conn = make_db()
    vectors = {"likes tea": [1.0, 0.0], "works at Acme": [0.0, 1.0]}
    monkeypatch.setattr(memory, "embed", _fake_embed(vectors))

    memory.save_memory(conn, "likes tea", session_id=1)
    second = memory.save_memory(conn, "works at Acme", session_id=1)

    assert second is not None
    assert len(store.list_memories(conn)) == 2


# --- retrieve_relevant / find_best_match ------------------------------------


def test_retrieve_relevant_orders_by_similarity_and_respects_threshold(monkeypatch):
    conn = make_db()
    vectors = {
        "likes tea": [1.0, 0.0],
        "works at Acme": [0.0, 1.0],
        "query": [0.9, 0.1],
    }
    monkeypatch.setattr(memory, "embed", _fake_embed(vectors))
    memory.save_memory(conn, "likes tea", session_id=1)
    memory.save_memory(conn, "works at Acme", session_id=1)

    results = memory.retrieve_relevant(conn, "query", top_k=5, min_similarity=0.35)

    assert results == ["likes tea"]


def test_retrieve_relevant_empty_when_no_memories(monkeypatch):
    conn = make_db()
    monkeypatch.setattr(memory, "embed", _fake_embed({"query": [1.0, 0.0]}))
    assert memory.retrieve_relevant(conn, "query") == []


def test_find_best_match_returns_highest_scoring(monkeypatch):
    conn = make_db()
    vectors = {
        "likes tea": [1.0, 0.0],
        "works at Acme": [0.0, 1.0],
        "forget the tea thing": [0.95, 0.05],
    }
    monkeypatch.setattr(memory, "embed", _fake_embed(vectors))
    memory.save_memory(conn, "likes tea", session_id=1)
    memory.save_memory(conn, "works at Acme", session_id=1)

    match = memory.find_best_match(conn, "forget the tea thing")

    assert match is not None
    _, content, _ = match
    assert content == "likes tea"


def test_find_best_match_none_below_threshold(monkeypatch):
    conn = make_db()
    vectors = {"likes tea": [1.0, 0.0], "unrelated query": [0.0, 1.0]}
    monkeypatch.setattr(memory, "embed", _fake_embed(vectors))
    memory.save_memory(conn, "likes tea", session_id=1)

    assert memory.find_best_match(conn, "unrelated query") is None


def test_find_best_match_none_when_no_memories(monkeypatch):
    conn = make_db()
    monkeypatch.setattr(memory, "embed", _fake_embed({"query": [1.0, 0.0]}))
    assert memory.find_best_match(conn, "query") is None


# --- extract_and_store --------------------------------------------------------


class _FakeTextBlock:
    def __init__(self, text):
        self.type = "text"
        self.text = text


class _FakeResponse:
    def __init__(self, text, stop_reason="end_turn"):
        self.content = [_FakeTextBlock(text)]
        self.stop_reason = stop_reason


def _fake_client(response):
    class _FakeMessages:
        def create(self, **kwargs):
            return response

    class _FakeClient:
        messages = _FakeMessages()

    return _FakeClient()


def test_extract_and_store_empty_transcript_is_noop(monkeypatch):
    conn = make_db()
    calls = []
    monkeypatch.setattr(memory, "_get_client", lambda: calls.append(1))

    stored = memory.extract_and_store(conn, session_id=1, transcript_text="")

    assert stored == 0
    assert calls == []  # never even reached the client


def test_extract_and_store_saves_extracted_facts(monkeypatch):
    conn = make_db()
    vectors = {"The user likes tea": [1.0, 0.0], "The user works at Acme": [0.0, 1.0]}
    monkeypatch.setattr(memory, "embed", _fake_embed(vectors))
    response = _FakeResponse(json.dumps({"facts": list(vectors.keys())}))
    monkeypatch.setattr(memory, "_get_client", lambda: _fake_client(response))

    stored = memory.extract_and_store(conn, session_id=1, transcript_text="user: I like tea and work at Acme")

    assert stored == 2
    assert {row["content"] for row in store.list_memories(conn)} == set(vectors.keys())


def test_extract_and_store_handles_refusal(monkeypatch):
    conn = make_db()
    response = _FakeResponse("", stop_reason="refusal")
    monkeypatch.setattr(memory, "_get_client", lambda: _fake_client(response))

    stored = memory.extract_and_store(conn, session_id=1, transcript_text="some transcript")

    assert stored == 0
    assert store.list_memories(conn) == []


def test_extract_and_store_handles_malformed_json(monkeypatch):
    conn = make_db()
    response = _FakeResponse("not json")
    monkeypatch.setattr(memory, "_get_client", lambda: _fake_client(response))

    stored = memory.extract_and_store(conn, session_id=1, transcript_text="some transcript")

    assert stored == 0


def test_extract_and_store_skips_empty_facts_list(monkeypatch):
    conn = make_db()
    response = _FakeResponse(json.dumps({"facts": []}))
    monkeypatch.setattr(memory, "_get_client", lambda: _fake_client(response))

    stored = memory.extract_and_store(conn, session_id=1, transcript_text="just chit-chat")

    assert stored == 0
    assert store.list_memories(conn) == []
