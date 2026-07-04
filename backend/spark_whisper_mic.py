import time
import subprocess
import functools
import threading
import queue
import whisper
import sounddevice as sd
import numpy as np
import warnings
import os
import signal
import sys
import atexit


from claude_client import route_claude_reply as route_gpt_reply
from tts import speak
from bridge import write_status
should_listen = True  # Global flag to pause listening during TTS
import signal

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
        unmute_microphone()
    except Exception as e:
        print(f"[Spark] ⚠️ Failed to unmute: {e}")
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


def run_speak(text):
    speak(text)

warnings.filterwarnings("ignore", category=UserWarning, module='whisper.transcribe')
print = functools.partial(print, flush=True)

model = whisper.load_model("small.en")
sample_rate = 16000
block_duration = 1.0
vad_threshold = calibrate_vad_threshold()
max_silence_time = 1.0
max_recording_time = 10.0  # Hard cap so a noisy room can't keep the recorder open forever

q = queue.Queue()

def mute_microphone():
    subprocess.run(['osascript', '-e', 'set volume input volume 0'])

def unmute_microphone():
    subprocess.run(['osascript', '-e', 'set volume input volume 100'])

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

def mic_listener(transcript_queue):
    try:
        device_id = get_device_id_by_name("MacBook Pro Microphone")
    except ValueError as e:
        print(f"[Error] {e}")
        return

    with sd.InputStream(device=device_id, samplerate=sample_rate, channels=1, callback=audio_callback):
        while True:
            print("[Spark] 🎧 Listening...")
            write_status("listening")
            audio_data = []
            speech_detected = False
            silence_timer = None
            speech_start_time = None

            while True:
                block = q.get()
                block = block.flatten().astype(np.float32)
                max_amplitude = np.max(np.abs(block))
                #print(f"[DEBUG] Amplitude: {max_amplitude:.6f}")

                if max_amplitude > vad_threshold and should_listen:  # Add "and should_listen" here
                    audio_data.append(block)
                    if not speech_detected:
                        speech_start_time = time.time()
                    speech_detected = True
                    silence_timer = None
                elif speech_detected:
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
            result = model.transcribe(audio_data, language="en")
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
            elif text:
                print(f"[User] {text}")
                transcript_queue.put(text)
            else:
                print("[Whisper] No valid text transcribed.")

def main():
    print("[Spark] Starting up with VAD and hotkeys...")
    unmute_microphone()
    chat_history = []
    transcript_queue = queue.Queue()
    threading.Thread(target=mic_listener, args=(transcript_queue,), daemon=True).start()

    write_status("listening")

    while True:
        if not transcript_queue.empty():
            line = transcript_queue.get().strip()
            normalized_line = line.lower().strip()

            # ✅ Ignore empty/noise-only transcriptions (e.g. just punctuation)
            cleaned_words = [w.strip(".,!?") for w in normalized_line.split()]
            cleaned_words = [w for w in cleaned_words if w]
            if not cleaned_words:
                print(f"[Spark] ⏭️ Ignored empty/noise prompt: '{line}'")
                write_status("listening")
                continue

            # ✅ Ignore polite phrases
            polite_phrases = {
                "thank you", "thanks", "i'm sorry", "sorry", "ok", "okay", "cool", "yep", "yes", "no", "all right"
            }

            if normalized_line in polite_phrases:
                print(f"[Spark] 🙏 Ignored polite-only phrase: '{line}'")
                write_status("listening")
                continue

            # ✅ Process prompt
            print(f"[Spark] [User] {line}")
            chat_history.append({"role": "user", "content": line})

            write_status("thinking")
            cancel_idle_timer()
            reply, model_used, tools_called = route_gpt_reply(line, chat_history, screenshot_enabled=True)

            print(f"[Spark] [GPT] {reply}")
            chat_history.append({"role": "assistant", "content": reply})

            is_multiline = reply.count("\\n") >= 3 or len(reply.splitlines()) >= 3
            show_popup = model_used == "gpt-4o" or is_multiline

            write_status("speaking", text=reply, show_popup=show_popup)

            global should_listen
            should_listen = False  # Stop listening during TTS
            mute_microphone()
            speak(reply)
            # Small safety buffer for audio hardware drain after speak() returns.
            # speak() already blocks for the full utterance via runAndWait(), so
            # this isn't scaled by reply length — it only covers residual lag
            # between the TTS engine reporting "done" and the speaker actually
            # finishing output.
            time.sleep(0.5)

            # Discard any audio blocks that leaked in while muted (e.g. TTS bleed)
            # so they aren't mistaken for the next real utterance.
            while not q.empty():
                try:
                    q.get_nowait()
                except queue.Empty:
                    break

            unmute_microphone()
            should_listen = True  # Resume listening
            start_idle_timer(45)

            if len(chat_history) > 20:
                chat_history = chat_history[-18:]
        else:
            time.sleep(0.1)


if __name__ == "__main__":
    main()