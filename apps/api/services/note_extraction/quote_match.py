"""Quote matching shared by the template-study inventory
(``services.edgar_inventory``, via ``FilingDocument.index``) and note-terms
extraction's hazard-ensemble quote verification (``services.
note_terms_extraction``) — ONE implementation, so a quote accepted by one is
accepted by the other.

A model copies a quote faithfully in MEANING but not always byte-for-byte: it
retypes a curly quote or an em dash as its straight-ASCII equivalent, or lets
a line wrap introduced at text-extraction time split what was one line in the
filing. This finds the quote anyway — matching on normalised FORM, never on
meaning, so a paraphrase is still rejected — and always resolves back to the
exact character offsets of the ORIGINAL (un-normalised) text.
"""
from __future__ import annotations

import re

MIN_QUOTE_CHARS = 8  # too short to be a distinctive anchor below this

# Curly quotes, primes and en/em dashes -> their straight-ASCII equivalent.
# Each maps exactly one character to one character, so the offset map in
# TextIndex stays a simple position-for-position correspondence.
_PUNCTUATION_MAP = {
    "‘": "'", "’": "'", "‚": "'", "′": "'",
    "“": '"', "”": '"', "„": '"', "″": '"',
    "–": "-", "—": "-", "−": "-",
}


def _fold(ch: str) -> str:
    return _PUNCTUATION_MAP.get(ch, ch).lower()


def normalise(s: str) -> str:
    """Collapse whitespace runs to one space and fold quote/dash variants —
    the same transform ``TextIndex`` applies internally, for callers that
    only need the normalised string, not an offset map."""
    return re.sub(r"\s+", " ", "".join(_fold(ch) for ch in (s or ""))).strip()


class TextIndex:
    """Whitespace- and punctuation-normalised view of a text, with a map back
    to its real character offsets.

    Models are unreliable at reporting character offsets and reliable at
    copying a phrase close to verbatim. So the model returns a quote and this
    class finds it, which makes a found quote's offsets a measured fact
    rather than a hallucinated integer.
    """

    __slots__ = ("text", "_norm", "_map")

    def __init__(self, text: str) -> None:
        self.text = text or ""
        chars: list[str] = []
        offsets: list[int] = []
        prev_space = False
        for i, ch in enumerate(self.text):
            if ch.isspace():
                if prev_space:
                    continue
                chars.append(" ")
                offsets.append(i)
                prev_space = True
            else:
                chars.append(_fold(ch))
                offsets.append(i)
                prev_space = False
        self._norm = "".join(chars)
        self._map = offsets

    def locate(self, quote: str | None) -> tuple[int, int] | None:
        """Absolute ``(start, end)`` of ``quote`` in the full text, or None."""
        if not quote or not isinstance(quote, str):
            return None
        stripped = quote.strip()
        if len(stripped) < MIN_QUOTE_CHARS:
            return None

        direct = self.text.find(stripped)
        if direct != -1:
            return direct, direct + len(stripped)

        needle = normalise(stripped)
        if not needle:
            return None
        pos = self._norm.find(needle)
        if pos == -1:
            return None
        end_idx = pos + len(needle) - 1
        if end_idx >= len(self._map):
            return None
        return self._map[pos], self._map[end_idx] + 1
