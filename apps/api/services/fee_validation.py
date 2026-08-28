"""Fee catalog validation — pure, no database, no I/O. Sprint fee34.

Everything here takes plain data and returns a list of typed errors. There is
no connection argument anywhere in this module and there must never be one: the
calculation engine (fee35) has to re-run these exact checks against a schedule
it has already loaded, and a validator that needed its own connection would
either be re-implemented there or would issue a second read of rows the caller
is already holding. Both of those end the same way — two copies of the rule
that drift, and the drifted copy is the one deciding what a client is billed.

So: :func:`validate_schedule` is callable from a unit test with two dicts.


WHY THESE RULES ARE HERE WHEN THE DATABASE ALREADY HAS MOST OF THEM
──────────────────────────────────────────────────────────────────────────────
Four of the seven rules the sprint names are already CHECK constraints. That is
not a reason to skip them; it is the reason they are phrased the way they are.
A constraint violation surfaces to an operator as

    asyncpg.exceptions.CheckViolationError: new row for relation
    "fee_schedules" violates check constraint
    "fee_schedules_minimum_fee_scope_required"

which names a constraint, not a field, and gives no hint that the fix is to
pick a scope. Every error class below carries ``field`` (and ``tier_seq`` where
one applies) as an ATTRIBUTE, so the router can attach it to the right input on
the form rather than dropping a constraint name into a toast.

Two of the rules are NOT in the database and cannot be:

  * A non-empty ``reason``. ``NOT NULL`` admits ``''``. Measured in Task 1: the
    same gap exists on ``fee_discounts.reason`` and ``fee_credits.reason``, not
    only on ``fee_exclusions.reason`` as the prompt has it, so all three are
    covered here.
  * Tier contiguity. It spans ROWS. ``fee_schedule_tiers_bounds_check`` proves
    one tier's upper exceeds its own lower and nothing about its neighbours; a
    set with a gap, a set with an overlap, and a set with two open-ended tiers
    all satisfy every deployed constraint.


[FIND] THE EXCLUSION RULES DO NOT BELONG TO THE APPROVAL GATE
──────────────────────────────────────────────────────────────────────────────
The sprint asks for the exclusion/discount/credit rules as part of "the module
fee_schedules will be checked against before any status transition to
APPROVED". Task 1 measured why they cannot be reached from there:
``fee_exclusions`` has no ``fee_schedule_id``. Its only schedule reference is
``alt_fee_schedule_id``, the REDUCED_RATE target — the schedule an exclusion
points AT, not the schedule it belongs to. Exclusions, discounts and credits
are scoped to an account, billing group, or household. There is no join path
from a schedule to "its" exclusions because a schedule does not have any.

Folding them into :func:`validate_schedule` would therefore have produced a
gate that always passes vacuously on an empty list — the shape of green that
proves nothing. They are instead :func:`validate_exclusion`,
:func:`validate_discount` and :func:`validate_credit`, each called at its own
row's write time, and :func:`validate_schedule` takes an optional
``exclusions`` argument for the one case that IS reachable: an operator
validating a proposed bundle before saving any of it.


DECIMAL, INCLUDING THE INTERMEDIATE STEPS
──────────────────────────────────────────────────────────────────────────────
``float`` is refused rather than coerced. ``Decimal(0.1)`` is
``0.1000000000000000055511151231257827...`` and a tier boundary compared that
way reports a gap between two bounds an operator entered as identical. Refusing
the type is the only version of this check that cannot be defeated by a caller
who "knows the values are fine" — and it is a collected error rather than a
raised ``TypeError`` so a form full of floats reports every one of them at
once instead of one per round trip.

``int`` and ``str`` are accepted and converted, because JSON has no decimal
type and a request body legitimately carries ``"1000000.00"``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field as dc_field
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable, Mapping, Sequence

# ── Vocabularies, mirrored from the deployed CHECK constraints ───────────────
#
# Task 1 measured three DIFFERENT scope vocabularies across these tables. They
# are deliberately three constants and not one: fee_exclusions admits 'ORG'
# where fee_assignments admits 'ORG_DEFAULT', and neither discounts nor credits
# admit 'ENTITY' at all. A single shared SCOPE_TYPES tuple would have made
# every one of those a runtime constraint violation instead of a clean refusal.

#: ``fee_assignments_scope_type_check``.
ASSIGNMENT_SCOPE_TYPES = (
    "ACCOUNT",
    "BILLING_GROUP",
    "HOUSEHOLD",
    "ENTITY",
    "ORG_DEFAULT",
)

#: ``fee_exclusions_scope_type_check`` — note 'ORG', not 'ORG_DEFAULT'.
EXCLUSION_SCOPE_TYPES = ("ACCOUNT", "BILLING_GROUP", "HOUSEHOLD", "ORG")

#: ``fee_discounts_scope_type_check`` and ``fee_credits_scope_type_check``.
DISCOUNT_SCOPE_TYPES = ("ACCOUNT", "BILLING_GROUP", "HOUSEHOLD")
CREDIT_SCOPE_TYPES = DISCOUNT_SCOPE_TYPES

#: ``fee_exclusions_treatment_check``.
EXCLUSION_TREATMENTS = ("EXCLUDE", "REDUCED_RATE", "FLAT")

#: ``fee_schedules_status_check``.
STATUS_DRAFT = "DRAFT"
STATUS_APPROVED = "APPROVED"
STATUS_RETIRED = "RETIRED"
SCHEDULE_STATUSES = (STATUS_DRAFT, STATUS_APPROVED, STATUS_RETIRED)

#: ``fee_schedules_minimum_fee_scope_check``.
MINIMUM_FEE_SCOPES = ("ACCOUNT", "BILLING_GROUP", "HOUSEHOLD")

#: The canonical calculation order, and the exact set ``ordering_policy`` must
#: be a permutation of. Measured in Task 1 as the deployed column DEFAULT, in
#: this order — not transcribed from the prompt.
ORDERING_STEPS = (
    "EXCLUSIONS",
    "TIERS",
    "DISCOUNTS",
    "CREDITS",
    "MINIMUM",
    "MAXIMUM",
)

#: The default itself, as a list, for a caller writing a new schedule.
DEFAULT_ORDERING_POLICY = list(ORDERING_STEPS)


# ═══════════════════════════════════════════════════════════════════════════
# Errors
# ═══════════════════════════════════════════════════════════════════════════


class FeeValidationError(ValueError):
    """One rule, broken, at one named place.

    A ``ValueError`` subclass so an existing ``except ValueError`` around a
    write path still catches it, but never raised as a bare ``ValueError``: the
    sprint's requirement is that every failure names the specific field or tier
    at fault, and prose in a generic exception cannot be attached to a form
    input or asserted on in a test without string matching.

    ``code`` is the stable identifier. Tests and the UI switch on it; the
    message is free to be reworded.
    """

    #: Overridden by every subclass. Stable across message edits.
    code = "fee_invalid"

    def __init__(
        self,
        message: str,
        *,
        field: str | None = None,
        tier_seq: int | None = None,
        **context: Any,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.field = field
        self.tier_seq = tier_seq
        self.context = context

    def as_dict(self) -> dict[str, Any]:
        """The wire form. What a 422 body carries, one entry per error."""
        out: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.field is not None:
            out["field"] = self.field
        if self.tier_seq is not None:
            out["tier_seq"] = self.tier_seq
        if self.context:
            out["context"] = self.context
        return out

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"{type(self).__name__}(code={self.code!r}, field={self.field!r}, "
            f"tier_seq={self.tier_seq!r}, message={self.message!r})"
        )


# ── Tier structure ───────────────────────────────────────────────────────────


class TierError(FeeValidationError):
    """Base for the row-spanning tier rules no CHECK constraint can express."""

    code = "tier_invalid"


class TierGapError(TierError):
    """A tier starts ABOVE where the previous one ended.

    Its own class, distinct from :class:`TierOverlapError`, because the two are
    different operator mistakes with different fixes and — measured, not
    assumed — a single "tiers are not contiguous" error would let a test that
    only ever produced gaps claim it had proved overlap detection too.

    The money between the two bounds is billed at no rate at all: a balance
    landing in the gap matches no tier and the engine has nothing to apply.
    """

    code = "tier_gap"


class TierOverlapError(TierError):
    """A tier starts BELOW where the previous one ended.

    The opposite failure and the more expensive one: a balance in the overlap
    matches two tiers, so which rate applies depends on iteration order rather
    than on the schedule.
    """

    code = "tier_overlap"


class TierUnboundedError(TierError):
    """The open-ended (``upper_bound IS NULL``) tier is missing, duplicated, or
    not the last one.

    One class covering three states, each with its own ``code`` set on the
    instance, because they share a fix — decide which single tier is the top
    one — and a caller catching "the top tier is wrong" wants all three.
    """

    code = "tier_unbounded"

    def __init__(self, message: str, *, code: str, **kwargs: Any) -> None:
        super().__init__(message, **kwargs)
        #: Shadows the class attribute with the specific variant.
        self.code = code


class TierBoundsError(TierError):
    """``upper_bound`` is not strictly above ``lower_bound`` on one tier."""

    code = "tier_bounds"


class TierSequenceError(TierError):
    """``tier_seq`` is missing, duplicated, or not a whole number."""

    code = "tier_sequence"


class TierRateError(TierError):
    """A tier carries both a rate and a flat amount, or neither."""

    code = "tier_rate"


class TiersMissingError(TierError):
    """``tier_method`` is set but the schedule has no tiers to apply it to."""

    code = "tiers_missing"


# ── Schedule fields ──────────────────────────────────────────────────────────


class MinimumFeeScopeError(FeeValidationError):
    """``minimum_fee`` and ``minimum_fee_scope`` are set independently.

    ``fee_schedules_minimum_fee_scope_required`` already refuses this. The
    value of raising it here first is the message: a minimum fee with no scope
    is not a malformed row, it is an unanswered question — a $2,500 minimum
    PER ACCOUNT and a $2,500 minimum per HOUSEHOLD bill a five-account family
    $12,500 and $2,500 respectively.
    """

    code = "minimum_fee_scope_required"


class VocabularyError(FeeValidationError):
    """A value outside the deployed CHECK's vocabulary."""

    code = "vocabulary"


class OrderingPolicyError(FeeValidationError):
    """``ordering_policy`` is not a permutation of the six canonical steps."""

    code = "ordering_policy"


class MoneyTypeError(FeeValidationError):
    """A monetary or rate field arrived as a ``float``, or as unparseable text.

    Refused rather than coerced. See the module docstring — ``Decimal(0.1)``
    does not equal ``Decimal("0.1")``, and tier contiguity is an equality test
    between two bounds.
    """

    code = "money_type"


# ── Exclusions, discounts, credits ───────────────────────────────────────────


class ExclusionAltScheduleError(FeeValidationError):
    """A ``REDUCED_RATE`` exclusion with no ``alt_fee_schedule_id``.

    ``fee_exclusions_reduced_rate_requires_schedule`` enforces it; this names
    the field and says what it is for — REDUCED_RATE means "bill this at a
    DIFFERENT schedule", and without one there is no different schedule.
    """

    code = "exclusion_alt_schedule_required"


class ExclusionFlatAmountError(FeeValidationError):
    """A ``FLAT`` exclusion with no ``flat_amount``."""

    code = "exclusion_flat_amount_required"


class ReasonRequiredError(FeeValidationError):
    """``reason`` is absent, or present and whitespace-only.

    The rule ``NOT NULL`` cannot express. Fee exceptions are the rows a
    regulator asks about by name; "why is this client not billed on this
    position" answered with ``''`` is the same as unanswered.
    """

    code = "reason_required"


class ApprovedByRequiredError(FeeValidationError):
    """``approved_by`` is absent on a discount or credit.

    Task 1 measured ``NOT NULL`` on both columns and found no gap of the
    empty-string kind — ``approved_by`` is ``uuid``, which has no blank form.
    Kept anyway so a discount assembled in memory and validated BEFORE its
    insert is refused here rather than at the constraint, which is the whole
    point of a pure validator.
    """

    code = "approved_by_required"


class FeeScheduleInvalid(FeeValidationError):
    """The aggregate raised by :func:`raise_if_invalid`. Carries the full list.

    A single exception rather than the first error, so a status transition that
    is refused reports everything wrong at once. ``errors`` is the list; the
    message summarises it.
    """

    code = "schedule_invalid"

    def __init__(self, errors: Sequence[FeeValidationError]) -> None:
        self.errors = list(errors)
        summary = "; ".join(e.message for e in self.errors)
        super().__init__(
            f"{len(self.errors)} validation "
            f"{'error' if len(self.errors) == 1 else 'errors'}: {summary}"
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "errors": [e.as_dict() for e in self.errors],
        }


# ═══════════════════════════════════════════════════════════════════════════
# Coercion
# ═══════════════════════════════════════════════════════════════════════════

_MISSING = object()


def _get(row: Mapping[str, Any] | Any, key: str, default: Any = None) -> Any:
    """Read a field from a mapping OR an object with attributes.

    asyncpg hands back ``Record`` (mapping-like), a router hands back a Pydantic
    model (attribute-like), and a unit test hands back a plain dict. Supporting
    all three is what keeps the fee35 reuse promise honest — a validator that
    only accepted dicts would be re-wrapped at every call site.
    """
    if isinstance(row, Mapping):
        return row.get(key, default)
    return getattr(row, key, default)


def _decimal(
    value: Any,
    *,
    field: str,
    tier_seq: int | None = None,
    errors: list[FeeValidationError],
) -> Decimal | None:
    """Coerce to ``Decimal``, or append a :class:`MoneyTypeError` and return None.

    ``bool`` is checked before ``int`` because ``bool`` IS an ``int`` in Python
    and ``Decimal(True)`` is ``Decimal(1)`` — a ``flat_amount`` of ``True``
    would otherwise become a one-dollar fee.
    """
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value
    if isinstance(value, bool):
        errors.append(
            MoneyTypeError(
                f"{field} must be a decimal amount, not a boolean",
                field=field,
                tier_seq=tier_seq,
                received=repr(value),
            )
        )
        return None
    if isinstance(value, float):
        errors.append(
            MoneyTypeError(
                f"{field} arrived as a float ({value!r}). Fee amounts must be "
                f"Decimal, int, or a decimal string — a float cannot represent "
                f"a tier boundary exactly, and two bounds an operator entered "
                f"as identical would compare unequal",
                field=field,
                tier_seq=tier_seq,
                received=repr(value),
            )
        )
        return None
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            errors.append(
                MoneyTypeError(
                    f"{field} is an empty string, not a number",
                    field=field, tier_seq=tier_seq,
                )
            )
            return None
        try:
            return Decimal(text)
        except InvalidOperation:
            errors.append(
                MoneyTypeError(
                    f"{field}={value!r} is not a valid decimal number",
                    field=field, tier_seq=tier_seq, received=value,
                )
            )
            return None
    errors.append(
        MoneyTypeError(
            f"{field} has unsupported type {type(value).__name__}",
            field=field, tier_seq=tier_seq, received=repr(value),
        )
    )
    return None


def _is_blank(value: Any) -> bool:
    """True for None and for any string that is empty once trimmed."""
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    return False


# ═══════════════════════════════════════════════════════════════════════════
# Tier contiguity
# ═══════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class _Tier:
    """One tier, coerced. Only built for tiers whose seq and bounds parsed."""

    tier_seq: int
    lower_bound: Decimal
    upper_bound: Decimal | None
    raw: Any = dc_field(repr=False, default=None)


def validate_tiers(tiers: Iterable[Any]) -> list[FeeValidationError]:
    """Every tier rule, on one schedule's tier set.

    Returns a list; raises nothing. Order of checks matters and is deliberate:

    1. Per-tier shape (``tier_seq``, bounds, rate-xor-flat) FIRST, because a
       tier whose ``lower_bound`` did not parse cannot participate in a
       contiguity comparison and would otherwise crash it.
    2. The open-ended tier's count and position.
    3. Contiguity between consecutive pairs.

    A tier that failed step 1 is EXCLUDED from steps 2 and 3 rather than
    guessed at. Reporting a phantom gap next to the real parse error would send
    an operator to fix the wrong row.
    """
    errors: list[FeeValidationError] = []
    parsed: list[_Tier] = []
    seen_seq: dict[int, int] = {}

    for index, raw in enumerate(tiers):
        seq_value = _get(raw, "tier_seq")
        if seq_value is None or isinstance(seq_value, bool) or not isinstance(
            seq_value, int
        ):
            errors.append(
                TierSequenceError(
                    f"tier at position {index} has tier_seq={seq_value!r}; "
                    f"tier_seq is required and must be a whole number",
                    field="tier_seq",
                )
            )
            continue
        if seq_value in seen_seq:
            errors.append(
                TierSequenceError(
                    f"tier_seq={seq_value} appears more than once (positions "
                    f"{seen_seq[seq_value]} and {index}); it identifies the "
                    f"tier's place in the ladder and must be unique",
                    field="tier_seq", tier_seq=seq_value,
                )
            )
            continue
        seen_seq[seq_value] = index

        tier_errors: list[FeeValidationError] = []
        lower = _decimal(
            _get(raw, "lower_bound"),
            field="lower_bound", tier_seq=seq_value, errors=tier_errors,
        )
        upper = _decimal(
            _get(raw, "upper_bound"),
            field="upper_bound", tier_seq=seq_value, errors=tier_errors,
        )

        if lower is None and not tier_errors:
            tier_errors.append(
                TierBoundsError(
                    f"tier {seq_value} has no lower_bound; every tier must "
                    f"state where it starts (the first tier normally starts "
                    f"at 0)",
                    field="lower_bound", tier_seq=seq_value,
                )
            )

        # Mirrors fee_schedule_tiers_rate_or_flat_check, which is an exclusive
        # or: exactly one of the two, never both and never neither.
        rate = _get(raw, "rate_bps")
        flat = _get(raw, "flat_amount")
        if rate is not None:
            _decimal(rate, field="rate_bps", tier_seq=seq_value, errors=tier_errors)
        if flat is not None:
            _decimal(flat, field="flat_amount", tier_seq=seq_value, errors=tier_errors)
        if (rate is None) == (flat is None):
            tier_errors.append(
                TierRateError(
                    f"tier {seq_value} must carry exactly one of rate_bps or "
                    f"flat_amount, not "
                    + ("both" if rate is not None else "neither"),
                    field="rate_bps", tier_seq=seq_value,
                )
            )

        if lower is not None and upper is not None and upper <= lower:
            tier_errors.append(
                TierBoundsError(
                    f"tier {seq_value} has upper_bound={upper} which is not "
                    f"above its lower_bound={lower}; a tier must cover a "
                    f"non-empty range",
                    field="upper_bound", tier_seq=seq_value,
                )
            )

        errors.extend(tier_errors)
        if lower is not None and not any(
            isinstance(e, (TierBoundsError, MoneyTypeError)) for e in tier_errors
        ):
            parsed.append(_Tier(seq_value, lower, upper, raw))

    if not parsed:
        return errors

    parsed.sort(key=lambda t: t.tier_seq)
    top_seq = parsed[-1].tier_seq

    # ── The open-ended tier ──────────────────────────────────────────────
    open_ended = [t for t in parsed if t.upper_bound is None]
    if not open_ended:
        errors.append(
            TierUnboundedError(
                f"no tier has a NULL upper_bound; the highest tier (tier_seq="
                f"{top_seq}) must be open-ended, or any balance above "
                f"{parsed[-1].upper_bound} matches no tier at all",
                code="tier_unbounded_missing",
                field="upper_bound", tier_seq=top_seq,
            )
        )
    elif len(open_ended) > 1:
        seqs = [t.tier_seq for t in open_ended]
        errors.append(
            TierUnboundedError(
                f"tiers {seqs} all have a NULL upper_bound; exactly one tier "
                f"may be open-ended, otherwise a balance above the lower of "
                f"them matches {len(seqs)} tiers and the rate applied depends "
                f"on iteration order rather than on the schedule",
                code="tier_unbounded_duplicate",
                field="upper_bound", tier_seq=seqs[0], tier_seqs=seqs,
            )
        )
    elif open_ended[0].tier_seq != top_seq:
        errors.append(
            TierUnboundedError(
                f"tier {open_ended[0].tier_seq} is open-ended but is not the "
                f"highest tier (tier_seq={top_seq}); an open-ended tier in the "
                f"middle swallows every tier above it",
                code="tier_unbounded_not_last",
                field="upper_bound", tier_seq=open_ended[0].tier_seq,
            )
        )

    # ── Contiguity ───────────────────────────────────────────────────────
    for previous, current in zip(parsed, parsed[1:]):
        if previous.upper_bound is None:
            # Already reported as tier_unbounded_not_last. Comparing against
            # "no upper bound" here would add a second, meaningless error.
            continue
        if current.lower_bound > previous.upper_bound:
            errors.append(
                TierGapError(
                    f"gap between tier {previous.tier_seq} and tier "
                    f"{current.tier_seq}: tier {previous.tier_seq} ends at "
                    f"{previous.upper_bound} but tier {current.tier_seq} "
                    f"starts at {current.lower_bound}. A balance between the "
                    f"two matches no tier and would be billed at no rate",
                    field="lower_bound", tier_seq=current.tier_seq,
                    previous_tier_seq=previous.tier_seq,
                    previous_upper_bound=str(previous.upper_bound),
                    lower_bound=str(current.lower_bound),
                )
            )
        elif current.lower_bound < previous.upper_bound:
            errors.append(
                TierOverlapError(
                    f"overlap between tier {previous.tier_seq} and tier "
                    f"{current.tier_seq}: tier {previous.tier_seq} ends at "
                    f"{previous.upper_bound} but tier {current.tier_seq} "
                    f"starts at {current.lower_bound}. A balance between the "
                    f"two matches both tiers",
                    field="lower_bound", tier_seq=current.tier_seq,
                    previous_tier_seq=previous.tier_seq,
                    previous_upper_bound=str(previous.upper_bound),
                    lower_bound=str(current.lower_bound),
                )
            )

    return errors


# ═══════════════════════════════════════════════════════════════════════════
# ordering_policy
# ═══════════════════════════════════════════════════════════════════════════


def validate_ordering_policy(value: Any) -> list[FeeValidationError]:
    """``ordering_policy`` must be a permutation of the six canonical steps.

    ``None`` is valid and means "the column default", which Task 1 measured to
    be exactly :data:`ORDERING_STEPS` in that order. A JSON string is accepted
    because ``jsonb`` round-trips as text through some drivers and a request
    body may carry either form.

    All three defects are reported separately — missing, duplicated, unknown —
    because they are three different operator mistakes and a single "not a
    valid ordering" would let a test that only ever dropped a step claim it had
    proved the other two.
    """
    errors: list[FeeValidationError] = []
    if value is None:
        return errors

    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return [
                OrderingPolicyError(
                    f"ordering_policy is not valid JSON: {value!r}",
                    field="ordering_policy",
                )
            ]

    if isinstance(value, tuple):
        value = list(value)
    if not isinstance(value, list):
        return [
            OrderingPolicyError(
                f"ordering_policy must be a list of steps, got "
                f"{type(value).__name__}",
                field="ordering_policy",
            )
        ]

    non_strings = [s for s in value if not isinstance(s, str)]
    if non_strings:
        return [
            OrderingPolicyError(
                f"ordering_policy entries must be strings; got {non_strings!r}",
                field="ordering_policy",
            )
        ]

    canonical = set(ORDERING_STEPS)
    seen: dict[str, int] = {}
    for step in value:
        seen[step] = seen.get(step, 0) + 1

    unknown = [s for s in value if s not in canonical]
    if unknown:
        errors.append(
            OrderingPolicyError(
                f"ordering_policy contains step(s) the engine does not know how "
                f"to run: {sorted(set(unknown))}. Valid steps are "
                f"{list(ORDERING_STEPS)}",
                field="ordering_policy", unknown=sorted(set(unknown)),
            )
        )

    duplicates = sorted(s for s, n in seen.items() if n > 1)
    if duplicates:
        errors.append(
            OrderingPolicyError(
                f"ordering_policy repeats step(s) {duplicates}; each step runs "
                f"exactly once and running one twice would apply it to its own "
                f"output",
                field="ordering_policy", duplicates=duplicates,
            )
        )

    missing = [s for s in ORDERING_STEPS if s not in seen]
    if missing:
        errors.append(
            OrderingPolicyError(
                f"ordering_policy omits step(s) {missing}; a customised order "
                f"must be a permutation of all six steps, not a subset — "
                f"dropping MINIMUM silently bills below an agreed floor",
                field="ordering_policy", missing=missing,
            )
        )

    return errors


# ═══════════════════════════════════════════════════════════════════════════
# Schedule
# ═══════════════════════════════════════════════════════════════════════════


def validate_schedule(
    schedule: Mapping[str, Any] | Any,
    tiers: Iterable[Any] | None = None,
    *,
    exclusions: Iterable[Any] | None = None,
) -> list[FeeValidationError]:
    """Everything that must hold before a schedule may become APPROVED.

    Zero database access. ``schedule`` is a mapping or any object with the
    column names as attributes; ``tiers`` is its tier rows in any order.

    ``exclusions`` is optional and defaults to not-checked. Per the module
    docstring, exclusions do not belong to a schedule — there is no
    ``fee_schedule_id`` on ``fee_exclusions`` to find them by. The argument
    exists for the one honest case: an operator validating a proposed bundle
    before saving any of it. Passing ``None`` is not "no exclusions were
    checked and they all passed", it is "no exclusions were offered".
    """
    errors: list[FeeValidationError] = []

    # ── minimum_fee / minimum_fee_scope ──────────────────────────────────
    minimum_fee = _get(schedule, "minimum_fee")
    minimum_scope = _get(schedule, "minimum_fee_scope")
    if minimum_fee is not None:
        _decimal(minimum_fee, field="minimum_fee", errors=errors)
    if minimum_fee is not None and _is_blank(minimum_scope):
        errors.append(
            MinimumFeeScopeError(
                f"minimum_fee is set ({minimum_fee}) but minimum_fee_scope is "
                f"not. A minimum has to say what it is a minimum PER — the "
                f"same {minimum_fee} applied per ACCOUNT and per HOUSEHOLD "
                f"bill a multi-account family completely different amounts. "
                f"Set minimum_fee_scope to one of {list(MINIMUM_FEE_SCOPES)}",
                field="minimum_fee_scope",
            )
        )
    elif minimum_fee is None and not _is_blank(minimum_scope):
        errors.append(
            MinimumFeeScopeError(
                f"minimum_fee_scope is set ({minimum_scope!r}) but minimum_fee "
                f"is not. A scope with no amount applies no floor; either set "
                f"an amount or clear the scope",
                field="minimum_fee",
            )
        )
    if not _is_blank(minimum_scope) and minimum_scope not in MINIMUM_FEE_SCOPES:
        errors.append(
            VocabularyError(
                f"minimum_fee_scope={minimum_scope!r} is not one of "
                f"{list(MINIMUM_FEE_SCOPES)}",
                field="minimum_fee_scope",
            )
        )

    # ── maximum_fee, and the one ordering trap it carries ────────────────
    maximum_fee = _get(schedule, "maximum_fee")
    if maximum_fee is not None:
        max_errors: list[FeeValidationError] = []
        maximum = _decimal(maximum_fee, field="maximum_fee", errors=max_errors)
        errors.extend(max_errors)
        min_errors: list[FeeValidationError] = []
        minimum = (
            _decimal(minimum_fee, field="minimum_fee", errors=min_errors)
            if minimum_fee is not None
            else None
        )
        # min_errors is intentionally discarded: minimum_fee was already
        # coerced above and appending its error twice would double-report.
        if maximum is not None and minimum is not None and maximum < minimum:
            errors.append(
                FeeValidationError(
                    f"maximum_fee ({maximum}) is below minimum_fee ({minimum}); "
                    f"no fee can satisfy both and the MINIMUM/MAXIMUM steps "
                    f"would fight, with the later one in ordering_policy "
                    f"winning",
                    field="maximum_fee",
                )
            )

    # ── status vocabulary ────────────────────────────────────────────────
    status = _get(schedule, "status")
    if status is not None and status not in SCHEDULE_STATUSES:
        errors.append(
            VocabularyError(
                f"status={status!r} is not one of {list(SCHEDULE_STATUSES)}",
                field="status",
            )
        )

    # ── ordering_policy ──────────────────────────────────────────────────
    errors.extend(validate_ordering_policy(_get(schedule, "ordering_policy")))

    # ── tiers ────────────────────────────────────────────────────────────
    tier_list = list(tiers or [])
    tier_method = _get(schedule, "tier_method")
    if tier_list:
        errors.extend(validate_tiers(tier_list))
    elif not _is_blank(tier_method):
        # A tier_method with no tiers is a schedule that says HOW to walk a
        # ladder it does not have. A schedule with tier_method NULL is a flat
        # or per-account arrangement and legitimately has no tiers at all,
        # which is why this is conditional rather than a blanket requirement.
        errors.append(
            TiersMissingError(
                f"tier_method={tier_method!r} is set but the schedule has no "
                f"tiers; either add the tier ladder or clear tier_method",
                field="tier_method",
            )
        )

    # ── optional bundle ──────────────────────────────────────────────────
    if exclusions is not None:
        for index, exclusion in enumerate(exclusions):
            errors.extend(validate_exclusion(exclusion, index=index))

    return errors


def raise_if_invalid(errors: Sequence[FeeValidationError]) -> None:
    """Raise :class:`FeeScheduleInvalid` carrying every error, or return.

    The one place a fee34 status transition turns a list into a refusal. Kept
    separate from :func:`validate_schedule` so the same checks can be run for
    display — a DRAFT screen wants to SHOW the operator what is still wrong
    without an exception in the middle of rendering.
    """
    if errors:
        raise FeeScheduleInvalid(errors)


# ═══════════════════════════════════════════════════════════════════════════
# Exclusions, discounts, credits
# ═══════════════════════════════════════════════════════════════════════════


def _label(index: int | None, noun: str) -> str:
    return f"{noun} at position {index}" if index is not None else noun


def validate_exclusion(
    exclusion: Mapping[str, Any] | Any, *, index: int | None = None,
) -> list[FeeValidationError]:
    """Every rule on one ``fee_exclusions`` row.

    Called at the exclusion's own write time. See the module docstring for why
    this is not reachable from the schedule-approval gate.
    """
    errors: list[FeeValidationError] = []
    what = _label(index, "exclusion")

    treatment = _get(exclusion, "treatment")
    if treatment is not None and treatment not in EXCLUSION_TREATMENTS:
        errors.append(
            VocabularyError(
                f"{what}: treatment={treatment!r} is not one of "
                f"{list(EXCLUSION_TREATMENTS)}",
                field="treatment",
            )
        )

    scope_type = _get(exclusion, "scope_type")
    if scope_type is not None and scope_type not in EXCLUSION_SCOPE_TYPES:
        errors.append(
            VocabularyError(
                f"{what}: scope_type={scope_type!r} is not one of "
                f"{list(EXCLUSION_SCOPE_TYPES)}. Note this vocabulary uses "
                f"'ORG', not fee_assignments' 'ORG_DEFAULT', and admits no "
                f"'ENTITY'",
                field="scope_type",
            )
        )

    if treatment == "REDUCED_RATE" and _get(exclusion, "alt_fee_schedule_id") is None:
        errors.append(
            ExclusionAltScheduleError(
                f"{what}: treatment is REDUCED_RATE but alt_fee_schedule_id is "
                f"not set. REDUCED_RATE means 'bill this at a different "
                f"schedule' — name which one, or use treatment EXCLUDE to drop "
                f"it from the fee base entirely",
                field="alt_fee_schedule_id",
            )
        )

    if treatment == "FLAT":
        flat_amount = _get(exclusion, "flat_amount")
        if flat_amount is None:
            errors.append(
                ExclusionFlatAmountError(
                    f"{what}: treatment is FLAT but flat_amount is not set. A "
                    f"FLAT exclusion bills a fixed amount instead of a rate; "
                    f"without the amount there is nothing to bill",
                    field="flat_amount",
                )
            )
        else:
            _decimal(flat_amount, field="flat_amount", errors=errors)

    if _is_blank(_get(exclusion, "reason")):
        errors.append(
            ReasonRequiredError(
                f"{what}: reason is required and cannot be blank. The column is "
                f"NOT NULL, which admits '' — an exclusion is the row a "
                f"regulator asks about by name, and an empty reason is the same "
                f"as no reason",
                field="reason",
            )
        )

    return errors


def validate_discount(
    discount: Mapping[str, Any] | Any, *, index: int | None = None,
) -> list[FeeValidationError]:
    """Every rule on one ``fee_discounts`` row.

    ``approved_by`` is ``NOT NULL`` in the database and Task 1 found no
    empty-string gap on it (``uuid`` has no blank form). It is checked here
    anyway so a discount validated BEFORE its insert is refused with a field
    name rather than at the constraint. ``reason`` DOES carry the same
    empty-string gap as the exclusion's, which the prompt names for exclusions
    only — measured, and covered here too.
    """
    errors: list[FeeValidationError] = []
    what = _label(index, "discount")

    scope_type = _get(discount, "scope_type")
    if scope_type is not None and scope_type not in DISCOUNT_SCOPE_TYPES:
        errors.append(
            VocabularyError(
                f"{what}: scope_type={scope_type!r} is not one of "
                f"{list(DISCOUNT_SCOPE_TYPES)}",
                field="scope_type",
            )
        )

    if _get(discount, "approved_by") is None:
        errors.append(
            ApprovedByRequiredError(
                f"{what}: approved_by is required. A discount is a departure "
                f"from the agreed schedule and has to name who authorised it",
                field="approved_by",
            )
        )

    if _is_blank(_get(discount, "reason")):
        errors.append(
            ReasonRequiredError(
                f"{what}: reason is required and cannot be blank",
                field="reason",
            )
        )

    value = _get(discount, "value")
    if value is not None:
        _decimal(value, field="value", errors=errors)

    return errors


def validate_credit(
    credit: Mapping[str, Any] | Any, *, index: int | None = None,
) -> list[FeeValidationError]:
    """Every rule on one ``fee_credits`` row.

    ``offset_pct`` is range-checked against ``fee_credits_offset_pct_range``
    (0..1 inclusive) here as well as in the database, because the constraint's
    message does not say that the column is a FRACTION — an operator entering
    ``50`` meaning fifty percent is the mistake this catches.
    """
    errors: list[FeeValidationError] = []
    what = _label(index, "credit")

    scope_type = _get(credit, "scope_type")
    if scope_type is not None and scope_type not in CREDIT_SCOPE_TYPES:
        errors.append(
            VocabularyError(
                f"{what}: scope_type={scope_type!r} is not one of "
                f"{list(CREDIT_SCOPE_TYPES)}",
                field="scope_type",
            )
        )

    if _get(credit, "approved_by") is None:
        errors.append(
            ApprovedByRequiredError(
                f"{what}: approved_by is required",
                field="approved_by",
            )
        )

    if _is_blank(_get(credit, "reason")):
        errors.append(
            ReasonRequiredError(
                f"{what}: reason is required and cannot be blank",
                field="reason",
            )
        )

    offset = _get(credit, "offset_pct")
    if offset is not None:
        offset_errors: list[FeeValidationError] = []
        parsed = _decimal(offset, field="offset_pct", errors=offset_errors)
        errors.extend(offset_errors)
        if parsed is not None and not (Decimal(0) <= parsed <= Decimal(1)):
            errors.append(
                FeeValidationError(
                    f"{what}: offset_pct={parsed} is outside 0..1. It is a "
                    f"FRACTION of the credit to pass through, not a percentage "
                    f"— 50% is 0.5, not 50",
                    field="offset_pct",
                )
            )

    return errors
