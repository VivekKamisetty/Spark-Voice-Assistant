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

import audio_bands
import protocol
import ws_server


class TTSEngine:
    """speak_stream(text_chunks) synthesizes and plays each chunk in order —
    designed to be fed sentences one at a time as they stream in from Claude,
    so speech starts before the full reply has finished generating. Returns
    True if playback completed all chunks, False if stop() interrupted it
    (checked both between chunks and mid-chunk).

    Each item in text_chunks is a plain string (no sentence-index tracking
    needed, e.g. speak_reply's fixed one-shot replies), an (index, text)
    tuple, or an (index, text, done_event) tuple. When an index is given, a
    speech_started broadcast fires right as that chunk starts playing, so the
    frontend can highlight exactly the sentence currently being spoken rather
    than guessing from when it was generated/displayed (which runs well ahead
    of playback). When a done_event (threading.Event) is given, it's set once
    that specific chunk has actually finished playing (or been skipped/
    interrupted) — callers that need to know playback genuinely completed,
    rather than just that it was handed to the TTS consumer, wait on this
    instead of guessing from word count (see confirmation_gate.py, which
    needs this so a fast "yes" can't resolve before the user has actually
    heard the command being read back).
    """

    def speak_stream(self, text_chunks) -> bool:
        raise NotImplementedError

    def stop(self) -> None:
        raise NotImplementedError

    @staticmethod
    def _unpack(item):
        if isinstance(item, tuple):
            if len(item) == 3:
                return item
            index, text = item
            return index, text, None
        return None, item, None


class KokoroTTSEngine(TTSEngine):
    SAMPLE_RATE = 24000
    VOICE = "af_heart"

    def __init__(self):
        from kokoro import KPipeline

        self._pipeline = KPipeline(lang_code="a")
        self._interrupted = threading.Event()

    def speak_stream(self, text_chunks) -> bool:
        self._interrupted.clear()
        for item in text_chunks:
            index, text, done_event = self._unpack(item)
            try:
                if self._interrupted.is_set():
                    return False
                if not text.strip():
                    continue
                if index is not None:
                    ws_server.broadcast(protocol.speech_started_message(index))
                for result in self._pipeline(text, voice=self.VOICE):
                    if self._interrupted.is_set():
                        return False
                    if not self._play(result.audio.numpy()):
                        return False
            finally:
                # Always signal, even on an early return above (interrupted)
                # or a skipped empty chunk — a waiter blocked on this event
                # must never hang just because this chunk didn't actually
                # play.
                if done_event is not None:
                    done_event.set()
        return True

    def _play(self, audio: np.ndarray) -> bool:
        """Play one synthesized chunk, broadcasting band energy in ~30Hz
        blocks (matching the mic throttle) and checking for an interrupt
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

            bass, mid, high = audio_bands.compute_bands(chunk, self.SAMPLE_RATE)
            ws_server.broadcast(protocol.amplitude_message("tts", bass, mid, high))

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
        for item in text_chunks:
            index, text, done_event = self._unpack(item)
            try:
                if self._interrupted.is_set():
                    return False
                if not text.strip():
                    continue
                if index is not None:
                    ws_server.broadcast(protocol.speech_started_message(index))

                # A fresh engine per utterance avoids a known pyttsx3/macOS
                # issue where the NSSpeechSynthesizer run loop silently stops
                # producing audio after repeated say()/runAndWait() cycles on
                # one long-lived engine instance (hit and fixed earlier this
                # project).
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
            finally:
                if done_event is not None:
                    done_event.set()
        return True

    def stop(self) -> None:
        self._interrupted.set()
        if self._current_engine is not None:
            self._current_engine.stop()
