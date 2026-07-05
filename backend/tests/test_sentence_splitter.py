import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from sentence_splitter import split_into_sentences


def deltas(*chunks):
    return list(chunks)


def test_splits_on_sentence_boundaries():
    result = list(split_into_sentences(deltas("Hello there. ", "How are you? ", "I'm fine.")))
    assert result == ["Hello there.", "How are you?", "I'm fine."]


def test_yields_incrementally_as_deltas_arrive():
    # Simulates token-by-token streaming rather than whole-sentence chunks.
    tokens = list("Hi. Bye.")
    result = list(split_into_sentences(tokens))
    assert result == ["Hi.", "Bye."]


def test_no_trailing_period_still_yielded_at_end():
    result = list(split_into_sentences(deltas("This has no ending punctuation")))
    assert result == ["This has no ending punctuation"]


def test_empty_stream_yields_nothing():
    assert list(split_into_sentences(deltas())) == []


def test_whitespace_only_remainder_yields_nothing():
    result = list(split_into_sentences(deltas("Done.", "   ")))
    assert result == ["Done."]


def test_decimal_number_does_not_split():
    # No whitespace after the period in "3.5", so it isn't treated as a
    # sentence boundary.
    result = list(split_into_sentences(deltas("The value is 3.5 exactly.")))
    assert result == ["The value is 3.5 exactly."]
