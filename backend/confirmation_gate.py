"""General-purpose confirmation gate (Phase 4) — shared by spark_whisper_mic.py
(voice turns, clear-history) and claude_client.py (tool-call risk gating).
Lives in its own module rather than spark_whisper_mic.py because
claude_client.py can't import from there without a circular import
(spark_whisper_mic.py already imports route_claude_reply from claude_client.py).

A confirmation can be resolved by either a chip click (WS confirmation_response,
routed here by incoming_message_watcher via offer_response) or a spoken
yes/no (routed here by mic_listener via offer_voice_text whenever a
confirmation is pending) — first response wins, exactly like the original
Phase 3 clear-history flow this generalizes.
"""

import re
import queue
import time
import uuid

import protocol
import ws_server
import mic_control

# A whole-word/phrase match resolves things — not requiring the entire
# utterance to be JUST one of these (that rejected completely reasonable
# natural phrasings like "Okay, go ahead." — extra words or punctuation
# meant it never matched anything at all), but still requiring one of these
# specific words to actually be present, which is what keeps this resistant
# to unrelated ambient noise or a hallucinated stray word.
CONFIRM_PHRASES = {"yes", "yeah", "yep", "confirm", "do it", "sure", "go ahead"}
DECLINE_PHRASES = {"no", "nope", "don't", "do not", "cancel", "stop", "never mind"}

# Per Phase 4 spec: a timeout with no response counts as a decline, not as
# "keep waiting" or "assume yes" — the safe default when nobody's there to ask.
DEFAULT_TIMEOUT = 30

pending_confirmation_id = None
confirmation_response_queue = queue.Queue()


def is_pending() -> bool:
    return pending_confirmation_id is not None


def offer_response(id_: str, choice: str) -> bool:
    """Called by incoming_message_watcher for a WS confirmation_response (a
    chip click). Returns True if it matched the currently pending
    confirmation and was queued; False if it's stale/unrelated and should be
    otherwise ignored.
    """
    if id_ == pending_confirmation_id:
        confirmation_response_queue.put(choice)
        return True
    return False


def offer_voice_text(text: str) -> bool:
    """Called by mic_listener for a fresh voice transcript. Returns True if a
    confirmation was pending and this text was routed here instead of the
    normal transcript_queue — the caller should skip its normal handling of
    this transcript in that case, win-or-lose, since it was never a fresh
    user request in the first place.
    """
    if not is_pending():
        return False
    confirmation_response_queue.put(text)
    return True


def _matches(normalized: str, phrases: set) -> bool:
    return any(re.search(rf"\b{re.escape(p)}\b", normalized) for p in phrases)


def _resolve(answer: str):
    """Returns True/False if `answer` clearly confirms or declines, None if
    it's ambiguous (matches both or neither) and the wait should continue.
    """
    # Apostrophes kept deliberately -- DECLINE_PHRASES has "don't", and
    # stripping it here (word chars/whitespace only) would leave "dont",
    # which the literal "don't" pattern below would then never match.
    normalized = re.sub(r"[^\w\s']", "", answer.lower())
    confirmed = _matches(normalized, CONFIRM_PHRASES)
    declined = _matches(normalized, DECLINE_PHRASES)
    if confirmed and not declined:
        return True
    if declined and not confirmed:
        return False
    return None


# Found live: speak_fn (on_sentence) just enqueues text for the one active
# TTS consumer and returns immediately — it does NOT block until that text
# has actually finished playing. Starting the response-timeout clock right
# after calling it meant the clock was already running (and silently
# consuming several seconds of it) while the prompt itself was still being
# synthesized and spoken, before the user had even heard the question yet.
# There's no cheap way to know exactly when Kokoro finishes a given chunk
# from here, so this estimates it from word count instead — approximate is
# fine, the goal is just "don't start the clock before the user could
# possibly have heard the question," not frame-accurate sync.
_WORDS_PER_SECOND = 2.5


def _estimate_speaking_seconds(text: str) -> float:
    return max(1.0, len(text.split()) / _WORDS_PER_SECOND)


def request_confirmation(prompt, risk="high", options=("Yes", "No"), timeout=DEFAULT_TIMEOUT, speak_fn=None):
    """Blocks the calling thread until the user confirms/declines via voice or
    a chip click, or `timeout` seconds elapse (timeout counts as a decline).
    Returns True if confirmed, False otherwise.

    Safe to call from any thread — voice resolution works even when the
    caller is a background thread with the main loop blocked waiting on it
    (e.g. mid tool-call), since mic_listener routes voice input here directly
    rather than relying on whichever loop happens to be free to poll it.

    speak_fn, if given, is called with the prompt text — callers that are
    already mid-stream (like claude_client's tool loop, which already has an
    on_sentence callback feeding the single active TTS consumer) should pass
    that through rather than this module opening a second, concurrent TTS
    call of its own.
    """
    global pending_confirmation_id

    # Drain anything stale left over from a previous confirmation so it can't
    # be misread as an answer to this one.
    while not confirmation_response_queue.empty():
        try:
            confirmation_response_queue.get_nowait()
        except queue.Empty:
            break

    confirmation_id = str(uuid.uuid4())
    pending_confirmation_id = confirmation_id
    ws_server.broadcast(protocol.confirmation_request_message(
        confirmation_id, prompt, risk=risk, options=list(options)
    ))
    if speak_fn:
        speak_fn(prompt)
        # speak_fn just enqueues the prompt for the one active TTS consumer
        # and returns immediately — waiting out an estimate of how long it
        # takes to actually finish being spoken before starting the response
        # clock below, so the user doesn't lose part of their response
        # window to a question they hadn't finished hearing yet.
        time.sleep(_estimate_speaking_seconds(prompt))
    # Only now, after the prompt has actually (approximately) finished being
    # spoken, are we truly just sitting and waiting on an answer — broadcast
    # the state here rather than before speak_fn, since speak_fn's own state
    # broadcasts (speaking -> listening) would otherwise immediately
    # overwrite an earlier "awaiting_confirmation".
    ws_server.broadcast(protocol.state_message("awaiting_confirmation"))

    # Found live (the actual root cause behind voice confirmations never
    # working): whatever's speaking the prompt already muted the mic on its
    # first sentence (see spark_whisper_mic.py's on_sentence) and normally
    # doesn't unmute again until the entire turn including this tool call
    # finishes — which can't happen until this confirmation resolves. That
    # left the mic's actual input volume near zero for the whole response
    # window, so a real spoken "yes" came through as a barely-there, mostly
    # silent clip that Whisper had no chance of transcribing correctly.
    # Explicitly unmute for the window where we're actually expecting an
    # answer, then mute again before returning to restore the "muted during
    # speech" state for whatever comes next in the turn.
    mic_control.unmute_microphone()

    deadline = time.monotonic() + timeout
    try:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            try:
                answer = confirmation_response_queue.get(timeout=remaining)
            except queue.Empty:
                return False

            resolution = _resolve(answer)
            if resolution is not None:
                return resolution
            # Anything ambiguous (stray noise, unrelated speech, or a phrase
            # matching both sets) is ignored and the wait continues within
            # whatever's left of the deadline.
    finally:
        mic_control.mute_microphone()
        pending_confirmation_id = None
        ws_server.broadcast(protocol.confirmation_resolved_message(confirmation_id))
