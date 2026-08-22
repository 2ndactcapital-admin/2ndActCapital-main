"""Offset-preserving HTML → plain text extraction.

WHY NOT lxml / BeautifulSoup / selectolax
──────────────────────────────────────────────────────────────────────────────
The requirement here is TRACEABILITY: a later sprint must be able to point at
the exact span of the stored raw HTML that a term was read from. None of the
tree-building parsers expose character offsets for text nodes — lxml gives
``sourceline`` only, BeautifulSoup and selectolax give nothing. Building on
them would mean discarding positional data the same way the Textract path
currently discards ``Geometry``.

The stdlib ``html.parser.HTMLParser`` does expose ``getpos()`` per callback, so
it is the only option that satisfies the requirement. It is also lenient with
the malformed markup EDGAR filings are full of, and adds no dependency.

WHAT IT PRODUCES
──────────────────────────────────────────────────────────────────────────────
``extract(html)`` returns an ``HtmlExtraction`` with

  * ``text``     — the plain text
  * ``segments`` — one entry per surviving text node:
                   ``(text_start, text_end, raw_start, raw_end)``
                   where ``text_*`` index into ``text`` and ``raw_*`` index into
                   the decoded raw HTML string.

The two spans have different lengths on purpose: character references are
resolved and internal whitespace is collapsed, so the raw span is the authority
for "where this came from" and the text span is the authority for "where this
reads". Synthetic separators inserted between blocks belong to no segment, so a
text offset that falls in a gap has no raw origin — that is correct, not a bug.

DECODING
──────────────────────────────────────────────────────────────────────────────
Offsets are CHARACTER offsets into the decoded string, so the decode must be
reproducible. ``decode_html`` tries UTF-8 and falls back to latin-1 (which never
fails and is 1 byte : 1 character). The encoding used is returned alongside the
extraction so a later sprint can decode the stored bytes identically before
applying the offsets.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from html.parser import HTMLParser

# Tags whose content is markup/styling, never prose.
SKIP_TAGS = frozenset({"script", "style", "head", "title", "meta", "link"})

# Tags that end a line of prose. Anything else joins with a single space.
BLOCK_TAGS = frozenset(
    {
        "p", "div", "br", "tr", "td", "th", "table", "li", "ul", "ol",
        "h1", "h2", "h3", "h4", "h5", "h6", "hr", "section", "article",
        "blockquote", "pre", "caption", "tbody", "thead", "body",
    }
)

_WS_RUN = re.compile(r"\s+")
_BLANK_RUN = re.compile(r"\n{3,}")


@dataclass
class HtmlExtraction:
    """Plain text plus the raw-HTML provenance of every piece of it."""

    text: str
    # (text_start, text_end, raw_start, raw_end)
    segments: list[tuple[int, int, int, int]] = field(default_factory=list)
    encoding: str = "utf-8"

    def raw_span_for(self, text_offset: int) -> tuple[int, int] | None:
        """Return the raw-HTML span containing ``text_offset``, or None.

        None means the offset landed on a synthetic separator between blocks.
        """
        for text_start, text_end, raw_start, raw_end in self.segments:
            if text_start <= text_offset < text_end:
                return (raw_start, raw_end)
        return None

    def to_offset_map(self) -> dict:
        """Serializable provenance map, for storage beside the raw bytes."""
        return {
            "version": 1,
            "encoding": self.encoding,
            "text_length": len(self.text),
            # Column-oriented: four parallel arrays compress far better as JSON
            # than a list of 4-tuples, and this map is stored per filing.
            "text_start": [s[0] for s in self.segments],
            "text_end": [s[1] for s in self.segments],
            "raw_start": [s[2] for s in self.segments],
            "raw_end": [s[3] for s in self.segments],
        }


def decode_html(raw: bytes) -> tuple[str, str]:
    """Decode raw filing bytes reproducibly. Returns ``(text, encoding)``."""
    try:
        return raw.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        return raw.decode("latin-1"), "latin-1"


class _OffsetTextParser(HTMLParser):
    """Collects text nodes with their character spans in the source string."""

    def __init__(self, source: str):
        # convert_charrefs resolves entities for us; it buffers consecutive data
        # runs, which is why the raw END of a run is closed by the NEXT markup
        # event rather than recorded at data time.
        super().__init__(convert_charrefs=True)
        self._source = source
        self._line_starts = self._compute_line_starts(source)
        self._skip_depth = 0
        self._pending: tuple[int, str] | None = None
        self._parts: list[str] = []
        self._length = 0
        self.segments: list[tuple[int, int, int, int]] = []

    @staticmethod
    def _compute_line_starts(source: str) -> list[int]:
        starts = [0]
        for index, char in enumerate(source):
            if char == "\n":
                starts.append(index + 1)
        return starts

    def _abs_pos(self) -> int:
        line, column = self.getpos()
        if line - 1 >= len(self._line_starts):
            return len(self._source)
        return min(self._line_starts[line - 1] + column, len(self._source))

    # ── output assembly ────────────────────────────────────────────────────
    def _append(self, chunk: str) -> None:
        self._parts.append(chunk)
        self._length += len(chunk)

    def _close_pending(self, raw_end: int) -> None:
        if self._pending is None:
            return
        raw_start, data = self._pending
        self._pending = None
        collapsed = _WS_RUN.sub(" ", data).strip()
        if not collapsed:
            return
        # Keep words apart when two text nodes abut with no block boundary.
        if self._parts and not self._parts[-1].endswith((" ", "\n")):
            self._append(" ")
        text_start = self._length
        self._append(collapsed)
        self.segments.append(
            (text_start, self._length, raw_start, max(raw_end, raw_start))
        )

    def _boundary(self, tag: str) -> None:
        self._close_pending(self._abs_pos())
        if tag in BLOCK_TAGS and self._parts and not self._parts[-1].endswith("\n"):
            self._append("\n")

    # ── HTMLParser callbacks ───────────────────────────────────────────────
    def handle_starttag(self, tag, attrs):
        self._boundary(tag)
        if tag in SKIP_TAGS:
            self._skip_depth += 1

    def handle_startendtag(self, tag, attrs):
        self._boundary(tag)

    def handle_endtag(self, tag):
        if tag in SKIP_TAGS and self._skip_depth:
            self._skip_depth -= 1
        self._boundary(tag)

    def handle_data(self, data):
        if self._skip_depth:
            return
        if self._pending is None:
            self._pending = (self._abs_pos(), data)
        else:
            raw_start, existing = self._pending
            self._pending = (raw_start, existing + data)

    def handle_comment(self, data):
        self._close_pending(self._abs_pos())

    def handle_decl(self, decl):
        self._close_pending(self._abs_pos())

    def handle_pi(self, data):
        self._close_pending(self._abs_pos())

    def finish(self) -> tuple[str, list[tuple[int, int, int, int]]]:
        self._close_pending(len(self._source))
        text = "".join(self._parts)
        # Tidy trailing/blank runs WITHOUT moving any recorded offset: only
        # trailing whitespace is stripped, and blank-run collapsing happens
        # before segments are consulted, so do it the safe way — leave interior
        # text untouched and only trim the tail.
        trimmed = text.rstrip()
        limit = len(trimmed)
        return trimmed, [
            (s[0], min(s[1], limit), s[2], s[3])
            for s in self.segments
            if s[0] < limit
        ]


def extract(html: str, encoding: str = "utf-8") -> HtmlExtraction:
    """Extract plain text from ``html``, preserving raw character offsets."""
    parser = _OffsetTextParser(html)
    try:
        parser.feed(html)
        parser.close()
    except Exception:  # noqa: BLE001 — malformed markup yields partial text
        pass
    text, segments = parser.finish()
    return HtmlExtraction(text=text, segments=segments, encoding=encoding)


def extract_bytes(raw: bytes) -> HtmlExtraction:
    """Decode ``raw`` reproducibly and extract text with offsets."""
    html, encoding = decode_html(raw)
    return extract(html, encoding=encoding)


def collapse_blank_runs(text: str) -> str:
    """Cosmetic-only helper. NOT used on stored text — it would move offsets."""
    return _BLANK_RUN.sub("\n\n", text)
