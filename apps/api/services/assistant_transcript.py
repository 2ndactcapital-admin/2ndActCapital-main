"""BUGFIX1 (R2-ASSIST-03) — what of an assistant turn the member may see.

The transcript showed internal narration: the model's "Let me look that up…"
text that accompanies a tool call became the visible message (and every such
intermediate turn was replayed as its own bubble on reload), and each READ
tool's description was listed under the reply as italic "disclosures". None
of that is the answer. The rule, applied server-side:

* only the text of the FINAL model turn (or a READ handler's own formatted
  `text`) is the reply; text that rode along with a tool call is narration;
* narration and tool disclosures stay in the stored conversation record
  (under `_display.internal`), where they were already kept — never in a
  response to the browser;
* any remaining line that is wholly italic (*…* / _…_) or opens with a
  tool-use phrase is removed from the reply.
"""
from __future__ import annotations

import re

_ITALIC_LINE = re.compile(r"^\s*(\*[^*\n]+\*|_[^_\n]+_)\s*$")
_NARRATION_LINE = re.compile(
    r"^\s*(?:\*|_)?\s*(?:let me (?:look|check|search|find|pull|use|query|see|get)\b"
    r"|i(?:'ll| will| am going to|'m going to) (?:look|check|search|find|pull|use|query|call|run)\b"
    r"|(?:using|calling|running|querying) (?:the )?[\w .]*(?:tool|action|function)\b"
    r"|\[(?:tool|system)[^\]]*\])",
    re.IGNORECASE,
)
FALLBACK_REPLY = "I'm unable to process your request right now."


def user_visible_text(text: str | None) -> str:
    """The reply with narration lines removed (empty string if nothing left)."""
    if not text:
        return ""
    kept = [ln for ln in str(text).splitlines()
            if not _ITALIC_LINE.match(ln) and not _NARRATION_LINE.match(ln)]
    return "\n".join(kept).strip()


def is_internal(stored_message: dict) -> bool:
    """A stored assistant message that is a tool-calling turn (narration)."""
    d = stored_message.get("_display")
    if isinstance(d, dict) and d.get("internal"):
        return True
    content = stored_message.get("content")
    return isinstance(content, list) and any(
        isinstance(b, dict) and b.get("type") == "tool_use" for b in content)
