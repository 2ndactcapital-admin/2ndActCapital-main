"""SPV carry waterfall — the pure calculation engine.

Zero database access, in the same discipline as fee35's ``services.fee_calc``:
everything this module needs arrives as an argument, and the same arguments
always produce the same numbers. That is what makes the golden cases in
``scripts/verify_fee42b.py`` hand-computable, and what lets a stored
``spv_carry_run_lines.calc_detail`` be replayed years later.

``services.spv_carry_runs`` is the half that touches the database.


DECIMAL, AT EVERY TIER — NOT JUST AT THE END
──────────────────────────────────────────────────────────────────────────────
A waterfall is four dependent subtractions. A float error in the return-of-
capital tier does not stay in that tier; it moves the hurdle boundary, which
moves the catch-up boundary, which moves the residual split. Every value in
this module is a :class:`~decimal.Decimal` from the boundary inwards, and
every input is coerced through :func:`_dec` — which refuses a ``float``
outright rather than converting it, because ``Decimal(0.08)`` is
``0.08000000000000000166...`` and would silently move a hurdle.


PERCENTS ARE FRACTIONS. THIS MODULE NEVER CONVERTS
──────────────────────────────────────────────────────────────────────────────
``carry_pct=0.20`` is twenty percent. ``hurdle_pct=0.08`` is eight percent.
``catchup_pct=1.00`` is a full catch-up. This is the convention
``services.spv_fee_terms`` already states for ``mgmt_fee_pct`` and step-down
``pct``, and it exists because fee35 found ``PCT_OFF`` and ``offset_pct``
differing by 100x elsewhere in the same subsystem.

The deployed ``spv_fee_terms`` has **no range CHECK** on ``carry_pct``,
``hurdle_pct`` or ``catchup_pct`` (measured — the only CHECKs on that table are
``carry_basis``, ``hurdle_type``, ``mgmt_fee_basis``, ``mgmt_fee_frequency``
and ``carry_requires_hurdle_type``). Nothing in the database stops ``20`` being
stored where ``0.20`` was meant. So the scale is enforced HERE, loudly:
:class:`PercentScaleError` on any rate above 1. A carry of ``20`` read as a
fraction pays the GP twenty times the entire distribution; there is no reading
of that number that is worth guessing at.


HARD vs SOFT — THE DISTINCTION, AND WHY THIS PAIRING
──────────────────────────────────────────────────────────────────────────────
Nothing in this repository defines the two. The only prior mention is a prose
example in ``services/spv_fee_terms.py``'s docstring ("an 8% soft hurdle and a
100% catch-up"), which uses the terms without defining them, and the deployed
CHECK admits ``HARD``/``SOFT``/``NONE`` with no semantics attached. So the
standard private-equity convention applies, and it is stated once, here:

  ``SOFT``  Once the preferred return is fully paid, the GP CATCHES UP on the
            whole of it. The catch-up tier runs until the GP has received
            ``carry_pct`` of ALL profit distributed so far — including the
            preferred return the LP already received. A soft hurdle is
            therefore a *timing* preference: it delays the GP's carry, it does
            not reduce it.

  ``HARD``  The GP receives carry ONLY on profit ABOVE the hurdle. There is no
            catch-up tier at all, and ``gp_catchup`` is always zero. The
            preferred return is permanently the LP's. A hard hurdle is an
            *economic* preference: it genuinely reduces the GP's take.

  ``NONE``  No preferred return. After capital is returned, every dollar of
            profit splits at ``carry_pct`` from the first one.

The direction is the check that matters: on identical facts a HARD hurdle must
pay the GP **less** than a SOFT one, never the same and never more. Golden
cases 3 and 4 in the verify script are exactly that fixture pair, and they
differ by ``hurdle_type`` and nothing else.

A ``SOFT`` hurdle with no ``catchup_pct`` is REFUSED
(:class:`CarryTermsIncompleteError`) rather than treated as a hard hurdle.
Those two readings differ by real money to a real GP, and the terms row is
saying neither.


THE PREFERRED RETURN CONVENTION, NAMED
──────────────────────────────────────────────────────────────────────────────
``hurdle_pct`` is a rate; a preferred return is an amount. Turning one into the
other needs a convention, and no deployed column, CHECK or document supplies
one. This module implements exactly ONE and says so in every ``calc_detail``:

    preferred_return_owed = hurdle_pct x cumulative_paid_in

— a **cumulative, non-compounding preferred return on contributed capital**
(:data:`PREF_CONVENTION`). It is not annualised and not time-weighted.

That is a real convention for single-asset deal-by-deal vehicles, and it is the
only one the Task-2 interface can support: the inputs the sprint specifies
(cumulative paid-in, cumulative distributed, the terms) carry no dates. A
time-weighted IRR-style accrual would need dated contribution and distribution
flows AND a compounding convention nobody has specified, and picking one
silently would move every hurdle boundary in the system.

:func:`compute_carry` therefore accepts an explicit ``preferred_return_owed``
override. When a later sprint settles the accrual convention it replaces one
argument at one call site; the waterfall below does not change.


THE FOUR TIERS TILE THE DISTRIBUTION EXACTLY
──────────────────────────────────────────────────────────────────────────────
``return_of_capital + preferred_return + catchup_tier + residual_tier`` equals
the distribution, to the cent, always — and
``net_to_lp + carry_to_gp == gross_gain_allocated`` exactly, which is the
deployed ``spv_carry_run_lines_balance_check``. A row whose arithmetic does not
reconcile cannot be written at all, so this is enforced by construction rather
than by hoping:

  * every tier BOUNDARY is quantised to cents;
  * the last tier in each split takes the REMAINDER rather than being computed
    independently (``residual = after_pref - catchup_tier``,
    ``lp = distribution - gp``), so a half-cent has nowhere to go.


CUMULATIVE FIRST, THEN DIFFERENCED — AND WHY
──────────────────────────────────────────────────────────────────────────────
The waterfall is run twice: once over ``cumulative_distributed`` (everything
this investor had already received) and once over
``cumulative_distributed + gross_gain_allocated``. THIS realization's numbers
are the difference.

Running it only over the new distribution would be wrong the moment an investor
has ever been paid before: the second realization would return capital that was
already returned, and pay a preferred return that was already paid. Differencing
cumulative states also makes the tiers monotonic — each tier function is
non-decreasing in the distributed total, so no incremental amount can come back
negative — and it means rounding never accumulates across a vehicle's life,
because every number is a difference of two independently-quantised cumulative
totals.

It is also what makes ``WHOLE_FUND`` a question about SCOPE rather than about
arithmetic: see :data:`CARRY_BASES` and ``services.spv_carry_runs``.


WHAT THIS MODULE DOES NOT DO
──────────────────────────────────────────────────────────────────────────────
It does not read a database, resolve terms, decide whether a distribution is a
realization, or write anything. It does not execute a clawback: it reports
:attr:`CarryResult.clawback_exposure` — the theoretical amount a GP would owe
back if the vehicle wound up on these numbers — and stops there.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Mapping

#: Bumped when a change here could change a number. Stamped into every
#: ``calc_detail`` and onto ``spv_carry_runs.engine_version``, so a line
#: recomputed under a later engine can be told apart from one that was always
#: wrong.
ENGINE_VERSION = "fee42b.1"

ZERO = Decimal(0)
ONE = Decimal(1)
CENTS = Decimal("0.01")

#: ``spv_fee_terms_hurdle_type_check``, read from the deployed constraint.
HURDLE_HARD = "HARD"
HURDLE_SOFT = "SOFT"
HURDLE_NONE = "NONE"
HURDLE_TYPES = (HURDLE_HARD, HURDLE_SOFT, HURDLE_NONE)

#: ``spv_fee_terms_carry_basis_check`` and ``spv_carry_runs_carry_basis_check``,
#: read from the deployed constraints. The basis does NOT change the arithmetic
#: below — it changes what the caller must pass as "cumulative": one deal's
#: history, or the whole vehicle's. See ``services.spv_carry_runs`` for what is
#: actually derivable today.
CARRY_DEAL_BY_DEAL = "DEAL_BY_DEAL"
CARRY_WHOLE_FUND = "WHOLE_FUND"
CARRY_BASES = (CARRY_DEAL_BY_DEAL, CARRY_WHOLE_FUND)

#: The one preferred-return convention this engine implements. Emitted into
#: every ``calc_detail`` so the assumption travels with the number.
PREF_CONVENTION = "HURDLE_PCT_X_CUMULATIVE_PAID_IN__NON_COMPOUNDING"


# ═══════════════════════════════════════════════════════════════════════════
# Errors
# ═══════════════════════════════════════════════════════════════════════════


class SpvCarryError(ValueError):
    """Base class. Carries structured context alongside the message."""

    code = "spv_carry_error"

    def __init__(self, message: str, **context: Any) -> None:
        super().__init__(message)
        self.context = context


class CarryInputError(SpvCarryError):
    """An amount or rate that cannot be read as money."""

    code = "carry_input_error"


class PercentScaleError(CarryInputError):
    """A rate above 1. See "PERCENTS ARE FRACTIONS" in the module docstring."""

    code = "carry_percent_scale_error"


class VocabularyError(CarryInputError):
    """A ``hurdle_type`` or ``carry_basis`` outside the deployed CHECK."""

    code = "carry_vocabulary_error"


class CarryTermsIncompleteError(SpvCarryError):
    """The terms row does not say enough to compute a number honestly."""

    code = "carry_terms_incomplete"


class CatchupUnreachableError(SpvCarryError):
    """``catchup_pct <= carry_pct``: the catch-up tier can never complete."""

    code = "carry_catchup_unreachable"


# ═══════════════════════════════════════════════════════════════════════════
# Coercion — the boundary where floats are refused
# ═══════════════════════════════════════════════════════════════════════════


def _dec(value: Any, *, field: str, allow_none: bool = False) -> Decimal | None:
    """Coerce to :class:`Decimal`, refusing ``float`` outright.

    ``Decimal(0.08)`` is 0.08000000000000000166533453693773481063544750213623,
    and a hurdle boundary built from it is wrong in a way nothing downstream
    can see. Callers hand over strings, ints or Decimals; a float is a bug at
    the boundary, not something to round away.
    """
    if value is None:
        if allow_none:
            return None
        raise CarryInputError(f"{field} is required and was None", field=field)
    if isinstance(value, Decimal):
        return value
    if isinstance(value, bool) or isinstance(value, float):
        raise CarryInputError(
            f"{field}={value!r} is a {type(value).__name__}. This engine takes "
            f"Decimal, str or int only — converting a float here would move a "
            f"tier boundary by an amount nothing downstream could detect",
            field=field,
        )
    try:
        return Decimal(str(value))
    except Exception as exc:  # noqa: BLE001
        raise CarryInputError(
            f"{field}={value!r} is not a number: {exc}", field=field
        ) from exc


def _rate(value: Any, *, field: str, allow_none: bool = False) -> Decimal | None:
    """A fraction in [0, 1]. See "PERCENTS ARE FRACTIONS"."""
    d = _dec(value, field=field, allow_none=allow_none)
    if d is None:
        return None
    if d < ZERO:
        raise CarryInputError(f"{field}={d} is negative", field=field, value=str(d))
    if d > ONE:
        raise PercentScaleError(
            f"{field}={d} is greater than 1. Rates in this subsystem are "
            f"FRACTIONS — 20% is 0.20, not 20. spv_fee_terms carries no range "
            f"CHECK on this column, so the scale is enforced here rather than "
            f"paying a GP {d}x the distribution",
            field=field, value=str(d),
        )
    return d


def _money(value: Any, *, field: str, allow_negative: bool = False) -> Decimal:
    d = _dec(value, field=field)
    if not allow_negative and d < ZERO:
        raise CarryInputError(
            f"{field}={d} is negative. A negative distribution is not a "
            f"realization; it is a correction, and it needs its own reversing "
            f"run rather than a waterfall run backwards",
            field=field, value=str(d),
        )
    return _q(d)


def _q(value: Decimal) -> Decimal:
    """Quantise to cents, half-up. Every tier boundary passes through here."""
    return value.quantize(CENTS, rounding=ROUND_HALF_UP)


def _s(value: Decimal) -> str:
    """Decimal -> exact JSON string. ``json.dumps`` works with no encoder,
    which is what ``spv_carry_run_lines.calc_detail`` (jsonb) needs."""
    return str(value)


# ═══════════════════════════════════════════════════════════════════════════
# Inputs
# ═══════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class CarryTerms:
    """The economics, already resolved through any side letter.

    Built from a ``services.spv_fee_terms.SpvFeeTerms`` by
    :func:`terms_from_resolved` — this engine never resolves anything itself.
    """

    carry_pct: Decimal
    hurdle_type: str
    carry_basis: str
    hurdle_pct: Decimal = ZERO
    catchup_pct: Decimal | None = None
    clawback_applies: bool = True
    source: str | None = None
    side_letter_id: str | None = None

    def as_detail(self) -> dict[str, Any]:
        return {
            "carry_pct": _s(self.carry_pct),
            "hurdle_pct": _s(self.hurdle_pct),
            "hurdle_type": self.hurdle_type,
            "catchup_pct": None if self.catchup_pct is None else _s(self.catchup_pct),
            "carry_basis": self.carry_basis,
            "clawback_applies": self.clawback_applies,
            "source": self.source,
            "side_letter_id": self.side_letter_id,
        }


@dataclass(frozen=True)
class InvestorState:
    """One investor's cumulative position in the vehicle, BEFORE this
    realization.

    ``cumulative_distributed`` is every distribution this investor has already
    received within the scope named by ``carry_basis`` — not including the one
    being calculated.
    """

    cumulative_paid_in: Decimal
    cumulative_distributed: Decimal = ZERO
    source: str | None = None


@dataclass(frozen=True)
class _Tiers:
    """The waterfall evaluated over ONE cumulative distributed total."""

    distributed: Decimal
    return_of_capital: Decimal
    preferred_return: Decimal
    catchup_tier: Decimal
    gp_catchup: Decimal
    catchup_to_lp: Decimal
    residual_tier: Decimal
    residual_carry: Decimal
    residual_to_lp: Decimal
    carry_to_gp: Decimal
    net_to_lp: Decimal
    hurdle_cleared: bool
    catchup_complete: bool


@dataclass(frozen=True)
class CarryResult:
    """THIS realization's share of the waterfall.

    ``carry_to_gp`` is the GP's TOTAL for this realization and INCLUDES
    ``gp_catchup``; ``gp_catchup`` is published separately because the deployed
    table has a column for it and because "the GP was caught up" and "the GP
    took its residual carry" are two different economic events. Only
    ``net_to_lp`` and ``carry_to_gp`` participate in the balance constraint.
    """

    gross_gain_allocated: Decimal
    return_of_capital: Decimal
    preferred_return: Decimal
    gp_catchup: Decimal
    carry_to_gp: Decimal
    net_to_lp: Decimal
    clawback_exposure: Decimal
    calc_detail: dict[str, Any]

    def reconciles(self) -> bool:
        """The deployed ``spv_carry_run_lines_balance_check``, checked here
        too so a caller learns before the INSERT rather than from a 23514."""
        return self.net_to_lp + self.carry_to_gp == self.gross_gain_allocated

    def as_row(self) -> dict[str, Any]:
        """The column set ``spv_carry_run_lines`` actually has."""
        return {
            "gross_gain_allocated": self.gross_gain_allocated,
            "return_of_capital": self.return_of_capital,
            "preferred_return": self.preferred_return,
            "gp_catchup": self.gp_catchup,
            "carry_to_gp": self.carry_to_gp,
            "net_to_lp": self.net_to_lp,
            "calc_detail": self.calc_detail,
        }


# ═══════════════════════════════════════════════════════════════════════════
# Terms
# ═══════════════════════════════════════════════════════════════════════════


def terms_from_resolved(resolved: Any, *, require_carry: bool = True) -> CarryTerms:
    """Adapt fee42's resolved ``SpvFeeTerms`` (or any mapping) to this engine.

    Deliberately an ADAPTER, not a second resolver: side letters, class terms
    and the temporal windows are ``services.spv_fee_terms``' job and are not
    re-implemented here. Everything this function does is read fields and
    validate that the set of them is complete enough to compute a number.
    """
    get = (
        resolved.get if isinstance(resolved, Mapping)
        else lambda k, d=None: getattr(resolved, k, d)
    )

    carry_pct = _rate(get("carry_pct"), field="carry_pct", allow_none=True)
    hurdle_type = get("hurdle_type")
    carry_basis = get("carry_basis")

    if carry_pct is None:
        if require_carry:
            raise CarryTermsIncompleteError(
                "carry_pct is NULL on the resolved terms. An SPV with no carry "
                "rate owes no carry, but that is a decision for whoever wrote "
                "the term sheet to make explicitly (carry_pct = 0), not "
                "something to infer from an absent value",
                field="carry_pct",
            )
        carry_pct = ZERO

    if hurdle_type is None:
        # Mirrors the deployed spv_fee_terms_carry_requires_hurdle_type CHECK.
        raise CarryTermsIncompleteError(
            "hurdle_type is NULL while carry_pct is set. Carry with an "
            "unspecified hurdle is not a term sheet — HARD, SOFT and NONE pay "
            "the GP three different numbers on the same facts",
            field="hurdle_type",
        )
    if hurdle_type not in HURDLE_TYPES:
        raise VocabularyError(
            f"hurdle_type={hurdle_type!r} is not one of {list(HURDLE_TYPES)}",
            field="hurdle_type", value=hurdle_type,
        )

    if carry_basis is None:
        raise CarryTermsIncompleteError(
            "carry_basis is NULL. DEAL_BY_DEAL and WHOLE_FUND net a "
            "realization against different histories; there is no safe default",
            field="carry_basis",
        )
    if carry_basis not in CARRY_BASES:
        raise VocabularyError(
            f"carry_basis={carry_basis!r} is not one of {list(CARRY_BASES)}",
            field="carry_basis", value=carry_basis,
        )

    hurdle_pct = _rate(get("hurdle_pct"), field="hurdle_pct", allow_none=True)
    catchup_pct = _rate(get("catchup_pct"), field="catchup_pct", allow_none=True)

    if hurdle_type == HURDLE_NONE:
        hurdle_pct = ZERO
    elif hurdle_pct is None:
        raise CarryTermsIncompleteError(
            f"hurdle_type={hurdle_type!r} but hurdle_pct is NULL. A hurdle "
            f"with no rate has no boundary; say hurdle_type='NONE' if the deal "
            f"genuinely has no preferred return",
            field="hurdle_pct",
        )

    if hurdle_type == HURDLE_SOFT:
        if catchup_pct is None:
            raise CarryTermsIncompleteError(
                "hurdle_type='SOFT' with catchup_pct NULL. A soft hurdle IS a "
                "catch-up; without a rate this row is not distinguishable from "
                "a HARD hurdle, and the two pay the GP different money",
                field="catchup_pct",
            )
        if catchup_pct <= carry_pct:
            raise CatchupUnreachableError(
                f"catchup_pct={catchup_pct} is not greater than "
                f"carry_pct={carry_pct}. The catch-up tier runs until the GP "
                f"holds carry_pct of all profit; at that rate it never gets "
                f"there and the tier would consume every remaining dollar",
                catchup_pct=str(catchup_pct), carry_pct=str(carry_pct),
            )

    return CarryTerms(
        carry_pct=carry_pct,
        hurdle_type=hurdle_type,
        carry_basis=carry_basis,
        hurdle_pct=hurdle_pct if hurdle_pct is not None else ZERO,
        catchup_pct=catchup_pct,
        clawback_applies=bool(get("clawback_applies", True)),
        source=get("source"),
        side_letter_id=(
            None if get("side_letter_id") is None else str(get("side_letter_id"))
        ),
    )


def preferred_return_owed(
    cumulative_paid_in: Decimal, hurdle_pct: Decimal
) -> Decimal:
    """The one convention this engine implements. See :data:`PREF_CONVENTION`.

    Kept as its own function so a later sprint that settles a time-weighted,
    compounding accrual replaces exactly this, and the waterfall does not move.
    """
    return _q(cumulative_paid_in * hurdle_pct)


def catchup_tier_size(pref_paid: Decimal, terms: CarryTerms) -> Decimal:
    """How large the catch-up tier is, given a preferred return of ``pref_paid``.

    The tier runs until the GP holds ``carry_pct`` of everything distributed as
    PROFIT so far — the preferred return included. Solving

        catchup_pct x C = carry_pct x (pref_paid + C)

    gives ``C = carry_pct x pref_paid / (catchup_pct - carry_pct)``. With a
    100% catch-up and 20% carry on an 80,000 preferred return that is 20,000:
    the GP takes all 20,000, total profit paid is 100,000, and the GP holds
    exactly 20% of it. That identity is the definition of a catch-up, and it is
    the check to apply to any change here.
    """
    if terms.hurdle_type != HURDLE_SOFT or terms.catchup_pct is None:
        return ZERO
    if pref_paid <= ZERO or terms.carry_pct <= ZERO:
        return ZERO
    denominator = terms.catchup_pct - terms.carry_pct
    if denominator <= ZERO:  # guarded in terms_from_resolved; belt and braces
        raise CatchupUnreachableError(
            f"catchup_pct={terms.catchup_pct} <= carry_pct={terms.carry_pct}",
            catchup_pct=str(terms.catchup_pct), carry_pct=str(terms.carry_pct),
        )
    return _q(terms.carry_pct * pref_paid / denominator)


# ═══════════════════════════════════════════════════════════════════════════
# The waterfall
# ═══════════════════════════════════════════════════════════════════════════


def _tiers(
    distributed: Decimal,
    *,
    paid_in: Decimal,
    pref_owed: Decimal,
    terms: CarryTerms,
) -> _Tiers:
    """Evaluate the whole waterfall over ONE cumulative distributed total.

    Non-decreasing in ``distributed`` in every component, which is what makes
    differencing two of these safe (see the module docstring).
    """
    # ── Tier 1. Return of capital. Every dollar to the LP.
    roc = distributed if distributed < paid_in else paid_in
    after_roc = distributed - roc

    # ── Tier 2. Preferred return, capped at what is owed. Every dollar to the
    #    LP under all three hurdle types.
    pref = after_roc if after_roc < pref_owed else pref_owed
    after_pref = after_roc - pref
    hurdle_cleared = pref >= pref_owed

    # ── Tier 3. GP catch-up. SOFT only — under HARD there is no catch-up at
    #    all, and that absence IS the hard hurdle.
    catchup_tier = ZERO
    if hurdle_cleared and after_pref > ZERO and terms.hurdle_type == HURDLE_SOFT:
        full = catchup_tier_size(pref, terms)
        catchup_tier = after_pref if after_pref < full else full
    gp_catchup = _q(catchup_tier * (terms.catchup_pct or ZERO))
    catchup_to_lp = catchup_tier - gp_catchup  # the remainder, never recomputed
    catchup_complete = (
        terms.hurdle_type != HURDLE_SOFT
        or catchup_tier >= catchup_tier_size(pref, terms)
    )

    # ── Tier 4. Residual, split carry_pct / (1 - carry_pct).
    residual_tier = after_pref - catchup_tier
    residual_carry = _q(residual_tier * terms.carry_pct)
    residual_to_lp = residual_tier - residual_carry  # the remainder

    carry_to_gp = gp_catchup + residual_carry
    net_to_lp = distributed - carry_to_gp  # the remainder: cannot drift

    return _Tiers(
        distributed=distributed,
        return_of_capital=roc,
        preferred_return=pref,
        catchup_tier=catchup_tier,
        gp_catchup=gp_catchup,
        catchup_to_lp=catchup_to_lp,
        residual_tier=residual_tier,
        residual_carry=residual_carry,
        residual_to_lp=residual_to_lp,
        carry_to_gp=carry_to_gp,
        net_to_lp=net_to_lp,
        hurdle_cleared=hurdle_cleared,
        catchup_complete=catchup_complete,
    )


def _tier_detail(t: _Tiers, *, paid_in: Decimal, pref_owed: Decimal) -> list[dict]:
    """The four tiers with their boundaries and the balance running down.

    An operator holding one ``spv_carry_run_lines`` row must be able to answer
    "why is this number this number" without re-running the engine. So each
    entry carries the balance it received, the cap that bounded it, what it
    took, who took it, and the balance it handed on.
    """
    b0 = t.distributed
    b1 = b0 - t.return_of_capital
    b2 = b1 - t.preferred_return
    b3 = b2 - t.catchup_tier
    b4 = b3 - t.residual_tier
    return [
        {
            "tier": 1,
            "name": "RETURN_OF_CAPITAL",
            "balance_in": _s(b0),
            "cap": _s(paid_in),
            "cap_source": "cumulative_paid_in",
            "amount": _s(t.return_of_capital),
            "to_lp": _s(t.return_of_capital),
            "to_gp": _s(ZERO),
            "balance_out": _s(b1),
            "exhausted": t.return_of_capital >= paid_in,
        },
        {
            "tier": 2,
            "name": "PREFERRED_RETURN",
            "balance_in": _s(b1),
            "cap": _s(pref_owed),
            "cap_source": PREF_CONVENTION,
            "amount": _s(t.preferred_return),
            "to_lp": _s(t.preferred_return),
            "to_gp": _s(ZERO),
            "balance_out": _s(b2),
            "hurdle_cleared": t.hurdle_cleared,
        },
        {
            "tier": 3,
            "name": "GP_CATCHUP",
            "balance_in": _s(b2),
            "cap": _s(t.catchup_tier),
            "cap_source": (
                "carry_pct x preferred_return / (catchup_pct - carry_pct)"
                if t.catchup_tier > ZERO
                else "no catch-up tier under this hurdle_type"
            ),
            "amount": _s(t.catchup_tier),
            "to_lp": _s(t.catchup_to_lp),
            "to_gp": _s(t.gp_catchup),
            "balance_out": _s(b3),
            "catchup_complete": t.catchup_complete,
        },
        {
            "tier": 4,
            "name": "RESIDUAL_SPLIT",
            "balance_in": _s(b3),
            "cap": None,
            "cap_source": "unbounded — takes the remainder",
            "amount": _s(t.residual_tier),
            "to_lp": _s(t.residual_to_lp),
            "to_gp": _s(t.residual_carry),
            "balance_out": _s(b4),
            "split": "carry_pct / (1 - carry_pct)",
        },
    ]


def compute_carry(
    *,
    gross_gain_allocated: Any,
    state: InvestorState,
    terms: CarryTerms,
    preferred_return_owed_override: Any = None,
    entity_id: Any = None,
) -> CarryResult:
    """One investor's share of one realization, through the four tiers.

    ``gross_gain_allocated`` is that investor's own allocated amount from the
    realizing distribution — ``spv_transaction_allocations.allocated_amount``,
    the posted split, never a re-derivation of it.

    Everything returned is quantised to cents and reconciles exactly:
    ``net_to_lp + carry_to_gp == gross_gain_allocated``.
    """
    g = _money(gross_gain_allocated, field="gross_gain_allocated")
    paid_in = _money(state.cumulative_paid_in, field="cumulative_paid_in")
    prior = _money(state.cumulative_distributed, field="cumulative_distributed")

    if preferred_return_owed_override is not None:
        pref_owed = _money(
            preferred_return_owed_override, field="preferred_return_owed"
        )
        pref_source = "EXPLICIT_OVERRIDE"
    else:
        pref_owed = preferred_return_owed(paid_in, terms.hurdle_pct)
        pref_source = PREF_CONVENTION

    total = prior + g
    prior_tiers = _tiers(prior, paid_in=paid_in, pref_owed=pref_owed, terms=terms)
    total_tiers = _tiers(total, paid_in=paid_in, pref_owed=pref_owed, terms=terms)

    # THIS realization is the difference of two cumulative states.
    roc = total_tiers.return_of_capital - prior_tiers.return_of_capital
    pref = total_tiers.preferred_return - prior_tiers.preferred_return
    gp_catchup = total_tiers.gp_catchup - prior_tiers.gp_catchup
    carry_to_gp = total_tiers.carry_to_gp - prior_tiers.carry_to_gp
    net_to_lp = total_tiers.net_to_lp - prior_tiers.net_to_lp
    catchup_tier = total_tiers.catchup_tier - prior_tiers.catchup_tier
    catchup_to_lp = total_tiers.catchup_to_lp - prior_tiers.catchup_to_lp
    residual_tier = total_tiers.residual_tier - prior_tiers.residual_tier
    residual_carry = total_tiers.residual_carry - prior_tiers.residual_carry
    residual_to_lp = total_tiers.residual_to_lp - prior_tiers.residual_to_lp

    # Clawback exposure: what the GP would owe back if the vehicle wound up on
    # these numbers — the carry it holds, less the carry the LP's cumulative
    # position actually supports. RECORDED, never executed (out of scope, and
    # a different mechanism entirely).
    cumulative_profit = total - paid_in
    supportable = (
        _q(cumulative_profit * terms.carry_pct) if cumulative_profit > ZERO else ZERO
    )
    exposure = total_tiers.carry_to_gp - supportable
    clawback_exposure = exposure if exposure > ZERO else ZERO

    result = CarryResult(
        gross_gain_allocated=g,
        return_of_capital=roc,
        preferred_return=pref,
        gp_catchup=gp_catchup,
        carry_to_gp=carry_to_gp,
        net_to_lp=net_to_lp,
        clawback_exposure=clawback_exposure,
        calc_detail={
            "engine_version": ENGINE_VERSION,
            "entity_id": None if entity_id is None else str(entity_id),
            "terms": terms.as_detail(),
            "assumptions": {
                "preferred_return_convention": pref_source,
                "preferred_return_owed": _s(pref_owed),
                "percent_scale": "FRACTION (0.20 == 20%)",
                "hurdle_semantics": {
                    "HARD": "no catch-up; GP carries only above the hurdle",
                    "SOFT": "GP catches up on the whole preferred return",
                    "NONE": "no preferred return; carry from the first profit "
                            "dollar after capital is returned",
                },
                "carry_basis_scope": (
                    "cumulative_paid_in / cumulative_distributed are scoped by "
                    "the CALLER to " + terms.carry_basis
                ),
            },
            "inputs": {
                "gross_gain_allocated": _s(g),
                "cumulative_paid_in": _s(paid_in),
                "cumulative_distributed_before": _s(prior),
                "cumulative_distributed_after": _s(total),
                "state_source": state.source,
            },
            # Both cumulative evaluations are published, not just their
            # difference. Without them "why did this realization pay no
            # preferred return" is unanswerable from the row alone — the answer
            # is that an earlier one already paid it.
            "cumulative_before": _tier_detail(
                prior_tiers, paid_in=paid_in, pref_owed=pref_owed
            ),
            "cumulative_after": _tier_detail(
                total_tiers, paid_in=paid_in, pref_owed=pref_owed
            ),
            "this_realization": {
                "return_of_capital": _s(roc),
                "preferred_return": _s(pref),
                "catchup_tier": _s(catchup_tier),
                "catchup_to_gp": _s(gp_catchup),
                "catchup_to_lp": _s(catchup_to_lp),
                "residual_tier": _s(residual_tier),
                "residual_to_gp": _s(residual_carry),
                "residual_to_lp": _s(residual_to_lp),
                "carry_to_gp": _s(carry_to_gp),
                "net_to_lp": _s(net_to_lp),
            },
            "reconciliation": {
                # The deployed CHECK, restated as arithmetic anyone can redo.
                "balance_check": f"{_s(net_to_lp)} + {_s(carry_to_gp)} = {_s(g)}",
                "balance_ok": net_to_lp + carry_to_gp == g,
                "tiers_tile": f"{_s(roc)} + {_s(pref)} + {_s(catchup_tier)} + "
                              f"{_s(residual_tier)} = {_s(g)}",
                "tiers_tile_ok": roc + pref + catchup_tier + residual_tier == g,
                "hurdle_cleared": total_tiers.hurdle_cleared,
                "catchup_complete": total_tiers.catchup_complete,
            },
            "clawback": {
                "applies": terms.clawback_applies,
                "cumulative_carry_to_gp": _s(total_tiers.carry_to_gp),
                "carry_supported_by_cumulative_profit": _s(supportable),
                "exposure": _s(clawback_exposure),
                "note": "RECORDED ONLY. Recovering carry already paid to a GP "
                        "is a separate mechanism and is out of scope here.",
            },
        },
    )

    # Both invariants, asserted rather than assumed. A violation here is an
    # engine bug and must surface as one, not as a 23514 from the INSERT with
    # no context attached.
    if not result.reconciles():
        raise SpvCarryError(
            f"engine did not reconcile: net_to_lp={net_to_lp} + "
            f"carry_to_gp={carry_to_gp} != gross_gain_allocated={g}",
            entity_id=None if entity_id is None else str(entity_id),
        )
    if roc + pref + catchup_tier + residual_tier != g:
        raise SpvCarryError(
            f"engine tiers did not tile: {roc} + {pref} + {catchup_tier} + "
            f"{residual_tier} != {g}",
            entity_id=None if entity_id is None else str(entity_id),
        )
    return result
