
import time
import subprocess
import functools
import threading
import queue
import whisper
import sounddevice as sd
import numpy as np
import warnings

from gpt_client import get_gpt_reply
from tts import speak
from bridge import write_to_bubble

# Silence Whisper FP16 warning
warnings.filterwarnings("ignore", category=UserWarning, module='whisper.transcribe')

print = functools.partial(print, flush=True)

# Whisper model
model = whisper.load_model("base")
sample_rate = 16000
block_duration = 5
q = queue.Queue()

def mute_microphone():
    subprocess.run(['osascript', '-e', 'set volume input volume 0'])

def unmute_microphone():
    subprocess.run(['osascript', '-e', 'set volume input volume 100'])

def audio_callback(indata, frames, time_info, status):
    if status:
        print(f"[Audio] {status}")
    q.put(indata.copy())

def mic_listener(transcript_queue):
    with sd.InputStream(samplerate=sample_rate, channels=1, callback=audio_callback):
        while True:
            audio_block = []
            start_time = time.time()
            while time.time() - start_time < block_duration:
                audio_block.append(q.get())
            audio_data = np.concatenate(audio_block, axis=0).flatten().astype(np.float32)

            max_val = np.max(np.abs(audio_data), axis=0)
            if max_val > 0:
                audio_data = audio_data / max_val
            else:
                print("[Whisper] Skipped silent audio block.")
                continue

            print("[Whisper] Transcribing...")
            result = model.transcribe(audio_data, language="en")
            text = result["text"].strip()
            if text:
                print(f"[User] {text}")
                transcript_queue.put(text)
            else:
                print("[Whisper] No valid input detected.")

def main():
    print("[Spark] Starting up with Python Whisper mic listener...")
    chat_history = []
    transcript_queue = queue.Queue()
    threading.Thread(target=mic_listener, args=(transcript_queue,), daemon=True).start()

    while True:
        if not transcript_queue.empty():
            line = transcript_queue.get().strip()
            if len(line.split()) <= 1:
                print(f"[Spark] Ignored short input: '{line}'")
                continue

            print(f"[Spark] Received: {line}")
            chat_history.append({"role": "user", "content": line})
            reply = get_gpt_reply(line, chat_history)
            print(f"[GPT] {reply}")
            chat_history.append({"role": "assistant", "content": reply})

            mute_microphone()
            speak(reply)
            unmute_microphone()

            print("[Spark] Waiting before listening again...")
            time.sleep(2.5)

            write_to_bubble(reply)

            if len(chat_history) > 20:
                chat_history = chat_history[-18:]
        else:
            time.sleep(0.1)

if __name__ == "__main__":
    main()
