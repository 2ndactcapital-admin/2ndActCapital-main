"""XML-safety helpers for BPMN text that originates outside our own code.

Two distinct jobs, both about the boundary where *untrusted text becomes XML*:

1. **Escaping at the point of insertion.** ``escape_xml_attr`` / ``escape_xml_text``
   apply the five standard XML entity escapes (``&amp; &lt; &gt; &quot; &apos;``).
   Any code that ever builds BPMN by string templating MUST route user-supplied or
   AI-derived text through these — never interpolate raw text into an attribute.

2. **Repairing model output.** The NL generator does not template BPMN itself: the
   whole document is authored by the model, which is *told* the member's process
   description and routinely copies fragments of it verbatim into ``name="..."``
   attributes.  That makes the model-output boundary the real point of insertion
   for user text, and models do not reliably escape what they copy — a description
   containing ``&``, ``<`` or ``>`` comes back as raw characters that make the
   document unparseable.  ``sanitize_model_bpmn`` escapes exactly those characters
   where they are illegal, and is a no-op on already-valid XML.

3. **Truncation detection.** ``is_complete_document`` answers "did we receive a
   whole document?" — the check that distinguishes a genuine syntax error from a
   response that simply ran out of output tokens mid-tag.
"""
from __future__ import annotations

import re

# A syntactically valid entity or character reference: &amp; &#10; &#x41; &foo;
_VALID_REF = re.compile(r"&(?:#[0-9]+|#[xX][0-9a-fA-F]+|[A-Za-z_][A-Za-z0-9._\-]*);")

# The root element closing tag, allowing any namespace prefix.
_CLOSING_DEFINITIONS = re.compile(r"</(?:[\w.\-]+:)?definitions\s*>\s*\Z")


# ── escaping at the point of insertion ───────────────────────────────────────
def escape_xml_text(value) -> str:
    """Escape text destined for XML *character data* (``&``, ``<``, ``>``)."""
    if value is None:
        return ""
    return (
        str(value)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def escape_xml_attr(value) -> str:
    """Escape text destined for an XML *attribute value* (all five entities).

    Escapes both quote styles so the result is safe inside ``"..."`` or ``'...'``.
    """
    return escape_xml_text(value).replace('"', "&quot;").replace("'", "&apos;")


# ── repairing model-authored XML ─────────────────────────────────────────────
def _escape_bare_ampersands(text: str) -> str:
    """Escape every ``&`` that does not already begin a valid reference."""
    out: list[str] = []
    i = 0
    for m in _VALID_REF.finditer(text):
        out.append(text[i:m.start()].replace("&", "&amp;"))
        out.append(m.group(0))
        i = m.end()
    out.append(text[i:].replace("&", "&amp;"))
    return "".join(out)


# Constructs whose contents are opaque and must be copied through untouched.
_PASSTHROUGH = (
    ("<!--", "-->"),
    ("<![CDATA[", "]]>"),
    ("<?", "?>"),
    ("<!", ">"),  # DOCTYPE and friends; must be tested after <!-- and <![CDATA[
)


def sanitize_model_bpmn(xml: str | None) -> str | None:
    """Escape characters that are illegal where the model left them raw.

    Walks the document with a small tag/attribute state machine and applies:

      * inside a quoted attribute value — ``<`` → ``&lt;``, ``>`` → ``&gt;``,
        and a bare ``&`` → ``&amp;``;
      * in character data — a bare ``&`` → ``&amp;``.

    An apostrophe inside a double-quoted value (and vice versa) is already legal
    XML and is deliberately left alone; only the *matching* quote closes the
    value, exactly as a conforming parser would read it.

    This is a no-op on well-formed XML — valid documents contain no bare ``&``
    and no raw ``<`` in an attribute value — so it can run unconditionally.
    """
    if not xml:
        return xml

    out: list[str] = []
    text_buf: list[str] = []          # character data pending &-escaping
    i, n = 0, len(xml)
    in_tag = False
    quote: str | None = None          # the quote char currently opening an attr

    def flush_text() -> None:
        if text_buf:
            out.append(_escape_bare_ampersands("".join(text_buf)))
            text_buf.clear()

    while i < n:
        ch = xml[i]

        if not in_tag:
            if ch == "<":
                # Copy comments / CDATA / PIs / DOCTYPE through verbatim.
                for opener, closer in _PASSTHROUGH:
                    if xml.startswith(opener, i):
                        end = xml.find(closer, i + len(opener))
                        end = n if end == -1 else end + len(closer)
                        flush_text()
                        out.append(xml[i:end])
                        i = end
                        break
                else:
                    flush_text()
                    out.append(ch)
                    in_tag = True
                    i += 1
                continue
            text_buf.append(ch)
            i += 1
            continue

        # ── inside a tag ────────────────────────────────────────────────────
        if quote is None:
            if ch in ('"', "'"):
                quote = ch
                out.append(ch)
            elif ch == ">":
                in_tag = False
                out.append(ch)
            else:
                out.append(ch)
            i += 1
            continue

        # ── inside a quoted attribute value ─────────────────────────────────
        if ch == quote:
            quote = None
            out.append(ch)
            i += 1
        elif ch == "<":
            out.append("&lt;")
            i += 1
        elif ch == ">":
            out.append("&gt;")
            i += 1
        elif ch == "&":
            m = _VALID_REF.match(xml, i)
            if m:
                out.append(m.group(0))
                i = m.end()
            else:
                out.append("&amp;")
                i += 1
        else:
            out.append(ch)
            i += 1

    flush_text()
    return "".join(out)


# ── truncation detection ─────────────────────────────────────────────────────
def is_complete_document(xml: str | None) -> bool:
    """True when ``xml`` ends with the ``</...definitions>`` root closing tag.

    A response cut off at the model's output-token ceiling stops mid-element, so
    the root never closes.  Checking for the closing tag separates "the model ran
    out of tokens" from "the model produced malformed XML" — two failures with
    very different fixes that otherwise surface as the same parser message.
    """
    return bool(xml and _CLOSING_DEFINITIONS.search(xml.strip()))
