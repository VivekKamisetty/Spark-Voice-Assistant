import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from tts_engine import TTSEngine, KokoroTTSEngine, Pyttsx3TTSEngine


def test_base_class_speak_stream_not_implemented():
    with pytest.raises(NotImplementedError):
        TTSEngine().speak_stream(iter(["hi"]))


def test_base_class_stop_not_implemented():
    with pytest.raises(NotImplementedError):
        TTSEngine().stop()


def test_kokoro_engine_implements_interface():
    assert issubclass(KokoroTTSEngine, TTSEngine)
    assert KokoroTTSEngine.speak_stream is not TTSEngine.speak_stream
    assert KokoroTTSEngine.stop is not TTSEngine.stop


def test_pyttsx3_engine_implements_interface():
    assert issubclass(Pyttsx3TTSEngine, TTSEngine)
    assert Pyttsx3TTSEngine.speak_stream is not TTSEngine.speak_stream
    assert Pyttsx3TTSEngine.stop is not TTSEngine.stop


def test_pyttsx3_engine_stop_before_any_speech_is_a_noop():
    # stop() with no in-progress engine shouldn't raise, since interrupt
    # could arrive when nothing is currently playing.
    engine = Pyttsx3TTSEngine()
    engine.stop()  # should not raise
