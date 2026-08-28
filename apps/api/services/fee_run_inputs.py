"""Loading the facts a fee run bills on, and hashing them. Sprint fee36.

fee35 built a pure engine with no database. This module is the half fee35
deliberately did not write: the part that reads the deployed tables and hands
the engine the frozen dataclasses it wants. Nothing here does arithmetic on a
fee. :mod:`services.fee_calc` is imported for its INPUT types and its
:class:`~services.fee_calc.GroupScopeMissingError`, never re-implemented.

The two fee35 findings this sprint owed an answer to are both answered here,
and both answers are the same shape: **find the real number, or raise a named
error — never substitute zero.**


[F1] fee_credits HAS NO amount COLUMN — WHERE THE BASIS ACTUALLY COMES FROM
──────────────────────────────────────────────────────────────────────────────
``fee_credits`` stores ``offset_pct`` (constrained to [0, 1]) and nothing to
multiply it by. ``CreditInput.basis_amount`` is required. So one of the five
``credit_source`` values has to be resolved from somewhere real, and the other
four have to say honestly that they cannot be.

Searched live for a column or table holding a period fee amount — every table
name matching ``%spv%``/``%journal%``/``%subscription%``/``%capital_account%``,
and every column matching ``%mgmt%``/``%management_fee%``/``%fee_amount%``/
``%fee_pct%``/``%fee_rate%`` across every non-system schema. The complete list
of what exists:

  * ``spvs.mgmt_fee_pct``            — a RATE, not a period amount. Turning it
                                      into an amount would mean this module
                                      calculating a fee, which is exactly what
                                      the sprint forbids.
  * ``spv_transactions``             — ``txn_type``/``transaction_type_id``,
                                      ``txn_date``, ``amount``, ``status``.
                                      The management fee CALL is a real, dated,
                                      signed row here: ``txn_type =
                                      'call_mgmt_fee'`` (live vocabulary, and
                                      ``transaction_types.code`` carries the
                                      same string).
  * ``spv_transaction_allocations``  — ``allocated_amount`` per
                                      ``subscription_id``/``entity_id``. This
                                      is the investor's OWN share of that call.
  * ``journal_lines`` + account 5000 — "Management Fee Expense", at the VEHICLE
                                      level, with no per-investor dimension
                                      except ``dim_member_series_id``.

:func:`resolve_credit_basis` therefore reads ``SPV_MGMT_FEE_OFFSET``'s basis as
**the sum of this account's owning entity's ``allocated_amount`` across every
POSTED ``call_mgmt_fee`` transaction dated inside the billing period.** Not the
SPV-level ``amount``: crediting an investor with 4% of the whole vehicle's
management fee, when they own 10% of it, is off by an order of magnitude in the
client's favour and would never be spotted from the invoice alone.

Note carefully which id is used. A credit's ``scope_type`` decides WHICH
ACCOUNTS the credit applies to; it does not decide WHOSE fee is the basis. The
basis is always resolved for the account whose line is being written. A
HOUSEHOLD-scoped credit across three accounts otherwise credits the same SPV
fee three times over.

``12B1``, ``SUB_TA``, ``SI_EMBEDDED_FEE_OFFSET`` and ``MODEL_FEE_OFFSET`` have
**no source in the deployed schema at all** — no trail table, no revenue table,
no commission table (searched; none exist). They raise
:class:`CreditBasisUnavailableError`. That is the finding, not a gap in this
module: a 12b-1 credit cannot be computed until somebody records the trail that
was actually received.


[F4] accounts HAS NO billing_group_id — RESOLVED, AND NOT PAPERED OVER
──────────────────────────────────────────────────────────────────────────────
:func:`resolve_billing_group_id` reads ``billing_group_members`` joined to
``billing_groups`` filtered to ``group_type = 'BREAKPOINT'``, as of the date
being billed. Three outcomes, all of them explicit:

  one row   -> that group id
  no rows   -> ``None``, which makes the ENGINE raise its own
               ``GroupScopeMissingError`` if (and only if) the schedule
               actually has a BILLING_GROUP-scoped minimum. Not swallowed, not
               downgraded to an account-scoped minimum — that would charge the
               minimum once per account instead of once per group.
  two rows  -> :class:`AmbiguousBillingGroupError`. ``billing_group_members``
               has no unique index, so this is reachable, and picking one
               silently puts the account in the wrong breakpoint.

The as-of predicate here is deliberately NOT
``fee_schedules._current()`` (``valid_to IS NULL AND system_to IS NULL``).
``_current`` means "the row as it stands now", which is the right question for
an editing screen and the wrong one for a run: billing January in March must
use the membership that was in force in January, not the one somebody changed
in February.

The two joined tables are treated on DIFFERENT axes, and this is the part that
is easy to get wrong — it was, once, here. The MEMBERSHIP is read as-of the
date being billed, because "was this account in this group in January" is a
real historical fact. The GROUP ROW is read as CURRENT, because its
``valid_from`` is a bi-temporal row-version marker, not the group's inception
date: it defaults to ``now()``, so restating a group's NAME today would set a
2026 ``valid_from`` on it and, under an as-of read, retroactively dissolve
every membership it ever had. Renaming a billing group must not change what
anybody was billed last quarter.


THE SNAPSHOT HASH IS OVER THE INPUTS, NOT THE OUTPUT
──────────────────────────────────────────────────────────────────────────────
:func:`canonical_inputs` renders one account's complete calculation input —
schedule, tiers, exclusions, discounts, credits (with their RESOLVED basis
amounts), balances, flows, positions, the resolved billing group, the period —
as a canonically-ordered, Decimal-as-string JSON document.
:func:`snapshot_hash` is sha256 over the concatenation of those documents for
every account in the run.

Hashing the OUTPUT would be circular: it would prove the numbers have not
changed, which is the thing the numbers themselves already say. Hashing the
INPUTS is what makes "has anything upstream moved since we posted this?"
answerable — a retroactively corrected balance, a re-pointed assignment, a
newly back-dated exclusion all change the hash while the stored line still
reads the same.

The document is also STORED, per line, under ``calc_detail['inputs']``. A hash
you cannot reconstruct the preimage of is a hash that can only ever tell you
that something changed, never what.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Iterable, Mapping, Sequence
from uuid import UUID

from services.fee_calc import GroupScopeMissingError  # noqa: F401  (re-exported)
from services.fee_calc_inputs import (
    AccountCalcRequest,
    AccountInput,
    AccountPeriodInput,
    BillingPeriod,
    CreditInput,
    DailyBalanceInput,
    DiscountInput,
    ExclusionInput,
    FeeCalcError,
    FeeScheduleInput,
    FeeTierInput,
    FlowInput,
    PositionInput,
)

# ── Tables, named once ──────────────────────────────────────────────────────
T_SCHEDULES = "public.fee_schedules"
T_TIERS = "public.fee_schedule_tiers"
T_EXCLUSIONS = "public.fee_exclusions"
T_DISCOUNTS = "public.fee_discounts"
T_CREDITS = "public.fee_credits"
T_ACCOUNTS = "public.accounts"
T_BALANCES = "public.account_balances_daily"
T_FLOWS = "public.account_flows"
T_BILLING_GROUPS = "public.billing_groups"
T_BILLING_GROUP_MEMBERS = "public.billing_group_members"
#: CLAUDE.md: ``portfolio`` is a real schema and is on nobody's search_path.
T_POSITIONS = "portfolio.positions"

#: ``billing_groups.group_type``. Only BREAKPOINT groups aggregate for fee
#: purposes — STATEMENT groups decide what arrives in one envelope and PAYER
#: groups decide who is debited, and neither changes a rate.
BREAKPOINT = "BREAKPOINT"

#: ``spv_transactions.txn_type`` / ``transaction_types.code`` for the
#: management-fee capital call. Read live; both carry the same string.
SPV_MGMT_FEE_TXN_TYPE = "call_mgmt_fee"

#: ``spv_transactions.status`` values that mean the fee was actually charged.
#: A ``draft`` call has not hit anybody's capital account yet, and crediting
#: against it would give back money that was never taken.
SPV_TXN_CHARGED_STATUSES = ("posted",)

#: The one ``credit_source`` this sprint can resolve from deployed data.
#: See [F1] in the module docstring for the other four.
RESOLVABLE_CREDIT_SOURCES = ("SPV_MGMT_FEE_OFFSET",)


# ═══════════════════════════════════════════════════════════════════════════
# Errors — every one of them is a refusal to guess a number
# ═══════════════════════════════════════════════════════════════════════════


class FeeRunInputError(FeeCalcError):
    """Base for everything this module refuses to resolve."""

    code = "fee_run_input_error"


class AmbiguousBillingGroupError(FeeRunInputError):
    """An account is an active member of more than one BREAKPOINT group.

    ``billing_group_members`` carries no unique index, so nothing in the
    database prevents this. Choosing one would silently place the account in a
    different breakpoint tier than the other choice would.
    """

    code = "ambiguous_billing_group"


class CreditBasisUnavailableError(FeeRunInputError):
    """A credit is in scope and no deployed table holds the amount it offsets.

    Raised rather than defaulted to zero. ``offset_pct`` times a zero basis is
    a credit worth nothing, applied and traced as though it were worth
    something — the single most invisible way to overbill a client.
    """

    code = "credit_basis_unavailable"


class AccountNotBillableError(FeeRunInputError):
    """The account row is missing, or not current, in this org."""

    code = "account_not_loadable"


class ScheduleNotLoadableError(FeeRunInputError):
    """An assignment names a schedule that is not a current row in this org."""

    code = "schedule_not_loadable"


# ═══════════════════════════════════════════════════════════════════════════
# Canonicalisation
# ═══════════════════════════════════════════════════════════════════════════


def _plain(value: Any) -> Any:
    """Anything asyncpg hands back, rendered so json.dumps is deterministic.

    ``Decimal`` becomes a STRING, never a float. ``Decimal('100.00')`` and
    ``Decimal('100')`` are equal numerically and are deliberately NOT
    normalised to each other here: a balance restated from ``100`` to
    ``100.00`` is a real edit to a real row, and the hash exists to notice
    exactly that class of change.
    """
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, float):  # pragma: no cover - refused far upstream
        raise FeeRunInputError(
            f"a float ({value!r}) reached the snapshot; every money value must "
            f"be Decimal by the time it gets here"
        )
    if isinstance(value, (UUID,)):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(k): _plain(v) for k, v in sorted(value.items(), key=lambda kv: str(kv[0]))}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    return str(value)


def _dc(obj: Any, fields: Sequence[str]) -> dict[str, Any]:
    return {f: _plain(getattr(obj, f)) for f in fields}


_SCHEDULE_FIELDS = (
    "id", "code", "version", "status", "product_type", "rate_type", "tier_method",
    "billing_frequency", "billing_timing", "valuation_method", "proration_method",
    "day_weight_flows", "day_weight_threshold", "minimum_fee", "minimum_fee_scope",
    "maximum_fee", "minimum_billable_value", "cash_treatment", "cash_exclusion_pct",
    "margin_treatment", "ordering_policy", "currency",
)
_TIER_FIELDS = ("tier_seq", "lower_bound", "upper_bound", "rate_bps", "flat_amount")
_EXCLUSION_FIELDS = (
    "id", "basis_type", "basis_value", "treatment", "scope_type", "scope_id",
    "alt_fee_schedule_id", "flat_amount", "effective_from", "effective_to",
)
_DISCOUNT_FIELDS = (
    "id", "discount_type", "value", "applies_to", "scope_type", "scope_id",
    "effective_from", "effective_to",
)
_CREDIT_FIELDS = (
    "id", "credit_source", "offset_pct", "basis_amount", "scope_type", "scope_id",
    "effective_from", "effective_to",
)
_BALANCE_FIELDS = (
    "as_of_date", "source_system", "total_market_value", "cash_value",
    "margin_balance", "accrued_income", "is_billing_source", "is_final",
)
_FLOW_FIELDS = ("id", "flow_date", "amount", "flow_type", "is_billable_flow")
_POSITION_FIELDS = ("id", "asset_id", "market_value", "taxonomy_key", "as_of_date", "tags")
_ACCOUNT_FIELDS = (
    "id", "household_id", "billing_group_id", "is_billable", "is_held_away",
    "base_currency", "opened_on", "closed_on",
)


def canonical_inputs(request: AccountCalcRequest) -> dict[str, Any]:
    """One account's complete calculation input as a canonical document.

    Sequences are SORTED, not left in query order: two runs that loaded the
    same rows through differently-ordered queries must hash identically, or the
    hash reports drift every time somebody adds an ORDER BY.

    Only fields that can change the number are included. ``created_at``,
    ``created_by``, ``approved_by`` and the bi-temporal bookkeeping columns are
    excluded on purpose — a schedule re-approved by a different person bills
    exactly the same, and a hash that moved when it did would cry wolf.
    """
    data = request.data
    doc: dict[str, Any] = {
        "account": _dc(data.account, _ACCOUNT_FIELDS),
        "period": {
            "period_start": _plain(data.period.period_start),
            "period_end": _plain(data.period.period_end),
            "service_start": _plain(data.period.service_start),
            "service_end": _plain(data.period.service_end),
        },
        "schedule": _dc(request.schedule, _SCHEDULE_FIELDS),
        "tiers": sorted(
            (_dc(t, _TIER_FIELDS) for t in request.tiers),
            key=lambda d: (d["tier_seq"], d["lower_bound"]),
        ),
        "exclusions": sorted(
            (_dc(x, _EXCLUSION_FIELDS) for x in request.exclusions),
            key=lambda d: json.dumps(d, sort_keys=True),
        ),
        "discounts": sorted(
            (_dc(x, _DISCOUNT_FIELDS) for x in request.discounts),
            key=lambda d: json.dumps(d, sort_keys=True),
        ),
        "credits": sorted(
            (_dc(x, _CREDIT_FIELDS) for x in request.credits),
            key=lambda d: json.dumps(d, sort_keys=True),
        ),
        "balances": sorted(
            (_dc(b, _BALANCE_FIELDS) for b in data.balances),
            key=lambda d: (d["as_of_date"], d["source_system"]),
        ),
        "flows": sorted(
            (_dc(f, _FLOW_FIELDS) for f in data.flows),
            key=lambda d: json.dumps(d, sort_keys=True),
        ),
        "positions": sorted(
            (_dc(p, _POSITION_FIELDS) for p in data.positions),
            key=lambda d: json.dumps(d, sort_keys=True),
        ),
        "alt_schedules": {
            str(k): {
                "schedule": _dc(v[0], _SCHEDULE_FIELDS),
                "tiers": sorted(
                    (_dc(t, _TIER_FIELDS) for t in v[1]),
                    key=lambda d: (d["tier_seq"], d["lower_bound"]),
                ),
            }
            for k, v in sorted((request.alt_schedules or {}).items())
        },
    }
    return doc


def canonical_json(doc: Any) -> str:
    """The exact bytes that get hashed. ``separators`` pinned so a Python
    upgrade that changed the default spacing could not change every hash."""
    return json.dumps(doc, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def snapshot_hash(
    docs: Sequence[Mapping[str, Any]], *, engine_version: str, run_type: str,
    period_start: date, period_end: date, billing_frequency: str,
) -> str:
    """sha256 over every account's input document plus the run's own identity.

    ``engine_version`` is inside the hash on purpose. The same inputs run
    through a different engine may legitimately produce a different number, and
    a hash that matched across an engine change would assert reproducibility
    that nobody had actually checked.

    The account documents are sorted by account id, so the hash does not depend
    on the order accounts happened to come back in.
    """
    payload = {
        "engine_version": engine_version,
        "run_type": run_type,
        "billing_frequency": billing_frequency,
        "period_start": _plain(period_start),
        "period_end": _plain(period_end),
        "accounts": sorted(
            (_plain(d) for d in docs),
            key=lambda d: str(d.get("account", {}).get("id", "")),
        ),
    }
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


# ═══════════════════════════════════════════════════════════════════════════
# [F4] billing_group_id
# ═══════════════════════════════════════════════════════════════════════════


async def resolve_billing_group_id(
    conn, org_id: str, account_id: str, *, as_of: date
) -> str | None:
    """The BREAKPOINT group this account belonged to on ``as_of``, or None.

    See [F4] in the module docstring. Returning ``None`` is a real answer, not
    a failure: most accounts are in no breakpoint group, and only a schedule
    whose ``minimum_fee_scope`` is ``'BILLING_GROUP'`` cares. The engine raises
    ``GroupScopeMissingError`` for that combination; this function does not
    pre-empt it, because it cannot see the schedule and guessing would either
    refuse accounts that were fine or silently excuse ones that were not.
    """
    rows = await conn.fetch(
        f"""
        SELECT g.id::text AS id, g.name
        FROM {T_BILLING_GROUP_MEMBERS} m
        JOIN {T_BILLING_GROUPS} g
          ON g.id = m.billing_group_id AND g.org_id = m.org_id
        WHERE m.org_id = $1::uuid
          AND m.account_id = $2::uuid
          AND g.group_type = $4
          AND m.system_to IS NULL
          AND g.valid_to IS NULL AND g.system_to IS NULL
          AND m.valid_from::date <= $3::date
          AND (m.valid_to IS NULL OR m.valid_to::date > $3::date)
        ORDER BY g.id
        """,
        org_id, account_id, as_of, BREAKPOINT,
    )
    if not rows:
        return None
    if len(rows) > 1:
        raise AmbiguousBillingGroupError(
            f"account {account_id} is an active member of "
            f"{len(rows)} {BREAKPOINT} billing groups as of {as_of} "
            f"({', '.join(r['name'] for r in rows)}). billing_group_members "
            f"has no unique index, so the database permits this; picking one "
            f"would put the account in a breakpoint the other choice would not",
            account_id=account_id,
            as_of=as_of.isoformat(),
            billing_group_ids=[r["id"] for r in rows],
        )
    return rows[0]["id"]


async def _all_group_ids(conn, org_id: str, account_id: str, as_of: date) -> list[str]:
    """Every billing group of ANY type the account was in on ``as_of``.

    Used to resolve BILLING_GROUP-scoped exclusions/discounts/credits, which —
    unlike a breakpoint minimum — legitimately hang off a STATEMENT or PAYER
    group too.
    """
    rows = await conn.fetch(
        f"""
        SELECT m.billing_group_id::text AS id
        FROM {T_BILLING_GROUP_MEMBERS} m
        JOIN {T_BILLING_GROUPS} g
          ON g.id = m.billing_group_id AND g.org_id = m.org_id
        WHERE m.org_id = $1::uuid AND m.account_id = $2::uuid
          AND m.system_to IS NULL
          AND g.valid_to IS NULL AND g.system_to IS NULL
          AND m.valid_from::date <= $3::date
          AND (m.valid_to IS NULL OR m.valid_to::date > $3::date)
        ORDER BY 1
        """,
        org_id, account_id, as_of,
    )
    return [r["id"] for r in rows]


# ═══════════════════════════════════════════════════════════════════════════
# [F1] credit basis_amount
# ═══════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class CreditBasis:
    """A resolved basis, and enough provenance to argue with a client about it."""

    amount: Decimal
    source: str
    detail: dict[str, Any]


async def resolve_credit_basis(
    conn, org_id: str, *, credit_source: str, account_id: str,
    owner_entity_id: str | None, period_start: date, period_end: date,
) -> CreditBasis:
    """The dollar figure ``offset_pct`` multiplies, for one credit source.

    See [F1] in the module docstring for why this is the only source that can
    currently be answered and why the others raise instead of returning zero.
    """
    if credit_source != "SPV_MGMT_FEE_OFFSET":
        raise CreditBasisUnavailableError(
            f"credit_source={credit_source!r} has no basis amount anywhere in "
            f"the deployed schema. fee_credits stores only offset_pct, and no "
            f"table records trail, sub-TA, embedded or model fee receipts "
            f"(searched every table and column matching trail/12b1/revenue/"
            f"commission/fee_amount). Only "
            f"{list(RESOLVABLE_CREDIT_SOURCES)} can be resolved today. "
            f"Returning zero here would apply a credit worth nothing while "
            f"tracing it as though it were worth something",
            credit_source=credit_source, account_id=account_id,
        )

    if owner_entity_id is None:
        raise CreditBasisUnavailableError(
            f"account {account_id} has no primary_entity_id, so its share of "
            f"any SPV management fee cannot be identified",
            credit_source=credit_source, account_id=account_id,
        )

    rows = await conn.fetch(
        """
        SELECT a.allocated_amount, a.subscription_id::text AS subscription_id,
               t.id::text AS transaction_id, t.txn_date, t.amount AS spv_level_amount,
               s.name AS spv_name
        FROM public.spv_transaction_allocations a
        JOIN public.spv_transactions t ON t.id = a.transaction_id
        JOIN public.spvs s ON s.id = t.spv_id
        LEFT JOIN public.transaction_types tt ON tt.id = t.transaction_type_id
        WHERE a.org_id = $1::uuid
          AND a.entity_id = $2::uuid
          AND (t.txn_type = $5 OR tt.code = $5)
          AND t.status = ANY($6::text[])
          AND t.txn_date >= $3::date
          AND t.txn_date <= $4::date
        ORDER BY t.txn_date, t.id
        """,
        org_id, owner_entity_id, period_start, period_end,
        SPV_MGMT_FEE_TXN_TYPE, list(SPV_TXN_CHARGED_STATUSES),
    )
    if not rows:
        raise CreditBasisUnavailableError(
            f"a SPV_MGMT_FEE_OFFSET credit applies to account {account_id} "
            f"(entity {owner_entity_id}) for {period_start}..{period_end}, but "
            f"no {SPV_MGMT_FEE_TXN_TYPE} allocation with status in "
            f"{list(SPV_TXN_CHARGED_STATUSES)} is dated inside that period. "
            f"There is no management fee to offset. A run must not silently "
            f"credit zero — either the call has not been posted yet, or the "
            f"credit should not be in scope for this period",
            credit_source=credit_source, account_id=account_id,
            entity_id=owner_entity_id,
            period=f"{period_start}..{period_end}",
        )

    total = sum((r["allocated_amount"] for r in rows), Decimal(0))
    return CreditBasis(
        amount=total,
        source="spv_transaction_allocations.allocated_amount",
        detail={
            "basis_origin": (
                "sum of this entity's allocated_amount across posted "
                "call_mgmt_fee spv_transactions dated inside the period — the "
                "investor's own share, NOT the vehicle-level amount"
            ),
            "entity_id": str(owner_entity_id),
            "allocations": [
                {
                    "transaction_id": r["transaction_id"],
                    "subscription_id": r["subscription_id"],
                    "spv": r["spv_name"],
                    "txn_date": r["txn_date"].isoformat(),
                    "allocated_amount": str(r["allocated_amount"]),
                    "spv_level_amount": str(r["spv_level_amount"]),
                }
                for r in rows
            ],
            "total": str(total),
        },
    )


# ═══════════════════════════════════════════════════════════════════════════
# Loading one account's calculation request
# ═══════════════════════════════════════════════════════════════════════════


async def _load_schedule(conn, org_id: str, schedule_id: str, as_of: date):
    row = await conn.fetchrow(
        f"""
        SELECT id::text AS id, org_id::text AS org_id, code, version, name, status,
               product_type, rate_type, tier_method, billing_frequency, billing_timing,
               valuation_method, day_weight_flows, day_weight_threshold,
               proration_method, minimum_fee, minimum_fee_scope, maximum_fee,
               minimum_billable_value, cash_treatment, cash_exclusion_pct,
               margin_treatment, ordering_policy, currency
        FROM {T_SCHEDULES}
        WHERE id = $1::uuid AND org_id = $2::uuid
          AND valid_to IS NULL AND system_to IS NULL
        """,
        schedule_id, org_id,
    )
    if row is None:
        raise ScheduleNotLoadableError(
            f"fee_schedule {schedule_id} is not a current row in org {org_id}",
            fee_schedule_id=schedule_id,
        )
    schedule = FeeScheduleInput.from_row(dict(row))
    tier_rows = await conn.fetch(
        f"""SELECT id::text AS id, fee_schedule_id::text AS fee_schedule_id, tier_seq,
                   lower_bound, upper_bound, rate_bps, flat_amount
            FROM {T_TIERS} WHERE fee_schedule_id = $1::uuid AND org_id = $2::uuid
            ORDER BY tier_seq""",
        schedule_id, org_id,
    )
    tiers = tuple(FeeTierInput.from_row(dict(r)) for r in tier_rows)
    return schedule, tiers


async def load_account_calc_request(
    conn, org_id: str, *, account_id: str, fee_schedule_id: str,
    period_start: date, period_end: date,
    service_start: date | None = None, service_end: date | None = None,
) -> tuple[AccountCalcRequest, dict[str, Any]]:
    """Build one :class:`AccountCalcRequest` out of the deployed tables.

    Returns the request and a ``provenance`` dict — the resolved billing group,
    every credit's basis and where it came from, and the alt schedules that had
    to be loaded for REDUCED_RATE carve-outs. The provenance is stored on the
    line so a client asking "why is this credit $412.50?" can be answered from
    the run itself rather than by re-deriving it against tables that have since
    moved.

    ``as_of`` for every scope resolution is ``period_end``: the state of the
    world at the close of the period being billed.
    """
    as_of = period_end

    acct = await conn.fetchrow(
        f"""
        SELECT id::text AS id, household_id::text AS household_id,
               primary_entity_id::text AS primary_entity_id,
               advisor_of_record_id::text AS advisor_of_record_id,
               is_billable, is_held_away, base_currency, opened_on, closed_on
        FROM {T_ACCOUNTS}
        WHERE id = $1::uuid AND org_id = $2::uuid
          AND valid_to IS NULL AND system_to IS NULL
        """,
        account_id, org_id,
    )
    if acct is None:
        raise AccountNotBillableError(
            f"account {account_id} is not a current account in org {org_id}",
            account_id=account_id,
        )

    billing_group_id = await resolve_billing_group_id(
        conn, org_id, account_id, as_of=as_of
    )
    group_ids = await _all_group_ids(conn, org_id, account_id, as_of)

    account = AccountInput.from_row(dict(acct), billing_group_id=billing_group_id)
    period = BillingPeriod(
        period_start=period_start, period_end=period_end,
        service_start=service_start, service_end=service_end,
    )

    schedule, tiers = await _load_schedule(conn, org_id, fee_schedule_id, as_of)

    # ── balances / flows / positions ────────────────────────────────────────
    # Deliberately loaded for the WHOLE period and handed over unfiltered: the
    # engine does its own period filtering and says in the trace what it
    # dropped. A loader that pre-filtered would move that decision somewhere
    # the trace cannot see it.
    bal_rows = await conn.fetch(
        f"""SELECT account_id::text AS account_id, as_of_date, total_market_value,
                   cash_value, margin_balance, accrued_income, source_system,
                   is_billing_source, is_final
            FROM {T_BALANCES}
            WHERE org_id = $1::uuid AND account_id = $2::uuid
              AND as_of_date >= $3::date AND as_of_date <= $4::date
            ORDER BY as_of_date, source_system""",
        org_id, account_id, period_start, period_end,
    )
    balances = tuple(DailyBalanceInput.from_row(dict(r)) for r in bal_rows)

    flow_rows = await conn.fetch(
        f"""SELECT id::text AS id, account_id::text AS account_id, flow_date, amount,
                   flow_type, is_billable_flow,
                   counterparty_account_id::text AS counterparty_account_id
            FROM {T_FLOWS}
            WHERE org_id = $1::uuid AND account_id = $2::uuid
              AND valid_to IS NULL AND system_to IS NULL
              AND flow_date >= $3::date AND flow_date <= $4::date
            ORDER BY flow_date, id""",
        org_id, account_id, period_start, period_end,
    )
    flows = tuple(FlowInput.from_row(dict(r)) for r in flow_rows)

    positions = await _load_positions(conn, org_id, account_id, as_of)

    # ── exclusions / discounts / credits, scope-resolved ────────────────────
    exclusions = await _load_exclusions(
        conn, org_id, account_id, acct["household_id"], group_ids,
        period_start, period_end,
    )
    discounts = await _load_discounts(
        conn, org_id, account_id, acct["household_id"], group_ids,
        period_start, period_end,
    )
    credits, credit_provenance = await _load_credits(
        conn, org_id, account_id, acct["household_id"], group_ids,
        acct["primary_entity_id"], period_start, period_end,
    )

    # ── alt schedules for REDUCED_RATE carve-outs ───────────────────────────
    alt: dict[str, tuple[FeeScheduleInput, tuple[FeeTierInput, ...]]] = {}
    for x in exclusions:
        if x.alt_fee_schedule_id and x.alt_fee_schedule_id not in alt:
            alt[x.alt_fee_schedule_id] = await _load_schedule(
                conn, org_id, x.alt_fee_schedule_id, as_of
            )

    request = AccountCalcRequest(
        data=AccountPeriodInput(
            account=account, period=period, balances=balances,
            flows=flows, positions=positions,
        ),
        schedule=schedule, tiers=tiers,
        exclusions=exclusions, discounts=discounts, credits=credits,
        alt_schedules=alt,
    )
    provenance = {
        "billing_group_id": billing_group_id,
        "billing_group_resolution": (
            f"billing_group_members joined to billing_groups "
            f"(group_type={BREAKPOINT}) active as of {as_of.isoformat()}"
        ),
        "all_billing_group_ids": group_ids,
        "household_id": acct["household_id"],
        "owner_entity_id": acct["primary_entity_id"],
        "advisor_of_record_id": acct["advisor_of_record_id"],
        "credit_basis": credit_provenance,
        "alt_schedule_ids": sorted(alt),
        "counts": {
            "balances": len(balances), "flows": len(flows),
            "positions": len(positions), "exclusions": len(exclusions),
            "discounts": len(discounts), "credits": len(credits),
        },
    }
    return request, provenance


async def _load_positions(conn, org_id: str, account_id: str, as_of: date):
    """``portfolio.positions`` for the account, plus its UDF tags.

    fee35 finding [2]: ``portfolio.positions`` has no tag column; tags live in
    ``portfolio.udf_values``, keyed ``(target_type, target_id)`` — NOT
    ``record_id``, which is what the name suggests and is not what the deployed
    column is called. A POSITION_TAG exclusion against positions carrying no
    tags excludes nothing, visibly, in the trace.

    ``taxonomy_key`` is COALESCEd from the position's own column onto the
    asset's ``default_taxonomy_key``. Both exist; the position-level value is
    an override and wins when it is set, and an ASSET_CLASS exclusion that
    matched only positions somebody had explicitly re-keyed would carve out a
    surprising subset of the book.
    """
    rows = await conn.fetch(
        f"""SELECT p.id::text AS id, p.account_id::text AS account_id,
                   p.asset_id::text AS asset_id,
                   p.owner_entity_id::text AS owner_entity_id,
                   p.market_value, p.as_of_date,
                   COALESCE(p.taxonomy_key, a.default_taxonomy_key) AS taxonomy_key
            FROM {T_POSITIONS} p
            LEFT JOIN portfolio.assets a
              ON a.id = p.asset_id AND a.org_id = p.org_id AND a.system_to IS NULL
            WHERE p.org_id = $1::uuid AND p.account_id = $2::uuid
              AND p.as_of_date <= $3::date
              AND p.valid_to IS NULL AND p.system_to IS NULL
            ORDER BY p.as_of_date, p.id""",
        org_id, account_id, as_of,
    )
    if not rows:
        return ()
    tags: dict[str, list[str]] = {}
    tag_rows = await conn.fetch(
        """SELECT target_id::text AS target_id, value_text
           FROM portfolio.udf_values
           WHERE org_id = $1::uuid AND target_type = 'position'
             AND target_id = ANY($2::uuid[])
             AND value_text IS NOT NULL
             AND valid_to IS NULL AND system_to IS NULL""",
        org_id, [r["id"] for r in rows],
    )
    for r in tag_rows:
        tags.setdefault(r["target_id"], []).append(r["value_text"])
    return tuple(
        PositionInput.from_row(dict(r), tags=tuple(sorted(tags.get(r["id"], ()))))
        for r in rows
    )


def _scope_clause(alias: str, *, org_scope: bool) -> str:
    """Rows whose scope covers this account.

    Parameter positions are shared by all three loaders below and must stay in
    step with them: ``$1`` org, ``$2`` account, ``$3`` household, ``$4`` group
    ids, ``$5`` period_start, ``$6`` period_end.

    ``ORG`` is only in ``fee_exclusions_scope_type_check``; ``fee_discounts``
    and ``fee_credits`` admit ACCOUNT/BILLING_GROUP/HOUSEHOLD only, and their
    ``scope_id`` is NOT NULL. Emitting an ORG branch for them would be dead SQL
    that reads like a supported case.
    """
    parts = [
        f"({alias}.scope_type = 'ACCOUNT'       AND {alias}.scope_id = $2::uuid)",
        f"({alias}.scope_type = 'BILLING_GROUP' AND {alias}.scope_id = ANY($4::uuid[]))",
        f"({alias}.scope_type = 'HOUSEHOLD'     AND {alias}.scope_id = $3::uuid)",
    ]
    if org_scope:
        parts.append(f"({alias}.scope_type = 'ORG' AND {alias}.scope_id IS NULL)")
    return " OR ".join(parts)


async def _load_exclusions(conn, org_id, account_id, household_id, group_ids,
                           period_start, period_end):
    rows = await conn.fetch(
        f"""
        SELECT x.id::text AS id, x.scope_type, x.scope_id::text AS scope_id,
               x.basis_type, x.basis_value, x.treatment,
               x.alt_fee_schedule_id::text AS alt_fee_schedule_id,
               x.flat_amount, x.reason, x.effective_from, x.effective_to
        FROM {T_EXCLUSIONS} x
        WHERE x.org_id = $1::uuid
          AND x.valid_to IS NULL AND x.system_to IS NULL
          AND x.effective_from <= $6::date
          AND (x.effective_to IS NULL OR x.effective_to >= $5::date)
          AND ({_scope_clause('x', org_scope=True)})
        ORDER BY x.id
        """,
        org_id, account_id, household_id, group_ids, period_start, period_end,
    )
    return tuple(ExclusionInput.from_row(dict(r)) for r in rows)


async def _load_discounts(conn, org_id, account_id, household_id, group_ids,
                          period_start, period_end):
    rows = await conn.fetch(
        f"""
        SELECT d.id::text AS id, d.scope_type, d.scope_id::text AS scope_id,
               d.discount_type, d.value, d.applies_to, d.reason,
               d.effective_from, d.effective_to
        FROM {T_DISCOUNTS} d
        WHERE d.org_id = $1::uuid
          AND d.valid_to IS NULL AND d.system_to IS NULL
          AND d.effective_from <= $6::date
          AND (d.effective_to IS NULL OR d.effective_to >= $5::date)
          AND ({_scope_clause('d', org_scope=False)})
        ORDER BY d.id
        """,
        org_id, account_id, household_id, group_ids, period_start, period_end,
    )
    return tuple(DiscountInput.from_row(dict(r)) for r in rows)


async def _load_credits(conn, org_id, account_id, household_id, group_ids,
                        owner_entity_id, period_start, period_end):
    """Credits in scope, each with its [F1]-resolved ``basis_amount``.

    ``CreditBasisUnavailableError`` from :func:`resolve_credit_basis` is NOT
    caught. A run that cannot price a credit it is in scope for must fail
    loudly at preview time, where somebody can fix the data, rather than bill
    the client the full fee and leave the credit to be noticed later.
    """
    rows = await conn.fetch(
        f"""
        SELECT c.id::text AS id, c.scope_type, c.scope_id::text AS scope_id,
               c.credit_source, c.offset_pct, c.reason,
               c.effective_from, c.effective_to
        FROM {T_CREDITS} c
        WHERE c.org_id = $1::uuid
          AND c.valid_to IS NULL AND c.system_to IS NULL
          AND c.effective_from <= $6::date
          AND (c.effective_to IS NULL OR c.effective_to >= $5::date)
          AND ({_scope_clause('c', org_scope=False)})
        ORDER BY c.id
        """,
        org_id, account_id, household_id, group_ids, period_start, period_end,
    )
    out: list[CreditInput] = []
    provenance: dict[str, Any] = {}
    for r in rows:
        basis = await resolve_credit_basis(
            conn, org_id,
            credit_source=r["credit_source"],
            account_id=account_id,
            owner_entity_id=owner_entity_id,
            period_start=period_start, period_end=period_end,
        )
        provenance[r["id"]] = {
            "credit_source": r["credit_source"],
            "basis_amount": str(basis.amount),
            "basis_source_table": basis.source,
            **basis.detail,
        }
        out.append(CreditInput.from_row(dict(r), basis_amount=basis.amount))
    return tuple(out), provenance


__all__ = [
    "BREAKPOINT",
    "RESOLVABLE_CREDIT_SOURCES",
    "SPV_MGMT_FEE_TXN_TYPE",
    "SPV_TXN_CHARGED_STATUSES",
    "AccountNotBillableError",
    "AmbiguousBillingGroupError",
    "CreditBasis",
    "CreditBasisUnavailableError",
    "FeeRunInputError",
    "GroupScopeMissingError",
    "ScheduleNotLoadableError",
    "canonical_inputs",
    "canonical_json",
    "load_account_calc_request",
    "resolve_billing_group_id",
    "resolve_credit_basis",
    "snapshot_hash",
]
