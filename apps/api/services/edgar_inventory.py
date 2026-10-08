"""The TEMPLATE STUDY inventory pass (edgarcohorts) — inventory, NOT extraction.

Before the extraction schema is settled, list EVERYTHING a small, varied set of
filings actually contains, so fields are added once, from evidence, instead of
one surprise at a time.

INPUT. For each chosen document, its selected SECTIONS (``terms_pages``), kept
wherever in the document they occur — not a straight read from the start that
stops at the first risk-factors heading. The whole document is scanned for
headings; a section is kept if its heading matches a KEEP pattern (cover /
summary / key terms, payment-at-maturity / coupon / call / schedule,
hypothetical payout examples, the estimated value section, and the
(supplemental) plan of distribution including "Conflicts of Interest"
variants) and dropped if it matches a DROP pattern (risk factors, tax, ERISA,
underlying/index description or methodology, historical performance, licence
/ disclaimer text). Text between two recognised headings belongs to the
heading above it, so an estimated-value or plan-of-distribution section that
comes AFTER the risk factors — the common case — is still kept. The opening
(before the first recognised heading) is always kept, capped, since the fee
table usually lives there. The assembled input is capped overall (about
20,000 tokens). Per document, which KEEP sections were actually FOUND and
which made it into the input before the cap (``sections_found`` /
``sections_included``) are both recorded, plus whether the estimated-value
section and the plan of distribution were found at all — the run's report
aggregates this into coverage, and lists the documents missing either.

A heading-only scan still misses issuers (several of the major banks) who
state the estimated value or the distribution economics in a SENTENCE on
the cover page or inside key terms, never under a heading of their own —
sometimes inside a DROP section such as risk factors. So, independent of
headings, any PARAGRAPH anywhere in the document containing one of a fixed
set of phrases (estimated value, underwriting discount, selling concession,
agent's commission, structuring fee, fee-based, advisory account, price to
public, proceeds to issuer, distribution agent) is always included — just
that paragraph, not the rest of whatever section (DROP or KEEP) it sits in.
Coverage is then measured two ways: by HEADING (a KEEP section was found)
and by CONTENT (the included text actually states a figure — a dollar
amount or percentage — near the estimated-value or fee language, not merely
the phrase in isolation); both counts are reported, since a document can
satisfy one without the other.

DOCUMENTS (``select_documents``). From a cohort (normally the template-study
preset): per issuer group, 4-6 FINAL pricing supplements chosen for VARIETY —
greedy over product families found by keyword (autocall, buffer, barrier,
digital / fixed payment, contingent coupon, participation / leverage), then
era — plus 1-2 product supplements, because notes lean on them for definitions.

THE MODEL lists every distinct data element: the label exactly as written, the
value as written, a short EXACT quote, the section, and the existing schema
field it corresponds to — given the field's own DESCRIPTION, not just its key
and label, and told to map only on IDENTICAL meaning, otherwise propose NEW —
(the field list is generated from ``services.note_extraction.schema.
build_field_specs``: registry rows plus the B1 extensions, e.g. cusip,
maturity_date, distribution, so an existing extraction field is never
re-proposed as NEW); and flags labels that are misleading about their meaning.
A model-claimed mapping to an existing field is only accepted if the item's
label shares real vocabulary with that field's own key/label — a mapping with
no shared meaning at all (e.g. "Issue Date" -> initial_valuation_date,
"Denominations" -> notional_currency) is rejected back to a proposal instead
(``plausible_mapping`` — a cheap safety net, not a semantic verifier). Per
document it also reports the product family as described, the program /
product supplement cited, whether a hypothetical payout table is present, and
the issue size.

EVERY QUOTE IS CHECKED against the filing's text (``FilingDocument.index``,
``services.note_extraction.quote_match.TextIndex`` — the SAME helper
``services.note_terms_extraction``'s hazard-ensemble quote verification uses,
whitespace- and punctuation-normalised so a line-wrapped or curly-quoted
re-typing of a quote is still found; whitespace-insensitive, so a table value
quoted as "$985.78" is found in extracted text reading "$ 985.78"; and an
abridged quote ("... ") is found when its fragments all occur in order within
a short window — while a paraphrase is still rejected). The prompt asks for
exact, contiguous, short quotes with no ellipsis in the first place. An item
whose quote is not found is REJECTED — stored WHOLE on the document row with
the reason, and counted — never as an item. ``rematch_run`` re-checks a stored
run's rejected items against the stored filing text with the current matcher
and promotes those that now match, with no model call.

CONCEPTS (``aggregate``). Items are grouped by their EFFECTIVE KEY (the mapped
existing field, else the proposed new field, else a slug of the label) — never
by raw label, which fragments into one concept per issuer the moment two
issuers word the same field differently. The model-assisted step then merges
keys that mean the same thing (issuer / issuer_name, cusip / cusip_isin, a
listing/registration-number variant) over the list of DISTINCT keys (with a
few sample labels and values each) — not the 1,000+ raw labels, which is what
let the grouping response blow past its own output-token budget and come back
unparseable before. The keys go to the model in chunks (``GROUPING_CHUNK_KEYS``,
each call with the run's own ``max_tokens``), merged across chunks afterwards;
a chunk whose answer is unusable falls back to one concept per key for that
chunk only, and each chunk's outcome is recorded (``report.grouping_chunks``).
``regroup_run`` reruns only this step on a stored run. If the model step
cannot run at all, grouping falls back to one concept per key
(``grouping_method`` 'field_key')
— and the reason is recorded on the run (``stop_reason`` /
``report.grouping_note``) and in the written report, never silently dropped
even when the main document loop already has its own stop reason to report
(the two are concatenated, never one clobbering the other). Every item keeps
its original label and quote; a concept records its synonyms, the issuers
using it, frequency, example values, the mapped or proposed field, and every
misleading-label flag. ``label_dictionary`` gives issuer -> label -> concept
for rules.

THE MODEL is chosen at run time (``choose_model``) from
``platform_model_catalog`` rows that are 'available', NOT Claude, not an
embedding provider and not an OpenRouter route, and that the LiteLLM proxy
actually serves under that exact name. None -> BLOCKED. Every call goes through
``services.note_extraction.proxy`` — the one HTTP chokepoint (no fallbacks,
provider-reported model recorded); never a provider directly. Every call also
writes a row to the central ``public.ai_decision_log`` (``log_decision``;
task_type 'edgar_inventory' / 'edgar_inventory_grouping'), like every other
AI call in the app.

COST. A hard spending cap (``SpendTracker``: every call reserves its estimate
first and is never made if it would cross the cap; the run then stops cleanly
as ``stopped_spend_cap``). ``dry_run`` plans documents, section selection and
an estimated cost and makes ZERO model calls.
"""
from __future__ import annotations

import asyncio
import json
import re
import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal

from services.database import platform_scope
from services.edgar_cohorts import era_of
from services.note_extraction import proxy, schema
from services.note_extraction.sanitize import strip_nul, strip_nul_deep
from services.note_extraction.spend import (
    SpendCapReached, SpendTracker, estimate_call_cost, priced_cost,
)
from services.note_extraction.trim import estimate_tokens_chars

PROMPT_VERSION = "edgarcohorts.inventory.v3"
GROUPING_PROMPT_VERSION = "edgarcohorts.grouping.v3"
NEW = "NEW"
MAX_TERMS_CHARS = 80_000           # ~20,000 tokens
OPENING_CHARS = 8_000              # cap on the opening/cover section specifically
DEFAULT_MAX_TOKENS = 8000          # per call, inventory AND grouping (the run's --max-tokens)
GROUPING_CHUNK_KEYS = 100          # distinct field keys per grouping call
QUOTE_NOT_FOUND = "quote not found in the filing"
# ai_decision_log.task_type for this module's two call kinds.
TASK_TYPE = {"inventory": "edgar_inventory", "grouping": "edgar_inventory_grouping"}
PER_ISSUER_MIN, PER_ISSUER_MAX = 4, 6
PRODUCT_SUPPLEMENTS_PER_ISSUER = 2
FETCHED_PRICING = ("ready_for_extraction", "prefilter_skipped")
EMBEDDING_PROVIDERS = frozenset({"voyage"})
MAX_CONSECUTIVE_FAILURES = 5
DEFAULT_CONCURRENCY = 4
MAX_HEADING_LEN = 120

# Calls made by this module (dry runs assert this does not move).
CALLS = {"inventory": 0, "grouping": 0}


class InventoryBlocked(RuntimeError):
    """No eligible model: nothing can run."""


# ═══ Section selection: KEEP/DROP by heading, wherever it occurs ═══════════
# Unlike a "read to the first risk-factors heading" pass, this scans the
# WHOLE document: every recognised heading is classified KEEP or DROP, and a
# section runs from its heading to the next recognised heading regardless of
# where either falls. An estimated-value or plan-of-distribution section that
# the issuer places AFTER the risk factors — the common case that caused the
# first real run's estimated-value coverage to be 3 of 22 documents — is kept
# because it is found by scanning onward, not because of a special case.
KEEP = "keep"
DROP = "drop"

_H = lambda *alts: re.compile(r"^(?:\d+\.\s+|[•■▪\-\*]\s*)?(?:" + "|".join(alts) + r")", re.IGNORECASE)

SECTION_RULES: tuple[tuple[str, str, re.Pattern], ...] = (
    # ── DROP: never needed for the inventory, no matter where they fall ────
    (DROP, "risk_factors", _H(r"(?:selected\s+|key\s+|additional\s+|summary\s+)?risk\s+(?:factors|considerations)",
                              r"risks?\s+relating\s+to", r"key\s+risks")),
    (DROP, "tax", _H(r"(?:material\s+|certain\s+)?(?:u\.\s?s\.\s+|united\s+states\s+)?federal\s+income\s+tax",
                     r"(?:material\s+|certain\s+)?(?:u\.\s?s\.\s+)?tax\s+(?:consequences|considerations|treatment|discussion)",
                     r"supplemental\s+(?:discussion\s+of\s+)?(?:u\.\s?s\.\s+)?federal\s+income\s+tax",
                     r"canadian\s+federal\s+income\s+tax", r"taxation")),
    (DROP, "erisa", _H(r"erisa", r"benefit\s+plan\s+investor", r"employee\s+retirement\s+income",
                       r"certain\s+erisa", r"plan\s+investor\s+considerations")),
    (DROP, "underlying_methodology", _H(
        r"(?:the\s+)?(?:index|indices|underlying)\s+(?:methodology|description|information)",
        r"description\s+of\s+the\s+(?:index|indices|underlyings?|reference\s+assets?)",
        r"information\s+(?:about|regarding|relating\s+to)\s+the\s+(?:index|indices|underlyings?|reference)",
        r"(?:the\s+)?underlying\s+(?:index|indices)\s*$")),
    (DROP, "historical_performance", _H(r"historical\s+(?:information|performance|data|closing)")),
    (DROP, "license", _H(r"licens(?:e|ing)(?:\s+agreements?)?\b", r"(?:index\s+)?disclaimers?\s*$",
                         r"trademarks?\b")),
    # ── KEEP: the sections the inventory actually needs ─────────────────────
    (KEEP, "plan_of_distribution", _H(
        r"(?:supplemental\s+)?plan\s+of\s+distribution(?:\s*;\s*conflicts?\s+of\s+interest)?",
        r"supplemental\s+plan\s+of\s+distribution",
        r"underwriting(?:\s*\(conflicts?\s+of\s+interest\))?\s*$",
        r"distribution\s*(?:\(conflicts?\s+of\s+interest\))?\s*$",
        r"supplemental\s+information\s+(?:regarding|relating\s+to)\s+(?:the\s+)?(?:plan\s+of\s+)?distribution",
        r"conflicts?\s+of\s+interest\s*$")),
    (KEEP, "estimated_value", _H(
        r"(?:additional\s+information\s+(?:regarding|about|relating\s+to)\s+)?(?:(?:the|our|its|issuer'?s?|bank'?s?|initial)\s+)*estimated\s+(?:initial\s+)?value",
        r"the\s+estimated\s+(?:initial\s+)?value", r"initial\s+estimated\s+(?:initial\s+)?value", r"valuation\s+of\s+the\s+notes")),
    (KEEP, "key_terms", _H(
        r"key\s+terms", r"summary\s+of\s+(?:the\s+)?terms", r"terms\s+of\s+the\s+(?:notes|securities)",
        r"final\s+terms", r"indicative\s+terms", r"general\s+terms", r"key\s+information",
        r"summary\s+information", r"the\s+(?:notes|securities)\s*$", r"product\s+terms")),
    (KEEP, "payoff_terms", _H(
        r"payment\s+at\s+maturity", r"payment\s+upon", r"payout", r"redemption\s+amount",
        r"what\s+(?:will|do)\s+(?:i|you)\s+receive", r"how\s+the\s+(?:notes|securities)\s+work",
        r"determining\s+the\s+payment", r"cash\s+settlement\s+amount", r"maturity\s+payment")),
    (KEEP, "coupon_call", _H(
        r"(?:contingent\s+)?(?:coupon|interest)\s+payments?", r"contingent\s+coupon",
        r"coupon\s+(?:barrier|rate)", r"automatic(?:ally)?\s+(?:call|redemption|early)",
        r"early\s+redemption", r"issuer\s+call", r"optional\s+(?:early\s+)?redemption",
        r"call\s+feature", r"redemption\s+at\s+the\s+option")),
    (KEEP, "schedule", _H(
        r"observation\s+(?:dates?|schedule)", r"(?:call\s+|coupon\s+)?valuation\s+dates?",
        r"coupon\s+payment\s+dates?", r"call\s+(?:observation|valuation)\s+dates?",
        r"determination\s+dates?", r"review\s+dates?")),
    (KEEP, "fees", _H(
        r"(?:selling\s+)?commissions?", r"fees\s+and\s+(?:commissions|expenses)",
        r"selling\s+concessions?", r"structuring\s+fees?", r"price\s+to\s+public",
        r"proceeds\s+to\s+(?:issuer|us)")),
    (KEEP, "hypothetical_examples", _H(
        r"hypothetical\s+(?:examples?|payments?|payouts?|returns?|amounts?|payment\s+at\s+maturity)",
        r"(?:examples?|illustrations?)\s+of\s+(?:hypothetical\s+)?(?:payments?|payouts?|amounts?|returns?|calculations?)",
        r"what\s+is\s+the\s+total\s+return", r"scenario\s+analysis")),
)
# Names of KEEP sections, for coverage reporting (excludes "opening").
KEEP_SECTION_NAMES = tuple(name for klass, name, _ in SECTION_RULES if klass == KEEP)


# ═══ Paragraph-level inclusion, independent of headings ════════════════════
# Several issuers (the major banks) state the estimated value or the
# distribution economics in a single SENTENCE on the cover page or in key
# terms — never under a heading of their own — and sometimes inside a DROP
# section (most often risk factors). These phrases force that one paragraph
# into the input no matter which section (KEEP or DROP) it falls in; the
# rest of a DROP section around it is still dropped.
ALWAYS_INCLUDE: tuple[tuple[str, re.Pattern], ...] = (
    ("estimated_value", re.compile(r"estimated\s+(?:initial\s+)?value", re.IGNORECASE)),
    ("plan_of_distribution", re.compile(
        r"underwriting\s+discounts?|selling\s+concessions?|agent'?s?\s+commissions?|"
        r"structuring\s+fees?|fee[\s-]based|advisory\s+accounts?|price\s+to\s+public|"
        r"proceeds\s+to\s+issuer|distribution\s+agents?", re.IGNORECASE)),
)


def _paragraphs(text: str, start: int, end: int) -> list[tuple[int, int]]:
    """(abs_start, abs_end) for each "paragraph" in ``text[start:end]``,
    offsets absolute into the full document. ``services.html_text``'s
    extractor emits exactly ONE newline per block element (``<p>``, ``<li>``,
    ``<td>``, ...) — never a blank line between them — so a LINE is the
    paragraph unit here, the same granularity ``classify_heading`` already
    uses for headings; a blank-line split would see the whole section as one
    paragraph and never isolate anything inside it."""
    spans: list[tuple[int, int]] = []
    pos = start
    for line in text[start:end].split("\n"):
        line_end = pos + len(line)
        if line.strip():
            spans.append((pos, line_end))
        pos = line_end + 1
    return spans


def _always_include_tag(paragraph: str) -> str | None:
    for tag, rx in ALWAYS_INCLUDE:
        if rx.search(paragraph):
            return tag
    return None


# ═══ Content-based coverage: a figure, not just the phrase in isolation ════
_FIGURE_RE = re.compile(r"\$\s?\d|\d+(?:\.\d+)?\s*%")
_AGENT_NAMED_RE = re.compile(
    r"\b(?:distribution\s+agents?|placement\s+agents?|as\s+agent|acting\s+as\s+agent)\b", re.IGNORECASE)
_COMMISSION_DISCOUNT_RE = re.compile(
    r"underwriting\s+discounts?|selling\s+concessions?|agent'?s?\s+commissions?|structuring\s+fees?",
    re.IGNORECASE)


def _figure_near(text: str, pos: int, window: int = 200) -> bool:
    lo, hi = max(0, pos - window), min(len(text), pos + window)
    return bool(_FIGURE_RE.search(text[lo:hi]))


def _estimated_value_content_found(text: str) -> bool:
    """True when the included text states the estimated value WITH a figure
    (dollar amount or percentage) nearby — not merely the phrase on its own,
    which could be a heading or a cross-reference with no number attached."""
    return any(_figure_near(text, m.start()) for m in re.finditer(r"estimated\s+(?:initial\s+)?value", text, re.IGNORECASE))


def _plan_of_distribution_content_found(text: str) -> bool:
    """True when the included text names a distribution/placement agent, or
    states a commission/discount/concession WITH a figure nearby."""
    if _AGENT_NAMED_RE.search(text):
        return True
    return any(_figure_near(text, m.start()) for m in _COMMISSION_DISCOUNT_RE.finditer(text))


@dataclass
class Section:
    name: str
    klass: str
    start: int
    end: int
    heading: str


def classify_heading(line: str) -> tuple[str, str] | None:
    s = line.strip()
    if not s or len(s) > MAX_HEADING_LEN:
        return None
    if s.endswith(".") and not s.lower().endswith(("inc.", "co.")):
        return None  # a sentence, not a heading
    for klass, name, rx in SECTION_RULES:
        if rx.match(s):
            return klass, name
    return None


def sections_of(text: str) -> list[Section]:
    """Split ``text`` at EVERY recognised heading, wherever it falls. The span
    before the first one is the always-kept 'opening'. A section runs to the
    next recognised heading (or the end of the text) regardless of its own or
    the next heading's class — text between two headings belongs to the
    heading above it, so an unrecognised sub-heading never creates a stray
    boundary."""
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
    return out


@dataclass
class TermsPages:
    text: str
    chars: int
    tokens_est: int
    full_chars: int
    sections: list[dict]                   # every recognised heading: name, klass, heading
    sections_found: list[str]              # KEEP sections present anywhere in the document
    sections_included: list[str]           # KEEP sections that made it into `text` before the cap
    estimated_value_found: bool
    plan_of_distribution_found: bool
    truncated: bool
    # Content-based detail, split out from the two fields above (which are
    # the OR of heading- and content-based, so existing callers that only
    # ever cared "was it found at all" see no change).
    always_included_tags: list[str] = field(default_factory=list)
    estimated_value_found_heading: bool = False
    estimated_value_found_content: bool = False
    plan_of_distribution_found_heading: bool = False
    plan_of_distribution_found_content: bool = False


def _candidate_segments(text: str, secs: list[Section], opening_chars: int
                        ) -> list[tuple[str, str | None, str | None]]:
    """Ordered (segment_text, keep_section_name_or_None, always_include_tag_or_None)
    candidates, in document order: a KEEP section contributes itself whole;
    a DROP section contributes only the paragraphs matching an
    ALWAYS_INCLUDE phrase — the rest of that section is dropped."""
    out: list[tuple[str, str | None, str | None]] = []
    for s in secs:
        if s.klass == KEEP:
            end = min(s.end, s.start + opening_chars) if s.name == "opening" else s.end
            out.append((text[s.start:end].strip("\n"), None if s.name == "opening" else s.name, None))
        else:
            for p_start, p_end in _paragraphs(text, s.start, s.end):
                tag = _always_include_tag(text[p_start:p_end])
                if tag:
                    out.append((text[p_start:p_end].strip("\n"), None, tag))
    return out


def terms_pages(text: str, *, max_chars: int = MAX_TERMS_CHARS, opening_chars: int = OPENING_CHARS) -> TermsPages:
    """Assemble the model's input from every KEEP section, in document order,
    wherever each falls, PLUS any individual paragraph inside an otherwise
    excluded section that states estimated-value or distribution-economics
    language (``ALWAYS_INCLUDE``) — capped overall at ``max_chars`` (about
    20,000 tokens). The opening (cover page) is always included, capped
    separately at ``opening_chars``."""
    secs = sections_of(text)
    found = sorted({s.name for s in secs if s.klass == KEEP and s.name != "opening"})
    kept_chars = 0
    out_parts: list[str] = []
    included: list[str] = []
    always_tags: set[str] = set()
    truncated = False
    for seg, section_name, tag in _candidate_segments(text, secs, opening_chars):
        if not seg.strip():
            continue
        remaining = max_chars - kept_chars
        if remaining <= 0:
            truncated = True
            break
        if len(seg) > remaining:
            seg = seg[:remaining]
            truncated = True
        out_parts.append(seg)
        kept_chars += len(seg)
        if section_name:
            included.append(section_name)
        if tag:
            always_tags.add(tag)
    kept_text = "\n\n[…]\n\n".join(out_parts)
    ev_heading = "estimated_value" in found
    pod_heading = "plan_of_distribution" in found
    ev_content = _estimated_value_content_found(kept_text)
    pod_content = _plan_of_distribution_content_found(kept_text)
    return TermsPages(
        text=kept_text, chars=len(kept_text), tokens_est=estimate_tokens_chars(len(kept_text)),
        full_chars=len(text),
        sections=[{"name": s.name, "klass": s.klass, "heading": s.heading} for s in secs],
        sections_found=found, sections_included=sorted(set(included)),
        estimated_value_found=ev_heading or ev_content,
        plan_of_distribution_found=pod_heading or pod_content,
        truncated=truncated,
        always_included_tags=sorted(always_tags),
        estimated_value_found_heading=ev_heading, estimated_value_found_content=ev_content,
        plan_of_distribution_found_heading=pod_heading, plan_of_distribution_found_content=pod_content,
    )


# ═══ Variety: product families by keyword ══════════════════════════════════
FAMILY_PATTERNS: dict[str, re.Pattern] = {
    "autocall": re.compile(r"automatic(?:ally)?\s+(?:call|redeem|redemption|early)|auto-?call", re.I),
    "buffer": re.compile(r"\bbuffer", re.I),
    "barrier": re.compile(r"\bbarrier|knock-?in|trigger\s+(?:level|value|price)|downside\s+threshold", re.I),
    "digital_fixed_payment": re.compile(r"\bdigital\b|fixed\s+(?:payment|return)|contingent\s+(?:fixed\s+)?return", re.I),
    "contingent_coupon": re.compile(r"contingent\s+(?:coupon|interest|payment)", re.I),
    "participation_leverage": re.compile(r"participation\s+rate|leverage(?:d)?\s+(?:factor|return)|upside\s+(?:participation|leverage)", re.I),
}


def families_of(text: str) -> list[str]:
    return [k for k, rx in FAMILY_PATTERNS.items() if rx.search(text or "")]


@dataclass
class ChosenDocument:
    reference_filing_id: str
    accession_number: str
    issuer_group: str
    document_kind: str
    filing_date: object
    families: list[str]
    reason: str
    doc: object = None            # FilingDocument
    terms: TermsPages | None = None


async def _cohort_documents(conn, cohort_id) -> list[dict]:
    async with platform_scope(conn):
        rows = await conn.fetch(
            """SELECT m.position, f.accession_number, f.reference_filing_id, f.document_kind,
                      f.pipeline_status, f.filing_date,
                      COALESCE(i.issuer_group, '(no listed issuer)') AS issuer_group
               FROM portfolio.edgar_cohort_members m
               JOIN portfolio.edgar_index_filings f ON f.accession_number = m.accession_number
               LEFT JOIN portfolio.structured_note_issuers i ON i.filer_cik = f.primary_issuer_cik
               WHERE m.cohort_id = $1 AND f.reference_filing_id IS NOT NULL
                 AND ((f.document_kind = 'pricing_supplement' AND f.pipeline_status = ANY($2::text[]))
                      OR f.document_kind = 'product_supplement')
               ORDER BY m.position""",
            cohort_id, list(FETCHED_PRICING))
    return [dict(r) for r in rows]


def choose_for_variety(cands: list[ChosenDocument], *, lo: int = PER_ISSUER_MIN,
                       hi: int = PER_ISSUER_MAX) -> list[ChosenDocument]:
    """Greedy, deterministic: each pick adds the most not-yet-covered product
    families, then a not-yet-covered era, then cohort order. Stops at ``hi``,
    or once ``lo`` are chosen and no remaining candidate adds anything new."""
    chosen: list[ChosenDocument] = []
    fam, eras = set(), set()
    rest = list(cands)
    while rest and len(chosen) < hi:
        def gain(c):
            return (len(set(c.families) - fam), int(era_of(c.filing_date) not in eras))
        best = max(rest, key=lambda c: (gain(c), -rest.index(c)))
        g = gain(best)
        if len(chosen) >= lo and g == (0, 0):
            break
        new_f = sorted(set(best.families) - fam)
        best.reason = (f"adds families {new_f}" if new_f else
                       f"adds era {era_of(best.filing_date)}" if g[1] else "fills the per-issuer minimum")
        chosen.append(best)
        fam |= set(best.families)
        eras.add(era_of(best.filing_date))
        rest.remove(best)
    return chosen


async def select_documents(conn, cohort_id, *, loader, lo: int = PER_ISSUER_MIN, hi: int = PER_ISSUER_MAX,
                           product_supplements: int = PRODUCT_SUPPLEMENTS_PER_ISSUER,
                           progress=None) -> tuple[list[ChosenDocument], list[dict]]:
    """``loader(conn, reference_filing_id) -> FilingDocument``. Returns
    (chosen documents, documents that could not be loaded)."""
    rows = await _cohort_documents(conn, cohort_id)
    by_issuer: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_issuer[r["issuer_group"]].append(r)
    chosen, unloadable = [], []
    for issuer in sorted(by_issuer):
        pricing, product = [], []
        for r in by_issuer[issuer]:
            if r["document_kind"] == "product_supplement" and len(product) >= product_supplements:
                continue
            try:
                doc = await loader(conn, r["reference_filing_id"])
            except Exception as exc:  # noqa: BLE001 — recorded, never fatal
                unloadable.append({"accession_number": r["accession_number"], "error": str(exc)[:200]})
                continue
            tp = terms_pages(doc.text)
            c = ChosenDocument(str(r["reference_filing_id"]), r["accession_number"], issuer,
                               r["document_kind"], r["filing_date"], families_of(tp.text), "", doc, tp)
            if r["document_kind"] == "product_supplement":
                c.reason = "product supplement (definitions)"
                product.append(c)
            else:
                pricing.append(c)
        picked = choose_for_variety(pricing, lo=lo, hi=hi)
        chosen += picked + product
        if progress:
            progress(issuer, f"{len(picked)} pricing supplements, {len(product)} product supplements")
    return chosen, unloadable


# ═══ Model choice ══════════════════════════════════════════════════════════
def _is_claude(*values) -> bool:
    return any(v and ("claude" in v.lower() or "anthropic" in v.lower()) for v in values)


def _is_openrouter(*values) -> bool:
    return any(v and "openrouter" in v.lower() for v in values)


async def eligible_models(conn, catalog: dict | None) -> tuple[list[str], list[dict]]:
    """(eligible model ids, every catalog row with why it is or is not)."""
    async with platform_scope(conn):
        rows = await conn.fetch(
            "SELECT model_id, display_name, provider, availability FROM platform_model_catalog ORDER BY model_id")
    ok, report = [], []
    for r in rows:
        dep = (catalog or {}).get(r["model_id"])
        why = None
        if r["availability"] != "available":
            why = f"availability {r['availability']}"
        elif _is_claude(r["model_id"], r["display_name"], r["provider"], dep and dep.upstream):
            why = "Claude models are ruled out for this work"
        elif _is_openrouter(r["model_id"], r["provider"], dep and dep.upstream):
            why = "OpenRouter routes are not used"
        elif (r["provider"] or "").lower() in EMBEDDING_PROVIDERS:
            why = "embedding provider"
        elif catalog is not None and dep is None:
            why = "not served by the LiteLLM proxy under this name"
        elif dep is not None and dep.duplicate:
            why = "more than one deployment behind this name (would load-balance)"
        report.append({"model_id": r["model_id"], "provider": r["provider"],
                       "availability": r["availability"], "eligible": why is None, "why_not": why})
        if why is None:
            ok.append(r["model_id"])
    return ok, report


async def choose_model(conn, catalog: dict | None, requested: str | None = None) -> tuple[str | None, list[dict]]:
    ok, report = await eligible_models(conn, catalog)
    if requested:
        return (requested if requested in ok else None), report
    return (ok[0] if ok else None), report


# ═══ Prompts ═══════════════════════════════════════════════════════════════
INSTRUCTIONS = """You are making an INVENTORY of one US structured-note filing (SEC 424B2). \
This is not extraction into a fixed form: list EVERY distinct data element the text below \
contains — every labelled term, date, level, rate, amount, schedule, party, identifier, \
condition and definition — even ones that fit no existing field.

For each element give:
- "label": the label EXACTLY as written in the filing (keep its capitalisation and wording)
- "value": the value as written (null if the element is a definition with no single value)
- "quote": a SHORT, EXACT, CONTIGUOUS quote copied character for character from the text \
(under 300 characters) that contains the element — it will be searched for in the filing and \
the item is discarded if it is not found. Copy one unbroken stretch of the text: never \
abridge it, never join two separate passages, and never use an ellipsis ("..." or "…"). If \
the passage is long, quote only the shortest part that contains the element. For a value in \
a table, quote the cell text exactly as it appears.
- "section": the heading of the section it appears under
- "maps_to": the key of an existing schema field below, ONLY if the item means EXACTLY what \
that field's own description says it means — read the description, not just its name. If the \
meaning differs in any way, or you are unsure, answer "NEW" instead: a wrong mapping is worse \
than a new proposal.
- "proposed_field_key": for NEW, a snake_case key you would propose; otherwise null
- "misleading_label": true when the label is misleading about what the element really does \
(for example a "Redemption Barrier" that is really only the threshold for a fixed payout and \
protects nothing); "misleading_note": one sentence saying what it really is

Also, for the document as a whole:
- "product_family": the product family as the DOCUMENT describes it, in its own words
- "program_supplement": the program / product supplement it cites (name and number), or null
- "has_hypothetical_payout_table": true or false
- "issue_size": the aggregate principal amount as written, or null

Answer with ONE JSON object only:
{"document": {"product_family": ..., "program_supplement": ..., \
"has_hypothetical_payout_table": ..., "issue_size": ...}, "items": [ {...}, ... ]}
No prose, no markdown."""

GROUPING_INSTRUCTIONS = """Below is a numbered list of the distinct FIELD KEYS proposed or \
mapped by an inventory pass over many structured-note filings, each with a few sample labels \
and an example value as evidence of what issuers actually call it. Group keys that mean THE \
SAME THING — synonyms used by different issuers or proposed independently by different \
documents (e.g. "issuer" and "issuer_name"; "cusip" and "cusip_isin"; two wordings of a listing \
or registration-number field) — into concepts. Do not merge keys that differ in meaning, even if \
they sound alike (a buffer is not a barrier; a coupon barrier is not a downside threshold; an \
issue date is not a valuation date; a schedule of dates is not a single date).

The list may be one part of a longer list; group only the keys you are given, and name \
each concept plainly (its "concept" name and "proposed_field_key" are used to join it with \
the same concept found in the other parts).

Answer with ONE JSON object only:
{"groups": [{"concept": "<short name>", "key_ids": [<ids>], "maps_to": "<existing field key or NEW>", \
"proposed_field_key": "<snake_case or null>"}]}
Every id should appear in exactly one group; a key with no synonyms is a group of one."""


def field_list_text(specs: list[schema.FieldSpec]) -> str:
    lines = [f"- {s.key}: {s.label} ({s.kind}) — {s.description}" for s in specs]
    return "Existing schema fields (key: label (type) — meaning):\n" + "\n".join(lines)


def build_inventory_messages(specs: list[schema.FieldSpec], terms_text: str, *, filer: str | None,
                             accession: str | None) -> list[dict]:
    system = INSTRUCTIONS + "\n\n" + field_list_text(specs)
    user = (f"Filer: {filer or 'unknown'}\nAccession: {accession or 'unknown'}\n\n"
            f"FILING TEXT (selected sections):\n{terms_text}")
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


# ── A cheap, meaning-aware safety net on "maps_to" ──────────────────────────
_GENERIC_WORDS = frozenset({
    "the", "a", "an", "of", "to", "and", "or", "is", "are", "date", "dates", "amount", "amounts",
    "value", "values", "pct", "percent", "percentage", "rate", "rates", "type", "types", "level",
    "levels", "number", "numbers", "price", "prices", "total", "aggregate", "per", "for", "on",
    "at", "in", "notes", "note", "securities", "security",
})


def _significant_words(*texts: str) -> set[str]:
    words: set[str] = set()
    for t in texts:
        for w in re.findall(r"[a-z]+", (t or "").lower()):
            if len(w) > 2 and w not in _GENERIC_WORDS:
                words.add(w)
    return words


# Known domain synonyms a legitimate mapping may use without ever sharing a
# word with the TARGET field's own key/label/description — e.g. "Trigger
# Value" for a barrier level: the word "trigger" is an established synonym
# (edgartools_reader.py's barrier regex, gold.py's threshold_trigger_buffer_
# wording) but only ever appears on the SIBLING protection_type field's
# description (schema.py), never on barrier_pct's own — so without this table
# a genuine mapping to barrier_pct was wrongly rejected.
_SYNONYMS: dict[str, frozenset[str]] = {
    "barrier": frozenset({"trigger", "knock", "knockin"}),
}


def _with_synonyms(words: set[str]) -> set[str]:
    out = set(words)
    for canonical, synonyms in _SYNONYMS.items():
        if canonical in words or words & synonyms:
            out.add(canonical)
            out |= synonyms
    return out


def plausible_mapping(label: str, spec: schema.FieldSpec) -> bool:
    """Not a semantic verifier — the model is given the field's own
    description and told to map only on identical meaning. This is the
    fallback for when it ignores that: reject a mapping whose label shares NO
    real vocabulary at all with the field's own key, label, description OR a
    known synonym (``_SYNONYMS``) — the description is included because it is
    where a true synonym actually shows up ("Pricing Date" ->
    initial_valuation_date, whose description says "pricing/strike date"),
    and the synonym table catches the rarer case where the synonym lives on a
    DIFFERENT field's description instead (barrier_pct / "Trigger Value") —
    the shape of the real misreads being guarded against is "Issue Date" ->
    initial_valuation_date, "Denominations" -> notional_currency, "notes are
    unsecured" -> protection_type, none of which share any vocabulary with
    the field at all, synonyms included."""
    item_words = _with_synonyms(_significant_words(label))
    field_words = _with_synonyms(_significant_words(spec.key.replace("_", " "), spec.label, spec.description))
    return bool(item_words & field_words)


# ═══ Calls (through the proxy chokepoint only) ═════════════════════════════
@dataclass
class CallResult:
    status: str                    # ok | failed | model_mismatch | invalid
    parsed: dict | None = None
    error: str | None = None
    provider_model: str | None = None
    proxy_model_id: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cost_usd: float = 0.0
    latency_ms: int | None = None
    call_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    finish_reason: str | None = None
    decision_log_id: str | None = None


async def _call(deployment: str, messages: list[dict], *, catalog: dict, spend: SpendTracker,
                max_tokens: int, what: str, kind: str, tags: list[str]) -> CallResult:
    """One call. Reserves its estimated cost FIRST (raises SpendCapReached
    without calling); provenance checked like B1's readers."""
    dep = catalog.get(deployment)
    chars = sum(len(m["content"]) for m in messages)
    reservation = await spend.reserve(estimate_call_cost(dep, chars, max_tokens), what)
    prompt_version = GROUPING_PROMPT_VERSION if kind == "grouping" else PROMPT_VERSION
    body = {"model": deployment, "messages": messages, "max_tokens": max_tokens,
            "response_format": {"type": "json_object"},
            "metadata": {"tags": tags + [f"usage:{kind}", f"prompt:{prompt_version}"]}}
    CALLS[kind] += 1
    resp = await proxy.chat(body)
    res = CallResult(status="failed", latency_ms=resp.latency_ms,
                     proxy_model_id=strip_nul(resp.headers.get("x-litellm-model-id")))
    res.input_tokens, res.output_tokens, _cached = proxy.response_usage(resp.body)
    actual = proxy.header_cost(resp)
    if actual is None:
        actual = priced_cost(dep, res.input_tokens, res.output_tokens, _cached)
    if resp.status != 200:
        actual = actual if actual is not None else 0.0
    res.cost_usd = await spend.settle(reservation, actual)
    if resp.status != 200 or resp.body is None:
        res.error = strip_nul(f"HTTP {resp.status}: {resp.error or (resp.text or '')[:300]}")
        return res
    res.provider_model = strip_nul(resp.body.get("model"))
    fallbacks = resp.headers.get("x-litellm-attempted-fallbacks")
    if fallbacks not in (None, "0"):
        res.status, res.error = "model_mismatch", strip_nul(f"the proxy attempted {fallbacks} fallback(s)")
        return res
    upstream = dep.upstream if dep else None
    if not proxy.reported_model_matches(upstream, res.provider_model):
        res.status = "model_mismatch"
        res.error = strip_nul(f"asked for '{deployment}' ({upstream}) but the provider reported "
                              f"'{res.provider_model}'")
        return res
    if _is_claude(res.provider_model, upstream):
        res.status, res.error = "model_mismatch", "a Claude model answered; Claude is ruled out for this work"
        return res
    try:
        res.finish_reason = strip_nul(resp.body["choices"][0].get("finish_reason"))
    except (KeyError, IndexError, TypeError, AttributeError):
        pass
    try:
        content = resp.body["choices"][0]["message"]["content"]
        parsed = json.loads(content)
        if not isinstance(parsed, dict):
            raise ValueError("not a JSON object")
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        res.status, res.error = "invalid", strip_nul(f"unparseable response: {exc}"[:300])
        return res
    res.status, res.parsed = "ok", strip_nul_deep(parsed)
    return res


# ═══ The central AI decision log ═══════════════════════════════════════════
# Every other AI call in the app writes one public.ai_decision_log row
# (services.extraction._write_ai_decision). These calls go through the
# proxy chokepoint instead of that chain executor, so they write the same row
# shape themselves — on the run's own connection, in the order the results
# are handled (never from inside the concurrent calls: one asyncpg connection
# cannot run two queries at once). The org is Hollisworks' own platform org:
# the template study is Hollisworks' own research usage, not usage on behalf
# of a client org. Non-blocking, like _safe_log: a failed write is printed
# and counted, never allowed to fail the run.
DECISION_LOG_IDS: list[str] = []     # every row this process wrote (verify reads the delta)
DECISION_LOG_FAILURES: list[str] = []


async def log_decision(conn, res: CallResult, *, deployment: str, kind: str,
                       success: bool | None = None, error: str | None = None) -> str | None:
    """``success`` / ``error`` default to the call's own outcome; the grouping
    step passes whether the ANSWER was usable, so an HTTP-200 answer with no
    groups in it is logged as the failure it was."""
    from services.litellm_credentials import HOLLISWORKS_ORG_ID

    ok = (res.status == "ok") if success is None else success
    detail = None if ok else (error or f"{res.status}: {res.error}")

    try:
        async with conn.transaction():
            await conn.execute(
                "SELECT set_config('app.current_org_id', $1, true), "
                "       set_config('app.is_super_admin', 'true', true)", HOLLISWORKS_ORG_ID)
            row_id = await conn.fetchval(
                """INSERT INTO ai_decision_log
                       (org_id, task_type, model_requested, model_used, fallback_used, fallback_reason,
                        cost_usd, latency_ms, success, error_detail, litellm_bypassed)
                   VALUES ($1::uuid, $2, $3, $4, false, NULL, $5, $6, $7, $8, false)
                   RETURNING id""",
                HOLLISWORKS_ORG_ID, TASK_TYPE[kind], deployment, res.provider_model or deployment,
                _dec(res.cost_usd), res.latency_ms, ok, strip_nul(detail)[:1500] if detail else None)
    except Exception as exc:  # noqa: BLE001 — non-blocking by design; printed and counted
        msg = f"{type(exc).__name__}: {exc}"[:300]
        DECISION_LOG_FAILURES.append(msg)
        print(f"[edgar_inventory] ai_decision_log write failed (non-blocking): {msg}")
        return None
    res.decision_log_id = str(row_id)
    DECISION_LOG_IDS.append(res.decision_log_id)
    return res.decision_log_id


# ═══ Parsing + quote verification ══════════════════════════════════════════
_NORM_RE = re.compile(r"[^a-z0-9%$#&/ ]+")


def normalise_label(label: str) -> str:
    s = (label or "").lower().replace("’", "'").replace("‘", "'")
    s = re.sub(r"\(s\)", "", s)
    s = _NORM_RE.sub(" ", s)
    s = re.sub(r"\s+", " ", s).strip()
    s = re.sub(r"^(?:the|a|an)\s+", "", s)
    return s


def _str_or_none(v) -> str | None:
    if v is None:
        return None
    if isinstance(v, (dict, list)):
        s = json.dumps(v, default=str)
    else:
        s = str(v).strip()
    s = strip_nul(s)[:2000]
    return s or None


def parse_items(parsed: dict, doc, field_specs: dict[str, schema.FieldSpec]) -> tuple[dict, list[dict], list[dict]]:
    """(document facts, accepted items, rejected items). An item whose quote
    is not found in the filing text (``TextIndex.locate`` — exact, normalised,
    whitespace-insensitive, or an abridged quote whose fragments are all found
    in order) is rejected with a reason, keeping every field of the item.

    A claimed ``maps_to`` is only accepted if ``plausible_mapping`` finds real
    shared vocabulary between the item's label and that field — a mapping
    with no shared meaning at all (e.g. "Issue Date" -> initial_valuation_date)
    is rejected back to a proposal instead, never silently kept."""
    meta = parsed.get("document") if isinstance(parsed.get("document"), dict) else {}
    facts = {
        "product_family": _str_or_none(meta.get("product_family")),
        "program_supplement": _str_or_none(meta.get("program_supplement")),
        "has_payout_table": meta.get("has_hypothetical_payout_table")
        if isinstance(meta.get("has_hypothetical_payout_table"), bool) else None,
        "issue_size": _str_or_none(meta.get("issue_size")),
    }
    accepted, rejected = [], []
    items = parsed.get("items") if isinstance(parsed.get("items"), list) else []
    for it in items:
        if not isinstance(it, dict):
            rejected.append({"item": strip_nul(str(it)[:200]), "reason": "not an object"})
            continue
        label = _str_or_none(it.get("label"))
        quote = _str_or_none(it.get("quote"))
        if not label:
            rejected.append({"label": label, "quote": quote, "reason": "no label"})
            continue
        if not quote:
            rejected.append({"label": label, "quote": quote, "reason": "no quote"})
            continue
        span = doc.index.locate(quote)
        if span is None:
            # The WHOLE item is kept (not just label + quote), so a later
            # --rematch can promote it intact if the matcher improves.
            rejected.append({"label": label, "quote": quote, "reason": QUOTE_NOT_FOUND,
                             "value": _str_or_none(it.get("value")), "section": _str_or_none(it.get("section")),
                             "maps_to": _str_or_none(it.get("maps_to")),
                             "proposed_field_key": _str_or_none(it.get("proposed_field_key")),
                             "misleading_label": it.get("misleading_label") is True,
                             "misleading_note": _str_or_none(it.get("misleading_note"))})
            continue
        maps_to = _str_or_none(it.get("maps_to")) or NEW
        spec = field_specs.get(maps_to) if maps_to != NEW else None
        mapped = maps_to if (spec is not None and plausible_mapping(label, spec)) else None
        proposed = _str_or_none(it.get("proposed_field_key"))
        if maps_to != NEW and mapped is None and not proposed:
            # Either the model named a field that does not exist (keep its own
            # suggestion as the proposal), or named a REAL field implausibly
            # (never reuse that field's key — propose a label-derived one).
            proposed = maps_to if spec is None else _slug(normalise_label(label) or label)
        accepted.append({
            "label": label, "label_normalized": normalise_label(label) or label.lower(),
            "value_text": _str_or_none(it.get("value")), "quote": quote,
            "quote_char_start": span[0], "quote_char_end": span[1],
            "section": _str_or_none(it.get("section")), "mapped_field_key": mapped,
            "proposed_field_key": None if mapped else proposed,
            "misleading_label": it.get("misleading_label") is True,
            "misleading_note": _str_or_none(it.get("misleading_note")),
        })
    return facts, accepted, rejected


# ═══ Storage ═══════════════════════════════════════════════════════════════
def _dec(v):
    return None if v is None else Decimal(str(round(float(v), 8)))


async def create_run(conn, *, cohort_id, deployment, spend_cap, planned, created_by=None) -> str:
    async with platform_scope(conn):
        return str(await conn.fetchval(
            """INSERT INTO portfolio.edgar_inventory_runs
                   (cohort_id, deployment_name, prompt_version, spend_cap_usd, documents_planned, created_by)
               VALUES ($1, $2, $3, $4, $5, $6) RETURNING id""",
            cohort_id, deployment, PROMPT_VERSION, _dec(spend_cap), planned, created_by))


async def store_document(conn, run_id, c: ChosenDocument, res: CallResult, deployment: str,
                         facts: dict, accepted: list[dict], rejected: list[dict]) -> str:
    async with platform_scope(conn):
        doc_id = await conn.fetchval(
            """INSERT INTO portfolio.edgar_inventory_documents
                   (run_id, accession_number, reference_filing_id, document_kind, issuer_group,
                    selection_reason, status, error, terms_chars, terms_tokens_est, terms_sections,
                    product_family, program_supplement, has_payout_table, issue_size,
                    items_accepted, items_rejected, rejected_items, deployment_name, provider_model,
                    proxy_model_id, call_id, input_tokens, output_tokens, cost_usd, latency_ms)
               VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11::jsonb,$12,$13,$14,$15,$16,$17,$18::jsonb,
                       $19,$20,$21,$22,$23,$24,$25,$26) RETURNING id""",
            run_id, c.accession_number, c.reference_filing_id, c.document_kind, c.issuer_group,
            c.reason, res.status, res.error, c.terms.chars, c.terms.tokens_est,
            json.dumps({
                "sections": c.terms.sections, "sections_found": c.terms.sections_found,
                "sections_included": c.terms.sections_included,
                "estimated_value_found": c.terms.estimated_value_found,
                "plan_of_distribution_found": c.terms.plan_of_distribution_found,
                "estimated_value_found_heading": c.terms.estimated_value_found_heading,
                "estimated_value_found_content": c.terms.estimated_value_found_content,
                "plan_of_distribution_found_heading": c.terms.plan_of_distribution_found_heading,
                "plan_of_distribution_found_content": c.terms.plan_of_distribution_found_content,
                "always_included_tags": c.terms.always_included_tags,
                "truncated": c.terms.truncated,
            }, default=str),
            facts.get("product_family"), facts.get("program_supplement"),
            facts.get("has_payout_table"), facts.get("issue_size"), len(accepted), len(rejected),
            json.dumps(rejected, default=str), deployment, res.provider_model, res.proxy_model_id,
            res.call_id, res.input_tokens, res.output_tokens, _dec(res.cost_usd), res.latency_ms)
        await _insert_items(conn, run_id, doc_id, c.accession_number, c.issuer_group, accepted,
                            deployment, res.provider_model, res.call_id)
    return str(doc_id)


async def _insert_items(conn, run_id, doc_id, accession_number, issuer_group, accepted: list[dict],
                        deployment: str, provider_model: str | None, call_id: str | None) -> None:
    """Caller holds ``platform_scope``."""
    if not accepted:
        return
    await conn.executemany(
        """INSERT INTO portfolio.edgar_inventory_items
               (run_id, document_id, accession_number, issuer_group, label, label_normalized,
                value_text, quote, quote_char_start, quote_char_end, section, mapped_field_key,
                proposed_field_key, misleading_label, misleading_note, deployment_name,
                provider_model, call_id)
           VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18)""",
        [(run_id, doc_id, accession_number, issuer_group, a["label"], a["label_normalized"],
          a["value_text"], a["quote"], a["quote_char_start"], a["quote_char_end"], a["section"],
          a["mapped_field_key"], a["proposed_field_key"], a["misleading_label"],
          a["misleading_note"], deployment, provider_model, call_id) for a in accepted])


async def finish_run(conn, run_id, **fields) -> None:
    allowed = {"status", "spent_usd", "documents_done", "items_accepted", "items_rejected",
               "grouping_method", "stop_reason", "report", "finished_at"}
    assert set(fields) <= allowed, set(fields) - allowed
    cols, vals = [], []
    for i, (k, v) in enumerate(fields.items(), start=2):
        if k == "report":
            cols.append(f"report = ${i}::jsonb")
            v = json.dumps(v, default=str)
        else:
            cols.append(f"{k} = ${i}")
            if k == "spent_usd":
                v = _dec(v)
        vals.append(v)
    async with platform_scope(conn):
        await conn.execute(f"UPDATE portfolio.edgar_inventory_runs SET {', '.join(cols)} WHERE id = $1",
                           run_id, *vals)


# ═══ The run ═══════════════════════════════════════════════════════════════
@dataclass
class InventorySummary:
    run_id: str | None
    status: str
    dry_run: bool
    deployment: str | None
    documents_planned: int = 0
    documents_done: int = 0
    items_accepted: int = 0
    items_rejected: int = 0
    spent_usd: float = 0.0
    est_cost_usd: float = 0.0
    stop_reason: str | None = None
    plan: list[dict] = field(default_factory=list)
    unloadable: list[dict] = field(default_factory=list)
    model_report: list[dict] = field(default_factory=list)
    sections_coverage: dict = field(default_factory=dict)


def _sections_coverage(plan: list[dict]) -> dict:
    """Share of planned documents where the estimated-value section and the
    plan of distribution were found — computed from the SAME per-document
    section scan whether or not the model is ever called, so it is visible
    even from a dry run. ``estimated_value_found`` / ``plan_of_distribution_
    found`` are the OR of heading- and content-based detection (unchanged
    shape for existing callers); the ``_heading`` / ``_content`` counts below
    break that down, since a document can satisfy one without the other."""
    n = len(plan)

    def count(key):
        return len([p for p in plan if p[key]])

    return {
        "documents": n,
        "estimated_value_found": count("estimated_value_found"),
        "plan_of_distribution_found": count("plan_of_distribution_found"),
        "estimated_value_found_heading": count("estimated_value_found_heading"),
        "estimated_value_found_content": count("estimated_value_found_content"),
        "plan_of_distribution_found_heading": count("plan_of_distribution_found_heading"),
        "plan_of_distribution_found_content": count("plan_of_distribution_found_content"),
        "missing_estimated_value": [p["accession_number"] for p in plan if not p["estimated_value_found"]],
        "missing_plan_of_distribution": [p["accession_number"] for p in plan
                                         if not p["plan_of_distribution_found"]],
    }


async def run_inventory(conn, cohort_id, *, catalog: dict, spend_cap_usd: float, dry_run: bool,
                        loader, registry_rows: list[dict], deployment: str | None = None,
                        created_by=None, max_tokens: int = DEFAULT_MAX_TOKENS,
                        lo: int = PER_ISSUER_MIN, hi: int = PER_ISSUER_MAX,
                        product_supplements: int = PRODUCT_SUPPLEMENTS_PER_ISSUER,
                        group_with_model: bool = True, concurrency: int = DEFAULT_CONCURRENCY,
                        progress=None) -> InventorySummary:
    """Plan (dry run) or run the inventory pass over a cohort.

    ``deployment``: a model id the caller already picked; otherwise
    ``choose_model``. No eligible model -> status 'blocked' (a dry run still
    plans, so the token counts are visible).

    ``concurrency`` documents are in flight at once: each one still reserves
    its estimated cost (under ``spend``'s own lock, so the cap stays exact)
    before its call is made, and documents are stored to ``conn`` one at a
    time in their original order (a single asyncpg connection cannot run
    concurrent queries) — only the model calls themselves overlap. The run
    stops cleanly, recording ``stop_reason``, after ``MAX_CONSECUTIVE_FAILURES``
    documents in a row come back anything other than 'ok' (checked in document
    order, so a batch may run up to ``concurrency - 1`` documents past the
    threshold). On Ctrl+C or any crash, the run is marked 'failed' with its
    real counts and spend so far, instead of being left 'running' forever."""
    field_specs = {s.key: s for s in schema.build_field_specs(registry_rows)}
    specs_list = list(field_specs.values())
    chosen_model, model_report = await choose_model(conn, catalog, deployment)
    docs, unloadable = await select_documents(conn, cohort_id, loader=loader, lo=lo, hi=hi,
                                              product_supplements=product_supplements, progress=progress)
    summary = InventorySummary(None, "planned" if dry_run else "running", dry_run, chosen_model,
                               documents_planned=len(docs), unloadable=unloadable, model_report=model_report)
    dep = catalog.get(chosen_model) if chosen_model else None
    for c in docs:
        msgs = build_inventory_messages(specs_list, c.terms.text, filer=c.issuer_group,
                                        accession=c.accession_number)
        chars = sum(len(m["content"]) for m in msgs)
        est = estimate_call_cost(dep, chars, max_tokens)
        summary.est_cost_usd += est
        summary.plan.append({"accession_number": c.accession_number, "issuer_group": c.issuer_group,
                             "document_kind": c.document_kind, "families": c.families, "reason": c.reason,
                             "terms_chars": c.terms.chars, "terms_tokens_est": c.terms.tokens_est,
                             "full_chars": c.terms.full_chars, "sections_found": c.terms.sections_found,
                             "sections_included": c.terms.sections_included,
                             "estimated_value_found": c.terms.estimated_value_found,
                             "plan_of_distribution_found": c.terms.plan_of_distribution_found,
                             "estimated_value_found_heading": c.terms.estimated_value_found_heading,
                             "estimated_value_found_content": c.terms.estimated_value_found_content,
                             "plan_of_distribution_found_heading": c.terms.plan_of_distribution_found_heading,
                             "plan_of_distribution_found_content": c.terms.plan_of_distribution_found_content,
                             "always_included_tags": c.terms.always_included_tags,
                             "truncated": c.terms.truncated, "est_cost_usd": round(est, 6)})
    summary.sections_coverage = _sections_coverage(summary.plan)
    if chosen_model is None:
        summary.status = "blocked"
        summary.stop_reason = ("BLOCKED: no platform_model_catalog entry is available, non-Claude, and "
                               "served by the proxy" + (f" (requested {deployment!r} is not eligible)"
                                                        if deployment else ""))
        return summary
    if dry_run:
        return summary

    spend = SpendTracker(cap_usd=float(spend_cap_usd))
    run_id = await create_run(conn, cohort_id=cohort_id, deployment=chosen_model, spend_cap=spend_cap_usd,
                              planned=len(docs), created_by=created_by)
    summary.run_id = run_id
    consecutive_failures = 0
    stopped = False
    try:
        for i in range(0, len(docs), max(1, concurrency)):
            chunk = docs[i:i + max(1, concurrency)]
            calls = [
                _call(chosen_model,
                      build_inventory_messages(specs_list, c.terms.text, filer=c.issuer_group,
                                               accession=c.accession_number),
                      catalog=catalog, spend=spend, max_tokens=max_tokens,
                      what=f"inventory:{c.accession_number}", kind="inventory",
                      tags=[f"inventory_run:{run_id}"])
                for c in chunk
            ]
            results = await asyncio.gather(*calls, return_exceptions=True)
            for c, outcome in zip(chunk, results):
                if isinstance(outcome, SpendCapReached):
                    summary.status, summary.stop_reason = "stopped_spend_cap", str(outcome)
                    stopped = True
                    break
                if isinstance(outcome, BaseException):
                    raise outcome
                res = outcome
                await log_decision(conn, res, deployment=chosen_model, kind="inventory")
                facts, accepted, rejected = ({}, [], [])
                if res.status == "ok":
                    facts, accepted, rejected = parse_items(res.parsed, c.doc, field_specs)
                    consecutive_failures = 0
                else:
                    consecutive_failures += 1
                await store_document(conn, run_id, c, res, chosen_model, facts, accepted, rejected)
                summary.documents_done += 1
                summary.items_accepted += len(accepted)
                summary.items_rejected += len(rejected)
                if progress:
                    progress(c.accession_number, f"{res.status} +{len(accepted)} items, {len(rejected)} "
                                                 f"rejected, ${res.cost_usd:.5f} (run ${spend.spent_usd:.4f})")
                if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                    summary.status = "stopped_consecutive_failures"
                    summary.stop_reason = strip_nul(
                        f"stopped after {consecutive_failures} consecutive failed documents "
                        f"(last: {c.accession_number} {res.status}"
                        + (f" — {res.error}" if res.error else "") + ")")[:1500]
                    stopped = True
                    break
            if stopped:
                break
        else:
            summary.status = "completed"
        agg = await aggregate(conn, run_id, catalog=catalog, deployment=chosen_model, spend=spend,
                              use_model=group_with_model, field_specs=field_specs, max_tokens=max_tokens)
        summary.spent_usd = spend.spent_usd
        loop_stop_reason = summary.stop_reason
        # Never let one stoppage silently swallow the other: the main
        # document loop and the grouping step can each have their own real
        # reason to report, and both must survive onto the run.
        summary.stop_reason = " | ".join(
            s for s in (summary.stop_reason, agg.get("grouping_note")) if s) or None
        await finish_run(conn, run_id, status=summary.status, spent_usd=spend.spent_usd,
                         documents_done=summary.documents_done, items_accepted=summary.items_accepted,
                         items_rejected=summary.items_rejected, grouping_method=agg["grouping_method"],
                         stop_reason=summary.stop_reason, finished_at=datetime.now(timezone.utc),
                         report={"plan": summary.plan, "unloadable": unloadable,
                                 "sections_coverage": summary.sections_coverage,
                                 "max_tokens": max_tokens, "loop_stop_reason": loop_stop_reason,
                                 **_grouping_report(agg)})
    except (Exception, asyncio.CancelledError, KeyboardInterrupt) as exc:  # noqa: BLE001 — recorded, re-raised
        summary.status = "failed"
        summary.stop_reason = strip_nul(f"{type(exc).__name__}: {exc}"[:1500]) or type(exc).__name__
        await finish_run(conn, run_id, status="failed", spent_usd=spend.spent_usd,
                         documents_done=summary.documents_done, items_accepted=summary.items_accepted,
                         items_rejected=summary.items_rejected, stop_reason=summary.stop_reason,
                         finished_at=datetime.now(timezone.utc))
        raise
    return summary


# ═══ Aggregation into concepts ═════════════════════════════════════════════
def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (s or "").lower()).strip("_")[:80] or "concept"


def effective_key(item: dict) -> str:
    """The key an item is really filed under: the mapped existing field,
    else its own proposal, else a slug of its label. Grouping over THIS —
    not the raw label — is what lets two issuers' different wording of the
    same field (or the same field proposed under two different snake_case
    names by two separate calls) land in one concept."""
    return (item.get("mapped_field_key") or item.get("proposed_field_key")
            or item.get("label_normalized") or _slug(item.get("label")))


def group_keys(items: list[dict], model_groups: list[dict] | None,
              unique_keys: list[str]) -> list[dict]:
    """Pure. ``items`` carry ``effective_key``; ``model_groups`` (optional) is
    the model's answer over ``unique_keys`` (by index) — grouping synonymous
    KEYS, not the much larger set of raw labels. Returns concepts:
    {concept_key, display_label, keys, method, maps_to, proposed}. Every key
    ends up in exactly ONE concept."""
    assigned: dict[str, int] = {}
    groups: list[dict] = []
    for g in model_groups or []:
        ids = [i for i in (g.get("key_ids") or []) if isinstance(i, int) and 0 <= i < len(unique_keys)]
        keys = [unique_keys[i] for i in ids if unique_keys[i] not in assigned]
        if not keys:
            continue
        idx = len(groups)
        for k in keys:
            assigned[k] = idx
        maps_to = g.get("maps_to") if isinstance(g.get("maps_to"), str) else None
        groups.append({"display_label": str(g.get("concept") or keys[0])[:200], "keys": keys,
                       "method": "model", "maps_to": maps_to,
                       "proposed": g.get("proposed_field_key") if isinstance(g.get("proposed_field_key"), str) else None})
    for k in unique_keys:
        if k not in assigned:
            assigned[k] = len(groups)
            groups.append({"display_label": k, "keys": [k], "method": "field_key",
                           "maps_to": None, "proposed": None})
    used: Counter = Counter()
    for g in groups:
        key = _slug(g["display_label"])
        used[key] += 1
        g["concept_key"] = key if used[key] == 1 else f"{key}_{used[key]}"
    return groups


def concept_rows(items: list[dict], groups: list[dict],
                 field_specs: dict[str, schema.FieldSpec]) -> list[dict]:
    """Per concept: original labels (synonyms), issuers, frequency, documents,
    example values, mapped/proposed field, misleading flags, and the item ids.
    Ranked by how many distinct BANKS use the concept, then frequency."""
    by_key: dict[str, list[dict]] = defaultdict(list)
    for it in items:
        by_key[it["effective_key"]].append(it)
    out = []
    for g in groups:
        its = [it for k in g["keys"] for it in by_key.get(k, [])]
        if not its:
            continue
        mapped_votes = Counter(it["mapped_field_key"] for it in its if it["mapped_field_key"])
        spec = field_specs.get(g["maps_to"]) if g["maps_to"] else None
        maps_to_plausible = spec is not None and any(plausible_mapping(it["label"], spec) for it in its)
        mapped = g["maps_to"] if maps_to_plausible else (
            mapped_votes.most_common(1)[0][0] if mapped_votes else None)
        proposed = None
        if mapped is None:
            proposed_votes = Counter(it["proposed_field_key"] for it in its if it["proposed_field_key"])
            proposed = g["proposed"] or (proposed_votes.most_common(1)[0][0] if proposed_votes else _slug(g["display_label"]))
        examples = []
        for it in its:
            v = it["value_text"]
            if v and v not in examples:
                examples.append(v)
            if len(examples) >= 5:
                break
        out.append({
            "concept_key": g["concept_key"], "display_label": g["display_label"],
            "labels": sorted({it["label"] for it in its}),
            "issuers": sorted({it["issuer_group"] or "(unknown)" for it in its}),
            "frequency": len(its), "document_count": len({it["document_id"] for it in its}),
            "example_values": examples, "mapped_field_key": mapped, "proposed_field_key": proposed,
            "misleading_flags": [{"label": it["label"], "note": it["misleading_note"],
                                  "issuer_group": it["issuer_group"], "accession_number": it["accession_number"],
                                  "quote": it["quote"][:300]} for it in its if it["misleading_label"]],
            "grouping_method": g["method"], "item_ids": [it["id"] for it in its],
        })
    out.sort(key=lambda c: (-len(c["issuers"]), -c["frequency"], c["concept_key"]))
    return out


def label_dictionary(concepts: list[dict], items: list[dict]) -> dict:
    """issuer -> original label -> concept_key (for issuer-specific rules)."""
    concept_of = {iid: c["concept_key"] for c in concepts for iid in c["item_ids"]}
    out: dict[str, dict[str, str]] = defaultdict(dict)
    for it in items:
        ck = concept_of.get(it["id"])
        if ck:
            out[it["issuer_group"] or "(unknown)"][it["label"]] = ck
    return {k: dict(sorted(v.items())) for k, v in sorted(out.items())}


async def _load_items(conn, run_id) -> list[dict]:
    async with platform_scope(conn):
        rows = await conn.fetch(
            """SELECT id, document_id, accession_number, issuer_group, label, label_normalized, value_text,
                      quote, mapped_field_key, proposed_field_key, misleading_label, misleading_note
               FROM portfolio.edgar_inventory_items WHERE run_id = $1 ORDER BY created_at, id""", run_id)
    out = [{**dict(r), "id": str(r["id"]), "document_id": str(r["document_id"])} for r in rows]
    for it in out:
        it["effective_key"] = effective_key(it)
    return out


async def _field_specs(conn) -> dict[str, schema.FieldSpec]:
    rows = await schema.load_registry_rows(conn)
    return {s.key: s for s in schema.build_field_specs(rows)}


def _key_samples(items: list[dict], unique_keys: list[str]) -> dict[str, dict]:
    samples: dict[str, dict] = {k: {"labels": [], "values": [], "is_existing": False} for k in unique_keys}
    for it in items:
        s = samples[it["effective_key"]]
        if it["mapped_field_key"]:
            s["is_existing"] = True
        if it["label"] not in s["labels"] and len(s["labels"]) < 3:
            s["labels"].append(it["label"])
        if it["value_text"] and it["value_text"] not in s["values"] and len(s["values"]) < 2:
            s["values"].append(it["value_text"])
    return samples


def _grouping_listing(unique: list[str], samples: dict[str, dict]) -> str:
    return "\n".join(
        f"{i}. {k} — labels: {', '.join(samples[k]['labels']) or k} — e.g. "
        f"{(samples[k]['values'][0] if samples[k]['values'] else '')!r} — "
        f"{'existing field' if samples[k]['is_existing'] else 'proposed NEW'}"
        for i, k in enumerate(unique))


def _usable_groups(res: CallResult, n_keys: int) -> tuple[list[dict] | None, str | None]:
    """(groups, None) when a chunk's answer can be used, else (None, why).
    A 200 whose JSON has no 'groups' list — the real run's "unusable (ok:
    None)" — now says what it DID contain, and whether it was cut off."""
    if res.status != "ok":
        why = f"{res.status}: {res.error}"
    else:
        groups = res.parsed.get("groups") if isinstance(res.parsed, dict) else None
        if isinstance(groups, list):
            groups = [g for g in groups if isinstance(g, dict)]
            if any(isinstance(i, int) and 0 <= i < n_keys for g in groups for i in (g.get("key_ids") or [])):
                return groups, None
            why = "the 'groups' list names none of this chunk's key ids"
        else:
            why = f"the answer has no 'groups' list (top-level keys: {sorted(res.parsed or {})[:5]})"
    if res.finish_reason:
        why += f"; finish_reason={res.finish_reason}"
    if res.output_tokens is not None:
        why += f"; output_tokens={res.output_tokens}"
    return None, strip_nul(why)[:500]


def merge_chunk_groups(chunk_groups: list[list[dict]]) -> list[dict]:
    """Pure. ``chunk_groups[c]`` is chunk c's model groups with ``key_ids``
    already translated to GLOBAL key indexes. A concept whose synonyms fell
    into two different chunks comes back as one group per chunk; groups from
    DIFFERENT chunks are joined when they share a concept name, a
    proposed_field_key, or an existing field (maps_to other than NEW).
    Groups within one chunk are never joined by this step — that is the
    model's own decision for the keys it saw together."""
    flat = [(c, g) for c, groups in enumerate(chunk_groups) for g in groups]
    parent = list(range(len(flat)))

    def root(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def signatures(g):
        out = []
        if isinstance(g.get("concept"), str) and _slug(g["concept"]) != "concept":
            out.append(("concept", _slug(g["concept"])))
        if isinstance(g.get("proposed_field_key"), str) and g["proposed_field_key"].strip():
            out.append(("proposed", _slug(g["proposed_field_key"])))
        if isinstance(g.get("maps_to"), str) and g["maps_to"] not in ("", NEW):
            out.append(("maps_to", g["maps_to"]))
        return out

    seen: dict[tuple, list[int]] = defaultdict(list)
    for i, (c, g) in enumerate(flat):
        for sig in signatures(g):
            for j in seen[sig]:
                if flat[j][0] != c:
                    parent[root(i)] = root(j)
            seen[sig].append(i)
    merged: dict[int, dict] = {}
    order: list[int] = []
    for i, (_c, g) in enumerate(flat):
        r = root(i)
        if r not in merged:
            merged[r] = {"concept": g.get("concept"), "key_ids": [], "maps_to": None, "proposed_field_key": None}
            order.append(r)
        m = merged[r]
        m["key_ids"] += [k for k in (g.get("key_ids") or []) if isinstance(k, int)]
        mt = g.get("maps_to") if isinstance(g.get("maps_to"), str) else None
        if mt and (m["maps_to"] in (None, NEW)):
            m["maps_to"] = mt
        if not m["proposed_field_key"] and isinstance(g.get("proposed_field_key"), str):
            m["proposed_field_key"] = g["proposed_field_key"]
    return [merged[r] for r in order]


async def aggregate(conn, run_id, *, catalog: dict | None = None, deployment: str | None = None,
                    spend: SpendTracker | None = None, use_model: bool = True,
                    field_specs: dict[str, schema.FieldSpec] | None = None,
                    max_tokens: int = DEFAULT_MAX_TOKENS, chunk_keys: int = GROUPING_CHUNK_KEYS) -> dict:
    """Group the run's items into concepts and store them. Re-runnable: the
    run's previous concepts are replaced.

    Grouping runs over the distinct EFFECTIVE KEYS the items carry (a few
    sample labels and values each) — not the much larger set of raw labels,
    which is both why most items used to end up singletons (two issuers'
    wordings of the same field never had a chance to be compared unless the
    labels themselves matched) and why the grouping response could blow past
    its own output-token budget and come back unparseable on a large cohort.

    The keys (sorted, so near-synonyms like issuer / issuer_name sit
    together) are sent ``chunk_keys`` at a time, each call with the run's own
    ``max_tokens`` — never a separate, smaller limit of its own: a reasoning
    model spends that budget thinking before it answers, and one call over
    every key with a fixed lower cap is what came back as an empty answer on
    run aa1c8c7c. The chunks' groups are then merged across chunks
    (``merge_chunk_groups``). Each chunk's outcome is recorded
    (``grouping_chunks``). A chunk that fails (or is stopped by the spending
    cap) falls back to one concept per key FOR THAT CHUNK ONLY, and says so in
    ``grouping_note``; the other chunks keep their model grouping. Every call
    writes an ``ai_decision_log`` row (task_type 'edgar_inventory_grouping').

    If grouping is disabled or cannot run at all, every key gets its own
    concept (``grouping_method`` 'field_key') — and the reason is ALWAYS
    recorded in ``grouping_note``, never silently absorbed by a different
    stop reason the caller might also have."""
    field_specs = field_specs if field_specs is not None else await _field_specs(conn)
    items = await _load_items(conn, run_id)
    unique = sorted({it["effective_key"] for it in items})
    model_groups, method, note = None, "field_key", None
    chunk_outcomes: list[dict] = []
    if not use_model:
        note = "model grouping skipped — disabled for this run (--no-model-grouping)"
    elif not unique:
        note = "model grouping skipped — no items to group"
    elif not (deployment and catalog is not None and spend is not None):
        note = "model grouping skipped — no model/catalog/spend tracker available"
    else:
        samples = _key_samples(items, unique)
        size = max(1, int(chunk_keys))
        chunks = [unique[i:i + size] for i in range(0, len(unique), size)]
        per_chunk: list[list[dict]] = []
        failures: list[str] = []
        cap_hit: str | None = None
        for ci, keys in enumerate(chunks):
            base = ci * size
            outcome = {"chunk": ci + 1, "of": len(chunks), "keys": len(keys),
                       "first_key": keys[0], "last_key": keys[-1], "max_tokens": max_tokens}
            chunk_outcomes.append(outcome)
            if cap_hit:
                outcome.update(status="skipped", error=cap_hit)
                continue
            msgs = [{"role": "system", "content": GROUPING_INSTRUCTIONS},
                    {"role": "user", "content": f"FIELD KEYS:\n{_grouping_listing(keys, samples)}"}]
            try:
                res = await _call(deployment, msgs, catalog=catalog, spend=spend, max_tokens=max_tokens,
                                  what=f"grouping:{ci + 1}/{len(chunks)}", kind="grouping",
                                  tags=[f"inventory_run:{run_id}", f"grouping_chunk:{ci + 1}"])
            except SpendCapReached as exc:
                cap_hit = str(exc)
                outcome.update(status="skipped", error=cap_hit)
                continue
            groups, why = _usable_groups(res, len(keys))
            await log_decision(conn, res, deployment=deployment, kind="grouping",
                               success=groups is not None, error=why)
            outcome.update(call_status=res.status, finish_reason=res.finish_reason,
                           input_tokens=res.input_tokens, output_tokens=res.output_tokens,
                           cost_usd=round(res.cost_usd or 0.0, 8), call_id=res.call_id,
                           decision_log_id=res.decision_log_id)
            if groups is None:
                outcome.update(status="fallback", error=why)
                failures.append(f"chunk {ci + 1} of {len(chunks)} ({len(keys)} keys) unusable ({why})")
                continue
            outcome.update(status="model", groups=len(groups))
            per_chunk.append([{**g, "key_ids": [base + i for i in (g.get("key_ids") or [])
                                                if isinstance(i, int) and 0 <= i < len(keys)]}
                              for g in groups])
        if per_chunk:
            model_groups, method = merge_chunk_groups(per_chunk), "model"
        skipped = [o for o in chunk_outcomes if o["status"] == "skipped"]
        parts = list(failures)
        if skipped:
            parts.append(f"{len(skipped)} of {len(chunks)} chunk(s) skipped — {cap_hit}")
        if parts:
            scope = ("grouped by field key only" if not per_chunk
                     else "grouped by field key for those chunks only; the other chunks were grouped by the model")
            note = "model grouping: " + "; ".join(parts) + f" — {scope}"
    groups = group_keys(items, model_groups, unique)
    concepts = concept_rows(items, groups, field_specs)
    async with platform_scope(conn):
        await conn.execute("UPDATE portfolio.edgar_inventory_items SET concept_id = NULL WHERE run_id = $1", run_id)
        await conn.execute("DELETE FROM portfolio.edgar_inventory_concepts WHERE run_id = $1", run_id)
        for c in concepts:
            cid = await conn.fetchval(
                """INSERT INTO portfolio.edgar_inventory_concepts
                       (run_id, concept_key, display_label, labels, issuers, frequency, document_count,
                        example_values, mapped_field_key, proposed_field_key, misleading_flags, grouping_method)
                   VALUES ($1,$2,$3,$4,$5,$6,$7,$8::jsonb,$9,$10,$11::jsonb,$12) RETURNING id""",
                run_id, c["concept_key"], c["display_label"], c["labels"], c["issuers"], c["frequency"],
                c["document_count"], json.dumps(c["example_values"]), c["mapped_field_key"],
                c["proposed_field_key"], json.dumps(c["misleading_flags"]), c["grouping_method"])
            await conn.execute(
                "UPDATE portfolio.edgar_inventory_items SET concept_id = $1 WHERE id = ANY($2::uuid[])",
                cid, c["item_ids"])
    dictionary = label_dictionary(concepts, items)
    return {"grouping_method": method, "grouping_note": note, "grouping_chunks": chunk_outcomes,
            "concepts": [{k: v for k, v in c.items() if k != "item_ids"} for c in concepts],
            "label_dictionary": dictionary}


def _grouping_report(agg: dict) -> dict:
    """The grouping step's part of a run's ``report`` (written by a full run
    and rewritten by ``regroup_run``)."""
    return {"grouping_method": agg["grouping_method"], "grouping_note": agg.get("grouping_note"),
            "grouping_chunks": agg.get("grouping_chunks") or [],
            "concepts": agg["concepts"], "label_dictionary": agg["label_dictionary"]}


def _json(v, default):
    if isinstance(v, str):
        try:
            return json.loads(v)
        except (TypeError, ValueError):
            return default
    return v if v is not None else default


async def _load_run(conn, run_id) -> dict | None:
    async with platform_scope(conn):
        row = await conn.fetchrow("SELECT * FROM portfolio.edgar_inventory_runs WHERE id = $1", run_id)
    if row is None:
        return None
    run = dict(row)
    run["report"] = _json(run.get("report"), {})
    return run


# ═══ Re-checking a stored run, with NO new document calls ═════════════════
async def rematch_run(conn, run_id, *, loader, field_specs: dict[str, schema.FieldSpec] | None = None,
                      progress=None) -> dict:
    """Re-check every item a stored run REJECTED as "quote not found" against
    that document's stored filing text (``loader`` — the same R2-backed
    loader the run used; no model is called) with the CURRENT matcher, and
    promote each one that now matches into ``edgar_inventory_items``.

    Each promoted item goes through ``parse_items`` exactly as a fresh answer
    would (same matcher, same ``plausible_mapping`` safety net). Items
    rejected before rejected entries kept the whole item carry only label +
    quote, so they are promoted with no value/section/mapping — never a
    guessed one. The document's ``rejected_items`` / counters and the run's
    counters are updated in the same transaction as the insert; a rerun
    finds nothing left to promote. Promoted items have no concept until the
    run is regrouped (``regroup_run``)."""
    run = await _load_run(conn, run_id)
    if run is None:
        raise LookupError(f"no inventory run {run_id}")
    field_specs = field_specs if field_specs is not None else await _field_specs(conn)
    async with platform_scope(conn):
        docs = await conn.fetch(
            """SELECT id, accession_number, reference_filing_id, issuer_group, rejected_items, items_accepted,
                      deployment_name, provider_model, call_id
               FROM portfolio.edgar_inventory_documents WHERE run_id = $1 ORDER BY accession_number""", run_id)
    out = {"run_id": str(run_id), "documents_checked": 0, "candidates": 0, "promoted": 0,
           "still_rejected": 0, "unloadable": [], "promoted_items": []}
    for d in docs:
        rejected = _json(d["rejected_items"], [])
        rejected = rejected if isinstance(rejected, list) else []
        cand_idx = [i for i, r in enumerate(rejected)
                    if isinstance(r, dict) and r.get("reason") == QUOTE_NOT_FOUND and r.get("label") and r.get("quote")]
        if not cand_idx:
            continue
        try:
            doc = await loader(conn, d["reference_filing_id"])
        except Exception as exc:  # noqa: BLE001 — recorded, never fatal
            out["unloadable"].append({"accession_number": d["accession_number"], "error": str(exc)[:200]})
            continue
        out["documents_checked"] += 1
        out["candidates"] += len(cand_idx)
        parsed = {"items": [{"label": rejected[i].get("label"), "value": rejected[i].get("value"),
                             "quote": rejected[i].get("quote"), "section": rejected[i].get("section"),
                             "maps_to": rejected[i].get("maps_to"),
                             "proposed_field_key": rejected[i].get("proposed_field_key"),
                             "misleading_label": rejected[i].get("misleading_label") is True,
                             "misleading_note": rejected[i].get("misleading_note")} for i in cand_idx]}
        _facts, accepted, still = parse_items(parsed, doc, field_specs)
        cand_set = set(cand_idx)
        keep = [r for i, r in enumerate(rejected) if i not in cand_set] + still
        out["still_rejected"] += len(still)
        if not accepted:
            continue
        deployment = d["deployment_name"] or run.get("deployment_name") or "unknown"
        async with platform_scope(conn):
            await _insert_items(conn, run_id, d["id"], d["accession_number"], d["issuer_group"], accepted,
                                deployment, d["provider_model"], d["call_id"])
            await conn.execute(
                """UPDATE portfolio.edgar_inventory_documents
                   SET rejected_items = $2::jsonb, items_rejected = $3, items_accepted = items_accepted + $4
                   WHERE id = $1""",
                d["id"], json.dumps(strip_nul_deep(keep), default=str), len(keep), len(accepted))
            await conn.execute(
                """UPDATE portfolio.edgar_inventory_runs
                   SET items_accepted = items_accepted + $2, items_rejected = GREATEST(items_rejected - $2, 0)
                   WHERE id = $1""", run_id, len(accepted))
        out["promoted"] += len(accepted)
        out["promoted_items"] += [{"accession_number": d["accession_number"], "label": a["label"],
                                   "quote": a["quote"][:200]} for a in accepted]
        if progress:
            progress(d["accession_number"], f"promoted {len(accepted)} of {len(cand_idx)}, {len(still)} still rejected")
    record = {k: v for k, v in out.items() if k != "promoted_items"}
    record["at"] = datetime.now(timezone.utc).isoformat()
    async with platform_scope(conn):
        await conn.execute(
            """UPDATE portfolio.edgar_inventory_runs
               SET report = jsonb_set(report, '{rematches}', COALESCE(report->'rematches', '[]'::jsonb) || $2::jsonb)
               WHERE id = $1""", run_id, json.dumps([record], default=str))
    return out


def _loop_stop_reason(run: dict) -> str | None:
    """The document loop's own stop reason, without any old grouping note.
    Runs written since chunked grouping store it in the report; older runs
    stored ``"<loop reason> | <grouping note>"`` — every grouping note starts
    with "model grouping"."""
    report = run.get("report") or {}
    if "loop_stop_reason" in report:
        return report["loop_stop_reason"]
    parts = [p for p in (run.get("stop_reason") or "").split(" | ")
             if p and not p.startswith("model grouping")]
    return " | ".join(parts) or None


async def regroup_run(conn, run_id, *, catalog: dict | None, spend_cap_usd: float | None,
                      deployment: str | None = None, use_model: bool = True,
                      max_tokens: int = DEFAULT_MAX_TOKENS, chunk_keys: int = GROUPING_CHUNK_KEYS,
                      field_specs: dict[str, schema.FieldSpec] | None = None) -> dict:
    """Rerun ONLY the grouping step over a stored run's items — no document is
    loaded and no inventory call is made — and rewrite the run's concepts,
    grouping method, stop reason (the loop's own reason is kept, the old
    grouping note replaced) and report. Grouping spend is added to the run's
    ``spent_usd``. The model defaults to the run's own deployment and must
    still be eligible (``choose_model``) — no Claude, served by the proxy."""
    run = await _load_run(conn, run_id)
    if run is None:
        raise LookupError(f"no inventory run {run_id}")
    model = deployment or run.get("deployment_name")
    spend = None
    if use_model:
        chosen, _report = await choose_model(conn, catalog, model)
        if chosen is None:
            raise InventoryBlocked(f"BLOCKED: model {model!r} is not eligible for grouping "
                                   "(available, non-Claude, served by the proxy)")
        model = chosen
        if spend_cap_usd is None:
            raise ValueError("a spending cap is required to regroup with the model")
        spend = SpendTracker(cap_usd=float(spend_cap_usd))
    agg = await aggregate(conn, run_id, catalog=catalog, deployment=model if use_model else None, spend=spend,
                          use_model=use_model, field_specs=field_specs, max_tokens=max_tokens,
                          chunk_keys=chunk_keys)
    spent = spend.spent_usd if spend else 0.0
    loop_reason = _loop_stop_reason(run)
    stop_reason = " | ".join(s for s in (loop_reason, agg.get("grouping_note")) if s) or None
    report = {**(run.get("report") or {}), **_grouping_report(agg), "loop_stop_reason": loop_reason}
    report["regroups"] = list(report.get("regroups") or []) + [{
        "at": datetime.now(timezone.utc).isoformat(), "deployment": model if use_model else None,
        "max_tokens": max_tokens, "chunk_keys": chunk_keys, "spent_usd": round(spent, 8),
        "grouping_method": agg["grouping_method"], "concepts": len(agg["concepts"])}]
    await finish_run(conn, run_id, grouping_method=agg["grouping_method"], stop_reason=stop_reason,
                     spent_usd=float(run.get("spent_usd") or 0) + spent, report=report)
    return {"run_id": str(run_id), "grouping_method": agg["grouping_method"],
            "grouping_note": agg.get("grouping_note"), "grouping_chunks": agg.get("grouping_chunks"),
            "concepts": len(agg["concepts"]), "spent_usd": spent, "stop_reason": stop_reason}


# ═══ Reads for the Cohorts tab + the markdown report ═══════════════════════
async def inventory_runs(conn, cohort_id) -> list[dict]:
    async with platform_scope(conn):
        rows = await conn.fetch(
            """SELECT id, status, deployment_name, prompt_version, spend_cap_usd, spent_usd,
                      documents_planned, documents_done, items_accepted, items_rejected,
                      grouping_method, stop_reason, started_at, finished_at
               FROM portfolio.edgar_inventory_runs WHERE cohort_id = $1 ORDER BY started_at DESC""",
            cohort_id)
    return [dict(r) for r in rows]


async def inventory_concepts(conn, run_id) -> list[dict]:
    async with platform_scope(conn):
        rows = await conn.fetch(
            """SELECT id, concept_key, display_label, labels, issuers, frequency, document_count,
                      example_values, mapped_field_key, proposed_field_key, misleading_flags, grouping_method
               FROM portfolio.edgar_inventory_concepts WHERE run_id = $1
               ORDER BY frequency DESC, concept_key""", run_id)
    out = []
    for r in rows:
        d = dict(r)
        for k in ("example_values", "misleading_flags"):
            if isinstance(d.get(k), str):
                d[k] = json.loads(d[k])
        out.append(d)
    return out


async def run_label_dictionary(conn, run_id) -> dict:
    async with platform_scope(conn):
        rows = await conn.fetch(
            """SELECT COALESCE(i.issuer_group, '(unknown)') AS issuer, i.label, c.concept_key
               FROM portfolio.edgar_inventory_items i
               JOIN portfolio.edgar_inventory_concepts c ON c.id = i.concept_id
               WHERE i.run_id = $1 ORDER BY 1, 2""", run_id)
    out: dict[str, dict[str, str]] = defaultdict(dict)
    for r in rows:
        out[r["issuer"]][r["label"]] = r["concept_key"]
    return dict(out)


def _doc_sections(d: dict) -> dict:
    raw = d.get("terms_sections")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (TypeError, ValueError):
            raw = {}
    if not isinstance(raw, dict):
        raw = {}
    return {
        "sections_found": raw.get("sections_found") or [],
        "estimated_value_found": bool(raw.get("estimated_value_found")),
        "plan_of_distribution_found": bool(raw.get("plan_of_distribution_found")),
        "estimated_value_found_heading": bool(raw.get("estimated_value_found_heading")),
        "estimated_value_found_content": bool(raw.get("estimated_value_found_content")),
        "plan_of_distribution_found_heading": bool(raw.get("plan_of_distribution_found_heading")),
        "plan_of_distribution_found_content": bool(raw.get("plan_of_distribution_found_content")),
        "truncated": bool(raw.get("truncated")),
    }


def render_markdown(run: dict, documents: list[dict], concepts: list[dict], dictionary: dict) -> str:
    def esc(s):
        return str(s if s is not None else "").replace("|", "\\|").replace("\n", " ")

    docs_sections = [{**d, "_sections": _doc_sections(d)} for d in documents]
    n = len(docs_sections)
    ev_n = sum(1 for d in docs_sections if d["_sections"]["estimated_value_found"])
    pod_n = sum(1 for d in docs_sections if d["_sections"]["plan_of_distribution_found"])
    ev_heading_n = sum(1 for d in docs_sections if d["_sections"]["estimated_value_found_heading"])
    ev_content_n = sum(1 for d in docs_sections if d["_sections"]["estimated_value_found_content"])
    pod_heading_n = sum(1 for d in docs_sections if d["_sections"]["plan_of_distribution_found_heading"])
    pod_content_n = sum(1 for d in docs_sections if d["_sections"]["plan_of_distribution_found_content"])
    missing_ev = [d for d in docs_sections if not d["_sections"]["estimated_value_found"]]
    missing_pod = [d for d in docs_sections if not d["_sections"]["plan_of_distribution_found"]]
    by_bank: dict[str, list[dict]] = defaultdict(list)
    for d in docs_sections:
        by_bank[d.get("issuer_group") or "(unknown)"].append(d)

    lines = [
        "# Template Study — inventory of what structured-note filings contain", "",
        f"Inventory run `{run['id']}` over cohort `{run['cohort_id']}` — model `{run['deployment_name']}` "
        f"(prompt {run['prompt_version']}), status **{run['status']}**, spent ${float(run['spent_usd'] or 0):.4f} "
        f"of a ${float(run['spend_cap_usd']):.2f} cap.", "",
        f"Documents read: {run['documents_done']} of {run['documents_planned']}. Items accepted: "
        f"{run['items_accepted']}. Items REJECTED because their quote was not found in the filing: "
        f"{run['items_rejected']}. Concept grouping: {run['grouping_method']}.", "",
    ]
    if run.get("stop_reason"):
        lines += [f"**Stop / skip reason:** {esc(run['stop_reason'])}", ""]
    chunks = (_json(run.get("report"), {}) or {}).get("grouping_chunks") or []
    if chunks:
        lines += ["Grouping chunks (each a separate model call; a failed chunk falls back to one concept "
                  "per key for that chunk only):", "",
                  "| Chunk | Keys | Outcome | Output tokens | Finish | Detail |", "|---|---|---|---|---|---|"]
        for o in chunks:
            lines.append(f"| {o.get('chunk')}/{o.get('of')} | {o.get('keys')} ({esc(o.get('first_key'))} … "
                         f"{esc(o.get('last_key'))}) | {esc(o.get('status'))} | {esc(o.get('output_tokens'))} | "
                         f"{esc(o.get('finish_reason'))} | {esc(o.get('error'))} |")
        lines.append("")
    lines += [
        "Generated by `apps/api/scripts/run_edgar_inventory.py --write-doc`; the same data is on the "
        "EDGAR Pipeline page, Cohorts tab.", "",
        "## Section coverage", "",
        f"Estimated value found (heading OR content) in {ev_n} of {n} documents "
        f"({(ev_n / n * 100) if n else 0:.0f}%) — by heading {ev_heading_n}, by content {ev_content_n}. "
        f"Plan of distribution found in {pod_n} of {n} documents "
        f"({(pod_n / n * 100) if n else 0:.0f}%) — by heading {pod_heading_n}, by content {pod_content_n}.", "",
    ]
    if missing_ev:
        lines.append("Documents where the estimated value section was NOT found: "
                     + ", ".join(esc(d["accession_number"]) for d in missing_ev) + ".")
    if missing_pod:
        lines.append("Documents where the plan of distribution was NOT found: "
                     + ", ".join(esc(d["accession_number"]) for d in missing_pod) + ".")
    lines += ["", "### Coverage per bank", "",
              "| Bank | Docs | Estimated value found | Plan of distribution found |",
              "|---|---|---|---|"]
    for bank, ds in sorted(by_bank.items()):
        lines.append(f"| {esc(bank)} | {len(ds)} | "
                     f"{sum(1 for d in ds if d['_sections']['estimated_value_found'])}/{len(ds)} | "
                     f"{sum(1 for d in ds if d['_sections']['plan_of_distribution_found'])}/{len(ds)} |")
    lines += ["", "### Coverage per document", "",
              "| Issuer | Accession | Sections found | Est. value | Plan of distribution | Truncated |",
              "|---|---|---|---|---|---|"]
    for d in docs_sections:
        s = d["_sections"]
        lines.append(f"| {esc(d['issuer_group'])} | {esc(d['accession_number'])} | "
                     f"{esc(', '.join(s['sections_found']))} | {'yes' if s['estimated_value_found'] else 'NO'} | "
                     f"{'yes' if s['plan_of_distribution_found'] else 'NO'} | {'yes' if s['truncated'] else 'no'} |")
    lines += ["", "## Documents", "",
              "| Issuer | Accession | Kind | Product family (as described) | Program / product supplement | Payout table | Issue size | Terms tokens | Items | Rejected |",
              "|---|---|---|---|---|---|---|---|---|---|"]
    for d in documents:
        lines.append(f"| {esc(d['issuer_group'])} | {esc(d['accession_number'])} | {esc(d['document_kind'])} | "
                     f"{esc(d['product_family'])} | {esc(d['program_supplement'])} | {esc(d['has_payout_table'])} | "
                     f"{esc(d['issue_size'])} | {d['terms_tokens_est']} | {d['items_accepted']} | {d['items_rejected']} |")
    lines += ["", "## Concepts", "",
              "Ranked by how many banks use the concept, then by frequency.", "",
              "| Concept | Labels (synonyms) | Banks | Frequency | Docs | Existing field | Proposed new field | Example values | Grouping |",
              "|---|---|---|---|---|---|---|---|---|"]
    for c in concepts:
        lines.append(f"| {esc(c['display_label'])} | {esc('; '.join(c['labels']))} | "
                     f"{len(c['issuers'])} ({esc(', '.join(c['issuers']))}) | "
                     f"{c['frequency']} | {c['document_count']} | {esc(c['mapped_field_key'])} | "
                     f"{esc(c['proposed_field_key'])} | {esc('; '.join(map(str, c['example_values'][:3])))} | "
                     f"{esc(c['grouping_method'])} |")
    lines += ["", "## Misleading-label flags", "",
              "Every item the model flagged as misleading about what it really does, with its quote.", ""]
    any_flags = False
    for c in concepts:
        for f in c["misleading_flags"]:
            any_flags = True
            lines.append(f"- **{esc(f['label'])}** ({esc(f['issuer_group'])}, {esc(f['accession_number'])}) "
                         f"— {esc(f['note'])}  \n  > {esc(f['quote'])}")
    if not any_flags:
        lines.append("(none this run)")
    lines += ["", "## Per-issuer label dictionary", "", "Issuer -> label exactly as written -> concept.", ""]
    for issuer, labels in sorted(dictionary.items()):
        lines.append(f"### {issuer}")
        lines.append("")
        for lab, ck in sorted(labels.items()):
            lines.append(f"- {esc(lab)} -> `{ck}`")
        lines.append("")
    return "\n".join(lines) + "\n"
