import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import confirmation_gate as cg


def test_exact_phrases_still_resolve():
    assert cg._resolve("yes") is True
    assert cg._resolve("no") is False
    assert cg._resolve("sure") is True
    assert cg._resolve("cancel") is False


def test_natural_phrasing_with_extra_words_resolves():
    # The original bug report: "Okay, go ahead." didn't match anything under
    # the old exact-full-string-match rule, silently leaving the
    # confirmation stuck pending until it timed out.
    assert cg._resolve("Okay, go ahead.") is True
    assert cg._resolve("Yes, please do that.") is True
    assert cg._resolve("No, don't do that.") in (False, None)
    assert cg._resolve("Sure, why not.") is True


def test_apostrophe_phrase_still_matches():
    # Regression check: punctuation stripping must not eat the apostrophe in
    # "don't" itself, or the literal pattern would never match its own
    # normalized form.
    assert cg._resolve("Don't do it.") is None or cg._resolve("Don't do it.") is False
    assert cg._resolve("I don't want that.") is False


def test_unrelated_speech_is_ambiguous_not_forced():
    # Ambient noise / hallucinated filler must never resolve anything --
    # this is the safety property the whole exact/whole-word design exists
    # to preserve even after loosening it to accept natural phrasing.
    assert cg._resolve("Thank you.") is None
    assert cg._resolve("What's the weather like?") is None


def test_ambiguous_when_both_present():
    # Contains "do it" (confirm) and "no"/"don't" (decline) at once -- should
    # not confidently resolve either way.
    assert cg._resolve("no, don't do it") is None


def test_estimate_speaking_seconds_scales_with_length_and_has_a_floor():
    short = cg._estimate_speaking_seconds("Yes")
    long = cg._estimate_speaking_seconds(
        "Should I run this: find /Users/example/Downloads -name '*.tmp' -delete"
    )
    assert short >= 1.0
    assert long > short
