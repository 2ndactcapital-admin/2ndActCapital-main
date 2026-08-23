"""Extract structured payoff terms from an EDGAR filing into note-terms rows.

INPUT  portfolio.reference_filings WHERE extraction_status = 'extracted'
       (which in the deployed corpus already means "text extracted AND passed
       the keyword prefilter" — see services/edgar_fetch.py)
OUTPUT one portfolio.securities_global_note_terms row per filing, plus the
       unresolved underlying edges on portfolio.securities_global_relationships

WHAT THIS DELIBERATELY DOES NOT DO
──────────────────────────────────────────────────────────────────────────────
It does not RESOLVE underlyings. "the Common Stock of NVIDIA Corporation" is
written to ``raw_underlying_text`` with ``link_state='unresolved'`` and
``to_global_security_id`` NULL. Creating the edge is extraction; pointing it at
a security is a separate and harder problem (decrement indices, fuzzy names,
tickers that moved) and is the next sprint. Do not add a resolver here.

It does no comparability scoring, no percentiles, no template induction, and no
UI. Extraction is per-document and LLM-driven.

THE HAZARD ENSEMBLE IS THE POINT OF THIS MODULE
──────────────────────────────────────────────────────────────────────────────
Six fields — protection_type, basket_type, return_basis, is_decrement_index,
autocall_frequency, terms_status — are read TWICE, by two different models, and
compared. They are singled out because a wrong answer on any of them is both
catastrophic and arithmetically invisible: every deterministic validator in
services/note_terms_validators.py still passes when a worst-of basket is read as
an equal-weighted one. Nothing else in the pipeline can catch these.

The comparison produces a BOOLEAN — agree or disagree. It never ranks the two
models against each other and never picks a winner on the strength of which
model said it. On disagreement both answers are recorded and the row is flagged
``needs_review`` for a person. That is why using two different models here does
not violate the rule against varying the model within a compared set: there is
no compared set, only a tripwire.

extraction_status IS NOT WRITTEN BACK — read this before "fixing" it
──────────────────────────────────────────────────────────────────────────────
``reference_filings.extraction_status`` already means "did the HTML yield text,
and did it pass the prefilter". Its CHECK constraint permits exactly
pending/fetched/extracted/failed/skipped — none of which can express "terms were
extracted from this filing". Writing to it would (a) require a value that does
not exist, or (b) overload one that does, which would destroy the prefilter
positive/negative set the corpus sprint keeps precisely so precision can be
measured later.

So this module NEVER updates that column. Whether terms have been extracted from
a filing is derived: a current ``securities_global_note_terms`` row exists with
that ``reference_filing_id``. One column, one meaning, no ambiguous states.
:func:`filings_with_terms_extracted` is the reader for that derived state.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field as dc_field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from models.note_terms import (
    AUTOCALL_FREQUENCIES,
    BASKET_TYPES,
    HAZARD_FIELD_KEYS,
    PRODUCT_ARCHETYPES,
    PROTECTION_TYPES,
    RETURN_BASES,
    NoteTermsFieldRegistryEntry,
    validate_field_status,
)
from services.extraction import (
    ASSISTANT_MODEL_KEY,
    DEFAULT_MODEL_KEY,
    call_claude_json,
    resolve_model,
)
from services.note_terms_corrections import log_hazard_disagreement
from services.note_terms_validators import cusip_checksum, run_numeric_validators

TERMS_TABLE = "portfolio.securities_global_note_terms"
REGISTRY_TABLE = "portfolio.note_terms_field_registry"
FILINGS_TABLE = "portfolio.reference_filings"
SECURITIES_TABLE = "portfolio.securities_global"
IDENTIFIERS_TABLE = "portfolio.securities_global_identifiers"
RELATIONSHIPS_TABLE = "portfolio.securities_global_relationships"

# form_type -> terms_status. An FWP is a free writing prospectus circulated
# BEFORE pricing: its terms are indicative. A 424B2 is the priced prospectus
# supplement. This mapping is deterministic ground truth from EDGAR and is
# strictly better than anything a model can infer from the prose, so it wins
# over the extracted value even though terms_status is a hazard field.
FORM_TYPE_TO_TERMS_STATUS = {"FWP": "preliminary", "424B2": "final"}

# Windowing. The median filing in this corpus is ~69k characters and the terms
# themselves occupy a few thousand of them; the rest is risk factors and tax
# discussion. Sending the whole document would cost ~4x more per filing for no
# accuracy gain, so a bounded window is selected. Offsets recorded on the row
# are always absolute into the FULL text — the window never leaks into them.
HEAD_CHARS = 12_000
DENSE_WINDOW_CHARS = 24_000
DENSE_STRIDE = 4_000

# Keyword set reused from the corpus prefilter so the window lands on the same
# signal that admitted the filing in the first place.
_WINDOW_KEYWORDS = (
    "barrier", "buffer", "autocall", "contingent coupon", "participation rate",
    "initial level", "underlying", "cusip", "valuation date", "principal amount",
    "call date", "coupon", "maturity", "worst of", "worst-of",
)

# The controlled vocabularies, keyed by field. A value outside these would blow
# the table's CHECK constraints, so an out-of-vocab answer is discarded and the
# field becomes extraction_failed rather than crashing the run.
_ENUMS: dict[str, frozenset[str]] = {
    "product_archetype": PRODUCT_ARCHETYPES,
    "protection_type": PROTECTION_TYPES,
    "basket_type": BASKET_TYPES,
    "return_basis": RETURN_BASES,
    "autocall_frequency": AUTOCALL_FREQUENCIES,
}

MAX_UNDERLYINGS = 12
PRIMARY_MAX_TOKENS = 3000
HAZARD_MAX_TOKENS = 1200


class NoteTermsExtractionError(RuntimeError):
    """Extraction could not proceed for a structural reason (not a bad answer)."""


@dataclass
class ExtractionResult:
    """The outcome of extracting one filing. Every field is reportable."""

    filing_id: str
    ok: bool
    note_terms_id: str | None = None
    global_security_id: str | None = None
    terms_status: str | None = None
    extraction_confidence: str | None = None
    field_status: dict[str, str] = dc_field(default_factory=dict)
    validator_failures: list[str] = dc_field(default_factory=list)
    validator_warnings: list[str] = dc_field(default_factory=list)
    hazard_disagreements: dict[str, dict] = dc_field(default_factory=dict)
    hazard_compared: list[str] = dc_field(default_factory=list)
    ensemble_measured: bool = False
    ensemble_model_used: str | None = None
    underlying_texts: list[str] = dc_field(default_factory=list)
    source_char_start: int | None = None
    source_char_end: int | None = None
    primary_model: str | None = None
    secondary_model: str | None = None
    reused_existing: bool = False
    error: str | None = None

    @property
    def needs_review(self) -> bool:
        return self.extraction_confidence == "needs_review"


# ── Registry ──────────────────────────────────────────────────────────────────


async def load_registry(conn) -> list[NoteTermsFieldRegistryEntry]:
    """Every governed field, read live. The registry is the source of truth.

    The hazard list is NEVER hardcoded here — it is whatever the registry says
    ``hazard_field = true`` for. ``models.note_terms.HAZARD_FIELD_KEYS`` mirrors
    it for callers that need the set without a database, and
    :func:`hazard_keys_from_registry` cross-checks the two.
    """
    rows = await conn.fetch(
        f"""
        SELECT field_key, display_label, data_type, applies_to_archetypes,
               hazard_field, created_at
        FROM {REGISTRY_TABLE}
        ORDER BY field_key
        """
    )
    return [
        NoteTermsFieldRegistryEntry(
            field_key=r["field_key"],
            display_label=r["display_label"],
            data_type=r["data_type"],
            applies_to_archetypes=list(r["applies_to_archetypes"]) if r["applies_to_archetypes"] else None,
            hazard_field=r["hazard_field"],
            created_at=r["created_at"],
        )
        for r in rows
    ]


def hazard_keys_from_registry(registry: list[NoteTermsFieldRegistryEntry]) -> list[str]:
    """The hazard keys as the LIVE registry declares them, sorted.

    Prints a loud warning — rather than raising — if the registry and the code
    mirror have drifted. Extraction should still run against the registry's
    answer; a stale constant in the model layer is a bug to fix, not a reason to
    stop extracting.
    """
    keys = sorted(e.field_key for e in registry if e.hazard_field)
    if set(keys) != set(HAZARD_FIELD_KEYS):
        print(
            "[note_terms_extraction] WARNING registry hazard fields "
            f"{keys} differ from models.note_terms.HAZARD_FIELD_KEYS "
            f"{sorted(HAZARD_FIELD_KEYS)} — using the registry"
        )
    return keys


# ── Windowing ─────────────────────────────────────────────────────────────────


def _keyword_hits(chunk: str) -> int:
    low = chunk.lower()
    return sum(low.count(k) for k in _WINDOW_KEYWORDS)


def select_window(text: str) -> tuple[str, list[tuple[int, int]]]:
    """Pick the slices of ``text`` worth sending to a model.

    Returns the assembled prompt text and the absolute spans it was built from.
    Two slices: the head, because a pricing supplement's term sheet is almost
    always in the first pages, and the densest keyword window elsewhere, because
    the "Key Terms" table sometimes sits after 40 pages of front matter. They are
    merged when they overlap.

    The spans are returned so a caller can reason about coverage, but they are
    NOT used to compute ``source_char_start`` / ``source_char_end`` — those come
    from locating the model's verbatim quotes in the full text, which is exact.
    """
    if not text:
        return "", []

    n = len(text)
    spans: list[tuple[int, int]] = [(0, min(HEAD_CHARS, n))]

    if n > HEAD_CHARS:
        best_start, best_score = HEAD_CHARS, -1
        start = 0
        while start < n:
            score = _keyword_hits(text[start:start + DENSE_WINDOW_CHARS])
            if score > best_score:
                best_score, best_start = score, start
            start += DENSE_STRIDE
        spans.append((best_start, min(best_start + DENSE_WINDOW_CHARS, n)))

    spans.sort()
    merged: list[tuple[int, int]] = []
    for s, e in spans:
        if merged and s <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], e))
        else:
            merged.append((s, e))

    parts = []
    for s, e in merged:
        parts.append(f"[characters {s}-{e} of the filing]\n{text[s:e]}")
    return "\n\n...\n\n".join(parts), merged


# ── Verbatim-quote location (real offsets, not model-reported integers) ───────


class _TextIndex:
    """Whitespace-normalised view of a filing, with a map back to real offsets.

    Models are unreliable at reporting character offsets and reliable at copying
    a phrase verbatim. So the model returns a quote and this class finds it,
    which makes ``source_char_start``/``source_char_end`` a measured fact rather
    than a hallucinated integer. Normalisation is needed because the extracted
    text carries the original document's line breaks and a model will
    silently re-wrap a quote it copies.
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
                chars.append(ch.lower())
                offsets.append(i)
                prev_space = False
        self._norm = "".join(chars)
        self._map = offsets

    def locate(self, quote: str | None) -> tuple[int, int] | None:
        """Absolute ``(start, end)`` of ``quote`` in the full text, or None."""
        if not quote or not isinstance(quote, str):
            return None
        stripped = quote.strip()
        if len(stripped) < 8:  # too short to be a distinctive anchor
            return None

        direct = self.text.find(stripped)
        if direct != -1:
            return direct, direct + len(stripped)

        needle = re.sub(r"\s+", " ", stripped).lower().strip()
        if not needle:
            return None
        pos = self._norm.find(needle)
        if pos == -1:
            return None
        end_idx = pos + len(needle) - 1
        if end_idx >= len(self._map):
            return None
        return self._map[pos], self._map[end_idx] + 1


# ── Coercion ──────────────────────────────────────────────────────────────────

_DATE_FORMATS = ("%Y-%m-%d", "%m/%d/%Y", "%B %d, %Y", "%b %d, %Y", "%d %B %Y")
_PCT_FIELDS = {
    "protection_pct", "cap_pct", "participation_rate", "coupon_rate",
    "coupon_barrier_pct", "autocall_barrier_pct",
}


def _coerce_decimal(value) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, Decimal):
        return value
    text = str(value).strip().replace(",", "").replace("$", "")
    had_pct = text.endswith("%")
    text = text.rstrip("%").strip()
    if not text:
        return None
    try:
        number = Decimal(text)
    except (InvalidOperation, ValueError):
        return None
    return number / Decimal(100) if had_pct else number


def _coerce_pct(value) -> Decimal | None:
    """Percentages are stored as FRACTIONS. 70, "70%" and 0.70 all mean 0.70.

    Anything above 5 is read as a percent-of-100. The cut is at 5 rather than 1
    because a participation rate of 1.5 (150%) is ordinary and must not be
    rescaled, while a 5.0 participation rate is not a structure that exists —
    that is a "500%" the model wrote without the sign.
    """
    number = _coerce_decimal(value)
    if number is None:
        return None
    return number / Decimal(100) if number > Decimal(5) else number


def _coerce_date(value) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if not text:
        return None
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def _coerce_bool(value) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"true", "yes", "y", "1"}:
        return True
    if text in {"false", "no", "n", "0"}:
        return False
    return None


def coerce_field(field_key: str, data_type: str, value):
    """Turn one raw model answer into the column's Python type, or None.

    None means "unusable" — either the model gave nothing or it gave something
    outside the controlled vocabulary. Both become a non-``extracted``
    field_status upstream rather than an exception, because one bad enum on one
    field must not lose the other eighteen.
    """
    if value is None:
        return None

    enum = _ENUMS.get(field_key)
    if enum is not None:
        text = str(value).strip().lower().replace(" ", "_").replace("-", "_")
        return text if text in enum else None

    if data_type == "boolean":
        return _coerce_bool(value)
    if data_type == "date":
        return _coerce_date(value)
    if data_type == "numeric":
        if field_key in _PCT_FIELDS:
            return _coerce_pct(value)
        number = _coerce_decimal(value)
        if number is None:
            return None
        if field_key == "no_call_months":  # integer column
            try:
                return int(number)
            except (InvalidOperation, ValueError):
                return None
        return number
    text = str(value).strip()
    return text or None


# ── Prompts ───────────────────────────────────────────────────────────────────

_PRIMARY_SYSTEM = """You extract structured payoff terms from US structured-note \
offering documents (SEC forms 424B2 and FWP).

Return ONE JSON object and nothing else. No prose, no markdown fences.

RULES
1. Use ONLY the field keys listed in the user message. Never invent a field key.
   A key you were not given is an error.
2. For every listed field return an object:
       {"value": <the value or null>, "absent": <true|false>, "quote": "<verbatim>"}
   - "value": the term as stated. Percentages as numbers (70 or 0.70, either is
     fine). Dates as YYYY-MM-DD. Booleans as true/false.
   - "absent": true ONLY when the document genuinely does not state this term.
     false when the term should be there and you could not determine it.
     This distinction matters: "absent" means nothing was missed.
   - "quote": a SHORT verbatim span copied EXACTLY from the document text that
     contains the answer, 10-200 characters. Copy it character for character —
     it is used to locate the answer in the source. Empty string if absent.
3. Fields constrained to a vocabulary must use one of the given values exactly.
4. Do not guess. A wrong confident answer is worse than "absent": false with a
   null value.

THE SIX FIELDS BELOW ARE READ ESPECIALLY CAREFULLY
  protection_type   buffer = absorbs the FIRST n% of loss; floor = caps total
                    loss at n%. Both get marketed as "n% downside protection".
  basket_type       single = one underlying; basket = weighted average of
                    several; worst_of = the SINGLE WORST performer drives the
                    payoff. worst_of is very common and is not a basket.
  return_basis      price = excludes dividends; total_return = includes them.
  is_decrement_index  true when the index deducts a fixed annual amount
                    (a "decrement", "synthetic dividend", or fixed % per annum
                    subtracted from the index level).
  autocall_frequency  how often the note can be called: monthly, quarterly,
                    annual, or none if it is not autocallable.
  terms_status      preliminary = indicative terms not yet priced;
                    final = priced terms."""

_HAZARD_SYSTEM = """You are a careful second reader of a US structured-note \
offering document (SEC form 424B2 or FWP). Another system has already read this \
document; you are reading it INDEPENDENTLY to see whether you reach the same \
conclusions. You have not been shown the other answers and must not try to guess \
them.

Return ONE JSON object and nothing else. No prose, no markdown fences.

For each requested field return {"value": <value or null>, "quote": "<verbatim>"}.
Use only the vocabularies given. Return null when the document does not \
determine the answer — a null that reflects genuine ambiguity is more useful \
here than a guess, because your job is to surface uncertainty, not to fill it in.

DEFINITIONS — these are the exact distinctions that matter:
  protection_type    buffer absorbs the FIRST n% of decline (you lose only
                     beyond it); floor limits total loss to n% (you lose down
                     to it). Opposite payoffs, identical marketing language.
  basket_type        single = one underlying. basket = weighted average of
                     several. worst_of = payoff tracks ONLY the worst performer.
                     If the document says "least performing", "worst
                     performing", or "lowest of", it is worst_of, not basket.
  return_basis       price = price return, dividends excluded.
                     total_return = dividends reinvested.
  is_decrement_index true if the underlying index subtracts a fixed annual
                     decrement / synthetic dividend / fixed percentage per annum
                     from its level. false otherwise.
  autocall_frequency monthly | quarterly | annual | none. Read the observation
                     schedule, not the coupon schedule, when they differ.
  terms_status       preliminary (indicative, not yet priced) | final (priced)."""


def _field_spec_lines(registry: list[NoteTermsFieldRegistryEntry]) -> str:
    lines = []
    for entry in sorted(registry, key=lambda e: e.field_key):
        enum = _ENUMS.get(entry.field_key)
        if entry.field_key == "terms_status":
            allowed = "preliminary | final | restated"
        elif enum:
            allowed = " | ".join(sorted(enum))
        else:
            allowed = entry.data_type
        applies = (
            f"  (only meaningful for: {', '.join(entry.applies_to_archetypes)})"
            if entry.applies_to_archetypes else ""
        )
        lines.append(f"  {entry.field_key}: {allowed}{applies}   — {entry.display_label}")
    return "\n".join(lines)


def build_primary_prompt(
    registry: list[NoteTermsFieldRegistryEntry], window: str, filer_name: str, form_type: str,
) -> str:
    return (
        f"Document: SEC form {form_type} filed by {filer_name}.\n\n"
        f"Extract exactly these field keys and no others:\n"
        f"{_field_spec_lines(registry)}\n\n"
        "Also return, at the top level of the JSON:\n"
        '  "issuer": the legal entity ISSUING the notes (not the guarantor, not\n'
        "            the calculation agent, not an index sponsor)\n"
        '  "cusip": the 9-character CUSIP, or null if the document states none\n'
        '  "security_name": the note\'s title as printed, e.g.\n'
        '                   "2.5 Year Market-Linked Securities Linked to NDX"\n'
        '  "initial_level": the underlying\'s initial level/price as a number, or null\n'
        '  "barrier_price": the absolute barrier LEVEL (not the percentage), or null\n'
        '  "barrier_pct": the barrier as a percentage of initial, or null\n'
        '  "underlyings": array of the underlying reference(s) EXACTLY as the\n'
        "                 document names them, e.g.\n"
        '                 ["the Common Stock of NVIDIA Corporation"].\n'
        "                 Raw strings only — do not normalise, abbreviate, or\n"
        "                 substitute a ticker.\n\n"
        'Put the per-field objects under a "fields" key.\n\n'
        "FILING TEXT\n"
        "───────────\n"
        f"{window}"
    )


def build_hazard_prompt(hazard_keys: list[str], window: str, filer_name: str, form_type: str) -> str:
    descriptions = {
        "protection_type": "buffer | floor | none",
        "basket_type": "single | basket | worst_of",
        "return_basis": "price | total_return",
        "is_decrement_index": "true | false",
        "autocall_frequency": "monthly | quarterly | annual | none",
        "terms_status": "preliminary | final | restated",
    }
    spec = "\n".join(f"  {k}: {descriptions.get(k, 'value')}" for k in sorted(hazard_keys))
    return (
        f"Document: SEC form {form_type} filed by {filer_name}.\n\n"
        f"Determine ONLY these fields:\n{spec}\n\n"
        "FILING TEXT\n"
        "───────────\n"
        f"{window}"
    )


# ── The pipeline ──────────────────────────────────────────────────────────────


def _unwrap(raw) -> tuple[object, bool, str | None]:
    """Read one ``{"value","absent","quote"}`` object defensively.

    A model that returns a bare scalar instead of the object is accommodated —
    that is a formatting slip, not a wrong answer, and discarding a correct value
    over it would be perverse.
    """
    if isinstance(raw, dict):
        return raw.get("value"), bool(raw.get("absent")), raw.get("quote")
    return raw, False, None


async def extract_terms(filing_id, pool, *, force: bool = False) -> ExtractionResult:
    """Extract one filing into a note-terms row. The whole pipeline.

    Idempotent by default: if a current terms row already exists for this
    filing, it is returned untouched and no model is called. ``force=True``
    supersedes it bitemporally (close the old row, insert a new one — Rule 3,
    never an in-place update) so a re-extraction preserves what the previous
    one said.
    """
    filing_id = str(filing_id)
    result = ExtractionResult(filing_id=filing_id, ok=False)

    async with pool.acquire() as conn:
        filing = await conn.fetchrow(
            f"""
            SELECT id, cik, filer_name, form_type, accession_number, filing_date,
                   extracted_text, extraction_status
            FROM {FILINGS_TABLE} WHERE id = $1::uuid
            """,
            filing_id,
        )
        if filing is None:
            result.error = "filing not found"
            return result
        if filing["extraction_status"] != "extracted":
            result.error = (
                f"filing extraction_status is {filing['extraction_status']!r}, "
                "not 'extracted' — it has no usable text or failed the prefilter"
            )
            return result

        text = filing["extracted_text"] or ""
        if not text.strip():
            result.error = "filing has no extracted_text"
            return result

        existing = await conn.fetchrow(
            f"""
            SELECT id, global_security_id, terms_status, extraction_confidence,
                   field_status, source_char_start, source_char_end
            FROM {TERMS_TABLE}
            WHERE reference_filing_id = $1::uuid AND valid_to IS NULL AND system_to IS NULL
            ORDER BY valid_from DESC LIMIT 1
            """,
            filing_id,
        )
        if existing is not None and not force:
            fs = existing["field_status"]
            result.ok = True
            result.reused_existing = True
            result.note_terms_id = str(existing["id"])
            result.global_security_id = str(existing["global_security_id"])
            result.terms_status = existing["terms_status"]
            result.extraction_confidence = existing["extraction_confidence"]
            result.field_status = json.loads(fs) if isinstance(fs, str) else dict(fs or {})
            result.source_char_start = existing["source_char_start"]
            result.source_char_end = existing["source_char_end"]
            return result

        registry = await load_registry(conn)

    if not registry:
        raise NoteTermsExtractionError(
            f"{REGISTRY_TABLE} is empty — there is nothing to extract against"
        )

    hazard_keys = hazard_keys_from_registry(registry)
    window, _spans = select_window(text)

    primary_model = await resolve_model(None, key=DEFAULT_MODEL_KEY)
    secondary_model = await resolve_model(None, key=ASSISTANT_MODEL_KEY)
    result.primary_model = primary_model
    result.secondary_model = secondary_model

    # ── Primary extraction ────────────────────────────────────────────────
    payload = await call_claude_json(
        _PRIMARY_SYSTEM,
        build_primary_prompt(registry, window, filing["filer_name"], filing["form_type"]),
        max_tokens=PRIMARY_MAX_TOKENS,
        org_id=None,
        model_key=DEFAULT_MODEL_KEY,
        task_type="note_terms_extraction",
    )
    if not isinstance(payload, dict):
        result.error = (
            "primary extraction returned nothing — no ANTHROPIC_API_KEY, the "
            "model chain was exhausted, or the response was unparseable"
        )
        return result

    raw_fields = payload.get("fields")
    if not isinstance(raw_fields, dict):
        raw_fields = {k: v for k, v in payload.items() if k not in {
            "issuer", "cusip", "security_name", "initial_level",
            "barrier_price", "barrier_pct", "underlyings",
        }}

    index = _TextIndex(text)
    registry_by_key = {e.field_key: e for e in registry}

    values: dict[str, object] = {}
    absent_flags: dict[str, bool] = {}
    spans: list[tuple[int, int]] = []

    for key, entry in registry_by_key.items():
        raw_value, absent, quote = _unwrap(raw_fields.get(key))
        coerced = coerce_field(key, entry.data_type, raw_value)
        values[key] = coerced
        absent_flags[key] = absent
        if coerced is not None:
            located = index.locate(quote)
            if located:
                spans.append(located)

    # terms_status is derived from the form type, which EDGAR guarantees. The
    # model's answer is still collected above and still goes through the hazard
    # ensemble, but it never becomes the stored value.
    derived_status = FORM_TYPE_TO_TERMS_STATUS.get(filing["form_type"])
    if derived_status is None:
        result.error = f"unmapped form_type {filing['form_type']!r} — cannot derive terms_status"
        return result
    model_status = values.get("terms_status")
    values["terms_status"] = derived_status

    archetype = values.get("product_archetype")

    # ── field_status: every registry field gets exactly one of four states ──
    field_status: dict[str, str] = {}
    for key, entry in registry_by_key.items():
        if key == "terms_status":
            field_status[key] = "extracted"  # derived from form_type, always known
        elif values.get(key) is not None:
            field_status[key] = "extracted"
        elif not entry.applies_to(archetype):
            field_status[key] = "not_applicable"
        elif absent_flags.get(key):
            field_status[key] = "not_in_template"
        else:
            field_status[key] = "extraction_failed"
    field_status = validate_field_status(field_status)

    # ── Deterministic validators ──────────────────────────────────────────
    cusip = payload.get("cusip")
    issuer = payload.get("issuer")
    outcome = run_numeric_validators(
        cusip=cusip,
        extracted_issuer=issuer,
        filing_cik=filing["cik"],
        barrier_pct=payload.get("barrier_pct"),
        initial_level=payload.get("initial_level"),
        barrier_price=payload.get("barrier_price"),
        coupon_barrier_pct=values.get("coupon_barrier_pct"),
        autocall_barrier_pct=values.get("autocall_barrier_pct"),
        initial_valuation_date=values.get("initial_valuation_date"),
        final_valuation_date=values.get("final_valuation_date"),
        tenor_years=values.get("tenor_years"),
    )
    result.validator_failures = list(outcome.failures)
    result.validator_warnings = list(outcome.warnings)
    for warning in outcome.warnings:
        print(f"[note_terms_extraction] filing {filing_id} {warning}")

    # ── Hazard ensemble: an independent second read of the six fields ─────
    hazard_payload = await call_claude_json(
        _HAZARD_SYSTEM,
        build_hazard_prompt(hazard_keys, window, filing["filer_name"], filing["form_type"]),
        max_tokens=HAZARD_MAX_TOKENS,
        org_id=None,
        model=secondary_model,
        model_key=ASSISTANT_MODEL_KEY,
        task_type="note_terms_hazard_ensemble",
    )

    # Did a genuinely different model serve that call? See
    # _last_ensemble_model_used for why silence here would be dangerous.
    ensemble_model_used = await _last_ensemble_model_used(pool)
    ensemble_is_independent = bool(
        ensemble_model_used and ensemble_model_used != primary_model
    )
    result.ensemble_model_used = ensemble_model_used
    if isinstance(hazard_payload, dict) and not ensemble_is_independent:
        print(
            f"[note_terms_extraction] filing {filing_id}: hazard ensemble ran on "
            f"{ensemble_model_used!r}, which is not independent of the primary "
            f"{primary_model!r} — treating the six hazard fields as NOT CROSS-CHECKED"
        )

    disagreements: dict[str, dict] = {}
    compared: list[str] = []
    if isinstance(hazard_payload, dict):
        for key in hazard_keys:
            entry = registry_by_key.get(key)
            if entry is None:
                continue
            raw_value, _absent, _quote = _unwrap(hazard_payload.get(key))
            second = coerce_field(key, entry.data_type, raw_value)
            if second is None:
                continue  # the second reader abstained — not a disagreement
            first = model_status if key == "terms_status" else values.get(key)
            compared.append(key)
            if first != second:
                disagreements[key] = {
                    "primary": first,
                    "primary_model": primary_model,
                    "secondary": second,
                    "secondary_model": secondary_model,
                }
    else:
        print(
            f"[note_terms_extraction] filing {filing_id}: hazard ensemble "
            "returned nothing — the six hazard fields were NOT cross-checked"
        )

    result.hazard_disagreements = disagreements
    result.hazard_compared = compared
    result.ensemble_measured = bool(compared) and ensemble_is_independent

    # ── Confidence ────────────────────────────────────────────────────────
    # Disagreement forces needs_review regardless of what the validators said,
    # and a validator failure forces it regardless of whether the models agreed.
    # Warnings do not (see autocall_le_coupon_barrier).
    #
    # A disagreement counts even when the second model turned out not to be
    # independent: two differing answers were genuinely produced, and that is
    # evidence of ambiguity however it arose.
    if disagreements or outcome.failures:
        confidence = "needs_review"
    elif not result.ensemble_measured:
        # The ensemble did not run, or ran on the primary model and so checked
        # nothing. Either way the hazard fields are UNMEASURED, not confirmed.
        # Calling this 'high' would let an API outage upgrade a whole run.
        confidence = "low"
    else:
        confidence = "high"
    result.extraction_confidence = confidence

    char_start = min(s for s, _ in spans) if spans else None
    char_end = max(e for _, e in spans) if spans else None
    result.source_char_start = char_start
    result.source_char_end = char_end

    underlyings = [
        str(u).strip() for u in (payload.get("underlyings") or [])
        if isinstance(u, (str, int, float)) and str(u).strip()
    ][:MAX_UNDERLYINGS]
    result.underlying_texts = underlyings

    # ── Persist ───────────────────────────────────────────────────────────
    security_name = (
        str(payload.get("security_name") or "").strip()
        or f"{filing['filer_name']} {filing['form_type']} {filing['accession_number']}"
    )

    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")

            security_id = await _resolve_security(
                conn, cusip=cusip, name=security_name[:500], filing_id=filing_id,
            )

            if force and existing is not None:
                # Rule 3: close the old row, never update it in place.
                await conn.execute(
                    f"UPDATE {TERMS_TABLE} SET valid_to = now() WHERE id = $1::uuid AND valid_to IS NULL",
                    str(existing["id"]),
                )

            note_terms_id = await conn.fetchval(
                f"""
                INSERT INTO {TERMS_TABLE}
                    (global_security_id, reference_filing_id, terms_status,
                     product_archetype, protection_type, basket_type, return_basis,
                     is_decrement_index, notional_currency, protection_pct, cap_pct,
                     participation_rate, coupon_rate, coupon_barrier_pct,
                     autocall_barrier_pct, autocall_frequency, has_no_call_period,
                     no_call_months, initial_valuation_date, final_valuation_date,
                     tenor_years, field_status, extraction_confidence,
                     source_char_start, source_char_end)
                VALUES ($1::uuid, $2::uuid, $3, $4, $5, $6, $7, $8, $9, $10, $11,
                        $12, $13, $14, $15, $16, $17, $18, $19, $20, $21,
                        $22::jsonb, $23, $24, $25)
                RETURNING id
                """,
                security_id, filing_id, derived_status,
                values.get("product_archetype"), values.get("protection_type"),
                values.get("basket_type"), values.get("return_basis"),
                bool(values.get("is_decrement_index")), values.get("notional_currency"),
                values.get("protection_pct"), values.get("cap_pct"),
                values.get("participation_rate"), values.get("coupon_rate"),
                values.get("coupon_barrier_pct"), values.get("autocall_barrier_pct"),
                values.get("autocall_frequency"), values.get("has_no_call_period"),
                values.get("no_call_months"), values.get("initial_valuation_date"),
                values.get("final_valuation_date"), values.get("tenor_years"),
                json.dumps(field_status), confidence, char_start, char_end,
            )
            note_terms_id = str(note_terms_id)

            # Only a checksum-valid CUSIP is attached as an identifier. A
            # mistyped one would silently merge two unrelated notes onto one
            # security the next time _resolve_security looks it up.
            if cusip and cusip_checksum(str(cusip))[0]:
                await _upsert_cusip(conn, security_id, str(cusip).strip().upper())

            await _write_underlyings(conn, security_id, underlyings)

        # Both hazard answers are recorded OUTSIDE the insert transaction so a
        # logging failure can never roll back a good extraction.
        for key, detail in disagreements.items():
            try:
                await log_hazard_disagreement(
                    conn,
                    note_terms_id=note_terms_id,
                    field_name=key,
                    primary_value=detail["primary"],
                    primary_model=detail["primary_model"],
                    secondary_value=detail["secondary"],
                    secondary_model=detail["secondary_model"],
                )
            except Exception as exc:  # noqa: BLE001 — never lose the row over a log
                print(
                    f"[note_terms_extraction] filing {filing_id}: failed to record "
                    f"hazard disagreement on {key}: {type(exc).__name__}: {exc}"
                )

    result.ok = True
    result.note_terms_id = note_terms_id
    result.global_security_id = str(security_id)
    result.terms_status = derived_status
    result.field_status = field_status
    return result


async def _last_ensemble_model_used(pool) -> str | None:
    """Which model the most recent hazard-ensemble call actually ran on.

    THIS GUARD EXISTS BECAUSE THE ENSEMBLE CAN SILENTLY COLLAPSE. The platform
    fallback chain is ``["claude-haiku-4-5-20251001"]`` — the same model as the
    primary. So if Sonnet is unreachable, ``call_claude_json`` transparently
    retries on Haiku and returns a perfectly good answer, and the "two model"
    ensemble becomes one model agreeing with itself. It would report 100%
    agreement and upgrade every row in the run to ``high`` confidence while
    having checked nothing at all.

    An outage must not be able to pass this gate vacuously, so the model that
    actually served the call is read back from ``ai_decision_log`` and compared
    to the primary. Assumes the run is sequential, which the bounded runner is;
    under concurrency this would need the call to return its own model id.
    """
    async with pool.acquire() as conn:
        return await conn.fetchval(
            """
            SELECT model_used FROM ai_decision_log
            WHERE task_type = 'note_terms_hazard_ensemble'
            ORDER BY created_at DESC LIMIT 1
            """
        )


async def _resolve_security(conn, *, cusip, name: str, filing_id: str) -> str:
    """Find or create the ``securities_global`` row this filing's terms hang off.

    CUSIP is the natural key when present, and finding it matters: an FWP and the
    424B2 that prices it describe the SAME security, and the versioned terms
    model only works if both rows point at one ``global_security_id``. Without a
    CUSIP there is nothing reliable to join on, so a filing-scoped security is
    created and the two versions will not be linked — an honest limitation of
    preliminary filings that state no CUSIP, not something to paper over with a
    name match.
    """
    if cusip:
        normalised = str(cusip).strip().upper()
        found = await conn.fetchval(
            f"""
            SELECT global_security_id FROM {IDENTIFIERS_TABLE}
            WHERE id_type = 'cusip' AND id_value = $1 AND valid_to IS NULL
            LIMIT 1
            """,
            normalised,
        )
        if found:
            return str(found)

    reused = await conn.fetchval(
        f"""
        SELECT global_security_id FROM {TERMS_TABLE}
        WHERE reference_filing_id = $1::uuid ORDER BY valid_from DESC LIMIT 1
        """,
        filing_id,
    )
    if reused:
        return str(reused)

    return str(await conn.fetchval(
        f"""
        INSERT INTO {SECURITIES_TABLE} (name, security_type, price_coverage)
        VALUES ($1, 'structured_note', 'unknown')
        RETURNING id
        """,
        name,
    ))


async def _upsert_cusip(conn, security_id: str, cusip: str) -> None:
    """Attach a checksum-valid CUSIP to the security, once."""
    exists = await conn.fetchval(
        f"""
        SELECT 1 FROM {IDENTIFIERS_TABLE}
        WHERE global_security_id = $1::uuid AND id_type = 'cusip'
          AND id_value = $2 AND valid_to IS NULL
        """,
        str(security_id), cusip,
    )
    if exists:
        return
    await conn.execute(
        f"""
        INSERT INTO {IDENTIFIERS_TABLE} (global_security_id, id_type, id_value, is_primary)
        VALUES ($1::uuid, 'cusip', $2, true)
        """,
        str(security_id), cusip,
    )


async def _write_underlyings(conn, security_id: str, underlyings: list[str]) -> list[str]:
    """Write one UNRESOLVED edge per underlying mention. No resolution here."""
    written: list[str] = []
    for raw in underlyings:
        exists = await conn.fetchval(
            f"""
            SELECT id FROM {RELATIONSHIPS_TABLE}
            WHERE from_global_security_id = $1::uuid AND raw_underlying_text = $2
              AND relationship_type = 'underlying_of' AND valid_to IS NULL
            """,
            str(security_id), raw,
        )
        if exists:
            written.append(str(exists))
            continue
        new_id = await conn.fetchval(
            f"""
            INSERT INTO {RELATIONSHIPS_TABLE}
                (from_global_security_id, to_global_security_id, raw_underlying_text,
                 link_state, relationship_type, resolution_notes)
            VALUES ($1::uuid, NULL, $2, 'unresolved', 'underlying_of', $3)
            RETURNING id
            """,
            str(security_id), raw,
            "extracted verbatim; resolution to a global_security_id is a later sprint",
        )
        written.append(str(new_id))
    return written


async def extract_underlying_mentions(
    filing_id, pool, *, global_security_id: str | None = None,
) -> list[str]:
    """Pull the raw underlying-reference strings out of one filing.

    Returns them verbatim — "the Common Stock of NVIDIA Corporation", not
    "NVDA". RESOLVING those strings to a ``global_security_id`` is explicitly not
    this sprint's job; the strings are the deliverable.

    When ``global_security_id`` is supplied, each mention is also written as a
    ``securities_global_relationships`` row with ``link_state='unresolved'``,
    ``to_global_security_id`` NULL. It is required to write, not optional, because
    ``from_global_security_id`` is NOT NULL — there is no such thing as a dangling
    edge in this schema. Called without it, this function reads only.
    """
    filing_id = str(filing_id)
    async with pool.acquire() as conn:
        filing = await conn.fetchrow(
            f"SELECT filer_name, form_type, extracted_text FROM {FILINGS_TABLE} WHERE id = $1::uuid",
            filing_id,
        )
    if filing is None or not (filing["extracted_text"] or "").strip():
        return []

    window, _ = select_window(filing["extracted_text"])
    payload = await call_claude_json(
        "You identify the underlying reference asset(s) of a structured note. "
        "Return ONE JSON object, no prose, no markdown fences: "
        '{"underlyings": ["...", "..."]}. Copy each reference EXACTLY as the '
        "document names it. Do not normalise it, do not abbreviate it, do not "
        "substitute a ticker symbol, and do not resolve it to a company. If the "
        "note is linked to the worst of several, list every one of them.",
        f"Document: SEC form {filing['form_type']} filed by {filing['filer_name']}.\n\n"
        f"FILING TEXT\n───────────\n{window}",
        max_tokens=600,
        org_id=None,
        model_key=DEFAULT_MODEL_KEY,
        task_type="note_terms_underlyings",
    )
    if not isinstance(payload, dict):
        return []

    mentions = [
        str(u).strip() for u in (payload.get("underlyings") or [])
        if isinstance(u, (str, int, float)) and str(u).strip()
    ][:MAX_UNDERLYINGS]

    if global_security_id and mentions:
        async with pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
                await _write_underlyings(conn, str(global_security_id), mentions)

    return mentions


async def filings_with_terms_extracted(conn) -> set[str]:
    """Filing ids that already have current terms — the DERIVED progress state.

    This exists because ``reference_filings.extraction_status`` is NOT used to
    track term extraction (see the module docstring). Progress is a join, not a
    second meaning bolted onto a column that already has one.
    """
    rows = await conn.fetch(
        f"""
        SELECT DISTINCT reference_filing_id FROM {TERMS_TABLE}
        WHERE reference_filing_id IS NOT NULL AND valid_to IS NULL AND system_to IS NULL
        """
    )
    return {str(r["reference_filing_id"]) for r in rows}
