"""Deterministic cross-checks on extracted structured-note terms.

WHAT THESE CATCH — AND WHAT THEY DO NOT
──────────────────────────────────────────────────────────────────────────────
Everything in this module is a NUMERICAL or IDENTIFIER check. Each one answers
"is this number self-consistent with the other numbers, or with a fact we
already hold for free". They are cheap, they need no model, and they run both
before and after the LLM extraction pass.

THEY DO NOT COVER THE SIX HAZARD FIELDS. Read that again before trusting this
module as a gate. The hazard fields are:

    protection_type, basket_type, return_basis,
    is_decrement_index, autocall_frequency, terms_status

Those are the fields where a misread is catastrophic AND arithmetically clean.
Reading a ``worst_of`` basket as a ``basket`` does not break a single equation
here — the barrier still multiplies out, the tenor still matches the dates, the
CUSIP still checksums — while changing the note's actual risk completely. The
same is true of buffer-vs-floor, price-vs-total-return, a missed decrement
drag, a quarterly-vs-annual call schedule, and preliminary terms read as final.

So: a filing that passes every function in this module has proved only that its
ARITHMETIC is coherent. The hazard fields are covered by the two-model
disagreement ensemble in ``services/note_terms_extraction.py``, not here. Do not
add a hazard field to this module expecting a deterministic rule to exist for
it; if one existed, the field would not be a hazard field.

CONTRACT
──────────────────────────────────────────────────────────────────────────────
Every validator returns ``(ok: bool, reason: str)``. ``reason`` is always
populated — on success it says what was checked and on failure it says what was
wrong, so a caller can log it verbatim without composing a message.

A validator returns ``ok=True`` when it CANNOT run (a missing input, a CIK
outside the known-issuer map). "Not contradicted" is not the same as "verified",
and the reason string says which one happened. A validator never invents a
failure out of absent data — that would flood every partial extraction with
false needs_review.

Decimal everywhere. No float touches a percentage or a price.
"""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal, InvalidOperation

__all__ = [
    "cusip_checksum",
    "cik_matches_filer",
    "barrier_price_consistent",
    "autocall_le_coupon_barrier",
    "tenor_consistent",
    "run_numeric_validators",
    "ValidatorOutcome",
]


# ── CUSIP ─────────────────────────────────────────────────────────────────────

# The CUSIP character set. Digits are their own value; letters run 10-35; the
# three special characters are defined by the CUSIP standard and do occur.
_CUSIP_SPECIALS = {"*": 36, "@": 37, "#": 38}


def _cusip_char_value(ch: str) -> int | None:
    if ch.isdigit():
        return int(ch)
    if ch.isalpha():
        return ord(ch.upper()) - ord("A") + 10
    return _CUSIP_SPECIALS.get(ch)


def cusip_checksum(cusip: str) -> tuple[bool, str]:
    """Validate a 9-character CUSIP's trailing check digit.

    The standard mod-10 Luhn variant: value each of the first 8 characters
    (digits as themselves, A-Z as 10-35, ``*@#`` as 36-38), double the value at
    every second position, sum the DIGITS of each result, and the check digit is
    ``(10 - sum % 10) % 10``.

    Returns ``(False, ...)`` for anything that is not a well-formed 9-character
    CUSIP, including None and the empty string — an unparseable identifier is a
    failed check, not an absent one, because the extractor either found a CUSIP
    or it did not, and a malformed one means it found the wrong thing.
    """
    if not cusip or not isinstance(cusip, str):
        return False, "no CUSIP supplied"

    raw = cusip.strip().upper().replace("-", "").replace(" ", "")
    if len(raw) != 9:
        return False, f"CUSIP {raw!r} is {len(raw)} characters, expected 9"

    if not raw[8].isdigit():
        return False, f"CUSIP {raw!r} check digit {raw[8]!r} is not a digit"

    total = 0
    for position, ch in enumerate(raw[:8]):
        value = _cusip_char_value(ch)
        if value is None:
            return False, f"CUSIP {raw!r} has invalid character {ch!r} at position {position + 1}"
        # 0-indexed odd positions are the 1-indexed even ones — those double.
        if position % 2 == 1:
            value *= 2
        total += value // 10 + value % 10

    expected = (10 - (total % 10)) % 10
    actual = int(raw[8])
    if expected != actual:
        return False, (
            f"CUSIP {raw!r} check digit is {actual}, computed {expected} — "
            f"the identifier is mistyped or misread"
        )
    return True, f"CUSIP {raw!r} check digit {actual} is correct"


# ── Issuer vs CIK ─────────────────────────────────────────────────────────────

# EDGAR's CIK is free ground truth: the filing was submitted BY a registrant, so
# the issuer the model reads out of the document body can be checked against the
# registrant we already know. The map below is seeded from the distinct
# (cik, filer_name) pairs actually present in portfolio.reference_filings —
# these are the structured-note shelf issuers, a small and slow-moving set.
#
# Values are distinctive lowercase STEMS. A match means any stem appears in the
# normalised extracted issuer string. Stems are chosen to survive the real
# variation between the EDGAR registrant name and the name printed in the
# document ("JPMorgan Chase Financial Company LLC" vs "JPMorgan Chase Financial
# Co. LLC"), while still separating the banks from each other.
_ISSUER_CIK_STEMS: dict[str, tuple[str, ...]] = {
    "1000275": ("royal bank of canada", "rbc"),
    "1114446": ("ubs",),
    "1419828": ("gs finance", "goldman sachs"),
    "1665650": ("jpmorgan chase financial", "jp morgan chase financial"),
    "1666268": ("morgan stanley finance",),
    "1682472": ("bofa finance", "bank of america"),
    "19617": ("jpmorgan chase", "jp morgan chase", "jpmorgan"),
    "200245": ("citigroup global markets holdings", "citigroup"),
    "312070": ("barclays",),
    "70858": ("bank of america", "bofa"),
    "831001": ("citigroup", "citibank"),
    "83246": ("hsbc",),
    "886982": ("goldman sachs",),
    "895421": ("morgan stanley",),
    "927971": ("bank of montreal", "bmo"),
    "947263": ("toronto dominion", "toronto-dominion", "td bank"),
    "9631": ("bank of nova scotia", "scotiabank"),
}

_PUNCT = re.compile(r"[^a-z0-9 ]+")
_SPACES = re.compile(r"\s+")


def _normalise_issuer(name: str) -> str:
    lowered = _PUNCT.sub(" ", (name or "").lower())
    return _SPACES.sub(" ", lowered).strip()


def cik_matches_filer(extracted_issuer: str | None, filing_cik: str | None) -> tuple[bool, str]:
    """Check the issuer read out of the document against the filing's CIK.

    ``filing_cik`` comes from ``portfolio.reference_filings.cik`` — it was never
    guessed, so it is a free control on the one field the model is most likely
    to lift from the wrong paragraph (a 424B2 names the guarantor, the calculation
    agent, and several index sponsors alongside the actual issuer).

    Returns ``ok=True`` with an explicit "not verified" reason when the CIK is
    outside the known-issuer map — an unrecognised shelf issuer is a gap in the
    map, not evidence the extraction is wrong.
    """
    if not extracted_issuer or not str(extracted_issuer).strip():
        return False, "no issuer was extracted from the filing"
    if not filing_cik or not str(filing_cik).strip():
        return True, "filing has no CIK to check against — not verified"

    cik = str(filing_cik).strip().lstrip("0") or "0"
    stems = _ISSUER_CIK_STEMS.get(cik)
    if stems is None:
        return True, (
            f"CIK {cik} is not in the known-issuer map — issuer "
            f"{extracted_issuer!r} not contradicted, but not verified either"
        )

    normalised = _normalise_issuer(str(extracted_issuer))
    for stem in stems:
        if stem in normalised:
            return True, f"issuer {extracted_issuer!r} matches CIK {cik} on stem {stem!r}"

    return False, (
        f"issuer {extracted_issuer!r} does not match CIK {cik} "
        f"(expected one of {list(stems)}) — the wrong party was read as the issuer"
    )


# ── Barrier arithmetic ────────────────────────────────────────────────────────


def _to_decimal(value) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None


def barrier_price_consistent(
    barrier_pct,
    initial_level,
    barrier_price,
    tolerance: Decimal = Decimal("0.01"),
) -> tuple[bool, str]:
    """Check that ``barrier_pct * initial_level`` reproduces ``barrier_price``.

    Term sheets state the barrier three ways at once — as a percentage, as the
    initial level, and as the absolute price that percentage lands on. They must
    multiply out. When they do not, one of the three was read from the wrong row
    of the table, which is the single most common numeric misread in this corpus.

    ``barrier_pct`` is accepted either as a fraction (``0.70``) or as a
    percentage (``70``). Values above 1 are read as percentages: a barrier above
    100% of initial is not a thing these notes do, so the ambiguity is
    resolvable without asking the caller to normalise first.

    ``tolerance`` is ABSOLUTE, in the same units as ``barrier_price`` — term
    sheets round the printed barrier price to the cent, so an exact equality
    test would fail on correct data.
    """
    pct = _to_decimal(barrier_pct)
    level = _to_decimal(initial_level)
    price = _to_decimal(barrier_price)

    missing = [
        name
        for name, val in (
            ("barrier_pct", pct), ("initial_level", level), ("barrier_price", price),
        )
        if val is None
    ]
    if missing:
        return True, (
            f"cannot check barrier arithmetic — {', '.join(missing)} absent; not verified"
        )
    if level <= 0:
        return False, f"initial_level {level} is not positive"

    fraction = pct / Decimal(100) if pct > 1 else pct
    expected = fraction * level
    delta = abs(expected - price)
    tol = abs(_to_decimal(tolerance) or Decimal("0.01"))

    if delta <= tol:
        return True, (
            f"barrier arithmetic holds: {fraction} x {level} = {expected} "
            f"vs stated {price} (delta {delta} <= {tol})"
        )
    return False, (
        f"barrier arithmetic FAILS: {fraction} x {level} = {expected} but the "
        f"filing states {price} (delta {delta} > {tol}) — one of the three was misread"
    )


# ── Barrier ordering ──────────────────────────────────────────────────────────


def autocall_le_coupon_barrier(coupon_barrier_pct, autocall_barrier_pct) -> tuple[bool, str]:
    """Check ``coupon_barrier <= autocall_barrier`` — a WARNING, not a hard failure.

    In nearly every Phoenix / contingent-coupon autocallable the coupon barrier
    sits at or below the autocall barrier: you get paid the coupon on a wider
    range of outcomes than you get called on. A pair the other way round usually
    means the two barriers were swapped in extraction.

    "Nearly all" is doing real work in that sentence. Step-down autocalls whose
    call level declines each observation genuinely invert this relationship in
    later periods, and low-strike structures exist that break it outright. So a
    violation returns ``ok=False`` with a reason a caller is expected to LOG as a
    warning — the extraction pipeline does not turn this one into
    ``needs_review`` on its own, because doing so would flag a real and
    legitimate structure as an error.

    Both inputs are accepted as fractions or percentages, normalised the same way
    as :func:`barrier_price_consistent`.
    """
    coupon = _to_decimal(coupon_barrier_pct)
    autocall = _to_decimal(autocall_barrier_pct)

    if coupon is None or autocall is None:
        absent = "coupon_barrier_pct" if coupon is None else "autocall_barrier_pct"
        if coupon is None and autocall is None:
            absent = "both barriers"
        return True, f"cannot compare barriers — {absent} absent; not verified"

    coupon_n = coupon / Decimal(100) if coupon > 1 else coupon
    autocall_n = autocall / Decimal(100) if autocall > 1 else autocall

    if coupon_n <= autocall_n:
        return True, (
            f"coupon barrier {coupon_n} <= autocall barrier {autocall_n} — "
            f"the usual Phoenix ordering"
        )
    return False, (
        f"WARNING coupon barrier {coupon_n} > autocall barrier {autocall_n}. "
        f"Usually means the two were swapped, but step-down and low-strike "
        f"autocalls legitimately invert this — treat as a warning, not an error"
    )


# ── Tenor vs dates ────────────────────────────────────────────────────────────

_DAYS_PER_YEAR = Decimal("365.25")


def tenor_consistent(
    initial_valuation_date: date | None,
    final_valuation_date: date | None,
    tenor_years,
    tolerance_days: int = 10,
) -> tuple[bool, str]:
    """Check the stated tenor against the gap between the two valuation dates.

    A term sheet states the tenor in prose ("2.5 Year Market-Linked Securities")
    and the valuation dates in the table. They are independently misread — the
    prose is a marketing title and the dates are easy to lift from the wrong row
    (pricing date vs initial valuation date vs issue date, which differ by days).
    Checking one against the other catches both.

    ``tolerance_days`` is the allowed gap in DAYS, defaulting to 10: the
    difference between a "2.5 year" note's title and its actual 913-day life is
    normal, but a month is not.
    """
    years = _to_decimal(tenor_years)

    missing = [
        name
        for name, val in (
            ("initial_valuation_date", initial_valuation_date),
            ("final_valuation_date", final_valuation_date),
            ("tenor_years", years),
        )
        if val is None
    ]
    if missing:
        return True, f"cannot check tenor — {', '.join(missing)} absent; not verified"

    if not isinstance(initial_valuation_date, date) or not isinstance(final_valuation_date, date):
        return False, "valuation dates must be date objects"

    if final_valuation_date <= initial_valuation_date:
        return False, (
            f"final_valuation_date {final_valuation_date} is not after "
            f"initial_valuation_date {initial_valuation_date}"
        )

    actual_days = Decimal((final_valuation_date - initial_valuation_date).days)
    implied_days = years * _DAYS_PER_YEAR
    delta = abs(actual_days - implied_days)
    tol = Decimal(abs(int(tolerance_days)))

    if delta <= tol:
        return True, (
            f"tenor holds: {initial_valuation_date} to {final_valuation_date} is "
            f"{actual_days} days, stated {years}y implies {implied_days} "
            f"(delta {delta} <= {tol} days)"
        )
    return False, (
        f"tenor FAILS: {initial_valuation_date} to {final_valuation_date} is "
        f"{actual_days} days but stated tenor {years}y implies {implied_days} days "
        f"(delta {delta} > {tol}) — the tenor or one of the dates was misread"
    )


# ── Orchestration ─────────────────────────────────────────────────────────────


class ValidatorOutcome:
    """The result of running every numeric validator over one extraction.

    ``failures`` drives ``extraction_confidence='needs_review'``. ``warnings``
    deliberately does not — see :func:`autocall_le_coupon_barrier`.
    """

    __slots__ = ("checks", "failures", "warnings")

    def __init__(self) -> None:
        self.checks: list[tuple[str, bool, str]] = []
        self.failures: list[str] = []
        self.warnings: list[str] = []

    def record(self, name: str, ok: bool, reason: str, *, warn_only: bool = False) -> None:
        self.checks.append((name, ok, reason))
        if ok:
            return
        (self.warnings if warn_only else self.failures).append(f"{name}: {reason}")

    @property
    def passed(self) -> bool:
        return not self.failures

    def summary(self) -> str:
        return "; ".join(f"{n}={'ok' if ok else 'FAIL'}" for n, ok, _ in self.checks)


def run_numeric_validators(
    *,
    cusip: str | None,
    extracted_issuer: str | None,
    filing_cik: str | None,
    barrier_pct=None,
    initial_level=None,
    barrier_price=None,
    coupon_barrier_pct=None,
    autocall_barrier_pct=None,
    initial_valuation_date: date | None = None,
    final_valuation_date: date | None = None,
    tenor_years=None,
) -> ValidatorOutcome:
    """Run all five checks and collect the outcome.

    The CUSIP check is SKIPPED (not failed) when no CUSIP was extracted: plenty
    of FWPs state terms before a CUSIP is assigned, and failing those would
    flag the whole preliminary population. A CUSIP that IS present and does not
    checksum is a hard failure.

    Remember what is not in here: nothing below touches a hazard field. See the
    module docstring.
    """
    outcome = ValidatorOutcome()

    if cusip and str(cusip).strip():
        outcome.record("cusip_checksum", *cusip_checksum(str(cusip)))
    else:
        outcome.record(
            "cusip_checksum", True,
            "no CUSIP stated in the filing — skipped, not failed",
        )

    outcome.record("cik_matches_filer", *cik_matches_filer(extracted_issuer, filing_cik))
    outcome.record(
        "barrier_price_consistent",
        *barrier_price_consistent(barrier_pct, initial_level, barrier_price),
    )
    outcome.record(
        "autocall_le_coupon_barrier",
        *autocall_le_coupon_barrier(coupon_barrier_pct, autocall_barrier_pct),
        warn_only=True,
    )
    outcome.record(
        "tenor_consistent",
        *tenor_consistent(initial_valuation_date, final_valuation_date, tenor_years),
    )
    return outcome
