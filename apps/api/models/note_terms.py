"""Data shapes for the structured-note payoff DSL.

SCOPE
──────────────────────────────────────────────────────────────────────────────
Shapes ONLY. This module contains no extraction logic, makes no LLM calls, and
reads no filing text. It mirrors two tables:

    portfolio.securities_global_note_terms  -> NoteTerms
    portfolio.note_terms_field_registry     -> NoteTermsFieldRegistryEntry

Extraction — turning a 424B2 or FWP into a NoteTerms row — is the next sprint.

TERMS ARE VERSIONED, NOT 1:1
──────────────────────────────────────────────────────────────────────────────
One ``global_security_id`` has MANY NoteTerms rows over its life: preliminary
terms from the FWP, final terms from the 424B2 that priced it, occasionally a
restated 424B2. Do not write code that assumes ``one security -> one terms
row``; always select by ``(global_security_id, terms_status)`` or take the
latest current row explicitly. The gap between preliminary and final terms — a
barrier that got worse at pricing — is a signal the comparison model exists to
surface, so it is preserved rather than overwritten.

DECIMAL, NEVER FLOAT
──────────────────────────────────────────────────────────────────────────────
Every monetary or percentage value is ``Decimal``, matching ``numeric`` in
Postgres. Binary floats cannot represent a 0.05 coupon exactly, and these
values are compared, differenced, and displayed to members. There is no float
anywhere terms touch money.

UNDERLYINGS LIVE ELSEWHERE
──────────────────────────────────────────────────────────────────────────────
NoteTerms carries no underlying references. A note's underlyings hang off
``portfolio.securities_global_relationships`` with
``relationship_type='underlying_of'`` and a ``link_state`` of
resolved/unresolved/ambiguous. A worst-of basket has several underlyings and
some may never resolve to a security row, which no direct FK could express.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Mapping

# ── Controlled vocabularies — kept in lockstep with the CHECK constraints in
#    docs/notetermsdsl_part1.sql. Changing one without the other is a bug. ───

TERMS_STATUSES: frozenset[str] = frozenset({"preliminary", "final", "restated"})

PRODUCT_ARCHETYPES: frozenset[str] = frozenset({
    "buffered_note",
    "autocallable",
    "reverse_convertible",
    "digital",
    "principal_protected",
    "other",
})

PROTECTION_TYPES: frozenset[str] = frozenset({"buffer", "floor", "none"})

BASKET_TYPES: frozenset[str] = frozenset({"single", "basket", "worst_of"})

RETURN_BASES: frozenset[str] = frozenset({"price", "total_return"})

AUTOCALL_FREQUENCIES: frozenset[str] = frozenset({
    "monthly", "quarterly", "annual", "none",
})

EXTRACTION_CONFIDENCES: frozenset[str] = frozenset({"high", "needs_review", "low"})

REGISTRY_DATA_TYPES: frozenset[str] = frozenset({"numeric", "text", "boolean", "date"})

# ── The four-state model ──────────────────────────────────────────────────────
# A NULL term is three different facts wearing one hat, and they must not
# collapse:
#
#   extracted         the value in the row came off the filing and is trusted
#   not_applicable    the field is meaningless for this product — a
#                     principal-protected note has no coupon barrier. NULL here
#                     is correct and final.
#   extraction_failed the field SHOULD have a value and we could not get one.
#                     NULL here is a defect to be worked, not an answer.
#   not_in_template   the filing genuinely does not state this term. Different
#                     from a failure: nothing was missed, there is nothing to
#                     find, and re-running extraction will not help.
FIELD_STATES: frozenset[str] = frozenset({
    "extracted",
    "not_applicable",
    "extraction_failed",
    "not_in_template",
})

# The six fields where a misread is catastrophic AND arithmetically clean — the
# wrong answer is as plausible as the right one, so nothing downstream trips.
# Must match ``hazard_field = true`` in the registry seed exactly.
HAZARD_FIELD_KEYS: frozenset[str] = frozenset({
    "protection_type",      # buffer absorbs the first N% of loss;
                            # floor caps loss at N%. Opposite payoffs, and
                            # both get marketed as "N% downside protection".
    "basket_type",          # basket = weighted average; worst_of = the single
                            # worst performer drives the payoff.
    "return_basis",         # price return strips dividends; total return keeps
                            # them — compounds over a multi-year tenor.
    "is_decrement_index",   # a fixed annual drag subtracted from the index
                            # level; missing it flatters the note badly.
    "autocall_frequency",   # drives how many call chances exist, hence
                            # expected life.
    "terms_status",         # reading a preliminary FWP as final presents
                            # indicative terms as priced terms.
})


class FieldStatusError(ValueError):
    """A ``field_status`` mapping contained something outside the four states."""


def validate_field_status(field_status: Mapping[str, Any] | None) -> dict[str, str]:
    """Validate a ``field_status`` mapping and return it as a plain dict.

    ``field_status`` maps a term's ``field_key`` to exactly one of
    ``FIELD_STATES``. Every writer of
    ``portfolio.securities_global_note_terms`` must call this before the
    INSERT/UPDATE.

    THIS IS THE ONLY GATE. Postgres cannot practically enforce per-key enum
    values inside a jsonb column — a CHECK would have to iterate the object's
    values, which is not IMMUTABLE-safe — so the constraint is documented in
    the migration and enforced here instead. The database WILL accept an
    invalid state string if this function is bypassed. That limitation is
    stated rather than silently skipped.

    Raises:
        FieldStatusError: on a non-mapping input, a non-string key, a
            non-string value, or any value outside the four states.
    """
    if field_status is None:
        return {}

    if not isinstance(field_status, Mapping):
        raise FieldStatusError(
            f"field_status must be a mapping, got {type(field_status).__name__}"
        )

    validated: dict[str, str] = {}
    for key, value in field_status.items():
        if not isinstance(key, str) or not key:
            raise FieldStatusError(f"field_status keys must be non-empty strings, got {key!r}")
        # Booleans are ints, not strings, but callers reach for True often
        # enough that a bare `not isinstance(value, str)` message is worth
        # spelling out.
        if not isinstance(value, str):
            raise FieldStatusError(
                f"field_status[{key!r}] must be a string, got {type(value).__name__}"
            )
        if value not in FIELD_STATES:
            raise FieldStatusError(
                f"field_status[{key!r}] = {value!r} is not one of "
                f"{sorted(FIELD_STATES)}"
            )
        validated[key] = value

    return validated


@dataclass
class NoteTerms:
    """One VERSION of a structured note's payoff terms.

    Mirrors ``portfolio.securities_global_note_terms``. A security holds one of
    these per (terms_status, reference_filing_id) — a preliminary row and a
    final row coexist by design.
    """

    global_security_id: str
    terms_status: str                                   # TERMS_STATUSES

    id: str | None = None
    reference_filing_id: str | None = None

    # ── Classification ────────────────────────────────────────────────────
    product_archetype: str | None = None                # PRODUCT_ARCHETYPES
    protection_type: str | None = None                  # HAZARD — PROTECTION_TYPES
    basket_type: str | None = None                      # HAZARD — BASKET_TYPES
    return_basis: str | None = None                     # HAZARD — RETURN_BASES
    is_decrement_index: bool = False                    # HAZARD

    # ── Economic terms — Decimal, never float ─────────────────────────────
    notional_currency: str | None = None
    protection_pct: Decimal | None = None               # buffer OR floor depth;
                                                        # meaning set by
                                                        # protection_type
    cap_pct: Decimal | None = None
    participation_rate: Decimal | None = None
    coupon_rate: Decimal | None = None
    coupon_barrier_pct: Decimal | None = None
    autocall_barrier_pct: Decimal | None = None
    autocall_frequency: str | None = None               # HAZARD — AUTOCALL_FREQUENCIES

    # ── Call schedule ─────────────────────────────────────────────────────
    has_no_call_period: bool | None = None
    no_call_months: int | None = None

    # ── Dates / tenor ─────────────────────────────────────────────────────
    initial_valuation_date: date | None = None
    final_valuation_date: date | None = None
    tenor_years: Decimal | None = None

    # ── Per-field extraction state ────────────────────────────────────────
    field_status: dict[str, str] = field(default_factory=dict)

    # ── Extraction provenance ─────────────────────────────────────────────
    extraction_confidence: str | None = None            # EXTRACTION_CONFIDENCES
    source_char_start: int | None = None                # offset into
    source_char_end: int | None = None                  # reference_filings.extracted_text

    # ── Bitemporal — convention copied from portfolio.securities_global ───
    valid_from: datetime | None = None
    valid_to: datetime | None = None
    system_from: datetime | None = None
    system_to: datetime | None = None

    def __post_init__(self) -> None:
        self.field_status = validate_field_status(self.field_status)

    @property
    def is_current(self) -> bool:
        """True when this is the live version — matches the partial indexes."""
        return self.valid_to is None and self.system_to is None

    @property
    def hazard_field_status(self) -> dict[str, str]:
        """The four-state entries for the six hazard fields only.

        A hazard field absent from ``field_status`` is absent from this dict —
        unrecorded, which is itself worth seeing.
        """
        return {
            key: value
            for key, value in self.field_status.items()
            if key in HAZARD_FIELD_KEYS
        }


@dataclass
class NoteTermsFieldRegistryEntry:
    """One governed field. Mirrors ``portfolio.note_terms_field_registry``."""

    field_key: str
    display_label: str
    data_type: str                                      # REGISTRY_DATA_TYPES
    applies_to_archetypes: list[str] | None = None      # None => all archetypes
    hazard_field: bool = False
    created_at: datetime | None = None

    def applies_to(self, product_archetype: str | None) -> bool:
        """Whether this field is meaningful for ``product_archetype``.

        A NULL ``applies_to_archetypes`` means the field applies everywhere.
        An unknown archetype (``None``) is treated as "cannot rule it out" and
        returns True — a field is only declared ``not_applicable`` once the
        archetype is actually known.
        """
        if self.applies_to_archetypes is None:
            return True
        if product_archetype is None:
            return True
        return product_archetype in self.applies_to_archetypes
