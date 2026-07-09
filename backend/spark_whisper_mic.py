import time
import subprocess
import functools
import threading
import queue
import mlx_whisper
import sounddevice as sd
import numpy as np
import os
import signal
import sys
import atexit


from claude_client import route_claude_reply as route_gpt_reply
import ws_server
import protocol
import store
import audio_bands
import confirmation_gate
import mic_control
from tts_engine import KokoroTTSEngine, Pyttsx3TTSEngine
import signal

# Set once main() starts a session; cleanup_before_exit() needs them to close
# the session out cleanly on shutdown, but doesn't have direct access to
# main()'s local variables.
_db = None
_session_id = None

# Claude is prompted (see claude_client.py) to lead every reply with a short
# spoken-friendly headline, then an optional ---DETAIL--- marker followed by
# more text — full detail always reaches the panel either way, this only
# controls how much of it also gets spoken aloud, since reading is faster
# than listening once the detail's already on screen. "brief" (default)
# speaks only the headline; "full" restores the old behavior of speaking
# everything; "muted" speaks nothing. Manual override for whichever the
# system-prompt heuristic gets wrong for a given user/context.
DETAIL_MARKER = "---DETAIL---"
VOICE_MODE = os.getenv("SPARK_VOICE_MODE", "brief").strip().lower()
if VOICE_MODE not in ("full", "brief", "muted"):
    VOICE_MODE = "brief"

def write_status(status, text="", show_popup=False):
    # "inactive" predates the v2 spec's state enum; it means the same thing
    # as "idle" so it's translated here rather than widening VALID_STATES.
    protocol_state = "idle" if status == "inactive" else status
    ws_server.broadcast(protocol.state_message(protocol_state))
    if text:
        # Always a single standalone message (confirmation prompts, etc.),
        # not part of a streamed multi-sentence reply, so there's no
        # sentence sequence to index — index 0 is simply "the whole thing".
        ws_server.broadcast(protocol.assistant_chunk_message(text, 0))
        done = protocol.assistant_done_message()
        done["show_popup"] = show_popup  # not in the v2 spec proper; Phase 3's
        # panel redesign replaces this flag with auto-unfold, kept for now so
        # the existing popup behavior doesn't regress in the meantime.
        ws_server.broadcast(done)

def handle_exit_signal(signum, frame):
    cleanup_before_exit()
    sys.exit(0)

signal.signal(signal.SIGTERM, handle_exit_signal)
signal.signal(signal.SIGINT, handle_exit_signal)


idle_timer = None

def start_idle_timer(timeout=15):
    global idle_timer

    if idle_timer:
        idle_timer.cancel()
        idle_timer = None

    def shutdown():
        print("[Spark] 💤 No input received — going idle.")
        write_status("inactive")

    idle_timer = threading.Timer(timeout, shutdown)
    idle_timer.daemon = True
    idle_timer.start()

def cancel_idle_timer():
    global idle_timer
    if idle_timer:
        print("[Spark] 🔄 Cancelling idle shutdown.")
        idle_timer.cancel()
        idle_timer = None

def cleanup_before_exit():
    print("[Spark] 🧼 Cleaning up before shutdown...")
    try:
        mic_control.unmute_microphone()
    except Exception as e:
        print(f"[Spark] ⚠️ Failed to unmute: {e}")
    if _db is not None and _session_id is not None:
        store.end_session(_db, _session_id)
    write_status("inactive")

atexit.register(cleanup_before_exit)

def calibrate_vad_threshold(duration=2.0):
    print("[Spark] 🧪 Calibrating ambient noise...")
    write_status("calibrating")
    audio = sd.rec(int(duration * sample_rate), samplerate=sample_rate, channels=1, dtype='float32')
    sd.wait()
    audio = audio.flatten()

    max_amp = np.max(np.abs(audio))
    avg_amp = np.mean(np.abs(audio))

    print(f"[Spark] [Calib] Max Amp: {max_amp:.6f}, Avg Amp: {avg_amp:.6f}")

    # Auto fallback if user is talking
    if max_amp > 0.2:
        print("[Spark] 🚨 Detected voice/spike during calibration. Using fallback threshold: 0.05")
        return 0.05

    # Hybrid threshold with safety floor
    threshold = max(0.03, avg_amp + (max_amp - avg_amp) * 0.2)
    print(f"[Spark] 🎯 Calibrated VAD threshold: {threshold:.4f}")
    return threshold


print = functools.partial(print, flush=True)

# Kokoro is the primary engine (chosen after a direct side-by-side listening
# comparison against pyttsx3); pyttsx3 stays available as a fallback behind
# the same interface if Kokoro's dependencies aren't installed.
try:
    tts_engine = KokoroTTSEngine()
except Exception as e:
    print(f"[Spark] ⚠️ Kokoro unavailable ({e}), falling back to pyttsx3.")
    tts_engine = Pyttsx3TTSEngine()

# mlx-whisper runs on Apple Silicon's GPU (via Metal), letting us use a much
# bigger, more accurate model than the old CPU-only openai-whisper setup for
# comparable latency. The prompt below biases decoding toward vocabulary that
# general-purpose Whisper models otherwise consistently mis-hear (proper
# nouns and uncommon names aren't well represented in training data).
MLX_MODEL_REPO = "mlx-community/whisper-large-v3-turbo"
VOCAB_PROMPT = "Vivek Kamisetty, Claude, Anthropic, Spark, Whisper."

ws_server.start()
sample_rate = 16000
print("[Spark] Warming up mlx-whisper model...")
mlx_whisper.transcribe(np.zeros(sample_rate, dtype=np.float32), path_or_hf_repo=MLX_MODEL_REPO)
block_duration = 1.0
vad_threshold = calibrate_vad_threshold()
max_silence_time = 1.0
max_recording_time = 10.0  # Hard cap so a noisy room can't keep the recorder open forever

q = queue.Queue()

def audio_callback(indata, frames, time_info, status):
    #print(f"[Audio] callback fired with {len(indata)} frames")
    if status:
        print(f"[Audio] {status}")
    q.put(indata.copy())

def get_device_id_by_name(name_keyword):
    devices = sd.query_devices()
    for i, dev in enumerate(devices):
        if name_keyword.lower() in dev["name"].lower() and dev["max_input_channels"] > 0:
            print(f"[Spark] Using device #{i}: {dev['name']}")
            return i
    print("[Spark] ❌ Mic keyword not found, falling back to default device #0")
    return 0

# Whisper reliably hallucinates these exact filler words on quiet/noisy
# clips with no real speech in them (see the no_speech_prob/temperature
# checks below for the more reliable signal this backs up). Shared at
# module level so mic_listener can skip ever displaying one as a fake user
# turn, not just skip acting on it once it's already in transcript_queue —
# broadcasting it first and filtering later meant a stray "Thank you." could
# wipe the panel's now-latest-exchange-only view of a real conversation
# with nothing, since nothing ever replies to it.
POLITE_PHRASES = {
    "thank you", "thanks", "i'm sorry", "sorry", "ok", "okay", "cool", "yep", "yes", "no", "all right"
}


def mic_listener(transcript_queue):
    try:
        device_id = get_device_id_by_name("MacBook Pro Microphone")
    except ValueError as e:
        print(f"[Error] {e}")
        return

    with sd.InputStream(device=device_id, samplerate=sample_rate, channels=1, callback=audio_callback):
        while True:
            print("[Spark] 🎧 Listening...")
            # Deliberately not broadcasting "listening" here: this loop restarts
            # on every capture attempt, including harmless false-starts on
            # background noise, which happens continuously and independently of
            # whatever main() is actually doing (thinking/speaking). Broadcasting
            # from both places races and stomps on main()'s real state — main()
            # is the sole owner of state broadcasts; "listening" is simply
            # whatever state persists whenever main() isn't busy with a turn.
            audio_data = []
            speech_detected = False
            silence_timer = None
            speech_start_time = None
            last_amplitude_broadcast = 0.0

            while True:
                block = q.get()
                block = block.flatten().astype(np.float32)
                max_amplitude = np.max(np.abs(block))
                #print(f"[DEBUG] Amplitude: {max_amplitude:.6f}")

                # Throttled to ~30Hz so the orb (Phase 3) has smooth live data
                # without flooding the socket on every audio block.
                now = time.time()
                if now - last_amplitude_broadcast >= 1 / 30:
                    bass, mid, high = audio_bands.compute_bands(block, sample_rate)
                    ws_server.broadcast(protocol.amplitude_message("mic", bass, mid, high))
                    last_amplitude_broadcast = now

                if max_amplitude > vad_threshold and mic_control.should_listen:
                    audio_data.append(block)
                    if not speech_detected:
                        speech_start_time = time.time()
                    speech_detected = True
                    silence_timer = None
                elif speech_detected:
                    # Found live: short single-syllable words like "yes"/"no"
                    # (exactly what voice confirmations need) often trail off
                    # in volume fast enough to dip back under vad_threshold
                    # well before the word is actually finished — previously
                    # only over-threshold blocks were appended, so the clip
                    # got truncated to just the loudest fragment (a handful
                    # of ms) instead of the whole word, and then got thrown
                    # out entirely by the "too short to be speech" floor
                    # below. Keep appending through the quieter tail too,
                    # for as long as we're still within the silence grace
                    # period — only actually stop once max_silence_time of
                    # true silence has passed.
                    audio_data.append(block)
                    if silence_timer is None:
                        silence_timer = time.time()
                    elif time.time() - silence_timer > max_silence_time:
                        break

                # Safety cap: finalize the utterance even if trailing noise keeps
                # resetting the silence timer, so we never get stuck recording forever.
                if speech_detected and time.time() - speech_start_time > max_recording_time:
                    print("[Spark] ⏱️ Max recording time reached, finalizing utterance.")
                    break

            if not audio_data:
                print("[Whisper] No significant speech detected.")
                continue

            audio_data = np.concatenate(audio_data, axis=0)

            # Found live: capturing through the full silence grace period
            # above (fixing short words like "yes" getting truncated to
            # nothing) means a quick word is now followed by up to a full
            # second of near-silent tail audio. That padding made the clip
            # mostly silence by volume, which pushed Whisper toward its
            # classic hallucination on quiet clips ("Thank you.") instead of
            # actually transcribing the short word — trim back down to a
            # small buffer after the last genuinely loud moment.
            _ANALYSIS_WINDOW = max(1, int(0.05 * sample_rate))  # ~50ms
            _TRAILING_PAD = int(0.2 * sample_rate)  # keep ~200ms of tail context
            last_loud_end = 0
            for i in range(0, len(audio_data), _ANALYSIS_WINDOW):
                chunk = audio_data[i:i + _ANALYSIS_WINDOW]
                if len(chunk) and np.max(np.abs(chunk)) > vad_threshold:
                    last_loud_end = i + len(chunk)
            if last_loud_end > 0:
                audio_data = audio_data[: min(len(audio_data), last_loud_end + _TRAILING_PAD)]

            max_val = np.max(np.abs(audio_data), axis=0)
            if max_val > 0:
                audio_data = audio_data / max_val
            else:
                print("[Whisper] Skipped silent audio block.")
                continue

            if len(audio_data) < int(0.3 * sample_rate):
                print(f"[Whisper] Skipped clip too short to be speech ({len(audio_data)} samples).")
                continue

            print(f"[Spark] Captured {len(audio_data)} samples, running Whisper...")
            result = mlx_whisper.transcribe(
                audio_data,
                path_or_hf_repo=MLX_MODEL_REPO,
                initial_prompt=VOCAB_PROMPT,
            )
            print(f"[Whisper Result] {result}")

            text = result["text"].strip()
            segments = result.get("segments", [])
            avg_no_speech_prob = (
                sum(s.get("no_speech_prob", 0.0) for s in segments) / len(segments)
                if segments else 1.0
            )
            max_temperature = max((s.get("temperature", 0.0) for s in segments), default=0.0)

            # Whisper hallucinates filler words ("you", ".", "thank you") on quiet/
            # noisy clips. Its own no_speech_prob is a much more reliable signal for
            # this than the transcribed text, so trust it over a non-empty string.
            # A high temperature means Whisper exhausted its confidence fallback
            # ladder and gave up on a clean decode — a sign of garbled audio
            # producing garbage text rather than a real (if quiet) utterance.
            if avg_no_speech_prob > 0.6:
                print(f"[Whisper] Ignored likely hallucination (no_speech_prob={avg_no_speech_prob:.2f}): '{text}'")
            elif max_temperature >= 0.8:
                print(f"[Whisper] Ignored low-confidence garbled transcription (temperature={max_temperature:.1f}): '{text}'")
            elif not confirmation_gate.is_pending() and text.strip().lower().strip(".,!?") in POLITE_PHRASES:
                # Skip displaying this at all, not just skip acting on it —
                # broadcasting it unconditionally and filtering only once it
                # reached transcript_queue (as before) meant a stray "Thank
                # you." still visibly wiped the panel's now-latest-exchange
                # view of a real conversation, with nothing ever replying to
                # it. Only applies when nothing's pending: "yes"/"no" must
                # still get through (and get displayed) to actually resolve a
                # confirmation — see the branch below.
                print(f"[Spark] 🙏 Ignored polite-only phrase (not shown): '{text}'")
            elif text:
                print(f"[User] {text}")
                # mlx-whisper only produces a full utterance after silence
                # is detected, not incremental word-by-word results, so this
                # is always the final transcript (partial=False) — there's
                # no true partial/live ASR in this pipeline to stream yet.
                ws_server.broadcast(protocol.transcript_message("user", text, partial=False))
                # A confirmation (clear-history, or now a tool-call risk gate
                # running on a background thread) is resolved here directly
                # rather than via transcript_queue — main()'s own loop isn't
                # necessarily free to poll it, e.g. while blocked waiting on
                # a tool call, so this has to work regardless of what else is
                # going on.
                if not confirmation_gate.offer_voice_text(text):
                    transcript_queue.put(text)
            else:
                print("[Whisper] No valid text transcribed.")

def _post_speech_cleanup():
    """Shared tail end of speaking, whether it finished naturally or was
    interrupted: drain any audio that leaked in while muted, unmute, and
    resume listening.
    """
    # Small safety buffer for audio hardware drain. Not needed while the
    # engine is actively synthesizing/playing further chunks, only once
    # speech has actually stopped (natural end or interrupt).
    time.sleep(0.5)

    # Discard any audio blocks that leaked in while muted (e.g. TTS bleed)
    # so they aren't mistaken for the next real utterance.
    while not q.empty():
        try:
            q.get_nowait()
        except queue.Empty:
            break

    mic_control.unmute_microphone()
    # mic_listener's own loop only re-broadcasts "listening" once it next
    # breaks out of its capture loop, which can't happen while should_listen
    # was False — so the state would otherwise be stuck showing "speaking"
    # until fresh audio nudges it. Send it here explicitly instead of relying
    # on that side effect.
    write_status("listening")


def speak_reply(text, show_popup=False):
    """Speak a single fixed, already-composed reply (no streaming needed —
    used by the clear-history confirmation flow, not the main Claude-driven
    conversation, which streams sentences as they're generated instead; see
    process_claude_turn below).
    """
    write_status("speaking", text=text, show_popup=show_popup)

    mic_control.mute_microphone()
    tts_engine.speak_stream(iter([text]))
    _post_speech_cleanup()


def incoming_message_watcher(transcript_queue):
    """Watches messages the frontend sends back over the WebSocket:
    - interrupt: tap-to-interrupt (Phase 2) — stop whatever TTS is playing.
    - text_input: typed fallback input — fed into the same queue voice
      transcripts use, so it's processed identically either way, unless a
      confirmation is pending (see confirmation_gate.py), in which case it
      resolves that instead of starting a new turn.
    - confirmation_response: resolves a pending confirmation (a chip click) —
      see confirmation_gate.py. "First response wins" between a chip click
      and a spoken/typed yes-or-no, per docs/SPARK_V2_SPEC.md.
    Runs as its own thread since main()'s loop blocks on
    tts_engine.speak_stream() while TTS plays and can't poll this itself.
    """
    while True:
        msg = ws_server.incoming_queue.get()
        msg_type = msg.get("type")

        if msg_type == "interrupt":
            print("[Spark] 🛑 Interrupt received, stopping TTS.")
            tts_engine.stop()

        elif msg_type == "text_input":
            text = msg.get("text", "").strip()
            if text:
                print(f"[Spark] ⌨️ Typed input: {text}")
                # Broadcast the same transcript message voice input produces,
                # rather than having the frontend render its own typed text
                # optimistically — one rendering path for both input methods
                # instead of two that could drift apart.
                ws_server.broadcast(protocol.transcript_message("user", text, partial=False))
                if not confirmation_gate.offer_voice_text(text):
                    transcript_queue.put(text)

        elif msg_type == "confirmation_response":
            confirmation_gate.offer_response(msg.get("id"), msg.get("choice", ""))


def process_claude_turn(line, chat_history):
    """Handles one full conversational turn: runs Claude in a background
    thread so its streamed sentences can be spoken as they arrive rather than
    waiting for the complete reply, while the calling (main) thread feeds
    those sentences to the TTS engine as they're produced. Returns
    (reply_text, model_used, tools_called, tool_calls_log).
    """
    sentence_queue = queue.Queue()
    DONE = object()
    first_sentence = threading.Event()
    result = {}
    past_detail_marker = False
    chunk_index = -1

    def on_sentence(sentence):
        nonlocal past_detail_marker, chunk_index
        chunk_index += 1

        if not first_sentence.is_set():
            first_sentence.set()
            write_status("speaking")
            mic_control.mute_microphone()

        # The panel always gets the full text (marker stripped) regardless of
        # voice mode — only how much of it also gets queued for TTS depends
        # on VOICE_MODE and whether we're still before the ---DETAIL--- split.
        display_text = sentence
        spoken_text = sentence

        if not past_detail_marker and DETAIL_MARKER in sentence:
            before, _, after = sentence.partition(DETAIL_MARKER)
            display_text = before + after
            spoken_text = before
            past_detail_marker = True
        elif past_detail_marker:
            spoken_text = ""

        if VOICE_MODE == "full":
            spoken_text = display_text
        elif VOICE_MODE == "muted":
            spoken_text = ""

        if display_text.strip():
            ws_server.broadcast(protocol.assistant_chunk_message(display_text, chunk_index))
        if spoken_text.strip():
            # Handed back to callers (e.g. confirmation_gate's speak_fn) that
            # need to know when this specific chunk has actually finished
            # playing, not just that it was handed to the TTS consumer —
            # tts_engine sets this once real playback of this chunk
            # completes (or is skipped/interrupted).
            done_event = threading.Event()
            sentence_queue.put((chunk_index, spoken_text, done_event))
            return done_event
        return None

    def run_claude():
        reply, model_used, tools_called, tool_calls_log = route_gpt_reply(
            line, chat_history, screenshot_enabled=True, on_sentence=on_sentence
        )
        result["reply"] = reply
        result["model_used"] = model_used
        result["tools_called"] = tools_called
        result["tool_calls_log"] = tool_calls_log
        sentence_queue.put(DONE)

    claude_thread = threading.Thread(target=run_claude, daemon=True)
    claude_thread.start()

    def sentence_stream():
        while True:
            item = sentence_queue.get()
            if item is DONE:
                return
            yield item

    tts_engine.speak_stream(sentence_stream())
    # Note: on interrupt, tts_engine.speak_stream() returns early, but we
    # still wait for the underlying Claude generation to finish here so its
    # result can be persisted — main() only touches the database from this
    # one thread (store.py isn't safe to call from multiple threads), so
    # abandoning this join in favor of starting the next turn immediately
    # isn't safe yet. The audible interruption is still immediate; only the
    # backend bookkeeping trails a moment behind. A real fix (actually
    # canceling the in-flight generation) is a natural follow-up, not
    # something to rush alongside everything else in this phase.
    claude_thread.join()

    reply = result["reply"]
    is_multiline = reply.count("\\n") >= 3 or len(reply.splitlines()) >= 3
    done = protocol.assistant_done_message()
    done["show_popup"] = is_multiline
    ws_server.broadcast(done)

    if first_sentence.is_set():
        _post_speech_cleanup()
    else:
        # Nothing was ever spoken (e.g. an immediate error before any
        # sentence streamed) — still need to return to listening.
        write_status("listening")

    return reply, result["model_used"], result["tools_called"], result["tool_calls_log"]


CLEAR_HISTORY_PHRASES = {"clear history", "clear my history", "clear the history"}


def main():
    print("[Spark] Starting up with VAD and hotkeys...")
    mic_control.unmute_microphone()

    global _db, _session_id
    _db = store.init_db()
    _session_id = store.start_session(_db)
    chat_history = store.load_recent_messages(_db, limit=20)

    transcript_queue = queue.Queue()
    threading.Thread(target=mic_listener, args=(transcript_queue,), daemon=True).start()
    threading.Thread(target=incoming_message_watcher, args=(transcript_queue,), daemon=True).start()

    write_status("listening")

    while True:
        if not transcript_queue.empty():
            line = transcript_queue.get().strip()
            normalized_line = line.lower().strip()
            # Whisper reliably appends sentence punctuation ("clear history."),
            # so exact-match phrase checks below compare against this stripped
            # form rather than normalized_line directly — otherwise none of
            # them would ever match real transcribed speech.
            stripped_line = normalized_line.strip(".,!?")

            if stripped_line in CLEAR_HISTORY_PHRASES:
                cancel_idle_timer()
                # request_confirmation blocks this loop until resolved (chip
                # click or spoken/typed yes-no) or it times out — fine here,
                # since mic_listener routes voice input to it directly and
                # doesn't need this loop to be free to poll anything.
                confirmed = confirmation_gate.request_confirmation(
                    "Are you sure you want to clear your conversation history? Say yes to confirm.",
                    risk="low",
                    options=["Yes", "No"],
                    speak_fn=speak_reply,
                )
                if confirmed:
                    store.clear_history(_db)
                    chat_history.clear()
                    print("[Spark] 🧹 Conversation history cleared.")
                    speak_reply("Done — I've cleared your conversation history.")
                else:
                    speak_reply("Okay, I won't clear anything.")
                start_idle_timer(45)
                continue

            # ✅ Ignore empty/noise-only transcriptions (e.g. just punctuation)
            cleaned_words = [w.strip(".,!?") for w in normalized_line.split()]
            cleaned_words = [w for w in cleaned_words if w]
            if not cleaned_words:
                print(f"[Spark] ⏭️ Ignored empty/noise prompt: '{line}'")
                write_status("listening")
                continue

            # ✅ Ignore polite phrases (typed input only reaches here — voice
            # input is already filtered earlier, before display, in mic_listener)
            if stripped_line in POLITE_PHRASES:
                print(f"[Spark] 🙏 Ignored polite-only phrase: '{line}'")
                write_status("listening")
                continue

            # ✅ Process prompt
            print(f"[Spark] [User] {line}")
            chat_history.append({"role": "user", "content": line})
            store.add_message(_db, _session_id, "user", line)

            write_status("thinking")
            cancel_idle_timer()
            reply, model_used, tools_called, tool_calls_log = process_claude_turn(line, chat_history)

            print(f"[Spark] [GPT] {reply}")
            chat_history.append({"role": "assistant", "content": reply})
            store.add_message(_db, _session_id, "assistant", reply)
            # Each actual command/call + its real result, not just which tool
            # names got used — needed to have a real audit trail of what
            # Spark actually did on this machine, not just that it did
            # "something" with execute_shell_command at some point.
            for call in tool_calls_log:
                store.add_message(
                    _db, _session_id, "tool",
                    f"{call['summary']} -> {call['result'][:200]}"
                )

            start_idle_timer(45)

            # Bounds Claude's context window only — the database keeps the
            # full history regardless of what's trimmed from memory here.
            if len(chat_history) > 20:
                chat_history = chat_history[-18:]
        else:
            time.sleep(0.1)


if __name__ == "__main__":
    main()