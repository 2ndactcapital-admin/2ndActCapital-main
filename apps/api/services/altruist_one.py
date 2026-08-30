"""Sprint fee38 — the Altruist One enrollment evaluator.

WHAT THIS IS
──────────────────────────────────────────────────────────────────────────────

Altruist One is a custodian subscription. Enrolling costs the firm a
subscription fee and returns a bundle of benefits — a better sweep-cash yield,
a high-yield cash program, a discount off third-party model fees, a narrower
margin spread. This module answers ONE question for ONE household on ONE date:
is the bundle worth more than it costs?

It computes against the firm's OWN account and balance data plus the rate card
fee37 seeded. **It does not call Altruist.** No API client lives here, and none
is planned until fee45 (gated on partner access and published API docs).


THE UNVERIFIED-RATE CAVEAT — READ BEFORE TRUSTING A NUMBER
──────────────────────────────────────────────────────────────────────────────

fee37 recorded finding F6: the rates seeded into ``cost_schedules`` came from
research that was never re-checked against a primary source. Every rate this
module reads — from ``cost_schedules`` AND from the
``provider_benefit_schedules`` rows it seeds itself — carries that identical
limitation.

So :data:`UNVERIFIED_CAVEAT` is attached to every :class:`Evaluation` this
module produces, and :meth:`Evaluation.to_row` writes it into the persisted
``benefit_breakdown`` JSON. It is NOT a code comment. A recommendation that
reaches a screen without the caveat attached to it is the failure mode this is
guarding against, and a comment cannot travel that far.

Corollary: this module seeds ``source_verified_on`` from a REQUIRED caller
argument with no default, exactly as ``cost_model.seed_altruist_profile`` does.
A default would stamp a verification date nobody chose.


WHAT THE DATA CANNOT DO (measured, Task 1)
──────────────────────────────────────────────────────────────────────────────

The formula wants five account-level inputs. The deployed schema supplies two
and a half of them:

* ``household_value``   — REAL. ``account_balances_daily.total_market_value``.
* ``margin_balance``    — REAL. ``account_balances_daily.margin_balance``.
* ``account_count``     — REAL. ``accounts`` filtered by ``household_id``.
* sweep vs HY cash      — **NOT SEPARABLE.** ``account_balances_daily`` holds a
  single ``cash_value`` numeric. There is no cash-type dimension, no
  ``portfolio.cash_balances`` table, nothing anywhere that says which dollars
  sit in the sweep and which sit in the high-yield program. So the split is a
  caller-supplied INPUT (:attr:`HouseholdInputs.sweep_cash`,
  :attr:`HouseholdInputs.hy_cash`), defaulting to "all cash is sweep", and the
  assumption is reported in :attr:`Evaluation.data_gaps` rather than hidden.
* ``model_marketplace_aum`` — **DOES NOT EXIST.** ``accounts.service_model`` is
  free text with no model-identity or model-allocated-value column behind it.
  Caller-supplied, defaults to zero, reported as a gap.
* ``trade_count``       — **DOES NOT EXIST** at the account level.
  ``portfolio.transactions`` reaches an account only through
  ``positions.account_id``, and both tables are empty. The sprint said: do not
  invent one. So ``trade_count=None`` is the default, ticket savings are
  OMITTED ENTIRELY from ``benefit_breakdown`` when it is None (not zeroed —
  omitted, because a zero reads as "measured and found to be nothing"), and the
  gap is reported.

Every gap is a caller-supplied override, never a silently-invented figure.


THE ``account_balances_daily`` DOUBLE-COUNT TRAP
──────────────────────────────────────────────────────────────────────────────

Its primary key is ``(org_id, account_id, as_of_date, source_system)`` — one
account can hold several balance rows for the SAME day from different feeds. A
plain ``SUM(total_market_value)`` over a household double-counts every
multi-feed account. This is the same shape as fee37's F4 (a dedupe index that
does not quite close), and the sprint warned not to assume reads are
duplicate-free.

:func:`load_household_inputs` takes ONE row per account via ``DISTINCT ON``,
restricted to ``is_billing_source``, and separately COUNTS the accounts that
have more than one billing-source row on their latest date, surfacing that as a
data gap. The dedupe is not left to a WHERE clause that might match two rows.


THE SUBSCRIPTION AMBIGUITY, AND WHICH READING THIS MODULE USES
──────────────────────────────────────────────────────────────────────────────

fee37 seeded BOTH readings of the subscription line as separate rows, plus
``assert_no_ambiguous_overlap`` to stop a consumer summing them:

* reading A, FLOOR    — monthly = max(0.01% x value, $1 x accounts)
* reading B, ADDITIVE — monthly = 0.01% x value + $1 x accounts

This module must pick one. It defaults to **FLOOR**, for two reasons, and it
says so in :attr:`Evaluation.subscription_reading` on every output:

1. The design doc states the cost as ``max(0.0012 * household_value,
   12 * account_count)``. That IS reading A, in annual terms.
2. FLOOR is one self-contained seeded row; ADDITIVE is two rows that must be
   summed, and summing is the operation the ambiguity guard exists to police.

fee37's own note argues the opposite — that ADDITIVE is the conservative
choice because it is the more expensive one. That conflict is real and
unresolved, so ``subscription_reading`` is a parameter, both readings are
implemented, and every evaluation records which one produced its number. See
finding F2 in the sprint report.


TLH TAX ALPHA IS COMPUTED AND DELIBERATELY EXCLUDED
──────────────────────────────────────────────────────────────────────────────

Per the design doc, tax-loss-harvesting alpha does NOT enter the
ENROLL / DO_NOT_ENROLL / MARGINAL decision. It is an estimate of a
client-side, tax-situation-dependent benefit, and letting it move a firm-side
cost/benefit threshold would let the softest number in the model decide the
hardest question.

So it is computed when the input exists, returned on
:attr:`Evaluation.tax_alpha` labelled ``estimated``, and
:func:`_recommend` never sees it. :attr:`Evaluation.annual_benefit` excludes it
too — the persisted ``annual_benefit`` and ``net_benefit`` columns are the
threshold's own numbers, and a reader summing ``benefit_breakdown`` must land
on exactly ``annual_benefit``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Sequence
from uuid import UUID

from services.cost_model import (
    ALTRUIST_PROVIDER_CODE,
    ALTRUIST_SOURCE_URL,
    assert_no_ambiguous_overlap,
)
from services.portfolio_assets import _OrgWrite, _require_org

TABLE_PROVIDERS = "public.cost_providers"
TABLE_SCHEDULES = "public.cost_schedules"
TABLE_BENEFITS = "public.provider_benefit_schedules"
TABLE_EVALUATIONS = "public.altruist_one_evaluations"
TABLE_ACCOUNTS = "public.accounts"
TABLE_BALANCES = "public.account_balances_daily"

READ_PERMISSION = "view_portfolio"
WRITE_PERMISSION = "manage_billing"

ZERO = Decimal("0")
CENTS = Decimal("0.01")

#: Deployed CHECK vocabularies, mirrored so drift fails a test rather than a
#: production insert. Verified as set equality against pg_constraint in [1].
BENEFIT_BASES = ("BPS_ON_VALUE", "RATE_DELTA", "FLAT_DISCOUNT_BPS", "INCLUDED_FEATURE")
BENEFIT_SCOPES = ("ACCOUNT", "HOUSEHOLD")
RECOMMENDATIONS = ("ENROLL", "DO_NOT_ENROLL", "MARGINAL")

#: The deployed ``altruist_one_evaluations_decision_check`` admits only these
#: two. MARGINAL is a recommendation the model can make and NOT a decision a
#: human can record — see :data:`MARGINAL_ALWAYS_DIVERGES`.
DECISIONS = ("ENROLL", "DO_NOT_ENROLL")

#: Measured consequence of that asymmetry: because ``decision`` can never equal
#: a MARGINAL ``recommendation``, the deployed
#: ``altruist_one_evaluations_override_requires_reason`` CHECK treats EVERY
#: decision on a MARGINAL evaluation as a divergence. That is correct — a
#: near-breakeven call is exactly the one that should carry a written reason —
#: but it is not obvious from the column list, so it is named here and
#: reported by :func:`record_decision`'s error message.
MARGINAL_ALWAYS_DIVERGES = True

UNVERIFIED_CAVEAT = (
    "UNVERIFIED RATES: every rate behind this recommendation comes from "
    "cost_schedules / provider_benefit_schedules rows whose source_url has not "
    "been re-checked against a primary source (fee37 finding F6). Treat the "
    "dollar figures as an order-of-magnitude comparison, not a quote."
)

MONTHS_PER_YEAR = Decimal("12")


# ═══════════════════════════════════════════════════════════════════════════
# Errors — each names the FIELD a caller can fix, per the fee34 pattern
# ═══════════════════════════════════════════════════════════════════════════


class AltruistOneError(ValueError):
    """An evaluator call was refused for a reason the caller can fix."""


class AltruistOneNotFoundError(AltruistOneError):
    """The row is not this org's, or does not exist.

    Deliberately indistinguishable from "does not exist", matching
    ``cost_model.CostModelNotFoundError``: "that id exists but is not yours"
    confirms a row across a tenant boundary.
    """


class OverrideReasonRequiredError(AltruistOneError):
    """A decision diverging from the recommendation arrived with no reason.

    The deployed CHECK ``altruist_one_evaluations_override_requires_reason``
    already refuses this, but a raw ``CheckViolationError`` names a constraint,
    not a form field. This carries ``missing`` so a router can point at the
    input the user actually has to fill in — the fee34 pattern.
    """

    def __init__(
        self,
        message: str,
        *,
        missing: Sequence[str],
        recommendation: str,
        decision: str,
    ) -> None:
        super().__init__(message)
        self.missing = tuple(missing)
        self.recommendation = recommendation
        self.decision = decision


class AlreadyDecidedError(AltruistOneError):
    """This evaluation already carries a decision.

    ``altruist_one_evaluations`` has no temporal axis: it is append-only by
    ``evaluated_on``. Overwriting a recorded decision would destroy the audit
    trail the table exists to hold. Re-evaluate and decide on the NEW row.
    """

    def __init__(self, message: str, *, evaluation_id: str, decision: str) -> None:
        super().__init__(message)
        self.evaluation_id = evaluation_id
        self.decision = decision


class MissingRateError(AltruistOneError):
    """A rate the calculation needs is not seeded in this org.

    Raised rather than defaulted. A missing rate silently treated as zero would
    understate a benefit and flip a recommendation, and nothing downstream
    would show that it happened.
    """

    def __init__(self, message: str, *, missing: Sequence[str], table: str) -> None:
        super().__init__(message)
        self.missing = tuple(missing)
        self.table = table


# ═══════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════


def _as_uuid_text(value: Any, *, field_name: str) -> str:
    if value is None:
        raise AltruistOneError(f"{field_name} is required")
    if isinstance(value, UUID):
        return str(value)
    try:
        return str(UUID(str(value)))
    except (ValueError, AttributeError, TypeError) as exc:
        raise AltruistOneError(f"{field_name}={value!r} is not a valid uuid") from exc


def _opt_uuid_text(value: Any, *, field_name: str) -> str | None:
    return None if value is None else _as_uuid_text(value, field_name=field_name)


def _decimal(value: Any, *, field_name: str) -> Decimal:
    """Refuse floats outright. STANDING RULE: Decimal everywhere.

    Same guard as ``cost_model._decimal``. A float arriving here is already
    inexact, and the damage surfaces as a penny of drift months later.
    """
    if isinstance(value, float):
        raise AltruistOneError(
            f"{field_name} was passed as a float ({value!r}); money and rates "
            "are Decimal here, and a float is already inexact before it "
            "reaches the calculation"
        )
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value))
    except Exception as exc:  # noqa: BLE001
        raise AltruistOneError(f"{field_name}={value!r} is not a number") from exc


def _money(value: Decimal) -> Decimal:
    return value.quantize(CENTS, rounding=ROUND_HALF_UP)


def _require_choice(value: Any, allowed: Sequence[str], *, field_name: str) -> str:
    text = str(value or "").strip().upper()
    if text not in allowed:
        raise AltruistOneError(f"{field_name}={value!r} is not one of {list(allowed)}")
    return text


def _require_date(value: Any, *, field_name: str) -> date:
    if isinstance(value, datetime) or not isinstance(value, date):
        raise AltruistOneError(f"{field_name} must be a date (not a datetime)")
    return value


def _current(alias: str) -> str:
    return f"{alias}.valid_to IS NULL AND {alias}.system_to IS NULL"


# ═══════════════════════════════════════════════════════════════════════════
# TASK 2a — the benefit rate card, seeded, NOT inlined as constants
# ═══════════════════════════════════════════════════════════════════════════
#
# Symmetric to fee37's ``ALTRUIST_SCHEDULES``: these literals exist only inside
# the SEEDER. The calculation reads them back out of the database through
# :class:`RateBook` and never sees a literal. That is what makes verification
# requirement [8] — "every dollar figure traces to a real seeded row" —
# checkable rather than aspirational.


BENEFIT_SWEEP_UPLIFT = "AONE_SWEEP_CASH_UPLIFT"
BENEFIT_HY_UPLIFT = "AONE_HY_CASH_UPLIFT"
BENEFIT_MODEL_DISCOUNT = "AONE_MODEL_MARKETPLACE_DISCOUNT"
BENEFIT_TICKET_SAVING = "AONE_TICKET_SAVING_PER_TRADE"
BENEFIT_TLH_ALPHA = "AONE_TLH_TAX_ALPHA"


@dataclass(frozen=True)
class BenefitRow:
    """One seeded ``provider_benefit_schedules`` row, plus what it cannot say.

    ``provider_benefit_schedules`` HAS a ``notes`` column (measured — unlike
    ``cost_schedules``, which does not), so the caveat travels into the
    database with the number instead of living only here.

    There is no ``frequency`` column on this table, so **every rate below is
    stated ANNUAL** and the notes say so. A monthly rate filed here would be
    read as annual by any consumer and understate a benefit twelvefold.
    """

    benefit_code: str
    basis: str
    applies_scope: str
    rate: Decimal | None = None
    flat_amount: Decimal | None = None
    note: str = ""


ALTRUIST_BENEFITS: tuple[BenefitRow, ...] = (
    BenefitRow(
        benefit_code=BENEFIT_SWEEP_UPLIFT,
        basis="RATE_DELTA",
        applies_scope="ACCOUNT",
        rate=Decimal("0.00250000"),  # 25 bps ANNUAL
        note=(
            "ANNUAL yield uplift on sweep cash under Altruist One, expressed "
            "as a delta over the non-subscriber sweep rate — not an absolute "
            "yield. UNVERIFIED (fee37 F6). This is the fee37 UNSEEDED item "
            "'CASH_SPREAD', which could not live in cost_schedules because a "
            "positive rate there would be summed as an EXPENSE; it belongs on "
            "the benefit side, which is this table."
        ),
    ),
    BenefitRow(
        benefit_code=BENEFIT_HY_UPLIFT,
        basis="RATE_DELTA",
        applies_scope="ACCOUNT",
        rate=Decimal("0.00100000"),  # 10 bps ANNUAL
        note=(
            "ANNUAL uplift on balances in the high-yield cash program, OVER "
            "AND ABOVE the sweep uplift. Kept as a separate row rather than "
            "one blended cash rate because the two programs hold different "
            "dollars and a household can use either, both, or neither. "
            "UNVERIFIED (fee37 F6). NOTE: account_balances_daily cannot tell "
            "the two balances apart (measured) — see the module docstring."
        ),
    ),
    BenefitRow(
        benefit_code=BENEFIT_MODEL_DISCOUNT,
        basis="FLAT_DISCOUNT_BPS",
        applies_scope="ACCOUNT",
        rate=Decimal("0.00150000"),  # up to 15 bps ANNUAL
        note=(
            "'Up to 15 bps' off third-party model-marketplace fees. This is "
            "the second fee37 UNSEEDED item: its BASE was unstated, and a "
            "negative-rate row in cost_schedules would have summed "
            "destructively against the PAID_LOW/PAID_HIGH rows. Filed here "
            "instead, where the evaluator can CAP it at the fee actually "
            "being paid. The cap is not decoration — 15 bps exceeds the 10 bps "
            "PAID_LOW rate, so an uncapped discount would hand back more than "
            "the fee it discounts. UNVERIFIED (fee37 F6)."
        ),
    ),
    BenefitRow(
        benefit_code=BENEFIT_TICKET_SAVING,
        basis="INCLUDED_FEATURE",
        applies_scope="ACCOUNT",
        flat_amount=Decimal("1.0000"),  # dollars per ticket
        note=(
            "Dollars saved per executed ticket. basis is INCLUDED_FEATURE "
            "because the deployed CHECK has no per-event basis and this is a "
            "bundled-execution benefit, not a rate on a balance. UNVERIFIED "
            "(fee37 F6). The RATE is seeded; the COUNT is not derivable from "
            "any deployed table (measured), so the evaluator omits this line "
            "entirely unless a caller supplies a real counted figure."
        ),
    ),
    BenefitRow(
        benefit_code=BENEFIT_TLH_ALPHA,
        basis="BPS_ON_VALUE",
        applies_scope="ACCOUNT",
        rate=Decimal("0.00500000"),  # 50 bps ANNUAL, estimated
        note=(
            "ESTIMATED annual tax alpha from automated tax-loss harvesting, on "
            "the harvestable (taxable, non-cash) basis. EXCLUDED from the "
            "ENROLL/DO_NOT_ENROLL/MARGINAL threshold by design-doc decision: "
            "it is a client-side, tax-situation-dependent estimate and the "
            "softest number in the model, so it must not move the firm-side "
            "cost/benefit call. Surfaced separately and labelled. UNVERIFIED "
            "(fee37 F6), and additionally an ESTIMATE even if the rate were "
            "verified."
        ),
    ),
)

#: cost_schedules rows this evaluator reads. Named as data so [8] can assert
#: the set it traces to is exactly the set it declares.
REQUIRED_COST_CODES_FLOOR = ("ALTRUIST_ONE_SUB_FLOOR",)
REQUIRED_COST_CODES_ADDITIVE = (
    "ALTRUIST_ONE_SUB_ADDITIVE_BPS",
    "ALTRUIST_ONE_SUB_ADDITIVE_PER_ACCOUNT",
)
#: Read regardless of reading: the model fee the discount is capped against,
#: and the two ends of the margin spread the saving is the difference of.
REQUIRED_COST_CODES_COMMON = (
    "ALTRUIST_MODEL_MARKETPLACE_PAID_LOW",
    "ALTRUIST_MARGIN_SPREAD_NON_SUBSCRIBER",
    "ALTRUIST_MARGIN_SPREAD_AONE_HIGH",
)

READING_FLOOR = "FLOOR"
READING_ADDITIVE = "ADDITIVE"
SUBSCRIPTION_READINGS = (READING_FLOOR, READING_ADDITIVE)


def required_cost_codes(reading: str = READING_FLOOR) -> tuple[str, ...]:
    """Every ``cost_schedules.cost_code`` one evaluation reads, for a reading.

    Routed through ``cost_model.assert_no_ambiguous_overlap`` before it is
    returned. That is the whole point of fee37's guard rail: the plausible bug
    here is "read every ALTRUIST schedule and sum", which would charge both
    readings of the subscription line.
    """
    reading = _require_choice(
        reading, SUBSCRIPTION_READINGS, field_name="subscription_reading"
    )
    sub = (
        REQUIRED_COST_CODES_FLOOR
        if reading == READING_FLOOR
        else REQUIRED_COST_CODES_ADDITIVE
    )
    codes = sub + REQUIRED_COST_CODES_COMMON
    assert_no_ambiguous_overlap(codes)
    return codes


@dataclass(frozen=True)
class SeededBenefits:
    provider_id: str
    benefit_ids: dict[str, str]
    source_verified_on: date
    created_codes: tuple[str, ...]


async def seed_altruist_benefits(
    conn,
    org_id: str,
    *,
    source_verified_on: date,
    effective_from: date | None = None,
    source_url: str = ALTRUIST_SOURCE_URL,
) -> SeededBenefits:
    """Create the ``provider_benefit_schedules`` rows for the ALTRUIST provider.

    Requires ``cost_model.seed_altruist_profile`` to have run — the FK to
    ``cost_providers`` is real, and the benefit card is meaningless without the
    cost card it is netted against.

    ``source_verified_on`` is REQUIRED with no default, for the same reason as
    fee37's seeder: a default would stamp a verification date nobody chose.

    Idempotent by ``(provider, benefit_code)``: re-running adopts the existing
    rows. ``created_codes`` reports what THIS call actually inserted, so a
    verify teardown can remove only its own rows and leave a production seed
    intact.
    """
    org_id = _require_org(org_id)
    if not isinstance(source_verified_on, date) or isinstance(
        source_verified_on, datetime
    ):
        raise AltruistOneError(
            "source_verified_on must be a date (not a datetime, and not "
            "omitted) — it is a claim about when a human last read the source"
        )
    effective_from = effective_from or source_verified_on
    if not source_url:
        raise AltruistOneError(
            "source_url is required: an unattributed benefit card cannot be "
            "re-verified, which is the only thing that makes it usable"
        )

    async with _OrgWrite(conn, org_id) as c:
        provider = await c.fetchrow(
            f"""
            SELECT id::text AS id FROM {TABLE_PROVIDERS} p
            WHERE p.org_id = $1::uuid AND p.provider_code = $2 AND {_current('p')}
            """,
            org_id,
            ALTRUIST_PROVIDER_CODE,
        )
        if provider is None:
            raise AltruistOneNotFoundError(
                f"no current {ALTRUIST_PROVIDER_CODE} row in cost_providers for "
                "this org — run cost_model.seed_altruist_profile first; the "
                "benefit card has a real FK to it and is meaningless without "
                "the cost card it is netted against"
            )
        provider_id = provider["id"]

        benefit_ids: dict[str, str] = {}
        created: list[str] = []
        for row in ALTRUIST_BENEFITS:
            existing = await c.fetchrow(
                f"""
                SELECT id::text AS id FROM {TABLE_BENEFITS} b
                WHERE b.org_id = $1::uuid AND b.cost_provider_id = $2::uuid
                  AND b.benefit_code = $3 AND {_current('b')}
                """,
                org_id,
                provider_id,
                row.benefit_code,
            )
            if existing is not None:
                benefit_ids[row.benefit_code] = existing["id"]
                continue
            inserted = await c.fetchrow(
                f"""
                INSERT INTO {TABLE_BENEFITS}
                    (org_id, cost_provider_id, benefit_code, basis, rate,
                     flat_amount, applies_scope, effective_from, source_url,
                     source_verified_on, notes)
                VALUES ($1::uuid, $2::uuid, $3, $4, $5::numeric, $6::numeric,
                        $7, $8::date, $9, $10::date, $11)
                RETURNING id::text AS id
                """,
                org_id,
                provider_id,
                row.benefit_code,
                row.basis,
                row.rate,
                row.flat_amount,
                row.applies_scope,
                effective_from,
                source_url,
                source_verified_on,
                row.note,
            )
            benefit_ids[row.benefit_code] = inserted["id"]
            created.append(row.benefit_code)

    return SeededBenefits(
        provider_id=provider_id,
        benefit_ids=benefit_ids,
        source_verified_on=source_verified_on,
        created_codes=tuple(created),
    )


# ═══════════════════════════════════════════════════════════════════════════
# TASK 2b — the rate book: every number, and where it came from
# ═══════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class SourcedRate:
    """One rate, carrying its own provenance to the output.

    ``row_id`` is the actual ``cost_schedules.id`` or
    ``provider_benefit_schedules.id`` the value was read from. It is threaded
    all the way into ``benefit_breakdown`` so a reader can re-query the row a
    dollar figure came from, and so verification [8] can assert that every
    figure traces to a real row rather than to a literal in this file.
    """

    code: str
    table: str
    row_id: str
    value: Decimal
    source_url: str | None
    source_verified_on: date | None
    verified: bool = False  # fee37 F6: nothing here is verified yet.

    def cite(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "table": self.table,
            "row_id": self.row_id,
            "value": str(self.value),
            "source_url": self.source_url,
            "source_verified_on": (
                self.source_verified_on.isoformat() if self.source_verified_on else None
            ),
            "verified": self.verified,
        }


@dataclass(frozen=True)
class RateBook:
    """Every rate one evaluation needs, already read from the database.

    Deliberately a plain value object with no connection on it: :func:`evaluate`
    takes one of these and does pure arithmetic. That is what makes the
    calculation testable without a database, and what keeps every literal
    number out of the calculation path.
    """

    as_of: date
    subscription_reading: str
    #: cost_schedules
    sub_bps_monthly: SourcedRate
    sub_per_account_monthly: SourcedRate
    model_fee_paid_annual: SourcedRate
    margin_spread_non_subscriber: SourcedRate
    margin_spread_subscriber: SourcedRate
    #: provider_benefit_schedules
    sweep_uplift_annual: SourcedRate
    hy_uplift_annual: SourcedRate
    model_discount_annual: SourcedRate
    ticket_saving_per_trade: SourcedRate
    tlh_alpha_annual: SourcedRate

    def citations(self) -> list[dict[str, Any]]:
        return [
            r.cite()
            for r in (
                self.sub_bps_monthly,
                self.sub_per_account_monthly,
                self.model_fee_paid_annual,
                self.margin_spread_non_subscriber,
                self.margin_spread_subscriber,
                self.sweep_uplift_annual,
                self.hy_uplift_annual,
                self.model_discount_annual,
                self.ticket_saving_per_trade,
                self.tlh_alpha_annual,
            )
        ]

    @property
    def any_verified(self) -> bool:
        return any(r.verified for r in (self.sub_bps_monthly, self.sweep_uplift_annual))


async def load_rate_book(
    conn,
    org_id: str,
    *,
    as_of: date,
    subscription_reading: str = READING_FLOOR,
) -> RateBook:
    """Read every rate for ``org_id`` as of ``as_of``. No literals leave here.

    Raises :class:`MissingRateError` naming EVERY missing code at once rather
    than the first — a caller fixing a seed wants the whole list.
    """
    org_id = _require_org(org_id)
    as_of = _require_date(as_of, field_name="as_of")
    reading = _require_choice(
        subscription_reading, SUBSCRIPTION_READINGS, field_name="subscription_reading"
    )
    wanted_costs = required_cost_codes(reading)

    async with _OrgWrite(conn, org_id) as c:
        provider = await c.fetchrow(
            f"""
            SELECT id::text AS id FROM {TABLE_PROVIDERS} p
            WHERE p.org_id = $1::uuid AND p.provider_code = $2 AND {_current('p')}
            """,
            org_id,
            ALTRUIST_PROVIDER_CODE,
        )
        if provider is None:
            raise AltruistOneNotFoundError(
                f"no current {ALTRUIST_PROVIDER_CODE} provider for this org"
            )
        provider_id = provider["id"]

        cost_rows = {
            r["cost_code"]: r
            for r in await c.fetch(
                f"""
                SELECT s.cost_code, s.id::text AS id, s.rate, s.flat_amount,
                       s.minimum_amount, s.frequency, s.source_url,
                       s.source_verified_on
                FROM {TABLE_SCHEDULES} s
                WHERE s.org_id = $1::uuid AND s.cost_provider_id = $2::uuid
                  AND s.cost_code = ANY($3::text[])
                  AND s.effective_from <= $4::date
                  AND (s.effective_to IS NULL OR s.effective_to >= $4::date)
                  AND {_current('s')}
                """,
                org_id,
                provider_id,
                list(wanted_costs),
                as_of,
            )
        }
        benefit_rows = {
            r["benefit_code"]: r
            for r in await c.fetch(
                f"""
                SELECT b.benefit_code, b.id::text AS id, b.rate, b.flat_amount,
                       b.source_url, b.source_verified_on
                FROM {TABLE_BENEFITS} b
                WHERE b.org_id = $1::uuid AND b.cost_provider_id = $2::uuid
                  AND b.effective_from <= $3::date
                  AND (b.effective_to IS NULL OR b.effective_to >= $3::date)
                  AND {_current('b')}
                """,
                org_id,
                provider_id,
                as_of,
            )
        }

    wanted_benefits = tuple(r.benefit_code for r in ALTRUIST_BENEFITS)
    missing = [c for c in wanted_costs if c not in cost_rows] + [
        b for b in wanted_benefits if b not in benefit_rows
    ]
    if missing:
        raise MissingRateError(
            "the evaluator cannot run: these rate rows are not seeded (or are "
            f"not effective on {as_of.isoformat()}) for this org: "
            f"{sorted(missing)}. Refusing rather than defaulting to zero — a "
            "missing benefit rate silently read as zero understates the "
            "benefit and can flip the recommendation invisibly.",
            missing=sorted(missing),
            table=f"{TABLE_SCHEDULES} / {TABLE_BENEFITS}",
        )

    def cost(code: str, column: str) -> SourcedRate:
        r = cost_rows[code]
        value = r[column]
        if value is None:
            raise MissingRateError(
                f"cost_schedules row {code} has a NULL {column}, which the "
                "evaluator needs",
                missing=[f"{code}.{column}"],
                table=TABLE_SCHEDULES,
            )
        return SourcedRate(
            code=code,
            table=TABLE_SCHEDULES,
            row_id=r["id"],
            value=Decimal(str(value)),
            source_url=r["source_url"],
            source_verified_on=r["source_verified_on"],
        )

    def benefit(code: str, column: str) -> SourcedRate:
        r = benefit_rows[code]
        value = r[column]
        if value is None:
            raise MissingRateError(
                f"provider_benefit_schedules row {code} has a NULL {column}",
                missing=[f"{code}.{column}"],
                table=TABLE_BENEFITS,
            )
        return SourcedRate(
            code=code,
            table=TABLE_BENEFITS,
            row_id=r["id"],
            value=Decimal(str(value)),
            source_url=r["source_url"],
            source_verified_on=r["source_verified_on"],
        )

    if reading == READING_FLOOR:
        # ONE row carries both halves: rate is the monthly bps, minimum_amount
        # is the $1 PER ACCOUNT floor. fee37's own note flags that
        # applies_scope='HOUSEHOLD' cannot express "per account", and that the
        # multiplication has to happen here. This is that multiplication site.
        sub_bps = cost("ALTRUIST_ONE_SUB_FLOOR", "rate")
        sub_per_acct = cost("ALTRUIST_ONE_SUB_FLOOR", "minimum_amount")
    else:
        sub_bps = cost("ALTRUIST_ONE_SUB_ADDITIVE_BPS", "rate")
        sub_per_acct = cost("ALTRUIST_ONE_SUB_ADDITIVE_PER_ACCOUNT", "flat_amount")

    return RateBook(
        as_of=as_of,
        subscription_reading=reading,
        sub_bps_monthly=sub_bps,
        sub_per_account_monthly=sub_per_acct,
        model_fee_paid_annual=cost("ALTRUIST_MODEL_MARKETPLACE_PAID_LOW", "rate"),
        margin_spread_non_subscriber=cost(
            "ALTRUIST_MARGIN_SPREAD_NON_SUBSCRIBER", "rate"
        ),
        margin_spread_subscriber=cost("ALTRUIST_MARGIN_SPREAD_AONE_HIGH", "rate"),
        sweep_uplift_annual=benefit(BENEFIT_SWEEP_UPLIFT, "rate"),
        hy_uplift_annual=benefit(BENEFIT_HY_UPLIFT, "rate"),
        model_discount_annual=benefit(BENEFIT_MODEL_DISCOUNT, "rate"),
        ticket_saving_per_trade=benefit(BENEFIT_TICKET_SAVING, "flat_amount"),
        tlh_alpha_annual=benefit(BENEFIT_TLH_ALPHA, "rate"),
    )


# ═══════════════════════════════════════════════════════════════════════════
# TASK 2c — the household's own numbers
# ═══════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class HouseholdInputs:
    """What one household looks like on one date.

    Every field the deployed schema cannot supply is an explicit argument with
    a documented default and a matching entry in :attr:`data_gaps`. Nothing
    here is invented from a proxy.
    """

    household_id: str
    as_of: date
    household_value: Decimal
    account_count: int
    sweep_cash: Decimal
    hy_cash: Decimal
    margin_balance: Decimal
    model_marketplace_aum: Decimal = ZERO
    #: None means "no counted figure exists" and the ticket line is OMITTED.
    #: Zero would mean "counted, and it was zero" — a different claim.
    trade_count: int | None = None
    #: Basis eligible for tax-loss harvesting. None omits the tax-alpha line.
    tlh_harvestable_basis: Decimal | None = None
    data_gaps: tuple[str, ...] = ()
    sources: dict[str, Any] = field(default_factory=dict)


#: The default assumption when the sweep/HY split is unknown. Documented as a
#: constant rather than buried in a signature default so it is greppable.
DEFAULT_SWEEP_SHARE_OF_CASH = Decimal("1")


async def load_household_inputs(
    conn,
    org_id: str,
    household_id: str,
    *,
    as_of: date,
    sweep_share_of_cash: Decimal | None = None,
    model_marketplace_aum: Decimal | None = None,
    trade_count: int | None = None,
    tlh_harvestable_basis: Decimal | None = None,
) -> HouseholdInputs:
    """Read what is REAL, take what is not as an argument, report the gaps.

    See the module docstring for the measured inventory. The two things worth
    knowing at the call site:

    * ``account_balances_daily`` is deduped to ONE row per account by
      ``DISTINCT ON``, restricted to ``is_billing_source``. Its primary key
      includes ``source_system``, so a plain SUM double-counts multi-feed
      accounts. Accounts that still have more than one billing-source row on
      their latest date are counted and reported as a gap — the dedupe picks
      one deterministically, but which one it picked is an unresolved fact.
    * ``sweep_share_of_cash`` defaults to 1 (all cash is sweep). That is the
      conservative direction for the HY line, whose uplift is the smaller of
      the two rates.
    """
    org_id = _require_org(org_id)
    household_id = _as_uuid_text(household_id, field_name="household_id")
    as_of = _require_date(as_of, field_name="as_of")

    share = (
        DEFAULT_SWEEP_SHARE_OF_CASH
        if sweep_share_of_cash is None
        else _decimal(sweep_share_of_cash, field_name="sweep_share_of_cash")
    )
    if share < ZERO or share > Decimal("1"):
        raise AltruistOneError(
            f"sweep_share_of_cash={share} is outside [0,1]; it is the fraction "
            "of the household's cash sitting in the sweep, and the remainder "
            "is treated as high-yield"
        )
    if trade_count is not None and (not isinstance(trade_count, int) or trade_count < 0):
        raise AltruistOneError(
            f"trade_count={trade_count!r} must be a non-negative int or None. "
            "None means 'no counted figure exists' and omits the ticket line; "
            "0 means 'counted, and it was zero'."
        )

    gaps: list[str] = []

    async with _OrgWrite(conn, org_id) as c:
        exists = await c.fetchval(
            "SELECT 1 FROM public.households WHERE id=$1::uuid AND org_id=$2::uuid",
            household_id,
            org_id,
        )
        if not exists:
            raise AltruistOneNotFoundError(
                f"household {household_id} not found for this org"
            )

        agg = await c.fetchrow(
            f"""
            WITH acct AS (
                SELECT a.id
                FROM {TABLE_ACCOUNTS} a
                WHERE a.org_id = $1::uuid AND a.household_id = $2::uuid
                  AND {_current('a')}
                  AND (a.closed_on IS NULL OR a.closed_on > $3::date)
            ),
            latest AS (
                SELECT DISTINCT ON (b.account_id)
                       b.account_id, b.as_of_date, b.total_market_value,
                       b.cash_value, b.margin_balance
                FROM {TABLE_BALANCES} b
                JOIN acct ON acct.id = b.account_id
                WHERE b.org_id = $1::uuid AND b.as_of_date <= $3::date
                  AND b.is_billing_source
                ORDER BY b.account_id, b.as_of_date DESC, b.source_system
            )
            SELECT
              (SELECT count(*) FROM acct)                       AS account_count,
              (SELECT count(*) FROM latest)                     AS balanced_accounts,
              coalesce((SELECT sum(total_market_value) FROM latest), 0) AS household_value,
              coalesce((SELECT sum(cash_value) FROM latest), 0)         AS cash_value,
              coalesce((SELECT sum(margin_balance) FROM latest), 0)     AS margin_balance
            """,
            org_id,
            household_id,
            as_of,
        )

        # The double-count trap, measured rather than assumed away. Counts
        # accounts whose LATEST date carries more than one billing-source feed.
        ambiguous = await c.fetchval(
            f"""
            WITH acct AS (
                SELECT a.id FROM {TABLE_ACCOUNTS} a
                WHERE a.org_id = $1::uuid AND a.household_id = $2::uuid
                  AND {_current('a')}
            ),
            latest_day AS (
                SELECT b.account_id, max(b.as_of_date) AS d
                FROM {TABLE_BALANCES} b
                JOIN acct ON acct.id = b.account_id
                WHERE b.org_id = $1::uuid AND b.as_of_date <= $3::date
                  AND b.is_billing_source
                GROUP BY b.account_id
            )
            SELECT count(*) FROM (
                SELECT b.account_id
                FROM {TABLE_BALANCES} b
                JOIN latest_day l
                  ON l.account_id = b.account_id AND l.d = b.as_of_date
                WHERE b.org_id = $1::uuid AND b.is_billing_source
                GROUP BY b.account_id
                HAVING count(*) > 1
            ) x
            """,
            org_id,
            household_id,
            as_of,
        )

    account_count = int(agg["account_count"])
    balanced = int(agg["balanced_accounts"])
    household_value = Decimal(str(agg["household_value"]))
    cash_value = Decimal(str(agg["cash_value"]))
    margin_balance = Decimal(str(agg["margin_balance"]))

    if account_count and balanced < account_count:
        gaps.append(
            f"{account_count - balanced} of {account_count} accounts have no "
            "is_billing_source row in account_balances_daily on or before "
            f"{as_of.isoformat()}; they contribute $0 of value but DO count "
            "toward the per-account subscription term"
        )
    if ambiguous:
        gaps.append(
            f"{ambiguous} account(s) carry more than one is_billing_source row "
            "on their latest date; account_balances_daily's primary key "
            "includes source_system, so this read deduped by "
            "DISTINCT ON (account_id) ORDER BY as_of_date DESC, source_system "
            "— deterministic, but which feed won is an unresolved fact"
        )

    gaps.append(
        "sweep vs high-yield cash is NOT separable in account_balances_daily "
        "(one cash_value numeric, no cash-type dimension); this evaluation "
        f"assumed sweep_share_of_cash={share}"
    )
    if model_marketplace_aum is None:
        gaps.append(
            "model-marketplace AUM has no deployed source (accounts."
            "service_model is free text with no allocated-value column); "
            "treated as $0, so the model-discount benefit is absent"
        )
    if trade_count is None:
        gaps.append(
            "no account-level trade count exists (portfolio.transactions "
            "reaches an account only via positions.account_id); the ticket-"
            "savings line is OMITTED rather than zeroed"
        )
    if tlh_harvestable_basis is None:
        gaps.append(
            "no TLH harvestable basis supplied; the estimated tax-alpha line "
            "is omitted (it is excluded from the recommendation either way)"
        )

    model_aum = (
        ZERO
        if model_marketplace_aum is None
        else _decimal(model_marketplace_aum, field_name="model_marketplace_aum")
    )
    tlh_basis = (
        None
        if tlh_harvestable_basis is None
        else _decimal(tlh_harvestable_basis, field_name="tlh_harvestable_basis")
    )

    sweep_cash = cash_value * share
    hy_cash = cash_value - sweep_cash

    return HouseholdInputs(
        household_id=household_id,
        as_of=as_of,
        household_value=household_value,
        account_count=account_count,
        sweep_cash=sweep_cash,
        hy_cash=hy_cash,
        margin_balance=margin_balance,
        model_marketplace_aum=model_aum,
        trade_count=trade_count,
        tlh_harvestable_basis=tlh_basis,
        data_gaps=tuple(gaps),
        sources={
            "household_value": f"{TABLE_BALANCES}.total_market_value (billing source, deduped)",
            "account_count": f"{TABLE_ACCOUNTS} current, not closed on/before as_of",
            "cash_value": f"{TABLE_BALANCES}.cash_value (split by argument)",
            "margin_balance": f"{TABLE_BALANCES}.margin_balance",
            "model_marketplace_aum": "caller-supplied (no deployed source)",
            "trade_count": "caller-supplied (no deployed source)",
            "tlh_harvestable_basis": "caller-supplied (no deployed source)",
            "accounts_without_balance": account_count - balanced,
            "accounts_with_ambiguous_feed": int(ambiguous or 0),
        },
    )


# ═══════════════════════════════════════════════════════════════════════════
# TASK 2d — the calculation. PURE. No connection, no literal rate.
# ═══════════════════════════════════════════════════════════════════════════


#: The MARGINAL band, stated as data so it can be PRINTED in the output rather
#: than described in prose. A bare ``>0 / <0`` split would call a $3 net
#: benefit on a $2,400 subscription "ENROLL", which is noise being read as
#: signal — every input here is an UNVERIFIED rate applied to an estimated
#: balance, and neither is accurate to a dollar.
#:
#: The band is a percentage of the SCALE of the comparison —
#: ``max(annual_cost, annual_benefit)`` — not of the cost alone. Anchoring on
#: cost alone gets it wrong at both ends: a household whose benefit dwarfs its
#: cost would be judged against a band sized for the small number, and a
#: household with a near-zero cost would get a near-zero band.
#:
#: The absolute floor is small on purpose. An earlier draft used $250, which
#: for an eight-account, $72k household (annual cost $96) was wider than the
#: entire decision and made DO_NOT_ENROLL unreachable — the band must not be
#: larger than the quantities it is judging.
MARGINAL_BAND_PCT = Decimal("0.10")
MARGINAL_BAND_FLOOR = Decimal("25.00")


def marginal_band(annual_cost: Decimal, annual_benefit: Decimal) -> Decimal:
    scale = max(abs(annual_cost), abs(annual_benefit))
    return max(_money(scale * MARGINAL_BAND_PCT), MARGINAL_BAND_FLOOR)


def marginal_band_description(annual_cost: Decimal, annual_benefit: Decimal) -> str:
    band = marginal_band(annual_cost, annual_benefit)
    scale = max(abs(annual_cost), abs(annual_benefit))
    return (
        f"MARGINAL when |net_benefit| <= ${band} — {MARGINAL_BAND_PCT * 100}% "
        f"of ${_money(scale)}, the larger of the ${_money(annual_cost)} annual "
        f"cost and the ${_money(annual_benefit)} annual benefit, floored at "
        f"${MARGINAL_BAND_FLOOR}. Inside the band the SIGN of net_benefit is "
        "not distinguishable from the error in UNVERIFIED rates and estimated "
        "balances, so the model declines to call it. ENROLL above the band, "
        "DO_NOT_ENROLL below it."
    )


def _recommend(net_benefit: Decimal, annual_cost: Decimal, annual_benefit: Decimal) -> str:
    """The threshold. Sees the three threshold numbers and NOTHING else.

    In particular it never sees tax alpha. That exclusion is enforced by this
    signature, not by a caller remembering to subtract it — verification [7]
    toggles a large synthetic TLH input and asserts the result is unchanged,
    and this is why it can be.
    """
    band = marginal_band(annual_cost, annual_benefit)
    if net_benefit > band:
        return "ENROLL"
    if net_benefit < -band:
        return "DO_NOT_ENROLL"
    return "MARGINAL"


@dataclass(frozen=True)
class BenefitLine:
    component: str
    amount: Decimal
    formula: str
    rate: SourcedRate
    #: Set when a line was capped, so a reader sees the uncapped figure too.
    uncapped_amount: Decimal | None = None
    estimated: bool = False
    included_in_threshold: bool = True

    def to_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "component": self.component,
            "amount": str(_money(self.amount)),
            "formula": self.formula,
            "rate_source": self.rate.cite(),
            "estimated": self.estimated,
            "included_in_threshold": self.included_in_threshold,
        }
        if self.uncapped_amount is not None:
            out["uncapped_amount"] = str(_money(self.uncapped_amount))
        return out


@dataclass(frozen=True)
class Evaluation:
    household_id: str
    evaluated_on: date
    subscription_reading: str
    annual_cost: Decimal
    cost_formula: str
    cost_sources: list[dict[str, Any]]
    benefit_lines: tuple[BenefitLine, ...]
    annual_benefit: Decimal
    net_benefit: Decimal
    recommendation: str
    marginal_band: Decimal
    marginal_band_description: str
    #: The excluded line. None when no TLH input was supplied.
    tax_alpha: BenefitLine | None
    data_gaps: tuple[str, ...]
    inputs: dict[str, Any]
    caveat: str = UNVERIFIED_CAVEAT

    def benefit_breakdown(self) -> dict[str, Any]:
        """The persisted JSON. The caveat rides along; it is not a comment.

        ``tax_alpha`` sits OUTSIDE ``lines`` on purpose: a consumer summing
        ``lines`` must land on exactly ``annual_benefit``, and a consumer that
        wants the estimate has to reach for it by name and meet its label.
        """
        return {
            "caveat": self.caveat,
            "subscription_reading": self.subscription_reading,
            "cost_formula": self.cost_formula,
            "cost_sources": self.cost_sources,
            "lines": [line.to_json() for line in self.benefit_lines],
            "annual_benefit": str(_money(self.annual_benefit)),
            "marginal_band": str(self.marginal_band),
            "marginal_band_description": self.marginal_band_description,
            "tax_alpha": (
                None if self.tax_alpha is None else self.tax_alpha.to_json()
            ),
            "tax_alpha_excluded_from_recommendation": True,
            "data_gaps": list(self.data_gaps),
        }


def evaluate(inputs: HouseholdInputs, rates: RateBook) -> Evaluation:
    """Pure arithmetic over one household and one rate book.

    No connection, no I/O, no literal rate. Every dollar figure it produces
    names the :class:`SourcedRate` — and therefore the database row id — it
    came from.
    """
    if inputs.as_of != rates.as_of:
        raise AltruistOneError(
            f"the rate book is as of {rates.as_of.isoformat()} but the "
            f"household inputs are as of {inputs.as_of.isoformat()}; comparing "
            "a cost from one date against balances from another is exactly the "
            "kind of silent mismatch this refuses to paper over"
        )

    # ── annual cost ────────────────────────────────────────────────────────
    bps_term = inputs.household_value * rates.sub_bps_monthly.value * MONTHS_PER_YEAR
    acct_term = (
        Decimal(inputs.account_count)
        * rates.sub_per_account_monthly.value
        * MONTHS_PER_YEAR
    )
    if rates.subscription_reading == READING_FLOOR:
        annual_cost = max(bps_term, acct_term)
        cost_formula = (
            f"max({rates.sub_bps_monthly.value} x 12 x "
            f"${_money(inputs.household_value)}, "
            f"${rates.sub_per_account_monthly.value} x 12 x "
            f"{inputs.account_count} accounts) = "
            f"max(${_money(bps_term)}, ${_money(acct_term)})"
        )
    else:
        annual_cost = bps_term + acct_term
        cost_formula = (
            f"{rates.sub_bps_monthly.value} x 12 x "
            f"${_money(inputs.household_value)} + "
            f"${rates.sub_per_account_monthly.value} x 12 x "
            f"{inputs.account_count} accounts = "
            f"${_money(bps_term)} + ${_money(acct_term)}"
        )
    annual_cost = _money(annual_cost)

    lines: list[BenefitLine] = []

    # ── sweep cash uplift ──────────────────────────────────────────────────
    if inputs.sweep_cash > ZERO:
        amt = inputs.sweep_cash * rates.sweep_uplift_annual.value
        lines.append(
            BenefitLine(
                component="sweep_cash_uplift",
                amount=amt,
                formula=(
                    f"${_money(inputs.sweep_cash)} sweep cash x "
                    f"{rates.sweep_uplift_annual.value} annual uplift"
                ),
                rate=rates.sweep_uplift_annual,
            )
        )

    # ── high-yield cash uplift ─────────────────────────────────────────────
    if inputs.hy_cash > ZERO:
        amt = inputs.hy_cash * rates.hy_uplift_annual.value
        lines.append(
            BenefitLine(
                component="hy_cash_uplift",
                amount=amt,
                formula=(
                    f"${_money(inputs.hy_cash)} high-yield cash x "
                    f"{rates.hy_uplift_annual.value} annual uplift (over and "
                    "above the sweep uplift)"
                ),
                rate=rates.hy_uplift_annual,
            )
        )

    # ── model marketplace discount, CAPPED at the fee actually paid ────────
    if inputs.model_marketplace_aum > ZERO:
        uncapped = inputs.model_marketplace_aum * rates.model_discount_annual.value
        paid = inputs.model_marketplace_aum * rates.model_fee_paid_annual.value
        amt = min(uncapped, paid)
        lines.append(
            BenefitLine(
                component="model_marketplace_discount",
                amount=amt,
                uncapped_amount=uncapped if amt != uncapped else None,
                formula=(
                    f"min(${_money(uncapped)} = "
                    f"${_money(inputs.model_marketplace_aum)} x "
                    f"{rates.model_discount_annual.value} discount, "
                    f"${_money(paid)} = model fee actually paid at "
                    f"{rates.model_fee_paid_annual.value}) — a discount can "
                    "never exceed the fee it discounts"
                ),
                rate=rates.model_discount_annual,
            )
        )

    # ── margin saving, only where margin is actually DRAWN ─────────────────
    if inputs.margin_balance > ZERO:
        delta = (
            rates.margin_spread_non_subscriber.value
            - rates.margin_spread_subscriber.value
        )
        amt = inputs.margin_balance * delta
        lines.append(
            BenefitLine(
                component="margin_savings",
                amount=amt,
                formula=(
                    f"${_money(inputs.margin_balance)} drawn margin x "
                    f"({rates.margin_spread_non_subscriber.value} non-"
                    f"subscriber - {rates.margin_spread_subscriber.value} "
                    f"subscriber) = x {delta}"
                ),
                rate=rates.margin_spread_non_subscriber,
            )
        )

    # ── ticket savings, ONLY on a real counted figure ──────────────────────
    if inputs.trade_count is not None:
        amt = Decimal(inputs.trade_count) * rates.ticket_saving_per_trade.value
        lines.append(
            BenefitLine(
                component="ticket_savings",
                amount=amt,
                formula=(
                    f"{inputs.trade_count} counted tickets x "
                    f"${rates.ticket_saving_per_trade.value} saved per ticket"
                ),
                rate=rates.ticket_saving_per_trade,
            )
        )

    annual_benefit = _money(sum((line.amount for line in lines), ZERO))
    net_benefit = _money(annual_benefit - annual_cost)
    recommendation = _recommend(net_benefit, annual_cost, annual_benefit)

    # ── the EXCLUDED line, computed last so it cannot reach _recommend ─────
    tax_alpha: BenefitLine | None = None
    if inputs.tlh_harvestable_basis is not None:
        tax_alpha = BenefitLine(
            component="tlh_tax_alpha",
            amount=inputs.tlh_harvestable_basis * rates.tlh_alpha_annual.value,
            formula=(
                f"${_money(inputs.tlh_harvestable_basis)} harvestable basis x "
                f"{rates.tlh_alpha_annual.value} estimated annual tax alpha"
            ),
            rate=rates.tlh_alpha_annual,
            estimated=True,
            included_in_threshold=False,
        )

    return Evaluation(
        household_id=inputs.household_id,
        evaluated_on=inputs.as_of,
        subscription_reading=rates.subscription_reading,
        annual_cost=annual_cost,
        cost_formula=cost_formula,
        cost_sources=[
            rates.sub_bps_monthly.cite(),
            rates.sub_per_account_monthly.cite(),
        ],
        benefit_lines=tuple(lines),
        annual_benefit=annual_benefit,
        net_benefit=net_benefit,
        recommendation=recommendation,
        marginal_band=marginal_band(annual_cost, annual_benefit),
        marginal_band_description=marginal_band_description(
            annual_cost, annual_benefit
        ),
        tax_alpha=tax_alpha,
        data_gaps=inputs.data_gaps,
        inputs={
            "household_value": str(_money(inputs.household_value)),
            "account_count": inputs.account_count,
            "sweep_cash": str(_money(inputs.sweep_cash)),
            "hy_cash": str(_money(inputs.hy_cash)),
            "margin_balance": str(_money(inputs.margin_balance)),
            "model_marketplace_aum": str(_money(inputs.model_marketplace_aum)),
            "trade_count": inputs.trade_count,
            "tlh_harvestable_basis": (
                None
                if inputs.tlh_harvestable_basis is None
                else str(_money(inputs.tlh_harvestable_basis))
            ),
            "sources": inputs.sources,
        },
    )


async def evaluate_household(
    conn,
    org_id: str,
    household_id: str,
    *,
    evaluated_on: date,
    subscription_reading: str = READING_FLOOR,
    sweep_share_of_cash: Decimal | None = None,
    model_marketplace_aum: Decimal | None = None,
    trade_count: int | None = None,
    tlh_harvestable_basis: Decimal | None = None,
) -> Evaluation:
    """Read + calculate. The convenience path; the arithmetic stays in
    :func:`evaluate`."""
    rates = await load_rate_book(
        conn,
        org_id,
        as_of=_require_date(evaluated_on, field_name="evaluated_on"),
        subscription_reading=subscription_reading,
    )
    inputs = await load_household_inputs(
        conn,
        org_id,
        household_id,
        as_of=evaluated_on,
        sweep_share_of_cash=sweep_share_of_cash,
        model_marketplace_aum=model_marketplace_aum,
        trade_count=trade_count,
        tlh_harvestable_basis=tlh_harvestable_basis,
    )
    return evaluate(inputs, rates)


# ═══════════════════════════════════════════════════════════════════════════
# TASK 3 — persistence, decision recording, override gate
# ═══════════════════════════════════════════════════════════════════════════


#: Not built here, deliberately. Re-evaluation on a schedule needs a Workflow
#: Manager trigger, and that is a fee-module-EXTERNAL dependency on S29b
#: landing (the standing sequencing decision). ``next_review_on`` is accepted,
#: persisted, and indexed by the deployed
#: ``altruist_one_evaluations_review_idx (org_id, next_review_on) WHERE
#: next_review_on IS NOT NULL`` — so the due-work query already has its index.
#:
#: TODO(S29b / fee-module-external): register a scheduled workflow action that
#: calls :func:`due_for_review` and re-runs :func:`evaluate_household` for each
#: row it returns. Nothing runs on a schedule today — the scheduler's action
#: registry is a separate subsystem and this module deliberately does not reach
#: into it.
NEXT_REVIEW_WORKFLOW_TODO = (
    "scheduled re-evaluation is NOT wired: it needs a Workflow Manager "
    "trigger, which is gated on S29b. due_for_review() is the query that "
    "trigger will call."
)


@dataclass(frozen=True)
class SavedEvaluation:
    id: str
    household_id: str
    evaluated_on: date
    recommendation: str
    annual_cost: Decimal
    annual_benefit: Decimal
    net_benefit: Decimal
    next_review_on: date | None


async def save_evaluation(
    conn,
    org_id: str,
    evaluation: Evaluation,
    *,
    next_review_on: date | None = None,
) -> SavedEvaluation:
    """INSERT. Always. Never an UPDATE, never an upsert.

    ``altruist_one_evaluations`` carries no temporal axis and no natural-key
    unique index (measured): it is append-only by ``(household_id,
    evaluated_on)``, and re-evaluating the same household on the same day
    deliberately produces a SECOND row. Two evaluations of one household on one
    day that disagree is a fact worth keeping — collapsing them would destroy
    the only evidence that the inputs changed underneath.
    """
    org_id = _require_org(org_id)
    if next_review_on is not None:
        next_review_on = _require_date(next_review_on, field_name="next_review_on")
        if next_review_on <= evaluation.evaluated_on:
            raise AltruistOneError(
                f"next_review_on={next_review_on.isoformat()} is on or before "
                f"evaluated_on={evaluation.evaluated_on.isoformat()}; a review "
                "already due at the moment it is scheduled is a scheduling bug"
            )

    async with _OrgWrite(conn, org_id) as c:
        row = await c.fetchrow(
            f"""
            INSERT INTO {TABLE_EVALUATIONS}
                (org_id, household_id, evaluated_on, inputs, annual_cost,
                 benefit_breakdown, annual_benefit, net_benefit,
                 recommendation, next_review_on)
            VALUES ($1::uuid, $2::uuid, $3::date, $4::jsonb, $5::numeric,
                    $6::jsonb, $7::numeric, $8::numeric, $9, $10::date)
            RETURNING id::text AS id
            """,
            org_id,
            evaluation.household_id,
            evaluation.evaluated_on,
            json.dumps(evaluation.inputs, default=str),
            evaluation.annual_cost,
            json.dumps(evaluation.benefit_breakdown(), default=str),
            evaluation.annual_benefit,
            evaluation.net_benefit,
            evaluation.recommendation,
            next_review_on,
        )

    return SavedEvaluation(
        id=row["id"],
        household_id=evaluation.household_id,
        evaluated_on=evaluation.evaluated_on,
        recommendation=evaluation.recommendation,
        annual_cost=evaluation.annual_cost,
        annual_benefit=evaluation.annual_benefit,
        net_benefit=evaluation.net_benefit,
        next_review_on=next_review_on,
    )


@dataclass(frozen=True)
class RecordedDecision:
    evaluation_id: str
    recommendation: str
    decision: str
    diverged: bool
    override_reason: str | None
    decided_by: str | None
    decided_at: datetime


async def record_decision(
    conn,
    org_id: str,
    evaluation_id: str,
    decision: str,
    *,
    override_reason: str | None = None,
    decided_by: Any = None,
    decided_at: datetime | None = None,
) -> RecordedDecision:
    """Stamp a human decision onto one evaluation, once.

    A decision EQUAL to the recommendation needs nothing else: no reason, no
    decider. ``override_reason`` and ``decided_by`` both stay NULL.

    A decision that DIVERGES requires both, and this raises
    :class:`OverrideReasonRequiredError` naming the missing FIELDS before the
    database's own CHECK fires. The CHECK is the real gate — this is the one
    that can explain itself, which is the fee34 pattern. Requirement [6] proves
    both halves.

    Note :data:`MARGINAL_ALWAYS_DIVERGES`: ``decision`` admits only ENROLL and
    DO_NOT_ENROLL, so any decision on a MARGINAL recommendation is a
    divergence and always needs a reason.
    """
    org_id = _require_org(org_id)
    evaluation_id = _as_uuid_text(evaluation_id, field_name="evaluation_id")
    decision = _require_choice(decision, DECISIONS, field_name="decision")
    decided_by_text = _opt_uuid_text(decided_by, field_name="decided_by")
    reason = (override_reason or "").strip() or None
    when = decided_at or datetime.now(timezone.utc)

    async with _OrgWrite(conn, org_id) as c:
        current = await c.fetchrow(
            f"""
            SELECT recommendation, decision FROM {TABLE_EVALUATIONS}
            WHERE id = $1::uuid AND org_id = $2::uuid
            """,
            evaluation_id,
            org_id,
        )
        if current is None:
            raise AltruistOneNotFoundError(
                f"evaluation {evaluation_id} not found for this org"
            )
        if current["decision"] is not None:
            raise AlreadyDecidedError(
                f"evaluation {evaluation_id} already records a "
                f"{current['decision']} decision. This table is append-only: "
                "re-evaluate the household and record the new decision on the "
                "new row rather than overwriting the audit trail.",
                evaluation_id=evaluation_id,
                decision=current["decision"],
            )

        recommendation = current["recommendation"]
        diverged = decision != recommendation
        if diverged:
            missing = [
                name
                for name, value in (
                    ("override_reason", reason),
                    ("decided_by", decided_by_text),
                )
                if not value
            ]
            if missing:
                extra = (
                    " (a MARGINAL recommendation can never be matched by a "
                    "decision — decision admits only ENROLL and "
                    "DO_NOT_ENROLL — so every decision on a MARGINAL "
                    "evaluation needs a reason)"
                    if recommendation == "MARGINAL"
                    else ""
                )
                raise OverrideReasonRequiredError(
                    f"decision '{decision}' diverges from the recommendation "
                    f"'{recommendation}', so {' and '.join(missing)} "
                    f"{'is' if len(missing) == 1 else 'are'} required"
                    + extra,
                    missing=missing,
                    recommendation=recommendation,
                    decision=decision,
                )
        else:
            # Matching decisions carry no reason. Accepting one silently would
            # let an override reason attach to a non-override and make the
            # column useless as evidence that a human departed from the model.
            if reason is not None:
                raise AltruistOneError(
                    f"decision '{decision}' matches the recommendation, so "
                    "override_reason must be omitted; a reason recorded "
                    "against a non-override makes the column useless as "
                    "evidence that anyone departed from the model"
                )

        # decided_at is paired with decided_by by
        # altruist_one_evaluations_decided_pair_check — both or neither.
        stamped_at = when if decided_by_text else None
        await c.execute(
            f"""
            UPDATE {TABLE_EVALUATIONS}
               SET decision = $1, override_reason = $2,
                   decided_by = $3::uuid, decided_at = $4::timestamptz
             WHERE id = $5::uuid AND org_id = $6::uuid
            """,
            decision,
            reason,
            decided_by_text,
            stamped_at,
            evaluation_id,
            org_id,
        )

    return RecordedDecision(
        evaluation_id=evaluation_id,
        recommendation=recommendation,
        decision=decision,
        diverged=diverged,
        override_reason=reason,
        decided_by=decided_by_text,
        decided_at=stamped_at,
    )


async def due_for_review(conn, org_id: str, *, as_of: date) -> list[dict[str, Any]]:
    """Evaluations whose ``next_review_on`` has arrived.

    The query a scheduled trigger will call once S29b lands. See
    :data:`NEXT_REVIEW_WORKFLOW_TODO` — nothing calls this on a schedule today.
    """
    org_id = _require_org(org_id)
    as_of = _require_date(as_of, field_name="as_of")
    async with _OrgWrite(conn, org_id) as c:
        rows = await c.fetch(
            f"""
            SELECT id::text AS id, household_id::text AS household_id,
                   evaluated_on, next_review_on, recommendation, decision
            FROM {TABLE_EVALUATIONS}
            WHERE org_id = $1::uuid AND next_review_on IS NOT NULL
              AND next_review_on <= $2::date
            ORDER BY next_review_on, evaluated_on
            """,
            org_id,
            as_of,
        )
    return [dict(r) for r in rows]
