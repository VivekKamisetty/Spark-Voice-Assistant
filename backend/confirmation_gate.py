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


# Fallback only: used when speak_fn doesn't hand back a real completion
# signal (e.g. it's a fire-and-forget caller with no way to report one).
# Approximate is fine there — the goal is just "don't start the clock before
# the user could possibly have heard the question," not frame-accurate sync.
# Whenever speak_fn hands back a real threading.Event (see request_confirmation
# below), that's used instead: this estimate has no relationship to actual
# TTS synthesis/playback time and can elapse well before the prompt has truly
# finished being spoken — which is exactly what let a fast "yes" resolve (and
# the command execute) while the prompt was still audibly playing.
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
    call of its own. If speak_fn returns a threading.Event, it's treated as a
    real "this chunk has actually finished playing" signal and waited on
    directly instead of guessing from word count — without this, a fast
    click/voice "yes" could be drained from confirmation_response_queue and
    acted on (including actually running the command) while the prompt was
    still audibly speaking, since offer_response()/offer_voice_text() queue a
    response the instant it arrives, independent of playback.
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
        done_event = speak_fn(prompt)
        if done_event is not None:
            # Real signal from the TTS engine: wait for this exact chunk to
            # actually finish playing (or be skipped/interrupted) rather than
            # guessing. Capped generously so a stuck/never-signaling engine
            # can't hang the tool call forever — normal playback always sets
            # this well before the cap.
            done_event.wait(timeout=max(_estimate_speaking_seconds(prompt) * 4, 15))
        else:
            # speak_fn didn't hand back anything to wait on — either nothing
            # was actually queued for TTS (e.g. muted voice mode), or the
            # caller already blocked synchronously before returning (e.g.
            # speak_reply, used by the clear-history flow). Fall back to the
            # word-count estimate as a safe minimum wait either way.
            time.sleep(_estimate_speaking_seconds(prompt))
    # Only now, after the prompt has actually finished being spoken, are we
    # truly just sitting and waiting on an answer — broadcast the state here
    # rather than before speak_fn, since speak_fn's own state broadcasts
    # (speaking -> listening) would otherwise immediately overwrite an
    # earlier "awaiting_confirmation".
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
