"""Postgres ``text`` columns reject an embedded NUL byte outright —
``UntranslatableCharacterError: \\u0000 cannot be converted to text`` — no
matter the encoding. A NUL inside a JSON-encoded value is NOT the same risk
(``json.dumps`` escapes it to the six-character sequence ``\\u0000``, not a
raw byte), but model output, filing text and human notes land in plain text
columns too (labels, quotes, error messages, model-reported names), and any
one of those can carry a real NUL byte straight from a filing or a provider
response. Strip it before anything is written, not after it crashes a run.
"""
from __future__ import annotations

NUL = "\x00"


def strip_nul(s):
    """Drop NUL bytes from a string; anything else (including None) passes
    through unchanged."""
    return s.replace(NUL, "") if isinstance(s, str) else s


def strip_nul_deep(value):
    """Recursively strip NUL bytes from every string inside a JSON-shaped
    value (dict / list / str nested arbitrarily); other types pass through."""
    if isinstance(value, str):
        return strip_nul(value)
    if isinstance(value, dict):
        return {k: strip_nul_deep(v) for k, v in value.items()}
    if isinstance(value, list):
        return [strip_nul_deep(v) for v in value]
    return value
