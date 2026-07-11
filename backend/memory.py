"""Semantic memory (Phase 5 of docs/SPARK_V2_SPEC.md) — local embeddings
(sentence-transformers, nothing leaves the machine) over the `memories`
table, whose schema store.py reserved back in Phase 1.

Every function here takes an explicit sqlite3.Connection, the same
convention store.py uses. store.py's own docstring restricts its shared
connection to spark_whisper_mic.py's main() thread — callers here that run
on a different thread (claude_client.py's tool handlers and per-turn
retrieval, which run on the route_claude_reply background thread, not
main()) must open and close their own short-lived connection via
store.init_db() rather than reuse the shared one, since sqlite3 connections
aren't safe to share across threads.
"""

import json
import os

import numpy as np
from anthropic import Anthropic
from dotenv import load_dotenv

import store

load_dotenv()

EMBED_MODEL_NAME = "all-MiniLM-L6-v2"

# Facts confidently similar to something already stored aren't worth a
# second row — 0.9 is high enough that only near-duplicate phrasings of the
# same fact collide, not merely related-but-distinct facts.
DEDUP_SIMILARITY_THRESHOLD = 0.9

# Facts below this aren't relevant enough to the current utterance to be
# worth spending system-prompt tokens on every turn.
RETRIEVAL_SIMILARITY_THRESHOLD = 0.35
RETRIEVAL_TOP_K = 5

# How often (in real conversational turns, not raw loop iterations) a
# mid-session extraction pass runs, in addition to the one at session end —
# so a long session doesn't lose everything to a crash before ever getting
# extracted.
EXTRACT_EVERY_N_TURNS = 15

_model = None
_client = None


def _get_model():
    # Loaded lazily rather than at import time — keeps the model's load
    # latency out of every module that imports this one (audio_bands,
    # protocol, etc. via spark_whisper_mic.py) for something only actually
    # needed once extraction or retrieval first runs.
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer

        _model = SentenceTransformer(EMBED_MODEL_NAME)
    return _model


def _get_client():
    global _client
    if _client is None:
        _client = Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
    return _client


def embed(text: str) -> np.ndarray:
    return np.asarray(_get_model().encode(text), dtype=np.float32)


def _to_blob(vector: np.ndarray) -> bytes:
    return np.asarray(vector, dtype=np.float32).tobytes()


def _from_blob(blob: bytes) -> np.ndarray:
    return np.frombuffer(blob, dtype=np.float32)


def cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    denom = np.linalg.norm(a) * np.linalg.norm(b)
    if denom == 0:
        return 0.0
    return float(np.dot(a, b) / denom)


def retrieve_relevant(conn, query_text: str, top_k: int = RETRIEVAL_TOP_K,
                       min_similarity: float = RETRIEVAL_SIMILARITY_THRESHOLD) -> list:
    """Up to `top_k` memory contents relevant to `query_text`, most-similar
    first, above `min_similarity`. Empty list if nothing qualifies (including
    when there are no memories yet) — a brute-force linear scan, which is
    fine at the scale a single user's memory table will ever reach.
    """
    rows = store.list_memories(conn)
    if not rows:
        return []
    query_vec = embed(query_text)
    scored = [
        (row["content"], cosine_similarity(query_vec, _from_blob(row["embedding"])))
        for row in rows
        if row["embedding"] is not None
    ]
    scored = [item for item in scored if item[1] >= min_similarity]
    scored.sort(key=lambda item: item[1], reverse=True)
    return [content for content, _ in scored[:top_k]]


def find_best_match(conn, query_text: str, min_similarity: float = RETRIEVAL_SIMILARITY_THRESHOLD):
    """(memory_id, content, similarity) for the single best match to
    `query_text`, or None if nothing clears `min_similarity`. Used by the
    forget tool, which needs one concrete memory to confirm deleting rather
    than a ranked list.
    """
    rows = store.list_memories(conn)
    if not rows:
        return None
    query_vec = embed(query_text)
    best = None
    for row in rows:
        if row["embedding"] is None:
            continue
        similarity = cosine_similarity(query_vec, _from_blob(row["embedding"]))
        if best is None or similarity > best[2]:
            best = (row["id"], row["content"], similarity)
    if best is not None and best[2] >= min_similarity:
        return best
    return None


def _is_duplicate(conn, candidate_vec: np.ndarray) -> bool:
    for row in store.list_memories(conn):
        if row["embedding"] is None:
            continue
        if cosine_similarity(candidate_vec, _from_blob(row["embedding"])) > DEDUP_SIMILARITY_THRESHOLD:
            return True
    return False


def save_memory(conn, content: str, session_id):
    """Embeds and stores one fact, skipping it if a near-duplicate already
    exists (cosine > DEDUP_SIMILARITY_THRESHOLD). Returns the new row id, or
    None if skipped as a duplicate. Used directly by the remember_this tool,
    and by extract_and_store below for each extracted fact.
    """
    vec = embed(content)
    if _is_duplicate(conn, vec):
        return None
    return store.add_memory(conn, content, session_id, _to_blob(vec))


_EXTRACTION_SYSTEM_PROMPT = (
    "Extract durable, user-specific facts worth remembering across future "
    "conversations from this transcript — preferences, ongoing projects, "
    "recurring schedule items, names of people/pets/places the user "
    "mentioned, and similar standing facts about the user's life. Do not "
    "extract one-off requests, questions, or anything only relevant to this "
    "single conversation (e.g. \"what's today's date\" or a specific command "
    "that was run). Phrase each fact so it makes sense read alone with no "
    "other context later (e.g. \"The user's standup is at 9:30am\", not "
    "\"it's at 9:30\"). Return an empty list if there's nothing worth "
    "remembering."
)

_EXTRACTION_SCHEMA = {
    "type": "object",
    "properties": {
        "facts": {
            "type": "array",
            "items": {"type": "string"},
        }
    },
    "required": ["facts"],
    "additionalProperties": False,
}


def extract_and_store(conn, session_id, transcript_text: str) -> int:
    """Sends the transcript to Claude to pull out durable facts, embeds and
    stores each (skipping near-duplicates of existing memories). Returns how
    many were actually stored. Safe to call with an empty/short transcript —
    returns 0 without making an API call.
    """
    if not transcript_text.strip():
        return 0

    response = _get_client().messages.create(
        model="claude-sonnet-5",
        max_tokens=1024,
        system=_EXTRACTION_SYSTEM_PROMPT,
        output_config={"format": {"type": "json_schema", "schema": _EXTRACTION_SCHEMA}},
        messages=[{"role": "user", "content": transcript_text}],
    )

    if response.stop_reason == "refusal":
        return 0

    raw = "".join(block.text for block in response.content if block.type == "text")
    try:
        facts = json.loads(raw)["facts"]
    except (json.JSONDecodeError, KeyError, TypeError):
        print(f"[Memory] Extraction returned an unexpected shape, skipping: {raw[:200]}")
        return 0

    stored = 0
    for fact in facts:
        if not isinstance(fact, str) or not fact.strip():
            continue
        if save_memory(conn, fact.strip(), session_id) is not None:
            stored += 1
    return stored
