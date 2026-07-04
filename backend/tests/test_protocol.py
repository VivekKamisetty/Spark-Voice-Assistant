import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

import protocol


def test_state_message_carries_version():
    msg = protocol.state_message("listening")
    assert msg["v"] == 2
    assert msg["type"] == "state"
    assert msg["value"] == "listening"


def test_state_message_rejects_unknown_value():
    with pytest.raises(ValueError):
        protocol.state_message("napping")


def test_amplitude_message_shape():
    msg = protocol.amplitude_message("mic", 0.42)
    assert msg == {"v": 2, "type": "amplitude", "source": "mic", "rms": 0.42}


def test_amplitude_message_rejects_unknown_source():
    with pytest.raises(ValueError):
        protocol.amplitude_message("speaker", 0.1)


def test_transcript_message_defaults_partial_false():
    msg = protocol.transcript_message("user", "hello")
    assert msg["partial"] is False


def test_tool_activity_message_shape():
    msg = protocol.tool_activity_message("search_files", "running", "Searching files...")
    assert msg["name"] == "search_files"
    assert msg["status"] == "running"


def test_confirmation_request_message_shape():
    msg = protocol.confirmation_request_message(
        "abc-123", "Delete everything in Downloads?", "high", ["Yes", "No"]
    )
    assert msg["id"] == "abc-123"
    assert msg["risk"] == "high"
    assert msg["options"] == ["Yes", "No"]


def test_parse_incoming_accepts_known_types():
    assert protocol.parse_incoming({"type": "interrupt"}) == {"type": "interrupt"}
    assert protocol.parse_incoming(
        {"type": "confirmation_response", "id": "abc", "choice": "Yes"}
    ) is not None
    assert protocol.parse_incoming({"type": "text_input", "text": "hi"}) is not None


def test_parse_incoming_ignores_unknown_or_malformed():
    assert protocol.parse_incoming({"type": "something_new"}) is None
    assert protocol.parse_incoming({"type": "confirmation_response", "id": "abc"}) is None
    assert protocol.parse_incoming("not a dict") is None
    assert protocol.parse_incoming({}) is None
