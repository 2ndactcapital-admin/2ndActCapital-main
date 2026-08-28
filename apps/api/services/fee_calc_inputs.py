"""Plain-data input contracts for the fee calculation engine. Sprint fee35.

Nothing in this module imports a database driver, holds a connection, or knows
what asyncpg is. Every type here is a frozen dataclass that can be built from
three different things with the same call:

    FeeScheduleInput.from_row(record)      # asyncpg.Record  (mapping-like)
    FeeScheduleInput.from_row(pydantic_m)  # router model    (attribute-like)
    FeeScheduleInput.from_row({...})       # a unit test     (a dict)

That is the whole point. fee36 will load these rows inside a transaction and
hand them straight in; a golden-case test builds them from literals and never
opens a socket. If the engine had needed a session, the test suite would have
needed a database, and a billing engine whose arithmetic can only be checked
against a live database is one whose arithmetic is never checked.


THE COLUMNS HERE ARE THE DEPLOYED COLUMNS
──────────────────────────────────────────────────────────────────────────────
Every field below was read out of the live database (``pg_constraint`` for the
vocabularies, the schema snapshot for the column list), not transcribed from
the sprint prompt. Four places where the two disagree, all measured:

  [1] ``fee_credits`` HAS NO AMOUNT COLUMN. Its only numeric field is
      ``offset_pct``, constrained to [0, 1] by ``fee_credits_offset_pct_range``.
      The prompt's phrase "offset_pct of the credit's stated basis" describes a
      basis the table does not store. So :class:`CreditInput` carries
      ``basis_amount`` as a REQUIRED, caller-supplied field with no column
      behind it, and :data:`ENGINE_SUPPLIED_FIELDS` names it. Defaulting it to
      zero would have made every credit silently worth nothing.

  [2] ``portfolio.positions`` HAS NO TAG COLUMN, but
      ``fee_exclusions_basis_type_check`` admits ``'POSITION_TAG'``. Tags live
      in ``portfolio.udf_values``. :class:`PositionInput.tags` is therefore also
      caller-supplied, and a POSITION_TAG exclusion against positions carrying
      no tags excludes nothing — visibly, in ``calc_detail``, not silently.

  [3] ``accounts`` HAS NO ``billing_group_id``. Membership is a separate table,
      ``billing_group_members``. :class:`AccountInput.billing_group_id` is the
      caller's already-resolved answer, consistent with the sprint's rule that
      scope resolution happened before the engine was called.

  [4] ``fee_exclusions.basis_type`` admits six values —
      ``SECURITY, ASSET_CLASS, ACCOUNT, HELD_AWAY, CASH, POSITION_TAG`` — not
      the one (``SECURITY``) the prompt names. All six are handled.


DECIMAL, AND FLOATS REFUSED AT THIS BOUNDARY
──────────────────────────────────────────────────────────────────────────────
Every money field is coerced through :func:`money`, which is a raising wrapper
around ``fee_validation._decimal`` — the SAME implementation fee34 uses, on
purpose. A second copy of "reject float, accept int and str" would be a second
copy of a rule that decides what a client is billed, and the copy that drifts
is the one nobody re-reads.

fee34 COLLECTS its errors, because it is validating a form. This module RAISES,
because it is validating an argument: there is no form to repaint, and a
``2500000.0`` that got past the constructor would be a wrong invoice rather
than a red field.

``int`` and ``str`` are accepted and converted. JSON has no decimal type and
asyncpg hands ``numeric`` back as ``Decimal`` already.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, fields as dc_fields
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Mapping, Sequence
from uuid import UUID

from services.fee_validation import (
    CREDIT_SCOPE_TYPES,
    DISCOUNT_SCOPE_TYPES,
    EXCLUSION_SCOPE_TYPES,
    EXCLUSION_TREATMENTS,
    MINIMUM_FEE_SCOPES,
    ORDERING_STEPS,
    _decimal as _fv_decimal,
)

# ── Vocabularies, mirrored from the deployed CHECK constraints ───────────────
#
# Read on 2026-08-28 from pg_constraint against the live database. fee34 already
# owns the four its validator needed; the rest are here because the ENGINE
# branches on them and a typo'd branch is a silently unbilled account.

#: ``fee_schedules_valuation_method_check``.
VALUATION_METHODS = ("PERIOD_END", "PERIOD_START", "AVG_DAILY", "AVG_MONTH_END")

#: ``fee_schedules_tier_method_check`` — nullable in the column, see
#: :data:`DEFAULT_TIER_METHOD`.
TIER_METHODS = ("GRADUATED", "CLIFF", "BLENDED_PUBLISHED")

#: ``fee_schedules_proration_method_check``.
PRORATION_METHODS = ("CALENDAR_DAYS", "BUSINESS_DAYS", "NONE")

#: ``fee_schedules_billing_frequency_check``.
BILLING_FREQUENCIES = ("MONTHLY", "QUARTERLY", "SEMIANNUAL", "ANNUAL")

#: ``fee_schedules_billing_timing_check``.
BILLING_TIMINGS = ("ADVANCE", "ARREARS")

#: ``fee_schedules_cash_treatment_check``.
CASH_TREATMENTS = ("INCLUDE", "EXCLUDE", "EXCLUDE_ABOVE_PCT")

#: ``fee_schedules_margin_treatment_check``.
MARGIN_TREATMENTS = ("IGNORE", "REDUCE_BILLABLE")

#: ``fee_schedules_rate_type_check``.
RATE_TYPES = ("BPS", "FLAT", "HYBRID", "HOURLY", "PER_ACCOUNT")

#: ``fee_schedules_product_type_check``.
PRODUCT_TYPES = (
    "ASSET_MANAGEMENT",
    "SPV",
    "STRUCTURED_INVESTMENT",
    "PLANNING",
    "CLUB_DUES",
    "TRANSACTION",
)

#: ``fee_exclusions_basis_type_check``. Six, not one.
EXCLUSION_BASIS_TYPES = (
    "SECURITY",
    "ASSET_CLASS",
    "ACCOUNT",
    "HELD_AWAY",
    "CASH",
    "POSITION_TAG",
)

#: ``fee_discounts_discount_type_check``.
DISCOUNT_TYPES = (
    "PCT_OFF",
    "BPS_OFF",
    "DOLLAR_CREDIT",
    "FEE_HOLIDAY",
    "SCHEDULE_OVERRIDE",
)

#: ``fee_discounts_applies_to_check``.
DISCOUNT_APPLIES_TO = ("GROSS", "NET_OF_CREDITS")

#: ``fee_credits_source_check``.
CREDIT_SOURCES = (
    "12B1",
    "SUB_TA",
    "SPV_MGMT_FEE_OFFSET",
    "SI_EMBEDDED_FEE_OFFSET",
    "MODEL_FEE_OFFSET",
)

#: How many billing periods one year contains, per ``billing_frequency``. Every
#: rate and every tier ``flat_amount`` in ``fee_schedule_tiers`` is treated as
#: ANNUAL and divided by this — see :mod:`services.fee_calc`, "ANNUAL RATES".
PERIODS_PER_YEAR: Mapping[str, int] = {
    "MONTHLY": 12,
    "QUARTERLY": 4,
    "SEMIANNUAL": 2,
    "ANNUAL": 1,
}

#: ``fee_schedules.tier_method`` is nullable. A schedule that leaves it NULL is
#: calculated as GRADUATED and says so in ``calc_detail['assumptions']``.
DEFAULT_TIER_METHOD = "GRADUATED"

#: Fields on these dataclasses that have NO column behind them. Named, so that
#: fee36's loader has a checkable list of what it must supply itself rather
#: than discovering it one wrong invoice at a time.
ENGINE_SUPPLIED_FIELDS: Mapping[str, tuple[str, ...]] = {
    "CreditInput": ("basis_amount",),
    "PositionInput": ("tags",),
    "AccountInput": ("billing_group_id",),
}

_BPS_DENOMINATOR = Decimal(10000)


# ═══════════════════════════════════════════════════════════════════════════
# Errors
# ═══════════════════════════════════════════════════════════════════════════


class FeeCalcError(ValueError):
    """Anything the engine refuses to do, named.

    A ``ValueError`` subclass for the same reason fee34's is: an existing
    ``except ValueError`` on a write path keeps working. ``code`` is stable;
    the message is free to be reworded.
    """

    code = "fee_calc_error"

    def __init__(self, message: str, **context: Any) -> None:
        super().__init__(message)
        self.message = message
        self.context = context

    def as_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.context:
            out["context"] = {k: v for k, v in self.context.items() if v is not None}
        return out


class FeeCalcInputError(FeeCalcError):
    """A value arrived in a type or shape the engine will not calculate on.

    Raised, never collected. The float case is the one that matters: a caller
    who passes ``0.1`` has already lost the exactness the rest of this module
    is built to preserve, and coercing it would hide that from them forever.
    """

    code = "fee_calc_input_invalid"


# ═══════════════════════════════════════════════════════════════════════════
# Coercion
# ═══════════════════════════════════════════════════════════════════════════


def money(value: Any, *, field: str, required: bool = False) -> Decimal | None:
    """Decimal, or raise. Delegates the float rule to fee34's implementation.

    ``fee_validation._decimal`` is imported by its private name deliberately.
    Making it public would have meant editing fee34's module during a sprint
    that is meant to consume fee34, and re-implementing it here would have
    meant two versions of "is this a float" — the failure mode being that the
    validator refuses a tier bound the engine happily bills on.
    """
    errors: list[Any] = []
    out = _fv_decimal(value, field=field, errors=errors)
    if errors:
        raise FeeCalcInputError(
            str(errors[0]), field=field, received=repr(value)
        ) from None
    if out is None and required:
        raise FeeCalcInputError(f"{field} is required", field=field)
    return out


def as_date(value: Any, *, field: str, required: bool = False) -> date | None:
    """``date``, an ISO ``YYYY-MM-DD`` string, or raise.

    ``datetime`` is accepted and truncated: ``valid_from`` columns are
    timestamptz and a caller narrowing a bi-temporal row to its date is doing
    the ordinary thing.
    """
    if value is None:
        if required:
            raise FeeCalcInputError(f"{field} is required", field=field)
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value.strip())
        except ValueError:
            raise FeeCalcInputError(
                f"{field}={value!r} is not an ISO date", field=field
            ) from None
    raise FeeCalcInputError(
        f"{field} has unsupported type {type(value).__name__}",
        field=field,
        received=repr(value),
    )


def as_text(value: Any, *, field: str, allowed: Sequence[str] | None = None,
            required: bool = False) -> str | None:
    """A stripped string, checked against a vocabulary if one is given."""
    if value is None:
        if required:
            raise FeeCalcInputError(f"{field} is required", field=field)
        return None
    if isinstance(value, UUID):
        text = str(value)
    elif isinstance(value, str):
        text = value.strip()
    else:
        raise FeeCalcInputError(
            f"{field} must be text, got {type(value).__name__}",
            field=field, received=repr(value),
        )
    if not text:
        if required:
            raise FeeCalcInputError(f"{field} is required", field=field)
        return None
    if allowed is not None and text not in allowed:
        raise FeeCalcInputError(
            f"{field}={text!r} is not one of {tuple(allowed)}",
            field=field, received=text,
        )
    return text


def as_bool(value: Any, *, field: str, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    raise FeeCalcInputError(
        f"{field} must be a boolean, got {type(value).__name__}",
        field=field, received=repr(value),
    )


def _identity(value: Any) -> str | None:
    """A uuid/str id, normalised to str so dict keys and equality behave.

    Ids are compared, never arithmetic'd. Keeping them as ``str`` means an
    ``asyncpg`` UUID and a test's string literal for the same id match.
    """
    if value is None:
        return None
    if isinstance(value, UUID):
        return str(value)
    return str(value).strip() or None


class _Row:
    """Shared ``from_row`` for every input type below.

    Reads by field name from a Mapping, or by attribute from anything else, so
    an ``asyncpg.Record``, a Pydantic model and a dict all work. ``extra``
    carries the engine-supplied fields that have no column
    (:data:`ENGINE_SUPPLIED_FIELDS`) — they cannot come from the row because
    the row does not have them.
    """

    @classmethod
    def from_row(cls, row: Mapping[str, Any] | Any, **extra: Any):
        values: dict[str, Any] = {}
        for f in dc_fields(cls):  # type: ignore[arg-type]
            if f.name in extra:
                values[f.name] = extra[f.name]
                continue
            if isinstance(row, Mapping):
                if f.name in row:
                    values[f.name] = row[f.name]
            elif hasattr(row, f.name):
                values[f.name] = getattr(row, f.name)
        unknown = set(extra) - {f.name for f in dc_fields(cls)}  # type: ignore[arg-type]
        if unknown:
            raise FeeCalcInputError(
                f"{cls.__name__}.from_row got unknown field(s) {sorted(unknown)}",
                received=sorted(unknown),
            )
        return cls(**values)  # type: ignore[operator]


# ═══════════════════════════════════════════════════════════════════════════
# The rule: schedule + tiers
# ═══════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class FeeTierInput(_Row):
    """One row of ``fee_schedule_tiers``.

    ``rate_bps`` XOR ``flat_amount`` — the database enforces it
    (``fee_schedule_tiers_rate_or_flat_check``) and so does
    ``fee_validation.validate_tiers``, which the engine runs before it tiers
    anything. Both bounds are exclusive-upper: a tier is ``[lower, upper)``.
    ``upper_bound IS NULL`` is the open-ended top tier.
    """

    tier_seq: int
    lower_bound: Decimal
    upper_bound: Decimal | None = None
    rate_bps: Decimal | None = None
    flat_amount: Decimal | None = None
    id: str | None = None
    fee_schedule_id: str | None = None

    def __post_init__(self) -> None:
        set_ = object.__setattr__
        if isinstance(self.tier_seq, bool) or not isinstance(self.tier_seq, int):
            raise FeeCalcInputError(
                f"tier_seq must be an int, got {type(self.tier_seq).__name__}",
                field="tier_seq", received=repr(self.tier_seq),
            )
        set_(self, "lower_bound",
             money(self.lower_bound, field="lower_bound", required=True))
        set_(self, "upper_bound", money(self.upper_bound, field="upper_bound"))
        set_(self, "rate_bps", money(self.rate_bps, field="rate_bps"))
        set_(self, "flat_amount", money(self.flat_amount, field="flat_amount"))
        set_(self, "id", _identity(self.id))
        set_(self, "fee_schedule_id", _identity(self.fee_schedule_id))

    @property
    def annual_rate(self) -> Decimal | None:
        """``rate_bps`` as a fraction. ``None`` for a flat-amount tier."""
        if self.rate_bps is None:
            return None
        return self.rate_bps / _BPS_DENOMINATOR


@dataclass(frozen=True)
class FeeScheduleInput(_Row):
    """One row of ``fee_schedules``, minus the columns the engine cannot use.

    ``created_by``/``approved_by``/the bi-temporal columns are deliberately
    absent: the engine's output must not depend on who approved a schedule or
    when a row was superseded, and a field that cannot change the number has no
    business being an input to it.

    ``status`` IS here, and is not checked. Whether a DRAFT schedule may be
    billed on is fee36's decision at run creation, not the arithmetic's — an
    engine that refused to price a DRAFT would make "what would this schedule
    charge?" unanswerable before approval, which is exactly the question an
    operator asks before approving one.
    """

    id: str
    code: str
    billing_frequency: str
    billing_timing: str
    valuation_method: str
    proration_method: str = "CALENDAR_DAYS"
    tier_method: str | None = None
    rate_type: str = "BPS"
    product_type: str = "ASSET_MANAGEMENT"
    day_weight_flows: bool = True
    day_weight_threshold: Decimal | None = None
    minimum_fee: Decimal | None = None
    minimum_fee_scope: str | None = None
    maximum_fee: Decimal | None = None
    minimum_billable_value: Decimal | None = None
    cash_treatment: str = "INCLUDE"
    cash_exclusion_pct: Decimal | None = None
    margin_treatment: str = "IGNORE"
    ordering_policy: tuple[str, ...] = ORDERING_STEPS
    currency: str = "USD"
    status: str = "APPROVED"
    version: int = 1
    name: str | None = None
    org_id: str | None = None

    def __post_init__(self) -> None:
        set_ = object.__setattr__
        set_(self, "id", as_text(self.id, field="schedule.id", required=True))
        set_(self, "org_id", _identity(self.org_id))
        set_(self, "code", as_text(self.code, field="schedule.code", required=True))
        set_(self, "billing_frequency", as_text(
            self.billing_frequency, field="billing_frequency",
            allowed=BILLING_FREQUENCIES, required=True))
        set_(self, "billing_timing", as_text(
            self.billing_timing, field="billing_timing",
            allowed=BILLING_TIMINGS, required=True))
        set_(self, "valuation_method", as_text(
            self.valuation_method, field="valuation_method",
            allowed=VALUATION_METHODS, required=True))
        set_(self, "proration_method", as_text(
            self.proration_method, field="proration_method",
            allowed=PRORATION_METHODS, required=True))
        set_(self, "tier_method", as_text(
            self.tier_method, field="tier_method", allowed=TIER_METHODS))
        set_(self, "rate_type", as_text(
            self.rate_type, field="rate_type", allowed=RATE_TYPES, required=True))
        set_(self, "product_type", as_text(
            self.product_type, field="product_type",
            allowed=PRODUCT_TYPES, required=True))
        set_(self, "day_weight_flows",
             as_bool(self.day_weight_flows, field="day_weight_flows", default=True))
        set_(self, "day_weight_threshold",
             money(self.day_weight_threshold, field="day_weight_threshold"))
        set_(self, "minimum_fee", money(self.minimum_fee, field="minimum_fee"))
        set_(self, "minimum_fee_scope", as_text(
            self.minimum_fee_scope, field="minimum_fee_scope",
            allowed=MINIMUM_FEE_SCOPES))
        set_(self, "maximum_fee", money(self.maximum_fee, field="maximum_fee"))
        set_(self, "minimum_billable_value",
             money(self.minimum_billable_value, field="minimum_billable_value"))
        set_(self, "cash_treatment", as_text(
            self.cash_treatment, field="cash_treatment",
            allowed=CASH_TREATMENTS, required=True))
        set_(self, "cash_exclusion_pct",
             money(self.cash_exclusion_pct, field="cash_exclusion_pct"))
        set_(self, "margin_treatment", as_text(
            self.margin_treatment, field="margin_treatment",
            allowed=MARGIN_TREATMENTS, required=True))
        set_(self, "ordering_policy", _ordering_policy(self.ordering_policy))
        set_(self, "currency", as_text(
            self.currency, field="currency", required=True))
        set_(self, "status", as_text(self.status, field="status", required=True))
        if self.cash_treatment == "EXCLUDE_ABOVE_PCT" and self.cash_exclusion_pct is None:
            raise FeeCalcInputError(
                "cash_treatment='EXCLUDE_ABOVE_PCT' needs cash_exclusion_pct; "
                "the column is nullable and no CHECK constraint pairs them, so "
                "the engine is the only place this can be caught",
                field="cash_exclusion_pct",
            )

    @property
    def periods_per_year(self) -> int:
        return PERIODS_PER_YEAR[self.billing_frequency]

    @property
    def effective_tier_method(self) -> str:
        return self.tier_method or DEFAULT_TIER_METHOD


def _ordering_policy(value: Any) -> tuple[str, ...]:
    """``ordering_policy`` as a tuple, validated as a permutation.

    ``jsonb`` reaches Python as a ``str`` through asyncpg unless a codec is
    registered, and as a ``list`` through a dict fixture. Both are accepted.

    The permutation check duplicates fee34's ``validate_ordering_policy`` in
    OUTCOME but not in kind: fee34 collects errors for a form, and the engine
    cannot proceed past a policy that is missing a step — it would silently
    skip whatever the missing step was. A schedule with ``MINIMUM`` dropped
    would under-bill every account on it and nothing would raise.
    """
    if value is None:
        return tuple(ORDERING_STEPS)
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            raise FeeCalcInputError(
                f"ordering_policy={value!r} is not valid JSON",
                field="ordering_policy",
            ) from None
    if not isinstance(value, (list, tuple)):
        raise FeeCalcInputError(
            f"ordering_policy must be a list, got {type(value).__name__}",
            field="ordering_policy",
        )
    steps = tuple(str(v).strip() for v in value)
    if sorted(steps) != sorted(ORDERING_STEPS):
        missing = sorted(set(ORDERING_STEPS) - set(steps))
        extra = sorted(set(steps) - set(ORDERING_STEPS))
        raise FeeCalcInputError(
            "ordering_policy must be a permutation of "
            f"{list(ORDERING_STEPS)} — missing {missing}, unexpected {extra}. "
            "A policy missing a step does not disable that step safely; it "
            "removes it from the calculation with no other trace",
            field="ordering_policy", missing=missing, extra=extra,
        )
    return steps


# ═══════════════════════════════════════════════════════════════════════════
# The adjustments: exclusions, discounts, credits
# ═══════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class ExclusionInput(_Row):
    """One row of ``fee_exclusions``, already scope-resolved by the caller.

    There is no ``fee_schedule_id`` here because there is none in the table
    (fee34 finding [2k], re-measured this sprint). ``alt_fee_schedule_id`` is
    the schedule a REDUCED_RATE carve-out bills ON, not the schedule this row
    belongs to — the engine needs it loaded and passed in ``alt_schedules``.
    """

    basis_type: str
    treatment: str
    scope_type: str = "ACCOUNT"
    scope_id: str | None = None
    basis_value: str | None = None
    alt_fee_schedule_id: str | None = None
    flat_amount: Decimal | None = None
    effective_from: date | None = None
    effective_to: date | None = None
    reason: str | None = None
    id: str | None = None

    def __post_init__(self) -> None:
        set_ = object.__setattr__
        set_(self, "id", _identity(self.id))
        set_(self, "basis_type", as_text(
            self.basis_type, field="exclusion.basis_type",
            allowed=EXCLUSION_BASIS_TYPES, required=True))
        set_(self, "treatment", as_text(
            self.treatment, field="exclusion.treatment",
            allowed=EXCLUSION_TREATMENTS, required=True))
        set_(self, "scope_type", as_text(
            self.scope_type, field="exclusion.scope_type",
            allowed=EXCLUSION_SCOPE_TYPES, required=True))
        set_(self, "scope_id", _identity(self.scope_id))
        set_(self, "basis_value", as_text(
            self.basis_value, field="exclusion.basis_value"))
        set_(self, "alt_fee_schedule_id", _identity(self.alt_fee_schedule_id))
        set_(self, "flat_amount", money(self.flat_amount, field="exclusion.flat_amount"))
        set_(self, "effective_from",
             as_date(self.effective_from, field="exclusion.effective_from"))
        set_(self, "effective_to",
             as_date(self.effective_to, field="exclusion.effective_to"))
        if self.treatment == "REDUCED_RATE" and self.alt_fee_schedule_id is None:
            raise FeeCalcInputError(
                "REDUCED_RATE exclusion has no alt_fee_schedule_id — there is "
                "nothing to bill the carve-out on",
                field="exclusion.alt_fee_schedule_id",
            )
        if self.treatment == "FLAT" and self.flat_amount is None:
            raise FeeCalcInputError(
                "FLAT exclusion has no flat_amount", field="exclusion.flat_amount",
            )


@dataclass(frozen=True)
class DiscountInput(_Row):
    """One row of ``fee_discounts``, already scope-resolved.

    ``applies_to`` is real and is NOT the same knob as ``ordering_policy``.
    ``ordering_policy`` says WHEN the DISCOUNTS step runs; ``applies_to`` says
    WHAT a percentage discount is a percentage OF once it runs. A
    ``NET_OF_CREDITS`` discount in a policy that puts CREDITS after DISCOUNTS
    is a contradiction the engine reports rather than resolves.
    """

    discount_type: str
    value: Decimal | None = None
    applies_to: str = "GROSS"
    scope_type: str = "ACCOUNT"
    scope_id: str | None = None
    effective_from: date | None = None
    effective_to: date | None = None
    reason: str | None = None
    id: str | None = None

    def __post_init__(self) -> None:
        set_ = object.__setattr__
        set_(self, "id", _identity(self.id))
        set_(self, "discount_type", as_text(
            self.discount_type, field="discount.discount_type",
            allowed=DISCOUNT_TYPES, required=True))
        set_(self, "value", money(self.value, field="discount.value"))
        set_(self, "applies_to", as_text(
            self.applies_to, field="discount.applies_to",
            allowed=DISCOUNT_APPLIES_TO, required=True))
        set_(self, "scope_type", as_text(
            self.scope_type, field="discount.scope_type",
            allowed=DISCOUNT_SCOPE_TYPES, required=True))
        set_(self, "scope_id", _identity(self.scope_id))
        set_(self, "effective_from",
             as_date(self.effective_from, field="discount.effective_from"))
        set_(self, "effective_to",
             as_date(self.effective_to, field="discount.effective_to"))
        needs_value = ("PCT_OFF", "BPS_OFF", "DOLLAR_CREDIT")
        if self.discount_type in needs_value and self.value is None:
            raise FeeCalcInputError(
                f"discount_type={self.discount_type} needs a value; the column "
                f"is nullable because FEE_HOLIDAY legitimately has none",
                field="discount.value",
            )


@dataclass(frozen=True)
class CreditInput(_Row):
    """One row of ``fee_credits``, plus the basis the table does not store.

    ``basis_amount`` is REQUIRED and caller-supplied. See [1] in the module
    docstring: ``fee_credits`` has ``offset_pct`` and nothing to multiply it
    by. For ``SPV_MGMT_FEE_OFFSET`` the basis is the SPV management fee charged
    for this same period; for ``12B1``/``SUB_TA`` it is the trail actually
    received. Both are facts the engine cannot derive and must be given.
    """

    credit_source: str
    basis_amount: Decimal
    offset_pct: Decimal = Decimal("1.0")
    scope_type: str = "ACCOUNT"
    scope_id: str | None = None
    effective_from: date | None = None
    effective_to: date | None = None
    reason: str | None = None
    id: str | None = None

    def __post_init__(self) -> None:
        set_ = object.__setattr__
        set_(self, "id", _identity(self.id))
        set_(self, "credit_source", as_text(
            self.credit_source, field="credit.credit_source",
            allowed=CREDIT_SOURCES, required=True))
        set_(self, "basis_amount",
             money(self.basis_amount, field="credit.basis_amount", required=True))
        set_(self, "offset_pct",
             money(self.offset_pct, field="credit.offset_pct", required=True))
        set_(self, "scope_type", as_text(
            self.scope_type, field="credit.scope_type",
            allowed=CREDIT_SCOPE_TYPES, required=True))
        set_(self, "scope_id", _identity(self.scope_id))
        set_(self, "effective_from",
             as_date(self.effective_from, field="credit.effective_from"))
        set_(self, "effective_to",
             as_date(self.effective_to, field="credit.effective_to"))
        if not (Decimal(0) <= self.offset_pct <= Decimal(1)):
            raise FeeCalcInputError(
                f"offset_pct={self.offset_pct} is outside [0, 1] — "
                f"fee_credits_offset_pct_range would refuse this row",
                field="credit.offset_pct",
            )


# ═══════════════════════════════════════════════════════════════════════════
# The facts: account, balances, flows, positions
# ═══════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class DailyBalanceInput(_Row):
    """One row of ``account_balances_daily``.

    ``is_billing_source`` is real and load-bearing: the table's primary key is
    ``(org_id, account_id, as_of_date, source_system)``, so the SAME day can
    carry two different market values from two custodian feeds. The engine
    prefers rows flagged ``is_billing_source`` and refuses to guess when two
    unflagged sources disagree, rather than averaging them into a number no
    statement will ever match.
    """

    as_of_date: date
    total_market_value: Decimal
    cash_value: Decimal = Decimal(0)
    margin_balance: Decimal = Decimal(0)
    accrued_income: Decimal = Decimal(0)
    source_system: str = "PRIMARY"
    is_billing_source: bool = False
    is_final: bool = False
    account_id: str | None = None

    def __post_init__(self) -> None:
        set_ = object.__setattr__
        set_(self, "as_of_date",
             as_date(self.as_of_date, field="balance.as_of_date", required=True))
        set_(self, "total_market_value", money(
            self.total_market_value, field="balance.total_market_value",
            required=True))
        set_(self, "cash_value",
             money(self.cash_value, field="balance.cash_value", required=True))
        set_(self, "margin_balance",
             money(self.margin_balance, field="balance.margin_balance", required=True))
        set_(self, "accrued_income",
             money(self.accrued_income, field="balance.accrued_income", required=True))
        set_(self, "source_system", as_text(
            self.source_system, field="balance.source_system", required=True))
        set_(self, "is_billing_source", as_bool(
            self.is_billing_source, field="balance.is_billing_source", default=False))
        set_(self, "is_final",
             as_bool(self.is_final, field="balance.is_final", default=False))
        set_(self, "account_id", _identity(self.account_id))


@dataclass(frozen=True)
class FlowInput(_Row):
    """One row of ``account_flows``. Sign convention: contributions positive.

    ``is_billable_flow`` defaults TRUE in the column. A false one is skipped by
    the day-weighting stage and still appears in ``calc_detail`` with the
    reason — an internal transfer between two of the same household's accounts
    is the case this exists for, and silently dropping it would make the two
    accounts' traces impossible to reconcile against each other.
    """

    flow_date: date
    amount: Decimal
    flow_type: str = "CONTRIBUTION"
    is_billable_flow: bool = True
    account_id: str | None = None
    counterparty_account_id: str | None = None
    id: str | None = None

    def __post_init__(self) -> None:
        set_ = object.__setattr__
        set_(self, "id", _identity(self.id))
        set_(self, "flow_date",
             as_date(self.flow_date, field="flow.flow_date", required=True))
        set_(self, "amount", money(self.amount, field="flow.amount", required=True))
        set_(self, "flow_type",
             as_text(self.flow_type, field="flow.flow_type", required=True))
        set_(self, "is_billable_flow", as_bool(
            self.is_billable_flow, field="flow.is_billable_flow", default=True))
        set_(self, "account_id", _identity(self.account_id))
        set_(self, "counterparty_account_id", _identity(self.counterparty_account_id))


@dataclass(frozen=True)
class PositionInput(_Row):
    """One row of ``portfolio.positions``, plus caller-supplied ``tags``.

    ``market_value`` is nullable in the column (a position can be held on an
    ``ownership_pct`` basis with no value yet). A position with no value
    contributes nothing to an exclusion and says so in the trace — treating
    ``NULL`` as zero silently would make a concentrated-stock carve-out worth
    nothing on exactly the day the price feed failed.
    """

    asset_id: str
    market_value: Decimal | None = None
    taxonomy_key: str | None = None
    as_of_date: date | None = None
    account_id: str | None = None
    owner_entity_id: str | None = None
    tags: tuple[str, ...] = ()
    id: str | None = None

    def __post_init__(self) -> None:
        set_ = object.__setattr__
        set_(self, "id", _identity(self.id))
        set_(self, "asset_id",
             as_text(self.asset_id, field="position.asset_id", required=True))
        set_(self, "market_value",
             money(self.market_value, field="position.market_value"))
        set_(self, "taxonomy_key",
             as_text(self.taxonomy_key, field="position.taxonomy_key"))
        set_(self, "as_of_date", as_date(self.as_of_date, field="position.as_of_date"))
        set_(self, "account_id", _identity(self.account_id))
        set_(self, "owner_entity_id", _identity(self.owner_entity_id))
        if isinstance(self.tags, str):
            raise FeeCalcInputError(
                "position.tags must be a sequence of strings, not one string — "
                "'GROWTH' would otherwise be seven single-character tags",
                field="position.tags",
            )
        set_(self, "tags", tuple(str(t).strip() for t in (self.tags or ())))


@dataclass(frozen=True)
class AccountInput(_Row):
    """One row of ``accounts``, plus the caller's resolved ``billing_group_id``.

    ``is_billable`` short-circuits the whole calculation to zero with a trace,
    rather than being filtered out upstream and leaving no record that the
    account was considered. A billing run that skipped an account and a billing
    run that billed it zero look identical downstream unless one of them says
    which happened.
    """

    id: str
    household_id: str | None = None
    billing_group_id: str | None = None
    is_billable: bool = True
    is_held_away: bool = False
    base_currency: str = "USD"
    opened_on: date | None = None
    closed_on: date | None = None

    def __post_init__(self) -> None:
        set_ = object.__setattr__
        set_(self, "id", as_text(self.id, field="account.id", required=True))
        set_(self, "household_id", _identity(self.household_id))
        set_(self, "billing_group_id", _identity(self.billing_group_id))
        set_(self, "is_billable",
             as_bool(self.is_billable, field="account.is_billable", default=True))
        set_(self, "is_held_away",
             as_bool(self.is_held_away, field="account.is_held_away", default=False))
        set_(self, "base_currency", as_text(
            self.base_currency, field="account.base_currency", required=True))
        set_(self, "opened_on", as_date(self.opened_on, field="account.opened_on"))
        set_(self, "closed_on", as_date(self.closed_on, field="account.closed_on"))


@dataclass(frozen=True)
class BillingPeriod:
    """The window being billed, and the account's service window inside it.

    ``service_start``/``service_end`` are what make proration possible, and
    they are separate from ``accounts.opened_on``/``closed_on`` on purpose: an
    account can be open for years before it is under management, and the fee
    starts when the advisory relationship does. The caller may of course pass
    ``opened_on``/``closed_on`` when those ARE the same dates.

    Both period bounds are INCLUSIVE. A quarter is 91 days, not 90 — the day
    count is ``(end - start).days + 1`` everywhere in this module.
    """

    period_start: date
    period_end: date
    service_start: date | None = None
    service_end: date | None = None

    def __post_init__(self) -> None:
        set_ = object.__setattr__
        set_(self, "period_start",
             as_date(self.period_start, field="period_start", required=True))
        set_(self, "period_end",
             as_date(self.period_end, field="period_end", required=True))
        set_(self, "service_start", as_date(self.service_start, field="service_start"))
        set_(self, "service_end", as_date(self.service_end, field="service_end"))
        if self.period_end < self.period_start:
            raise FeeCalcInputError(
                f"period_end {self.period_end} precedes period_start "
                f"{self.period_start}",
                field="period_end",
            )

    @property
    def calendar_days(self) -> int:
        return (self.period_end - self.period_start).days + 1

    @property
    def effective_start(self) -> date:
        if self.service_start is None or self.service_start < self.period_start:
            return self.period_start
        return self.service_start

    @property
    def effective_end(self) -> date:
        if self.service_end is None or self.service_end > self.period_end:
            return self.period_end
        return self.service_end

    @property
    def is_partial(self) -> bool:
        return (self.effective_start != self.period_start
                or self.effective_end != self.period_end)

    @property
    def is_termination(self) -> bool:
        """True when service ends inside the period. Drives the refund case."""
        return self.service_end is not None and self.service_end < self.period_end


@dataclass(frozen=True)
class AccountPeriodInput(_Row):
    """Everything measured about one account over one period.

    Balances, flows and positions arrive as given; the engine filters them to
    the period itself rather than trusting the caller to have done it, because
    a query that accidentally returned last quarter's balances would otherwise
    change the fee with nothing in the trace to show it.
    """

    account: AccountInput
    period: BillingPeriod
    balances: tuple[DailyBalanceInput, ...] = ()
    flows: tuple[FlowInput, ...] = ()
    positions: tuple[PositionInput, ...] = ()

    def __post_init__(self) -> None:
        set_ = object.__setattr__
        if not isinstance(self.account, AccountInput):
            raise FeeCalcInputError("account must be an AccountInput", field="account")
        if not isinstance(self.period, BillingPeriod):
            raise FeeCalcInputError("period must be a BillingPeriod", field="period")
        set_(self, "balances", tuple(self.balances))
        set_(self, "flows", tuple(self.flows))
        set_(self, "positions", tuple(self.positions))


@dataclass(frozen=True)
class AccountCalcRequest:
    """One account's complete calculation input. The engine's unit of work.

    ``alt_schedules`` maps ``alt_fee_schedule_id`` to the loaded schedule and
    its tiers. It is a mapping and not a list so a REDUCED_RATE exclusion that
    points at a schedule the caller forgot to load fails by name
    (:class:`~services.fee_calc.AltScheduleMissingError`) instead of billing
    the carve-out at zero.
    """

    data: AccountPeriodInput
    schedule: FeeScheduleInput
    tiers: tuple[FeeTierInput, ...]
    exclusions: tuple[ExclusionInput, ...] = ()
    discounts: tuple[DiscountInput, ...] = ()
    credits: tuple[CreditInput, ...] = ()
    alt_schedules: Mapping[str, tuple[FeeScheduleInput, tuple[FeeTierInput, ...]]] | None = None

    def __post_init__(self) -> None:
        set_ = object.__setattr__
        set_(self, "tiers", tuple(self.tiers))
        set_(self, "exclusions", tuple(self.exclusions))
        set_(self, "discounts", tuple(self.discounts))
        set_(self, "credits", tuple(self.credits))
        set_(self, "alt_schedules", dict(self.alt_schedules or {}))
