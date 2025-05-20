import pvporcupine
import sounddevice as sd
import struct
import queue
import threading
import os
import time
from dotenv import load_dotenv
from backend_controller import start_backend, is_backend_running
from bridge import write_status

def wake_word_listener():
    load_dotenv()
    ACCESS_KEY = os.getenv("PICOVOICE_API_KEY")

    print("[WakeWord] 🔁 Starting wake-word loop")

    while True:
        try:
            porcupine = pvporcupine.create(
                access_key=ACCESS_KEY,
                keywords=["hey siri"]
            )

            q = queue.Queue()

            def audio_callback(indata, frames, time, status):
                if status:
                    print(f"[WakeWord] Audio Status: {status}")
                q.put(bytes(indata))

            with sd.RawInputStream(
                samplerate=porcupine.sample_rate,
                blocksize=porcupine.frame_length,
                dtype='int16',
                channels=1,
                callback=audio_callback
            ):
                print("[WakeWord] 👂 Listening for 'Hey Siri'...")

                while True:
                    pcm = q.get()
                    pcm = struct.unpack_from("h" * porcupine.frame_length, pcm)

                    keyword_index = porcupine.process(pcm)
                    if keyword_index >= 0:
                        print("[WakeWord] 🗣️ 'Hey Siri' detected!")
                        write_status("calibrating")
                        start_backend()

                        # Wait for backend to finish
                        while is_backend_running():
                            time.sleep(1)

                        print("[WakeWord] ✅ Backend stopped, resuming wake-word detection.")
                        break  # Exit the inner stream loop and restart Porcupine fresh

        except Exception as e:
            print(f"[WakeWord] ❌ Error in wake listener: {e}")
            time.sleep(2)  # Prevent tight loop in case of mic errors
