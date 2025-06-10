import time
import subprocess
import functools
import threading
import queue
import whisper
import sounddevice as sd
import numpy as np
import warnings

from gpt_client import route_gpt_reply
from tts import speak
from bridge import write_status
from tts import speak

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
    threshold = avg_amp + (max_amp - avg_amp) * 0.2
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

            while True:
                block = q.get()
                block = block.flatten().astype(np.float32)
                max_amplitude = np.max(np.abs(block))
                #print(f"[DEBUG] Amplitude: {max_amplitude:.6f}")

                if max_amplitude > vad_threshold:
                    audio_data.append(block)
                    speech_detected = True
                    silence_timer = None
                elif speech_detected:
                    if silence_timer is None:
                        silence_timer = time.time()
                    elif time.time() - silence_timer > max_silence_time:
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

            print(f"[Spark] Captured {len(audio_data)} samples, running Whisper...")
            result = model.transcribe(audio_data, language="en")
            print(f"[Whisper Result] {result}")

            text = result["text"].strip()
            if text:
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
            words = line.lower().split()

            # ✅ Ignore very short prompts
            if len(words) <= 2:
                print(f"[Spark] ⏭️ Ignored short prompt: '{line}'")
                write_status("listening")
                continue

            # ✅ Ignore polite phrases
            polite_phrases = {
                "thank you", "thanks", "i'm sorry", "sorry", "ok", "okay", "cool", "yep", "yes", "no", "all right"
            }

            normalized_line = line.lower().strip()
            if normalized_line in polite_phrases:
                print(f"[Spark] 🙏 Ignored polite-only phrase: '{line}'")
                write_status("listening")
                continue

            # ✅ Process prompt
            print(f"[Spark] [User] {line}")
            chat_history.append({"role": "user", "content": line})

            write_status("thinking")
            reply, model_used = route_gpt_reply(line, chat_history, screenshot_enabled=True)

            print(f"[Spark] [GPT] {reply}")
            chat_history.append({"role": "assistant", "content": reply})

            is_multiline = reply.count("\\n") >= 3 or len(reply.splitlines()) >= 3
            show_popup = model_used == "gpt-4o" or is_multiline

            write_status("speaking", text=reply, show_popup=show_popup)

            mute_microphone()
            speak(reply)
            time.sleep(0.75)
            unmute_microphone()

            write_status("listening")

            if len(chat_history) > 20:
                chat_history = chat_history[-18:]
        else:
            time.sleep(0.1)


if __name__ == "__main__":
    main()