"""Cascade step (c): TRIM with heading rules, not embeddings.

A pricing supplement is mostly boilerplate the readers do not need — risk
factors, tax, ERISA, historical performance. Sending it costs money and buries
the terms. Trimming keeps:

  * the OPENING (cover page) — price to public, fees, CUSIP and estimated value
    usually live there;
  * the key-terms section; the payment-at-maturity / payoff description;
    coupon and autocall terms; observation-date schedules; estimated value and
    fees; dates;
  * the (supplemental) PLAN OF DISTRIBUTION — IN FULL. It names the selling
    agents and distributors, splits the fees and states any fee-based-account
    price. It is never cut short by a keep-class sub-heading inside it, and
    never truncated for length.

and drops risk factors, tax and ERISA boilerplate and the other DROP headings.

Only lines that MATCH a known heading pattern are section boundaries. Key-terms
tables are full of short title-case lines ("Pricing Date:", "CUSIP") that look
like headings and are not, so "looks like a heading" alone never cuts a
section. Text between recognised headings belongs to the heading above it.

``fuller_text`` (used once, when both readers return null on a critical field)
keeps everything except the DROP sections.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

OPENING_CHARS = 8000
MAX_HEADING_LEN = 120

KEEP = "keep"
DROP = "drop"
DISTRIBUTION = "distribution"

_P = lambda *alts: re.compile(r"^(?:\d+\.\s+|[•■▪\-\*]\s*)?(?:" + "|".join(alts) + r")", re.IGNORECASE)

# Order matters: DISTRIBUTION before KEEP (both mention "underwriting"/"fees").
HEADING_RULES: tuple[tuple[str, str, re.Pattern], ...] = (
    (DISTRIBUTION, "plan_of_distribution", _P(
        r"(?:supplemental\s+)?plan\s+of\s+distribution",
        r"supplemental\s+plan\s+of\s+distribution",
        r"underwriting(?:\s*\(conflicts?\s+of\s+interest\))?\s*$",
        r"distribution\s*(?:\(conflicts?\s+of\s+interest\))?\s*$",
        r"supplemental\s+information\s+(?:regarding|relating\s+to)\s+(?:the\s+)?(?:plan\s+of\s+)?distribution",
    )),
    (DROP, "risk_factors", _P(
        r"(?:selected\s+|key\s+|additional\s+|summary\s+)?risk\s+(?:factors|considerations)",
        r"risks?\s+relating\s+to", r"key\s+risks",
    )),
    (DROP, "tax", _P(
        r"(?:material\s+|certain\s+)?(?:u\.\s?s\.\s+|united\s+states\s+)?federal\s+income\s+tax",
        r"(?:material\s+|certain\s+)?(?:u\.\s?s\.\s+)?tax\s+(?:consequences|considerations|treatment|discussion)",
        r"supplemental\s+(?:discussion\s+of\s+)?(?:u\.\s?s\.\s+)?federal\s+income\s+tax",
        r"canadian\s+federal\s+income\s+tax", r"taxation",
    )),
    (DROP, "erisa", _P(
        r"erisa", r"benefit\s+plan\s+investor", r"employee\s+retirement\s+income",
        r"certain\s+erisa", r"plan\s+investor\s+considerations",
    )),
    (DROP, "boilerplate", _P(
        r"validity\s+of\s+the\s+(?:notes|securities)", r"legal\s+matters",
        r"historical\s+(?:information|performance|data|closing)",
        r"where\s+you\s+can\s+find", r"use\s+of\s+proceeds", r"the\s+bank'?s?\s+estimated\s+profit",
        r"information\s+about\s+the\s+(?:underlying|index|reference)",
        r"(?:the\s+)?(?:underlying|reference\s+asset|index)\s+description",
        r"consent\s+to\s+u\.\s?k\.\s+bail-?in", r"additional\s+information\s+about",
    )),
    (KEEP, "estimated_value", _P(
        r"(?:additional\s+information\s+(?:regarding|about|relating\s+to)\s+)?(?:(?:the|our|its|issuer'?s?|bank'?s?)\s+)*estimated\s+value",
        r"the\s+estimated\s+value",
    )),
    (KEEP, "key_terms", _P(
        r"key\s+terms", r"summary\s+of\s+(?:the\s+)?terms", r"terms\s+of\s+the\s+(?:notes|securities)",
        r"final\s+terms", r"indicative\s+terms", r"general\s+terms", r"key\s+information",
        r"summary\s+information", r"the\s+(?:notes|securities)\s*$", r"product\s+terms",
    )),
    (KEEP, "payoff", _P(
        r"payment\s+at\s+maturity", r"payment\s+upon", r"payout", r"redemption\s+amount",
        r"what\s+(?:will|do)\s+(?:i|you)\s+receive", r"how\s+the\s+(?:notes|securities)\s+work",
        r"determining\s+the\s+payment", r"cash\s+settlement\s+amount", r"maturity\s+payment",
    )),
    (KEEP, "coupon_call", _P(
        r"(?:contingent\s+)?(?:coupon|interest)\s+payments?", r"contingent\s+coupon",
        r"coupon\s+(?:barrier|rate)", r"automatic(?:ally)?\s+(?:call|redemption|early)",
        r"early\s+redemption", r"issuer\s+call", r"optional\s+(?:early\s+)?redemption",
        r"call\s+feature", r"redemption\s+at\s+the\s+option",
    )),
    (KEEP, "schedule", _P(
        r"observation\s+(?:dates?|schedule)", r"(?:call\s+|coupon\s+)?valuation\s+dates?",
        r"coupon\s+payment\s+dates?", r"call\s+(?:observation|valuation)\s+dates?",
        r"determination\s+dates?", r"review\s+dates?",
    )),
    (KEEP, "fees", _P(
        r"(?:selling\s+)?commissions?", r"fees\s+and\s+(?:commissions|expenses)",
        r"selling\s+concessions?", r"structuring\s+fees?", r"price\s+to\s+public",
    )),
)


@dataclass
class Section:
    name: str
    klass: str
    start: int
    end: int
    heading: str


@dataclass
class TrimResult:
    text: str
    sections: list[Section]
    full_chars: int
    kept_chars: int
    distribution_spans: list[tuple[int, int]] = field(default_factory=list)

    @property
    def full_tokens_est(self) -> int:
        return estimate_tokens_chars(self.full_chars)

    @property
    def trimmed_tokens_est(self) -> int:
        return estimate_tokens_chars(self.kept_chars)


def estimate_tokens_chars(n_chars: int) -> int:
    """~4 characters per token for English filing prose; the dry-run estimate
    and the recorded trimmed-token count both use this one rule. The provider's
    real prompt_tokens is recorded on every call alongside it."""
    return (n_chars + 3) // 4


def estimate_tokens(text: str) -> int:
    return estimate_tokens_chars(len(text or ""))


def classify_heading(line: str) -> tuple[str, str] | None:
    s = line.strip()
    if not s or len(s) > MAX_HEADING_LEN:
        return None
    if s.endswith(".") and not s.lower().endswith(("inc.", "co.")):
        return None  # a sentence, not a heading
    for klass, name, rx in HEADING_RULES:
        if rx.match(s):
            return klass, name
    return None


def sections_of(text: str) -> list[Section]:
    """Split ``text`` at recognised headings. The span before the first one is
    the 'opening'."""
    marks: list[tuple[int, str, str, str]] = []
    pos = 0
    for line in text.splitlines(keepends=True):
        c = classify_heading(line)
        if c:
            marks.append((pos, c[0], c[1], line.strip()))
        pos += len(line)
    out: list[Section] = []
    first = marks[0][0] if marks else len(text)
    if first > 0:
        out.append(Section("opening", KEEP, 0, first, ""))
    for i, (start, klass, name, heading) in enumerate(marks):
        end = marks[i + 1][0] if i + 1 < len(marks) else len(text)
        out.append(Section(name, klass, start, end, heading))
    # Plan of distribution runs IN FULL: a KEEP-class heading inside it does
    # not end it; only a DROP heading (or the end of the text) does.
    merged: list[Section] = []
    for sec in out:
        if merged and merged[-1].klass == DISTRIBUTION and sec.klass == KEEP and sec.name != "opening":
            merged[-1] = Section(merged[-1].name, DISTRIBUTION, merged[-1].start, sec.end, merged[-1].heading)
            continue
        merged.append(sec)
    return merged


def _assemble(text: str, keep: list[Section]) -> str:
    parts = [text[s.start:s.end].strip("\n") for s in keep]
    return "\n\n[…]\n\n".join(p for p in parts if p.strip())


def trim(text: str, *, opening_chars: int = OPENING_CHARS) -> TrimResult:
    secs = sections_of(text)
    keep: list[Section] = []
    for s in secs:
        if s.klass == DROP:
            continue
        if s.name == "opening":
            s = Section("opening", KEEP, s.start, min(s.end, s.start + opening_chars), "")
        keep.append(s)
    out = _assemble(text, keep)
    return TrimResult(
        text=out, sections=secs, full_chars=len(text), kept_chars=len(out),
        distribution_spans=[(s.start, s.end) for s in secs if s.klass == DISTRIBUTION],
    )


def fuller_text(text: str) -> str:
    """Everything except the DROP sections (the single 'retry with fuller
    text' when both readers return null on a critical field)."""
    return _assemble(text, [s for s in sections_of(text) if s.klass != DROP])


def recall(trimmed_text: str, quotes) -> dict:
    """How much of the needed text trimming keeps: of the non-empty gold
    quotes, how many are found (whitespace-normalised, case-insensitive) in
    the trimmed text."""
    norm = re.sub(r"\s+", " ", trimmed_text or "").lower()
    total = found = 0
    missing = []
    for q in quotes:
        if not q or not str(q).strip():
            continue
        total += 1
        needle = re.sub(r"\s+", " ", str(q)).strip().lower()
        if needle in norm:
            found += 1
        else:
            missing.append(q)
    return {"total": total, "found": found, "recall": (found / total) if total else None,
            "missing": missing}
