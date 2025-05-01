import pyttsx3
import threading

engine = pyttsx3.init()

def speak(text):
    done = threading.Event()

    def on_end(name, completed):
        print("[TTS] Done speaking.")
        done.set()

    engine.connect('finished-utterance', on_end)
    engine.say(text)
    engine.runAndWait()
    done.wait()