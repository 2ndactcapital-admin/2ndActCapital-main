"""SPV fee TERMS — the source of truth for what an SPV actually charges. fee42.

``spvs.mgmt_fee_pct`` and ``spvs.carry_pct`` are two flat scalars. They cannot
say "2% on committed capital for the first three years, then 1.5% on invested
cost, capped at ten years, with an 8% soft hurdle and a 100% catch-up, offset
against the advisory fee, except for the investor who signed a side letter."
``spv_fee_terms`` can. This module reads and writes it.

This sprint is ADDITIVE. Nothing here touches, rewrites, or deprecates the two
flat columns; ``routers/spv.py`` keeps reading them exactly as it did.


WHAT TASK 1 MEASURED, AND WHERE IT CONTRADICTED THE BRIEF
──────────────────────────────────────────────────────────────────────────────

**[F1] fee36 does not read ``spvs.mgmt_fee_pct``. It never did.**
The brief said this sprint should re-point fee36's SPV_MGMT_FEE_OFFSET basis
resolution from the flat column onto ``spv_fee_terms``. There is nothing to
re-point. ``fee_run_inputs.resolve_credit_basis`` resolves the basis as the sum
of the entity's own ``spv_transaction_allocations.allocated_amount`` across
POSTED ``call_mgmt_fee`` transactions dated inside the period — an ACTUAL
BILLED AMOUNT, not a rate. A rate is exactly the wrong thing to credit against:
it would require this module to recompute the fee that the SPV already charged
and posted, and then credit the recomputation rather than the charge.

So ``spv_fee_terms`` is the source of truth for what the SPV *will* charge, and
``spv_transaction_allocations`` remains the source of truth for what it *did*
charge. Those are different questions and they must not be conflated. No fee36
code is modified by this sprint.

**[F2] The deployed uniqueness is on the SYSTEM axis, so restatement must be
too.** ``spv_fee_terms_active_uq`` is ``UNIQUE (org_id, spv_id, class_label)
NULLS NOT DISTINCT WHERE system_to IS NULL``. Closing a row on the VALID axis
alone — ``SET valid_to = now()`` per the usual Rule 3 restatement — leaves
``system_to`` NULL, so the replacement row collides with the row it replaces.
:func:`create_terms` therefore archives on the system axis. Reads still use the
house ``_current`` predicate (both axes NULL), which is a strict subset and
stays correct either way.

Note also what ``NULLS NOT DISTINCT`` buys: the whole-fund row (``class_label``
NULL) is genuinely unique. Under Postgres' default NULLS DISTINCT it would not
have been, and a fund could silently carry two contradictory whole-fund term
sets.

**[F3] ``spv_fee_side_letters`` has ZERO check constraints and no uniqueness.**
Measured live: five foreign keys, a primary key, and nothing else. ``overrides``
is unconstrained jsonb — the database will accept ``{"mgmt_fee_pct": "banana"}``
or ``{"carry_pct": 0.2}`` with no ``hurdle_type``, which is precisely the
invariant ``spv_fee_terms_carry_requires_hurdle_type`` enforces one table over.
And with no unique index, two overlapping active side letters for the same
(spv, entity) are reachable.

Both holes are closed HERE, in the application layer, and both are reported as
real gaps rather than papered over:

  * :func:`resolve_terms_for_entity` validates the MERGED row, not the override
    delta. An override that is individually harmless can still produce a
    resolved term set that the base table's own CHECKs would have refused.
  * Two active side letters raise :class:`AmbiguousSideLetterError` rather than
    picking one. Picking one silently bills an investor under terms nobody
    approved.

**[F4] ``fee_credits`` has no application write path at all.** fee34 shipped
the table, the CHECK vocabulary and ``fee_validation.validate_credit`` (pure),
but no service function and no router ever inserts a credit — only verify
scripts do, directly. So "reuse the existing mechanism" cannot mean "call the
existing creator"; there is none. :func:`ensure_advisory_fee_offset_credit`
reuses the existing TABLE, the existing ``SPV_MGMT_FEE_OFFSET`` vocabulary and
the existing ``validate_credit`` rules, and does not invent a second offset
concept. See its docstring for the one thing it deliberately refuses to do.

**[F5] mgmt_fee_basis: two of the four values are computable today.**
Measured against the deployed schema:

  ``COMMITTED``      ✅ ``spv_subscriptions.commitment_amount`` (NOT NULL).
  ``FUNDED``         ✅ ``spv_subscriptions.funded_amount`` (NOT NULL DEFAULT 0
                        — and every deployed row is 0.00 today, so a FUNDED
                        fee currently bills nothing. That is arithmetic, not a
                        bug, but it will surprise anyone who sets it.)
  ``NAV``            ⚠️ The PATH exists and is empty. Portfolio Phase D's
                        ``portfolio.spv_derived_positions`` resolves an SPV
                        interest's mark through
                        ``portfolio.assets.internal_spv_id`` →
                        ``portfolio.valuations``. Live: zero assets carry
                        ``internal_spv_id`` and ``portfolio.valuations`` has
                        zero rows, so NAV resolves to nothing for every SPV.
  ``INVESTED_COST``  ❌ No column holds it. It would have to be summed from
                        ``spv_transactions``, whose ``txn_type`` has NO check
                        constraint (free text — measured), and whose only
                        deployed purchase row is ``status='draft'``.

This module stores and resolves the basis. It deliberately does NOT compute the
basis AMOUNT: two of the four cannot be computed honestly, and a resolver that
silently returned zero for those two would understate a fee by 100%.


THE CALCULATION LAYER IS PURE, AND ANSWERS IN SEGMENTS
──────────────────────────────────────────────────────────────────────────────
:func:`schedule_mgmt_fee` takes terms, an inception date and a billing period,
and returns the RATE SEGMENTS covering that period. It touches no database, in
the same discipline as fee35's engine.

Segments, not a single rate, because both of the things this sprint is asked to
get right happen MID-PERIOD:

  * A step-down at the third anniversary lands inside Q1 if the SPV closed in
    February. Returning "the rate for the quarter" forces the caller to pick
    the old rate (overcharging) or the new one (undercharging). Returning two
    dated segments lets them prorate, which is the only correct answer.
  * A ten-year term limit expires mid-quarter for exactly the same reason. The
    final period is a PARTIAL accrual, not a whole one and not a skipped one.

Both boundaries are half-open in the same direction and this is stated once so
it is not re-derived per call site: **an anniversary belongs to the period it
BEGINS.** The step declared ``after_year: 3`` takes effect ON the third
anniversary; the day before it still bills the old rate. A ``term_years`` of 10
stops accrual ON the tenth anniversary; the last billable day is the day
before. Every off-by-one in fee arithmetic is one of these two, so both are
asserted at the exact boundary date AND the day either side of it in
``verify_fee42.py``.

Anniversaries are real calendar anniversaries, not ``n * 365`` days. Over a ten
year term the naive form drifts by more than two days, which is enough to move
a step-down across a quarter end.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable, Mapping, Sequence

from services.fee_validation import validate_credit
from services.portfolio_assets import _OrgWrite, _require_org

TABLE_TERMS = "public.spv_fee_terms"
TABLE_SIDE_LETTERS = "public.spv_fee_side_letters"
TABLE_SPVS = "public.spvs"
TABLE_SUBSCRIPTIONS = "public.spv_subscriptions"
TABLE_CREDITS = "public.fee_credits"

#: ``spv_fee_terms_mgmt_fee_basis_check``, read from the deployed constraint.
MGMT_FEE_BASES = ("COMMITTED", "FUNDED", "NAV", "INVESTED_COST")

#: Of those four, the ones whose amount is actually derivable from the deployed
#: schema today. See [F5]. Published so a caller can warn BEFORE a run rather
#: than discover it as a zero on an invoice.
COMPUTABLE_MGMT_FEE_BASES = ("COMMITTED", "FUNDED")

#: ``spv_fee_terms_mgmt_fee_frequency_check``.
MGMT_FEE_FREQUENCIES = ("MONTHLY", "QUARTERLY", "ANNUAL")

#: ``spv_fee_terms_hurdle_type_check``.
HURDLE_TYPES = ("HARD", "SOFT", "NONE")

#: ``spv_fee_terms_carry_basis_check``.
CARRY_BASES = ("DEAL_BY_DEAL", "WHOLE_FUND")

#: ``fee_credits_source_check`` — the one credit_source this module writes.
CREDIT_SOURCE_SPV_OFFSET = "SPV_MGMT_FEE_OFFSET"

#: The economic fields. These are what a side letter may override and what
#: :func:`create_terms` writes. Identity, scope and temporal columns are
#: deliberately absent: a side letter that could rewrite ``spv_id`` would be
#: pointing an investor at a different fund's economics.
TERM_FIELDS = (
    "mgmt_fee_pct",
    "mgmt_fee_basis",
    "mgmt_fee_frequency",
    "mgmt_fee_term_years",
    "mgmt_fee_step_down",
    "organizational_cost_cap",
    "admin_fee_flat",
    "placement_fee_pct",
    "carry_pct",
    "hurdle_pct",
    "hurdle_type",
    "catchup_pct",
    "carry_basis",
    "clawback_applies",
    "offsets_advisory_fee",
)

#: Every field a side letter may carry. Same set — an investor's side letter can
#: move any economic term, and nothing else.
OVERRIDABLE_FIELDS = TERM_FIELDS

_DECIMAL_FIELDS = (
    "mgmt_fee_pct", "mgmt_fee_term_years", "organizational_cost_cap",
    "admin_fee_flat", "placement_fee_pct", "carry_pct", "hurdle_pct",
    "catchup_pct",
)
_BOOL_FIELDS = ("clawback_applies", "offsets_advisory_fee")

#: One step of a step-down ladder. No other key is accepted — a typo'd
#: ``"after_years"`` that was silently ignored would leave the fund billing its
#: day-one rate forever, and nothing downstream would ever notice.
STEP_KEYS = ("after_year", "pct")


# ═══════════════════════════════════════════════════════════════════════════
# Errors — every one names the field the caller has to fix
# ═══════════════════════════════════════════════════════════════════════════


class SpvFeeTermsError(ValueError):
    """A terms read or write was refused for a reason the caller can fix."""

    def __init__(self, message: str, *, field: str | None = None, **context: Any):
        super().__init__(message)
        self.field = field
        self.context = context


class TermsNotFoundError(SpvFeeTermsError):
    """No class-specific and no whole-fund terms are in force for this SPV."""


class VocabularyError(SpvFeeTermsError):
    """A value is outside the deployed CHECK's vocabulary."""


class HurdleTypeRequiredError(SpvFeeTermsError):
    """``carry_pct`` is set and ``hurdle_type`` is not.

    Mirrors ``spv_fee_terms_carry_requires_hurdle_type`` in the application
    layer so the operator is told WHICH field is missing. The raw constraint
    violation names the constraint, which tells them nothing actionable.
    """


class StepDownError(SpvFeeTermsError):
    """The step-down ladder is not a usable ladder."""


class InceptionRequiredError(SpvFeeTermsError):
    """A step-down or term limit was asked for with nothing to measure from."""


class FractionalYearError(SpvFeeTermsError):
    """A year count that is not a whole number of months.

    ``mgmt_fee_term_years`` is ``numeric(5,2)``, so ``2.10`` is storable and
    means 2.1 years — 25.2 months. There is no defensible rounding: down
    truncates a fee the fund is owed, up charges one it is not. Refused rather
    than guessed.
    """


class AmbiguousSideLetterError(SpvFeeTermsError):
    """Two side letters are in force for the same investor and SPV.

    ``spv_fee_side_letters`` has no unique index (measured — see [F3]), so this
    is reachable. Choosing one would bill the investor under terms nobody
    approved and would do it silently.
    """


class OffsetNotAuthorisedError(SpvFeeTermsError):
    """A management-fee offset credit was asked for on terms that forbid it."""


# ═══════════════════════════════════════════════════════════════════════════
# Coercion
# ═══════════════════════════════════════════════════════════════════════════


def _dec(value: Any, *, field: str) -> Decimal | None:
    """Decimal or nothing. A float is refused, never silently accepted.

    ``0.1`` is not one tenth. On a rate that multiplies a nine-figure
    commitment the difference is real money, so the caller is made to say what
    they meant rather than have it inferred from a binary float.
    """
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value
    if isinstance(value, bool) or isinstance(value, float):
        raise SpvFeeTermsError(
            f"{field}={value!r} is a {type(value).__name__}. Fee terms are "
            f"Decimal — pass Decimal('{value}') or the string '{value}', so "
            f"the value that is stored is the value that was meant",
            field=field,
        )
    if isinstance(value, int):
        return Decimal(value)
    try:
        return Decimal(str(value).strip())
    except (InvalidOperation, ValueError) as exc:
        raise SpvFeeTermsError(
            f"{field}={value!r} is not a number", field=field
        ) from exc


def _dec_json(value: Any, *, field: str) -> Decimal | None:
    """Decimal from a value that came out of a ``jsonb`` column.

    Deliberately more permissive than :func:`_dec`. asyncpg decodes a JSON
    number to a Python ``int``/``float``, so refusing floats here — correct at
    the Python API boundary, where the caller chose the type — would make
    ``mgmt_fee_step_down`` and ``spv_fee_side_letters.overrides`` unreadable for
    any document written the obvious way.

    The conversion goes through ``str()``, never ``Decimal(float)``.
    ``Decimal(0.015)`` is 0.01499999999999999944488848768742172978818416595458984375;
    ``Decimal(str(0.015))`` is exactly ``0.015``, because ``repr`` of a float is
    its shortest round-tripping decimal form — which is the number the person
    who wrote the JSON meant.
    """
    if isinstance(value, float):
        return Decimal(repr(value))
    return _dec(value, field=field)


def _bool(value: Any, *, field: str) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    raise SpvFeeTermsError(
        f"{field}={value!r} must be a boolean, not {type(value).__name__}",
        field=field,
    )


def _as_date(value: Any, *, field: str) -> date:
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError as exc:
            raise SpvFeeTermsError(
                f"{field}={value!r} is not an ISO date", field=field
            ) from exc
    raise SpvFeeTermsError(f"{field}={value!r} is not a date", field=field)


# ═══════════════════════════════════════════════════════════════════════════
# Calendar arithmetic
# ═══════════════════════════════════════════════════════════════════════════


def add_years(anchor: date, years: int) -> date:
    """The ``years``-th anniversary of ``anchor``, on the calendar.

    Feb 29 is clamped to Feb 28 in a non-leap year — the only ambiguous case,
    and the one that ``anchor + timedelta(days=365 * years)`` gets wrong by a
    day per leap year, compounding to more than two days over a ten-year fund
    term. Two days is enough to move a step-down across a quarter end and bill
    a whole quarter at the wrong rate.
    """
    year = anchor.year + years
    try:
        return anchor.replace(year=year)
    except ValueError:
        return anchor.replace(year=year, day=28)


def add_months(anchor: date, months: int) -> date:
    """``months`` calendar months after ``anchor``, clamped to month end."""
    total = anchor.month - 1 + months
    year = anchor.year + total // 12
    month = total % 12 + 1
    day = anchor.day
    while day > 0:
        try:
            return date(year, month, day)
        except ValueError:
            day -= 1
    raise SpvFeeTermsError(f"cannot add {months} months to {anchor}")


def _years_to_months(years: Decimal, *, field: str) -> int:
    """Whole months, or a refusal. See :class:`FractionalYearError`."""
    months = years * 12
    if months != months.to_integral_value():
        raise FractionalYearError(
            f"{field}={years} is {months} months, which is not a whole number "
            f"of months. Express the term in quarter-years (x.25 / x.5 / x.75) "
            f"or whole years — there is no correct way to round a part month, "
            f"and rounding either way changes what the fund is owed",
            field=field,
        )
    return int(months)


def offset_years(anchor: date, years: Decimal, *, field: str) -> date:
    """``years`` (possibly fractional) after ``anchor``, as whole months."""
    return add_months(anchor, _years_to_months(years, field=field))


# ═══════════════════════════════════════════════════════════════════════════
# The step-down ladder
# ═══════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class MgmtFeeStep:
    """One rung: from the ``after_year``-th anniversary, the rate is ``pct``."""

    after_year: Decimal
    pct: Decimal


def parse_step_down(raw: Any, *, field: str = "mgmt_fee_step_down") -> tuple[MgmtFeeStep, ...]:
    """Validate and order a step-down ladder.

    Accepts the deployed jsonb in exactly one shape::

        [{"after_year": 3, "pct": "0.015"}, {"after_year": 5, "pct": "0.01"}]

    ``after_year`` is the anniversary the rate CHANGES ON, counted from the
    inception date the caller supplies. ``pct`` is the same unit as
    ``mgmt_fee_pct`` — this module never converts between fractions and
    percents, because fee35 already found that ``PCT_OFF`` and ``offset_pct``
    differ by 100x in scale elsewhere in this module and a second silent
    conversion here would be undetectable on an invoice.

    Unknown keys are refused rather than ignored: a ladder written with
    ``"after_years"`` that parsed to an empty ladder would leave the fund
    billing its day-one rate for its whole life, silently.
    """
    if raw is None or raw == []:
        return ()
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise StepDownError(
                f"{field} is not valid JSON: {exc}", field=field
            ) from exc
    if not isinstance(raw, (list, tuple)):
        raise StepDownError(
            f"{field} must be a JSON array of "
            f"{{'after_year': n, 'pct': r}} objects, got "
            f"{type(raw).__name__}",
            field=field,
        )

    steps: list[MgmtFeeStep] = []
    for i, entry in enumerate(raw):
        where = f"{field}[{i}]"
        if not isinstance(entry, Mapping):
            raise StepDownError(
                f"{where} must be an object with keys {list(STEP_KEYS)}, got "
                f"{type(entry).__name__}",
                field=field,
            )
        unknown = set(entry) - set(STEP_KEYS)
        if unknown:
            raise StepDownError(
                f"{where} has unknown key(s) {sorted(unknown)}; a step is "
                f"exactly {list(STEP_KEYS)}. An ignored key here would leave "
                f"the ladder silently flat",
                field=field,
            )
        missing = [k for k in STEP_KEYS if entry.get(k) is None]
        if missing:
            raise StepDownError(
                f"{where} is missing {missing}", field=field
            )
        after_year = _dec_json(entry["after_year"], field=f"{where}.after_year")
        pct = _dec_json(entry["pct"], field=f"{where}.pct")
        if after_year <= 0:
            raise StepDownError(
                f"{where}.after_year={after_year} must be greater than 0. A "
                f"step at year 0 is not a step down, it is the base rate — put "
                f"it in mgmt_fee_pct",
                field=field,
            )
        if pct < 0:
            raise StepDownError(
                f"{where}.pct={pct} is negative", field=field
            )
        steps.append(MgmtFeeStep(after_year=after_year, pct=pct))

    ordered = sorted(steps, key=lambda s: s.after_year)
    for a, b in zip(ordered, ordered[1:]):
        if a.after_year == b.after_year:
            raise StepDownError(
                f"{field} has two steps at after_year={a.after_year} "
                f"({a.pct} and {b.pct}). Which one applies is undefined",
                field=field,
            )
    return tuple(ordered)


# ═══════════════════════════════════════════════════════════════════════════
# The terms themselves
# ═══════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class SpvFeeTerms:
    """One resolved set of economic terms, with its provenance attached.

    ``source`` and ``side_letter_id`` are not decoration. "Why is this investor
    being charged 1.5%?" is the question every fee dispute starts with, and the
    answer — class terms, whole-fund terms, or a named side letter — has to
    survive out of this function rather than be re-derived later from tables
    that may have moved on.
    """

    mgmt_fee_pct: Decimal | None = None
    mgmt_fee_basis: str = "COMMITTED"
    mgmt_fee_frequency: str = "QUARTERLY"
    mgmt_fee_term_years: Decimal | None = None
    mgmt_fee_step_down: Any = None
    organizational_cost_cap: Decimal | None = None
    admin_fee_flat: Decimal | None = None
    placement_fee_pct: Decimal | None = None
    carry_pct: Decimal | None = None
    hurdle_pct: Decimal | None = None
    hurdle_type: str | None = None
    catchup_pct: Decimal | None = None
    carry_basis: str | None = None
    clawback_applies: bool = True
    offsets_advisory_fee: bool = False

    # provenance
    id: str | None = None
    org_id: str | None = None
    spv_id: str | None = None
    class_label: str | None = None
    effective_from: date | None = None
    effective_to: date | None = None
    source: str = "CLASS"
    side_letter_id: str | None = None
    overridden_fields: tuple[str, ...] = ()

    def steps(self) -> tuple[MgmtFeeStep, ...]:
        return parse_step_down(self.mgmt_fee_step_down)

    def economics(self) -> dict[str, Any]:
        """Just the economic fields, for diffing one resolution against another."""
        return {f: getattr(self, f) for f in TERM_FIELDS}


def coerce_terms_fields(
    payload: Mapping[str, Any], *, from_json: bool = False
) -> dict[str, Any]:
    """Coerce a caller's economic fields, refusing anything unrecognised.

    ``from_json`` relaxes the no-floats rule for values that were decoded out of
    a ``jsonb`` column rather than chosen by a Python caller. See
    :func:`_dec_json` for why the two cases genuinely differ.
    """
    unknown = set(payload) - set(TERM_FIELDS)
    if unknown:
        raise SpvFeeTermsError(
            f"unknown fee term field(s) {sorted(unknown)}; the economic fields "
            f"are {list(TERM_FIELDS)}",
            field=sorted(unknown)[0],
        )
    to_decimal = _dec_json if from_json else _dec
    out: dict[str, Any] = {}
    for key, value in payload.items():
        if key in _DECIMAL_FIELDS:
            out[key] = to_decimal(value, field=key)
        elif key in _BOOL_FIELDS:
            out[key] = _bool(value, field=key)
        elif key == "mgmt_fee_step_down":
            parse_step_down(value)          # validate, store as given
            out[key] = value
        else:
            out[key] = value
    return out


def validate_terms(payload: Mapping[str, Any]) -> None:
    """Every deployed CHECK on ``spv_fee_terms``, in the application layer.

    Raises the FIRST failure with the field named. The database enforces all of
    these too and must keep doing so — this is a better error message, never a
    replacement for the constraint. ``verify_fee42.py`` proves both layers
    independently: the app raises :class:`HurdleTypeRequiredError`, and a
    direct INSERT that bypasses this function is still refused by
    ``spv_fee_terms_carry_requires_hurdle_type``.
    """
    basis = payload.get("mgmt_fee_basis")
    if basis is not None and basis not in MGMT_FEE_BASES:
        raise VocabularyError(
            f"mgmt_fee_basis={basis!r} is not one of {list(MGMT_FEE_BASES)}",
            field="mgmt_fee_basis",
        )
    freq = payload.get("mgmt_fee_frequency")
    if freq is not None and freq not in MGMT_FEE_FREQUENCIES:
        raise VocabularyError(
            f"mgmt_fee_frequency={freq!r} is not one of "
            f"{list(MGMT_FEE_FREQUENCIES)}",
            field="mgmt_fee_frequency",
        )
    hurdle_type = payload.get("hurdle_type")
    if hurdle_type is not None and hurdle_type not in HURDLE_TYPES:
        raise VocabularyError(
            f"hurdle_type={hurdle_type!r} is not one of {list(HURDLE_TYPES)}",
            field="hurdle_type",
        )
    carry_basis = payload.get("carry_basis")
    if carry_basis is not None and carry_basis not in CARRY_BASES:
        raise VocabularyError(
            f"carry_basis={carry_basis!r} is not one of {list(CARRY_BASES)}",
            field="carry_basis",
        )
    if payload.get("carry_pct") is not None and hurdle_type is None:
        raise HurdleTypeRequiredError(
            "hurdle_type is required whenever carry_pct is set. Carry with an "
            "unspecified hurdle is not a term sheet — say 'NONE' if the deal "
            "genuinely has no preferred return, 'HARD' or 'SOFT' if it does. "
            "Leaving it blank makes every future waterfall guess",
            field="hurdle_type",
        )
    parse_step_down(payload.get("mgmt_fee_step_down"))
    term_years = payload.get("mgmt_fee_term_years")
    if term_years is not None:
        years = _dec(term_years, field="mgmt_fee_term_years")
        if years <= 0:
            raise SpvFeeTermsError(
                f"mgmt_fee_term_years={years} must be greater than 0; leave it "
                f"NULL for a fee with no term limit",
                field="mgmt_fee_term_years",
            )
        _years_to_months(years, field="mgmt_fee_term_years")


# ═══════════════════════════════════════════════════════════════════════════
# TASK 2 — the pure calculation layer. No database, in fee35's discipline.
# ═══════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class RateSegment:
    """A dated span of the billing period, and the rate in force across it.

    ``start`` and ``end`` are both INCLUSIVE dates, because that is how a
    billing period is written on an invoice. The half-open boundary rule lives
    in :func:`schedule_mgmt_fee`, which converts once, here, rather than at
    every call site.
    """

    start: date
    end: date
    rate_pct: Decimal | None
    step_index: int | None      # None = the base rate, 0-based into the ladder
    basis: str

    @property
    def days(self) -> int:
        return (self.end - self.start).days + 1


@dataclass(frozen=True)
class MgmtFeeAccrual:
    """What :func:`schedule_mgmt_fee` answers for one period."""

    segments: tuple[RateSegment, ...]
    term_end: date | None
    truncated_by_term: bool
    refusal: str | None

    @property
    def accrues(self) -> bool:
        return bool(self.segments)

    @property
    def rate_unknown(self) -> bool:
        """True when any segment's rate is NULL, i.e. never recorded.

        The backfill carries a NULL ``mgmt_fee_pct`` through as NULL rather
        than defaulting it to zero (see :func:`backfill_active_spv_terms`), so
        this state is reachable on real data and a caller must not read the
        None as "no fee". A fee of zero and a fee nobody wrote down are
        different facts and only one of them is safe to invoice.
        """
        return any(s.rate_pct is None for s in self.segments)


def schedule_mgmt_fee(
    terms: SpvFeeTerms,
    *,
    inception: date | None,
    period_start: date,
    period_end: date,
) -> MgmtFeeAccrual:
    """The rate segments covering ``period_start``..``period_end``, inclusive.

    Pure. No database, no clock — ``inception`` is an explicit argument and has
    no default, because there are at least three dates that could plausibly
    anchor a fund's fee clock (``spvs.close_date``, the terms row's
    ``effective_from``, and the first capital call) and they routinely differ by
    months. Inferring one would silently move every step-down.

    Returns:
      * ``segments`` — one per rate in force, in date order, covering the
        period exactly with no gaps and no overlaps. Empty when the term limit
        has already expired, with ``refusal`` saying so.
      * ``truncated_by_term`` — True when the period ran past ``term_end`` and
        the final segment was cut short. This is the flag that distinguishes
        "billed a partial quarter correctly" from "billed a whole quarter",
        which the numbers alone cannot.

    THE BOUNDARY RULE, ONCE: an anniversary belongs to the period it BEGINS.
    ``after_year: 3`` bills the new rate ON the third anniversary; the day
    before bills the old one. ``mgmt_fee_term_years: 10`` stops accrual ON the
    tenth anniversary; the last billable day is the day before it.
    """
    if period_end < period_start:
        raise SpvFeeTermsError(
            f"period_end {period_end} is before period_start {period_start}",
            field="period_end",
        )

    steps = terms.steps()
    needs_inception = bool(steps) or terms.mgmt_fee_term_years is not None
    if needs_inception and inception is None:
        raise InceptionRequiredError(
            "this SPV's terms carry a step-down ladder and/or a "
            "mgmt_fee_term_years limit, both of which are measured from the "
            "fund's inception. Pass the inception date explicitly — it is not "
            "inferred, because close_date, the terms' effective_from and the "
            "first capital call are three different dates and picking the "
            "wrong one moves every step boundary",
            field="inception",
        )

    # ── the term limit clips the window before anything else is decided ──
    term_end: date | None = None
    if terms.mgmt_fee_term_years is not None:
        term_end = offset_years(
            inception, terms.mgmt_fee_term_years, field="mgmt_fee_term_years"
        )
        if period_start >= term_end:
            return MgmtFeeAccrual(
                segments=(), term_end=term_end, truncated_by_term=True,
                refusal=(
                    f"management fee term of {terms.mgmt_fee_term_years} years "
                    f"from {inception} ended {term_end}; the period beginning "
                    f"{period_start} is entirely past it, so no fee accrues"
                ),
            )

    window_end = period_end
    truncated = False
    if term_end is not None and term_end <= period_end:
        window_end = term_end - timedelta(days=1)
        truncated = True

    # ── cut the window at every step boundary that falls inside it ──
    boundaries: list[tuple[date, int]] = []
    for i, step in enumerate(steps):
        at = offset_years(
            inception, step.after_year,
            field=f"mgmt_fee_step_down[{i}].after_year",
        )
        boundaries.append((at, i))

    def rate_at(day: date) -> tuple[Decimal | None, int | None]:
        rate, index = terms.mgmt_fee_pct, None
        for at, i in boundaries:
            if day >= at:
                rate, index = steps[i].pct, i
        return rate, index

    cuts = sorted({
        at for at, _ in boundaries if period_start < at <= window_end
    })
    segments: list[RateSegment] = []
    cursor = period_start
    for cut in cuts:
        rate, index = rate_at(cursor)
        segments.append(RateSegment(
            start=cursor, end=cut - timedelta(days=1),
            rate_pct=rate, step_index=index, basis=terms.mgmt_fee_basis,
        ))
        cursor = cut
    rate, index = rate_at(cursor)
    segments.append(RateSegment(
        start=cursor, end=window_end, rate_pct=rate, step_index=index,
        basis=terms.mgmt_fee_basis,
    ))

    return MgmtFeeAccrual(
        segments=tuple(segments), term_end=term_end,
        truncated_by_term=truncated,
        refusal=None,
    )


def effective_mgmt_fee_pct(
    terms: SpvFeeTerms, *, inception: date | None, as_of: date
) -> Decimal | None:
    """The single rate in force on one day. A thin read of the same logic.

    Provided because "what is the rate today" is a real question a screen asks,
    and re-deriving it beside :func:`schedule_mgmt_fee` is how two answers that
    disagree get shipped. Returns None when the term has expired.
    """
    accrual = schedule_mgmt_fee(
        terms, inception=inception, period_start=as_of, period_end=as_of
    )
    return accrual.segments[0].rate_pct if accrual.accrues else None


# ═══════════════════════════════════════════════════════════════════════════
# TASK 3 — side letters, as a PARTIAL override
# ═══════════════════════════════════════════════════════════════════════════


def apply_overrides(
    base: SpvFeeTerms, overrides: Mapping[str, Any], *, side_letter_id: str | None = None
) -> SpvFeeTerms:
    """Lay a side letter's ``overrides`` over base terms. Partial, not a swap.

    Only the keys PRESENT in ``overrides`` move. An absent key is not "set to
    null" — an investor whose side letter waives the placement fee has not
    thereby waived the hurdle, the clawback and the term limit, which is what a
    whole-row replacement would quietly do.

    An explicit ``null`` IS honoured as "clear this field", because waiving a
    fee to nothing is a real thing a side letter says. Absent and null are
    therefore different, deliberately.

    The MERGED row is validated, not the delta. ``{"carry_pct": "0.2"}`` on
    base terms with no ``hurdle_type`` is individually innocent and produces a
    resolved term set the base table's own CHECK would have refused —
    ``spv_fee_side_letters`` has no CHECKs of its own to stop it (see [F3]).
    """
    if not isinstance(overrides, Mapping):
        raise SpvFeeTermsError(
            f"overrides must be a JSON object, got {type(overrides).__name__}",
            field="overrides",
        )
    unknown = set(overrides) - set(OVERRIDABLE_FIELDS)
    if unknown:
        raise SpvFeeTermsError(
            f"side letter overrides {sorted(unknown)}, which is not an "
            f"economic term. A side letter may move {list(OVERRIDABLE_FIELDS)} "
            f"and nothing else — it cannot repoint spv_id, class_label or the "
            f"row's own identity",
            field=sorted(unknown)[0],
            side_letter_id=side_letter_id,
        )

    coerced = coerce_terms_fields(overrides, from_json=True)
    merged = replace(
        base,
        **coerced,
        side_letter_id=side_letter_id,
        overridden_fields=tuple(sorted(overrides)),
    )
    validate_terms(merged.economics())
    return merged


# ═══════════════════════════════════════════════════════════════════════════
# Database layer
# ═══════════════════════════════════════════════════════════════════════════


def _current(alias: str) -> str:
    """Current on both temporal axes — the house predicate.

    Correct here even though :func:`create_terms` archives on the SYSTEM axis
    only (see [F2]): ``system_to IS NULL`` alone would also be correct, and
    requiring both is a strict subset that stays right if a valid-axis
    restatement is ever added.
    """
    return f"{alias}.valid_to IS NULL AND {alias}.system_to IS NULL"


_TERM_COLUMNS = ", ".join(
    [f"t.{c}" for c in TERM_FIELDS]
    + ["t.id::text AS id", "t.org_id::text AS org_id", "t.spv_id::text AS spv_id",
       "t.class_label", "t.effective_from", "t.effective_to"]
)


def _row_to_terms(row: Mapping[str, Any], *, source: str) -> SpvFeeTerms:
    return SpvFeeTerms(
        **{f: row[f] for f in TERM_FIELDS},
        id=row["id"], org_id=row["org_id"], spv_id=row["spv_id"],
        class_label=row["class_label"], effective_from=row["effective_from"],
        effective_to=row["effective_to"], source=source,
    )


async def load_terms(
    conn, org_id: str, spv_id: Any, *, class_label: str | None = None,
    as_of: date | None = None,
) -> SpvFeeTerms:
    """The terms in force for one SPV class on one date.

    Precedence is CLASS then WHOLE_FUND, and it is a fallback, not a merge. A
    class-specific row that omits ``placement_fee_pct`` means this class has no
    placement fee — not "inherit the fund's". Merging the two would invent a
    term that neither row states, and there is no marker in the schema to tell
    an intentional omission from an unset one.

    The org predicate is in the WHERE clause and not left to RLS: ``_OrgWrite``
    raises the org GUC FROM its argument, so RLS confirms the connection's
    context, never the caller's intent.
    """
    org_id = _require_org(org_id)
    as_of = as_of or date.today()
    rows = await conn.fetch(
        f"""
        SELECT {_TERM_COLUMNS}
        FROM {TABLE_TERMS} t
        WHERE t.org_id = $1::uuid AND t.spv_id = $2::uuid AND {_current('t')}
          AND t.effective_from <= $3::date
          AND (t.effective_to IS NULL OR t.effective_to > $3::date)
          AND (t.class_label = $4 OR t.class_label IS NULL)
        """,
        org_id, str(spv_id), as_of, class_label,
    )
    by_class = {r["class_label"]: r for r in rows}
    if class_label is not None and class_label in by_class:
        return _row_to_terms(by_class[class_label], source="CLASS")
    if None in by_class:
        return _row_to_terms(by_class[None], source="WHOLE_FUND")
    raise TermsNotFoundError(
        f"SPV {spv_id} has no fee terms in force on {as_of} for class "
        f"{class_label!r}, and no whole-fund terms either. A fee cannot be "
        f"billed from an absent term sheet — the flat spvs.mgmt_fee_pct is a "
        f"legacy scalar, not a fallback",
        field="spv_id", spv_id=str(spv_id), class_label=class_label, as_of=str(as_of),
    )


async def load_side_letter(
    conn, org_id: str, spv_id: Any, entity_id: Any, *, as_of: date | None = None,
) -> Mapping[str, Any] | None:
    """The one side letter in force, or None. Two is an error, never a choice."""
    org_id = _require_org(org_id)
    as_of = as_of or date.today()
    rows = await conn.fetch(
        f"""
        SELECT sl.id::text AS id, sl.overrides, sl.reason,
               sl.effective_from, sl.effective_to, sl.approved_by::text AS approved_by
        FROM {TABLE_SIDE_LETTERS} sl
        WHERE sl.org_id = $1::uuid AND sl.spv_id = $2::uuid
          AND sl.entity_id = $3::uuid AND {_current('sl')}
          AND sl.effective_from <= $4::date
          AND (sl.effective_to IS NULL OR sl.effective_to > $4::date)
        ORDER BY sl.effective_from DESC, sl.id
        """,
        org_id, str(spv_id), str(entity_id), as_of,
    )
    if not rows:
        return None
    if len(rows) > 1:
        raise AmbiguousSideLetterError(
            f"entity {entity_id} has {len(rows)} side letters in force on "
            f"{as_of} for SPV {spv_id}: {[r['id'] for r in rows]}. "
            f"spv_fee_side_letters has no unique index (measured), so this is "
            f"reachable; retire all but one rather than let the resolver pick",
            field="entity_id",
            side_letter_ids=[r["id"] for r in rows],
        )
    return rows[0]


async def resolve_terms_for_entity(
    conn, org_id: str, spv_id: Any, entity_id: Any, *,
    class_label: str | None = None, as_of: date | None = None,
) -> SpvFeeTerms:
    """What THIS investor is actually charged: base terms plus their side letter.

    Three layers, most specific last: whole-fund → class → side letter. The
    result carries ``source``, ``side_letter_id`` and ``overridden_fields`` so
    the answer to "why this number" survives the call.
    """
    base = await load_terms(
        conn, org_id, spv_id, class_label=class_label, as_of=as_of
    )
    letter = await load_side_letter(
        conn, org_id, spv_id, entity_id, as_of=as_of
    )
    if letter is None:
        return base
    overrides = letter["overrides"]
    if isinstance(overrides, str):
        try:
            overrides = json.loads(overrides)
        except json.JSONDecodeError as exc:
            raise SpvFeeTermsError(
                f"side letter {letter['id']} has unparseable overrides: {exc}",
                field="overrides", side_letter_id=letter["id"],
            ) from exc
    return apply_overrides(base, overrides, side_letter_id=letter["id"])


async def create_terms(
    conn, org_id: str, spv_id: Any, *, effective_from: Any,
    class_label: str | None = None, effective_to: Any = None,
    created_by: Any = None, **fields: Any,
) -> str:
    """Write one terms row, archiving whatever it replaces on the SYSTEM axis.

    See [F2]: ``spv_fee_terms_active_uq`` is partial on ``system_to IS NULL``,
    so the usual valid-axis restatement would collide with the row it replaces.
    The superseded row keeps its ``id`` and gets ``system_to = now()``.

    Validated in Python BEFORE the insert so the operator gets a message naming
    the field, and validated again by the database because the CHECK is the
    real gate and this function is not the only door.
    """
    org_id = _require_org(org_id)
    payload = coerce_terms_fields(fields)
    payload.setdefault("mgmt_fee_basis", "COMMITTED")
    payload.setdefault("mgmt_fee_frequency", "QUARTERLY")
    validate_terms(payload)
    effective_from = _as_date(effective_from, field="effective_from")
    if effective_to is not None:
        effective_to = _as_date(effective_to, field="effective_to")
        if effective_to <= effective_from:
            raise SpvFeeTermsError(
                f"effective_to {effective_to} must be after effective_from "
                f"{effective_from}",
                field="effective_to",
            )

    step_down = payload.get("mgmt_fee_step_down")
    async with _OrgWrite(conn, org_id) as tx:
        exists = await tx.fetchval(
            f"SELECT count(*) FROM {TABLE_SPVS} s "
            f"WHERE s.id = $1::uuid AND s.org_id = $2::uuid",
            str(spv_id), org_id,
        )
        if not exists:
            raise SpvFeeTermsError(
                f"SPV {spv_id} does not exist in org {org_id}",
                field="spv_id",
            )
        await tx.execute(
            f"""
            UPDATE {TABLE_TERMS} SET system_to = now()
            WHERE org_id = $1::uuid AND spv_id = $2::uuid
              AND class_label IS NOT DISTINCT FROM $3 AND system_to IS NULL
            """,
            org_id, str(spv_id), class_label,
        )
        row = await tx.fetchrow(
            f"""
            INSERT INTO {TABLE_TERMS}
                (org_id, spv_id, class_label, mgmt_fee_pct, mgmt_fee_basis,
                 mgmt_fee_frequency, mgmt_fee_term_years, mgmt_fee_step_down,
                 organizational_cost_cap, admin_fee_flat, placement_fee_pct,
                 carry_pct, hurdle_pct, hurdle_type, catchup_pct, carry_basis,
                 clawback_applies, offsets_advisory_fee, effective_from,
                 effective_to, created_by)
            VALUES ($1::uuid, $2::uuid, $3, $4::numeric, $5, $6, $7::numeric,
                    $8::jsonb, $9::numeric, $10::numeric, $11::numeric,
                    $12::numeric, $13::numeric, $14, $15::numeric, $16,
                    COALESCE($17::boolean, true), COALESCE($18::boolean, false),
                    $19::date, $20::date, $21::uuid)
            RETURNING id::text
            """,
            org_id, str(spv_id), class_label,
            payload.get("mgmt_fee_pct"), payload["mgmt_fee_basis"],
            payload["mgmt_fee_frequency"], payload.get("mgmt_fee_term_years"),
            json.dumps(step_down) if step_down is not None else None,
            payload.get("organizational_cost_cap"), payload.get("admin_fee_flat"),
            payload.get("placement_fee_pct"), payload.get("carry_pct"),
            payload.get("hurdle_pct"), payload.get("hurdle_type"),
            payload.get("catchup_pct"), payload.get("carry_basis"),
            payload.get("clawback_applies"), payload.get("offsets_advisory_fee"),
            effective_from, effective_to,
            str(created_by) if created_by else None,
        )
    return row["id"]


# ═══════════════════════════════════════════════════════════════════════════
# TASK 2 — the backfill, and what it will and will not infer
# ═══════════════════════════════════════════════════════════════════════════

#: ``spvs.spv_status`` has NO check constraint — measured live, the column is
#: free text. The real vocabulary is ``routers/spv.py``'s
#: ``SPV_STATUS_TRANSITIONS``, which is the only writer:
#: ``forming → open → closing → closed``, plus ``cancelled`` from anywhere.
#:
#: **[F6] 'closed' means the RAISE is closed, not the fund.** This is the trap
#: in this whole task and it is worth being explicit about, because getting it
#: backwards excludes precisely the SPVs that bill the most. A closed-end
#: vehicle reaches ``closed`` when subscriptions stop — which is the moment its
#: management fee STARTS, and it then sits at ``closed`` for its entire
#: ten-year life. Treating ``closed`` as historical would backfill terms for
#: nothing except funds still raising.
#:
#: The consequence, reported as a real gap and not fixed here: the deployed
#: vocabulary has **no wound-down / dissolved / terminated status at all**, so
#: an SPV that has actually finished cannot be distinguished from one that is
#: mid-life. ``mgmt_fee_term_years`` is the only thing that will stop the
#: clock, which is one more reason this sprint makes it storable.
ACTIVE_SPV_STATUSES = ("open", "closing", "closed")

#: Not billing: ``forming`` has not raised anything yet, ``cancelled`` is dead.
INACTIVE_SPV_STATUSES = ("forming", "cancelled")


@dataclass
class BackfillDecision:
    """One SPV's backfill outcome, with every inference named as an inference."""

    spv_id: str
    name: str
    spv_status: str
    class_label: str | None
    action: str                     # CREATED | SKIPPED_INACTIVE | SKIPPED_EXISTS
    terms_id: str | None = None
    known: dict[str, Any] = None
    inferred: dict[str, str] = None
    reason: str = ""

    def __post_init__(self):
        self.known = self.known or {}
        self.inferred = self.inferred or {}


async def backfill_active_spv_terms(
    conn, org_id: str, *, effective_from: date | None = None,
    created_by: Any = None, dry_run: bool = False,
) -> list[BackfillDecision]:
    """Give every ACTIVE SPV a ``spv_fee_terms`` row. Historical ones are skipped.

    WHAT IS CARRIED VERSUS WHAT IS INFERRED, and why the distinction is the
    whole point of this function:

      CARRIED (genuinely known)   ``mgmt_fee_pct`` and ``carry_pct``, copied
                                  from the flat columns exactly, including
                                  NULL. A NULL rate is carried as NULL — it is
                                  not a zero and it is not a default. An SPV
                                  whose rate was never recorded gets a terms
                                  row that says so.

      INFERRED (a default)        ``mgmt_fee_basis`` = COMMITTED and
                                  ``mgmt_fee_frequency`` = QUARTERLY. Both are
                                  the closed-end market standard, and COMMITTED
                                  is additionally the only basis whose amount is
                                  actually computable from the deployed schema
                                  together with FUNDED (see [F5]). Every
                                  inference is recorded per SPV in
                                  ``BackfillDecision.inferred``.

      NOT INFERRED (left NULL)    ``hurdle_type``. The brief is explicit and it
                                  is right: 'NONE' asserts the deal has no
                                  preferred return, which is a substantive
                                  claim about a document nobody has read. NULL
                                  says 'unknown', which is the truth. The
                                  deployed CHECK permits this precisely when
                                  ``carry_pct`` is also NULL — so an SPV with a
                                  real carry_pct and no known hurdle CANNOT be
                                  backfilled, and is reported as needing a
                                  human rather than given a fabricated hurdle.

    Never overwrites: an SPV that already has active terms is SKIPPED_EXISTS.
    A backfill that clobbered hand-entered terms with inferred defaults would
    be the worst possible outcome of running this twice.
    """
    org_id = _require_org(org_id)
    effective_from = effective_from or date.today()

    # The read raises org context ITSELF rather than assuming the caller's
    # connection already carries it. Every other read in this module follows
    # the house convention (fee_schedules.load_schedule et al) of inheriting the
    # request's GUC, which is right for a request. This one is a MIGRATION, run
    # from a script against a raw connection where no middleware has set
    # anything — and under RLS an unset ``app.current_org_id`` NULLIFs to NULL,
    # so ``org_id = NULL`` matches nothing and the whole population reads back
    # EMPTY. The first run of this function reported "0 SPVs" against a database
    # holding one, and reported it as a clean success. A migration that silently
    # migrates nothing is worse than one that fails.
    async with _OrgWrite(conn, org_id) as scoped:
        rows = await scoped.fetch(
            f"""
            SELECT s.id::text AS id, s.name, s.spv_status, s.class_label,
                   s.mgmt_fee_pct, s.carry_pct, s.close_date, s.currency,
                   (SELECT count(*) FROM {TABLE_TERMS} t
                     WHERE t.spv_id = s.id AND t.org_id = s.org_id
                       AND t.valid_to IS NULL AND t.system_to IS NULL) AS terms_count
            FROM {TABLE_SPVS} s
            WHERE s.org_id = $1::uuid
            ORDER BY s.name
            """,
            org_id,
        )

    decisions: list[BackfillDecision] = []
    for r in rows:
        d = BackfillDecision(
            spv_id=r["id"], name=r["name"], spv_status=r["spv_status"],
            class_label=r["class_label"], action="SKIPPED_INACTIVE",
        )
        if r["spv_status"] not in ACTIVE_SPV_STATUSES:
            d.reason = (
                f"spv_status={r['spv_status']!r} is not billing "
                f"(active set: {list(ACTIVE_SPV_STATUSES)}). Historical "
                f"vehicles are deliberately not backfilled — inventing terms "
                f"for a fund that stopped charging years ago puts a fee "
                f"schedule on a closed book"
            )
            decisions.append(d)
            continue
        if r["terms_count"]:
            d.action = "SKIPPED_EXISTS"
            d.reason = (
                "already has active spv_fee_terms; a backfill never overwrites "
                "terms somebody entered by hand"
            )
            decisions.append(d)
            continue

        d.known = {
            "mgmt_fee_pct": r["mgmt_fee_pct"],
            "carry_pct": r["carry_pct"],
            "class_label": r["class_label"],
            "close_date": r["close_date"],
        }
        d.inferred = {
            "mgmt_fee_basis": (
                "COMMITTED — closed-end market standard, and one of only two "
                "bases computable from the deployed schema"
            ),
            "mgmt_fee_frequency": "QUARTERLY — closed-end market standard",
        }
        if r["carry_pct"] is not None:
            d.action = "SKIPPED_NEEDS_HURDLE"
            d.reason = (
                f"carry_pct={r['carry_pct']} is recorded but hurdle_type is "
                f"not, and hurdle_type is NOT inferable — 'NONE' would assert "
                f"this deal has no preferred return, which no deployed data "
                f"supports. spv_fee_terms_carry_requires_hurdle_type would "
                f"refuse the row anyway. Needs a human to read the LPA"
            )
            decisions.append(d)
            continue

        d.inferred["hurdle_type"] = (
            "NOT inferred — left NULL, meaning unknown, because carry_pct is "
            "also NULL so nothing is being asserted about a hurdle"
        )
        if not dry_run:
            d.terms_id = await create_terms(
                conn, org_id, r["id"],
                class_label=r["class_label"],
                effective_from=effective_from,
                created_by=created_by,
                mgmt_fee_pct=r["mgmt_fee_pct"],
                mgmt_fee_basis="COMMITTED",
                mgmt_fee_frequency="QUARTERLY",
                carry_pct=None,
            )
        d.action = "CREATED"
        d.reason = (
            f"spv_status={r['spv_status']!r} is billing; flat scalars carried "
            f"verbatim (mgmt_fee_pct={r['mgmt_fee_pct']}), basis and frequency "
            f"inferred, hurdle_type left unknown"
        )
        decisions.append(d)
    return decisions


# ═══════════════════════════════════════════════════════════════════════════
# TASK 3 — the offset, wired into fee34's EXISTING fee_credits mechanism
# ═══════════════════════════════════════════════════════════════════════════


async def ensure_advisory_fee_offset_credit(
    conn, org_id: str, *, spv_id: Any, entity_id: Any, scope_type: str,
    scope_id: Any, approved_by: Any, reason: str,
    effective_from: Any, offset_pct: Any = Decimal("1"),
    class_label: str | None = None, as_of: date | None = None,
) -> str:
    """Turn ``offsets_advisory_fee`` into the fee_credits row fee36 already reads.

    THE CONNECTION IS REAL, AND HERE IS EXACTLY WHERE IT JOINS. fee36's
    ``resolve_credit_basis`` resolves a ``SPV_MGMT_FEE_OFFSET`` credit's basis
    from ``spv_transaction_allocations.allocated_amount`` for **the owning
    entity of the account whose line is being written** — not the scope_id, and
    not the SPV. So this credit only produces a number when the account's
    ``primary_entity_id`` is the entity that actually subscribed. That join is
    asserted end-to-end in ``verify_fee42.py`` by calling fee36's own resolver
    against the row this function writes, rather than by assuming it.

    WHAT THIS DELIBERATELY DOES NOT DO. ``offsets_advisory_fee`` is a boolean;
    ``fee_credits.offset_pct`` is a fraction in [0, 1]. The boolean cannot
    supply the fraction, so ``offset_pct`` is an explicit argument defaulting to
    a FULL offset. Deriving it from the boolean — 1.0 for true — is what the
    default does, visibly; inventing a partial offset from a boolean would be
    fabrication. Note the scale trap fee35 already found in this module:
    ``offset_pct`` is a FRACTION (50% is 0.5), unlike the PCT_OFF discount
    values which are percents. ``validate_credit`` catches the confusion.

    Refuses when the resolved terms say ``offsets_advisory_fee`` is false: a
    credit that the term sheet does not authorise is an unearned discount, and
    it must not be creatable through the path whose entire job is to honour the
    term sheet.
    """
    org_id = _require_org(org_id)
    terms = await resolve_terms_for_entity(
        conn, org_id, spv_id, entity_id, class_label=class_label, as_of=as_of
    )
    if not terms.offsets_advisory_fee:
        raise OffsetNotAuthorisedError(
            f"SPV {spv_id}'s terms for entity {entity_id} have "
            f"offsets_advisory_fee=false"
            + (f" (side letter {terms.side_letter_id} applied)"
               if terms.side_letter_id else "")
            + ". No SPV_MGMT_FEE_OFFSET credit may be created: crediting an "
              "advisory fee against a management fee the term sheet does not "
              "say is offsettable gives away revenue nobody agreed to give",
            field="offsets_advisory_fee",
            spv_id=str(spv_id), entity_id=str(entity_id),
        )

    offset = _dec(offset_pct, field="offset_pct")
    candidate = {
        "scope_type": scope_type, "scope_id": scope_id,
        "credit_source": CREDIT_SOURCE_SPV_OFFSET, "offset_pct": offset,
        "approved_by": approved_by, "reason": reason,
    }
    errors = validate_credit(candidate)
    if errors:
        raise SpvFeeTermsError(
            "; ".join(str(e) for e in errors),
            field=getattr(errors[0], "field", None),
        )

    effective_from = _as_date(effective_from, field="effective_from")
    async with _OrgWrite(conn, org_id) as tx:
        row = await tx.fetchrow(
            f"""
            INSERT INTO {TABLE_CREDITS}
                (org_id, scope_type, scope_id, credit_source, offset_pct,
                 effective_from, reason, approved_by)
            VALUES ($1::uuid, $2, $3::uuid, $4, $5::numeric, $6::date, $7, $8::uuid)
            RETURNING id::text
            """,
            org_id, scope_type, str(scope_id), CREDIT_SOURCE_SPV_OFFSET,
            offset, effective_from, reason, str(approved_by),
        )
    return row["id"]
