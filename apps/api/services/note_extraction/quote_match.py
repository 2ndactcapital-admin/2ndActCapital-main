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

Tiers, tried in order (the first that finds the quote wins):

  1. exact — the quote verbatim.
  2. normalised — whitespace runs collapsed to one space, quote/dash variants
     folded, case folded.
  3. whitespace-insensitive — the same folding with ALL whitespace removed on
     both sides. Table cells are extracted with spacing the model does not
     reproduce: the filing's ``$ 985.78`` (a currency symbol in its own
     cell) is quoted as ``$985.78``, ``14.50 %`` as ``14.50%``. Removing
     whitespace never changes a single letter or digit, so a paraphrase still
     fails here too.
  4. ellipsis — a quote containing ``...`` or ``…`` is a real passage the
     model ABRIDGED. It matches when every fragment of at least
     ``ELLIPSIS_MIN_FRAGMENT_CHARS`` characters is found (tiers 1-3 per
     fragment), IN ORDER, all within ``ELLIPSIS_WINDOW_CHARS`` of the
     original text; the span runs from the first fragment's start to the last
     fragment's end. Out-of-order fragments, or fragments scattered further
     apart than the window, are rejected — that is stitching, not abridging.
"""
from __future__ import annotations

import bisect
import re

# Too short to be a distinctive anchor below this many NON-WHITESPACE
# characters. Short table values ("$985.78", "14.50%", "ASML UW") are real,
# checkable quotes; a 1-3 character quote ("Yes", "5%") is not.
MIN_QUOTE_CHARS = 4
ELLIPSIS_MIN_FRAGMENT_CHARS = 12
ELLIPSIS_WINDOW_CHARS = 1500

# Curly quotes, primes and en/em dashes -> their straight-ASCII equivalent.
# Each maps exactly one character to one character, so the offset maps in
# TextIndex stay a simple position-for-position correspondence.
_PUNCTUATION_MAP = {
    "‘": "'", "’": "'", "‚": "'", "′": "'",
    "“": '"', "”": '"', "„": '"', "″": '"',
    "–": "-", "—": "-", "−": "-",
}

# "...", ". . .", "…", optionally bracketed "[...]" — the marks a model uses
# when it shortens a passage.
_ELLIPSIS_RE = re.compile(r"\[?\s*(?:…|\.(?:\s*\.){2,})\s*\]?")
_WS_RE = re.compile(r"\s+")


def _fold(ch: str) -> str:
    return _PUNCTUATION_MAP.get(ch, ch).lower()


def normalise(s: str) -> str:
    """Collapse whitespace runs to one space and fold quote/dash variants —
    the same transform ``TextIndex`` applies internally, for callers that
    only need the normalised string, not an offset map."""
    return re.sub(r"\s+", " ", "".join(_fold(ch) for ch in (s or ""))).strip()


def compact(s: str) -> str:
    """``normalise`` with ALL whitespace removed (tier 3)."""
    return _WS_RE.sub("", "".join(_fold(ch) for ch in (s or "")))


def has_ellipsis(s: str | None) -> bool:
    return bool(s) and bool(_ELLIPSIS_RE.search(s))


def ellipsis_fragments(quote: str) -> list[str]:
    """The quote's pieces between ellipses that are long enough to anchor
    (``ELLIPSIS_MIN_FRAGMENT_CHARS``), in order. Shorter pieces ("the", "and
    ...") are dropped: too short to prove anything on their own."""
    return [f.strip() for f in _ELLIPSIS_RE.split(quote)
            if len(f.strip()) >= ELLIPSIS_MIN_FRAGMENT_CHARS]


class TextIndex:
    """Whitespace- and punctuation-normalised views of a text, each with a map
    back to its real character offsets.

    Models are unreliable at reporting character offsets and reliable at
    copying a phrase close to verbatim. So the model returns a quote and this
    class finds it, which makes a found quote's offsets a measured fact
    rather than a hallucinated integer.
    """

    __slots__ = ("text", "_norm", "_map", "_compact", "_cmap")

    def __init__(self, text: str) -> None:
        self.text = text or ""
        chars: list[str] = []
        offsets: list[int] = []
        cchars: list[str] = []
        coffsets: list[int] = []
        prev_space = False
        for i, ch in enumerate(self.text):
            if ch.isspace():
                if prev_space:
                    continue
                chars.append(" ")
                offsets.append(i)
                prev_space = True
            else:
                f = _fold(ch)
                chars.append(f)
                offsets.append(i)
                cchars.append(f)
                coffsets.append(i)
                prev_space = False
        self._norm = "".join(chars)
        self._map = offsets
        self._compact = "".join(cchars)
        self._cmap = coffsets

    # ── single contiguous span, tiers 1-3 ───────────────────────────────────
    def _contiguous(self, quote: str, start_at: int = 0) -> tuple[int, int] | None:
        """Earliest-tier ``(start, end)`` of ``quote`` beginning at or after
        original offset ``start_at``, or None."""
        direct = self.text.find(quote, start_at)
        if direct != -1:
            return direct, direct + len(quote)

        needle = normalise(quote)
        if needle:
            pos = self._norm.find(needle, bisect.bisect_left(self._map, start_at))
            if pos != -1:
                end_idx = pos + len(needle) - 1
                if end_idx < len(self._map):
                    return self._map[pos], self._map[end_idx] + 1

        needle = compact(quote)
        if needle:
            pos = self._compact.find(needle, bisect.bisect_left(self._cmap, start_at))
            if pos != -1:
                return self._cmap[pos], self._cmap[pos + len(needle) - 1] + 1
        return None

    def _compact_occurrences(self, fragment: str):
        """Every original start offset where ``fragment`` occurs (tier 3 —
        it subsumes tiers 1 and 2: any exact or normalised match is also a
        whitespace-insensitive match at the same start)."""
        needle = compact(fragment)
        if not needle:
            return
        pos = self._compact.find(needle)
        while pos != -1:
            yield self._cmap[pos]
            pos = self._compact.find(needle, pos + 1)

    # ── tier 4: an abridged quote ───────────────────────────────────────────
    def _locate_abridged(self, quote: str) -> tuple[int, int] | None:
        frags = ellipsis_fragments(quote)
        if not frags:
            return None
        best: tuple[int, int] | None = None
        for first_start in self._compact_occurrences(frags[0]):
            span = self._contiguous(frags[0], first_start)
            if span is None:
                continue
            start, end = span
            ok = True
            for frag in frags[1:]:
                nxt = self._contiguous(frag, end)
                if nxt is None or nxt[1] - start > ELLIPSIS_WINDOW_CHARS:
                    ok = False
                    break
                end = nxt[1]
            if ok and end - start <= ELLIPSIS_WINDOW_CHARS:
                if best is None or (end - start) < (best[1] - best[0]):
                    best = (start, end)
        return best

    def locate(self, quote: str | None) -> tuple[int, int] | None:
        """Absolute ``(start, end)`` of ``quote`` in the full text, or None."""
        if not quote or not isinstance(quote, str):
            return None
        stripped = quote.strip()
        if len(compact(stripped)) < MIN_QUOTE_CHARS:
            return None
        span = self._contiguous(stripped)
        if span is not None:
            return span
        if has_ellipsis(stripped):
            return self._locate_abridged(stripped)
        return None
