"""Splits a stream of text deltas into sentences as soon as each one
completes, instead of waiting for the whole text. Used to start speaking a
reply before Claude has finished generating all of it (Phase 2).

This is a simple heuristic (punctuation + whitespace), not real sentence
tokenization — it will occasionally over-split on abbreviations like "Mr."
followed by a space. That produces slightly choppier pacing in that case,
not incorrect or missing speech, which is an acceptable tradeoff for how
much simpler it keeps this over pulling in a full NLP sentence tokenizer.
"""

SENTENCE_END_CHARS = ".!?"
BOUNDARY_WHITESPACE = " \n\t"


def _find_boundary(buffer: str):
    for i, ch in enumerate(buffer):
        if ch in SENTENCE_END_CHARS and i + 1 < len(buffer) and buffer[i + 1] in BOUNDARY_WHITESPACE:
            return i + 1
    return None


def split_into_sentences(text_deltas):
    """Given an iterable of text deltas (e.g. from a streaming API), yields
    each complete sentence as soon as its boundary is seen, then whatever
    text remains once the iterable is exhausted.
    """
    buffer = ""
    for delta in text_deltas:
        buffer += delta
        while True:
            boundary = _find_boundary(buffer)
            if boundary is None:
                break
            yield buffer[:boundary]
            buffer = buffer[boundary:].lstrip()
    if buffer.strip():
        yield buffer
