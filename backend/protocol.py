"""Spark WebSocket protocol v2 — message shapes shared by backend and frontend.

Every message carries "v": 2. Unknown message types must be ignored
gracefully by both sides (see docs/SPARK_V2_SPEC.md, Phase 0).
"""

PROTOCOL_VERSION = 2

# "calibrating" isn't in the v2 spec's state list, but the current app already
# shows a distinct "Calibrating..." bubble during startup VAD calibration —
# dropping it would be a regression (Phase 0's acceptance bar), so it's kept
# as a Phase 0 addition to the enum.
VALID_STATES = {"idle", "listening", "thinking", "speaking", "awaiting_confirmation", "calibrating"}


def _base(msg_type: str) -> dict:
    return {"v": PROTOCOL_VERSION, "type": msg_type}


def state_message(value: str) -> dict:
    if value not in VALID_STATES:
        raise ValueError(f"Unknown state: {value!r}")
    return {**_base("state"), "value": value}


def amplitude_message(source: str, rms: float) -> dict:
    if source not in ("mic", "tts"):
        raise ValueError(f"Unknown amplitude source: {source!r}")
    return {**_base("amplitude"), "source": source, "rms": float(rms)}


def transcript_message(role: str, text: str, partial: bool = False) -> dict:
    return {**_base("transcript"), "role": role, "text": text, "partial": partial}


def assistant_chunk_message(text: str) -> dict:
    return {**_base("assistant_chunk"), "text": text}


def assistant_done_message() -> dict:
    return _base("assistant_done")


def tool_activity_message(name: str, status: str, summary: str = "") -> dict:
    if status not in ("running", "done", "error"):
        raise ValueError(f"Unknown tool_activity status: {status!r}")
    return {**_base("tool_activity"), "name": name, "status": status, "summary": summary}


def confirmation_request_message(id_: str, prompt: str, risk: str, options: list) -> dict:
    if risk not in ("low", "high"):
        raise ValueError(f"Unknown risk level: {risk!r}")
    return {**_base("confirmation_request"), "id": id_, "prompt": prompt, "risk": risk, "options": options}


def briefing_message(text: str) -> dict:
    return {**_base("briefing"), "text": text}


def parse_incoming(raw: dict) -> dict | None:
    """Validate a message received from the frontend. Returns None (and the
    caller should ignore it) if it isn't a recognized, well-formed message —
    per spec, unknown types must be ignored gracefully, not raise.
    """
    if not isinstance(raw, dict):
        return None

    msg_type = raw.get("type")

    if msg_type == "confirmation_response":
        if "id" in raw and "choice" in raw:
            return raw
        return None

    if msg_type == "interrupt":
        return raw

    if msg_type == "text_input":
        if "text" in raw:
            return raw
        return None

    return None
