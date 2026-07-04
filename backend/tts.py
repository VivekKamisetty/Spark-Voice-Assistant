import pyttsx3
import threading

def speak(text):
    # A fresh engine per call avoids a known pyttsx3/macOS issue where the
    # NSSpeechSynthesizer run loop silently stops producing audio after
    # repeated say()/runAndWait() cycles on a long-lived engine instance.
    engine = pyttsx3.init()
    done = threading.Event()

    def on_end(name, completed):
        done.set()

    token = engine.connect('finished-utterance', on_end)
    engine.say(text)
    engine.runAndWait()
    done.wait()
    engine.disconnect(token)
    engine.stop()
    print("[TTS] Done speaking.")
