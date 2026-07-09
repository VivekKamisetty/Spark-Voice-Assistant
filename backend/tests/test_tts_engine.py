import os
import sys
import threading

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


def test_unpack_plain_string_has_no_index_or_event():
    assert TTSEngine._unpack("hello") == (None, "hello", None)


def test_unpack_two_tuple_defaults_event_to_none():
    assert TTSEngine._unpack((3, "hello")) == (3, "hello", None)


def test_unpack_three_tuple_passes_event_through():
    event = threading.Event()
    assert TTSEngine._unpack((3, "hello", event)) == (3, "hello", event)


def test_pyttsx3_done_event_set_when_chunk_skipped_for_empty_text():
    # An empty chunk is skipped via `continue` before ever touching the real
    # engine — the done_event must still fire, or a caller waiting on it
    # (confirmation_gate) would hang forever.
    engine = Pyttsx3TTSEngine()
    done_event = threading.Event()
    engine.speak_stream(iter([(0, "   ", done_event)]))
    assert done_event.is_set()


def test_pyttsx3_done_event_set_when_interrupted_mid_stream():
    # speak_stream() clears _interrupted at the top of every call (a fresh
    # call always starts unblocked), so to exercise the early-return path
    # without ever touching the real pyttsx3 engine, set it partway through
    # iteration instead of before the call. The done_event for the chunk that
    # gets skipped this way must still fire, or a waiter would hang forever.
    engine = Pyttsx3TTSEngine()
    done_event = threading.Event()

    def items():
        yield (0, "   ", None)  # empty text, skipped via `continue`
        engine._interrupted.set()
        yield (1, "hello", done_event)

    result = engine.speak_stream(items())
    assert result is False
    assert done_event.is_set()
