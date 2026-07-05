"""TTSEngine abstraction (Phase 2 of docs/SPARK_V2_SPEC.md).

Two implementations behind the same interface: KokoroTTSEngine (primary,
neural TTS, chosen over pyttsx3 after a direct side-by-side listening
comparison) and Pyttsx3TTSEngine (fallback, the pre-Phase-2 engine). Kept
swappable so a machine without Kokoro's dependencies, or a future engine
change, doesn't require touching the caller.
"""

import threading

import numpy as np
import sounddevice as sd

import protocol
import ws_server


class TTSEngine:
    """speak_stream(text_chunks) synthesizes and plays each chunk in order —
    designed to be fed sentences one at a time as they stream in from Claude,
    so speech starts before the full reply has finished generating. Returns
    True if playback completed all chunks, False if stop() interrupted it
    (checked both between chunks and mid-chunk).
    """

    def speak_stream(self, text_chunks) -> bool:
        raise NotImplementedError

    def stop(self) -> None:
        raise NotImplementedError


class KokoroTTSEngine(TTSEngine):
    SAMPLE_RATE = 24000
    VOICE = "af_heart"

    def __init__(self):
        from kokoro import KPipeline

        self._pipeline = KPipeline(lang_code="a")
        self._interrupted = threading.Event()

    def speak_stream(self, text_chunks) -> bool:
        self._interrupted.clear()
        for text in text_chunks:
            if self._interrupted.is_set():
                return False
            if not text.strip():
                continue
            for result in self._pipeline(text, voice=self.VOICE):
                if self._interrupted.is_set():
                    return False
                if not self._play(result.audio.numpy()):
                    return False
        return True

    def _play(self, audio: np.ndarray) -> bool:
        """Play one synthesized chunk, broadcasting amplitude in ~30Hz blocks
        (matching the mic amplitude throttle) and checking for an interrupt
        between blocks. Returns False if stopped mid-playback.
        """
        block_size = int(self.SAMPLE_RATE / 30)
        done = threading.Event()
        position = 0

        def callback(outdata, frames, time_info, status):
            nonlocal position
            if self._interrupted.is_set():
                outdata[:] = 0
                raise sd.CallbackStop()

            end = position + frames
            chunk = audio[position:end]
            outdata[: len(chunk), 0] = chunk
            if len(chunk) < frames:
                outdata[len(chunk) :, 0] = 0
            position = end

            rms = float(np.sqrt(np.mean(chunk ** 2))) if len(chunk) else 0.0
            ws_server.broadcast(protocol.amplitude_message("tts", rms))

            if position >= len(audio):
                raise sd.CallbackStop()

        stream = sd.OutputStream(
            samplerate=self.SAMPLE_RATE,
            channels=1,
            dtype="float32",
            blocksize=block_size,
            callback=callback,
            finished_callback=done.set,
        )
        with stream:
            done.wait()
        return not self._interrupted.is_set()

    def stop(self) -> None:
        self._interrupted.set()


class Pyttsx3TTSEngine(TTSEngine):
    """Fallback engine — the pre-Phase-2 pyttsx3 setup, adapted to the same
    interface. No TTS amplitude broadcasting: pyttsx3 doesn't expose the
    audio buffer during playback, only an opaque call into the OS's speech
    synthesizer.
    """

    def __init__(self):
        self._interrupted = threading.Event()
        self._current_engine = None

    def speak_stream(self, text_chunks) -> bool:
        import pyttsx3

        self._interrupted.clear()
        for text in text_chunks:
            if self._interrupted.is_set():
                return False
            if not text.strip():
                continue

            # A fresh engine per utterance avoids a known pyttsx3/macOS issue
            # where the NSSpeechSynthesizer run loop silently stops producing
            # audio after repeated say()/runAndWait() cycles on one
            # long-lived engine instance (hit and fixed earlier this project).
            engine = pyttsx3.init()
            self._current_engine = engine
            done = threading.Event()
            engine.connect("finished-utterance", lambda name, completed: done.set())
            engine.say(text)
            engine.runAndWait()
            done.wait()
            self._current_engine = None

            if self._interrupted.is_set():
                return False
        return True

    def stop(self) -> None:
        self._interrupted.set()
        if self._current_engine is not None:
            self._current_engine.stop()
