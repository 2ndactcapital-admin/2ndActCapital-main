"""Fee run lifecycle, approvals, reversal and reproducibility. Sprint fee36.

This module writes rows. fee35 deliberately did not; every number it stores
comes out of :func:`services.fee_calc.calculate_group_fees` and not one
arithmetic operation on a fee happens here. The only Decimal arithmetic in this
file is negation (a REVERSAL line is the original line's ``net_fee`` with its
sign flipped) and summing lines for a total — both of which are bookkeeping on
numbers the engine already produced.


THE LIFECYCLE, AND WHICH PART OF IT THE DATABASE ENFORCES
──────────────────────────────────────────────────────────────────────────────

    DRAFT ──preview──▶ PREVIEW ──advisor──▶ ADVISOR_APPROVED
                          ▲                        │
                          └── re-preview ──┐   compliance
                                            │       ▼
                                   COMPLIANCE_APPROVED ──post──▶ POSTED ✱

``✱`` is the only immutable state, and it is immutable in the DATABASE
(``fee_runs_immutable_once_posted`` / ``fee_run_lines_immutable_once_posted``),
not here. Everything before it is freely re-runnable: a PREVIEW replaces its
lines wholesale as many times as an operator wants, which is the entire point
of a preview.

:data:`ALLOWED_TRANSITIONS` is checked in this module as well, but that check
is a courtesy — it produces a readable error instead of a trigger exception.
It is not the guarantee. Anything that matters is refused by the trigger even
when this module is bypassed entirely, which is what
``scripts/verify_fee36.py`` checks by issuing raw SQL.


APPROVALS ARE assistant_activities ROWS, NOT FLAGS
──────────────────────────────────────────────────────────────────────────────
``fee_runs`` carries ``advisor_approved_by``/``advisor_approved_at`` and the
compliance pair. Those columns are written, but they are a DENORMALISED MIRROR
and nothing reads them to decide anything. The authority is a row in
``public.assistant_activities`` with ``related_type='fee_run'``,
``related_id`` = the run, and ``status='approved'``.

That is not a new convention. ``services/trading_authority.py`` already runs
money movement through the same ledger with the same two statuses
(``'proposed'`` → ``'approved'``), and ``assistant_activities`` carries the
``assistant_activities_maker_checker_chk`` CHECK — ``approved_by <>
proposed_by`` — which means the database refuses a self-approval independently
of anything Python does. Reading the vocabulary live confirmed there is NO
CHECK constraint on ``status`` itself; the vocabulary is a code convention
(``proposed``/``approved``/``awaiting_review``/``done``/``undone``/
``in_progress``/``blocked``), so this module reuses the pair the existing
maker-checker path already uses rather than inventing a seventh value.

Approval is genuinely two steps. :func:`propose_approval` opens the gate and
leaves the run where it is; :func:`approve` closes it and advances the run in
the SAME transaction as the activity update. A single-call "approve" would
have made maker and checker the same person by construction, which is the one
thing maker-checker exists to prevent.


WHAT A REVERSAL IS
──────────────────────────────────────────────────────────────────────────────
A new ``run_type='REVERSAL'`` run whose lines are the target's lines with
``net_fee`` (and every component amount) negated, carrying
``reverses_run_id``. The original is untouched — it cannot be touched; the
trigger refuses. Two runs both remain, and they sum to zero per account.

Note the deployed status vocabulary includes ``'REVERSED'``, and the original
run is NOT moved to it. It cannot be: ``fee_runs_immutable_once_posted``
refuses every UPDATE on a POSTED row, by design. Recorded as finding [F36-C];
the link is ``reverses_run_id``, read in the other direction.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any, Iterable, Mapping, Sequence

from services.fee_calc import ENGINE_VERSION, FeeCalcResult, calculate_group_fees
from services.fee_calc_inputs import AccountCalcRequest, FeeCalcError
from services.fee_run_inputs import (
    canonical_inputs,
    load_account_calc_request,
    snapshot_hash,
)
from services.fee_schedules import resolve_assignment_for_account

T_RUNS = "public.fee_runs"
T_LINES = "public.fee_run_lines"
T_ACTIVITIES = "public.assistant_activities"
T_ACCOUNTS = "public.accounts"

#: ``fee_runs_status_check``, read live.
RUN_STATUSES = (
    "DRAFT", "PREVIEW", "ADVISOR_APPROVED", "COMPLIANCE_APPROVED",
    "POSTED", "EXPORTED", "RECONCILED", "REVERSED",
)

#: ``fee_runs_run_type_check``, read live.
RUN_TYPES = ("SCHEDULED", "PRORATED_INCEPTION", "TERMINATION", "ADJUSTMENT", "REVERSAL")

#: The statuses the DB triggers treat as frozen. Mirrored here only so this
#: module's error messages can say so before the trigger does.
IMMUTABLE_STATUSES = ("POSTED", "EXPORTED", "RECONCILED")

#: Legal moves. A PREVIEW may be re-previewed; that self-edge is the one that
#: makes the whole screen usable and is easy to lose in a refactor.
ALLOWED_TRANSITIONS: Mapping[str, tuple[str, ...]] = {
    "DRAFT": ("PREVIEW",),
    "PREVIEW": ("PREVIEW", "ADVISOR_APPROVED"),
    "ADVISOR_APPROVED": ("PREVIEW", "COMPLIANCE_APPROVED"),
    "COMPLIANCE_APPROVED": ("PREVIEW", "POSTED"),
    "POSTED": (),
    "EXPORTED": (),
    "RECONCILED": (),
    "REVERSED": (),
}

#: ``assistant_activities.action_key`` per gate, and the run status each one
#: unlocks. One dict so a new gate cannot be half-added.
APPROVAL_GATES: Mapping[str, dict[str, str]] = {
    "ADVISOR": {
        "action_key": "fee_run.advisor_approve",
        "advances_to": "ADVISOR_APPROVED",
        "from_status": "PREVIEW",
        "by_column": "advisor_approved_by",
        "at_column": "advisor_approved_at",
        "title": "Advisor approval of fee run",
    },
    "COMPLIANCE": {
        "action_key": "fee_run.compliance_approve",
        "advances_to": "COMPLIANCE_APPROVED",
        "from_status": "ADVISOR_APPROVED",
        "by_column": "compliance_approved_by",
        "at_column": "compliance_approved_at",
        "title": "Compliance approval of fee run",
    },
}

#: ``assistant_activities.related_type`` for everything this module writes.
RELATED_TYPE = "fee_run"

#: The two statuses the existing maker-checker ledger already uses.
ACTIVITY_PROPOSED = "proposed"
ACTIVITY_APPROVED = "approved"

#: ``fee_run_lines.payment_method`` default, from the deployed CHECK.
DEFAULT_PAYMENT_METHOD = "CUSTODIAL_DEBIT"

ZERO = Decimal(0)


# ═══════════════════════════════════════════════════════════════════════════
# Errors
# ═══════════════════════════════════════════════════════════════════════════


class FeeRunError(FeeCalcError):
    code = "fee_run_error"


class FeeRunStateError(FeeRunError):
    """The run is not in a state where this operation means anything."""

    code = "fee_run_state_invalid"


class FeeRunNotFoundError(FeeRunError):
    code = "fee_run_not_found"


class MakerCheckerError(FeeRunError):
    """Approver and proposer are the same person.

    Raised before the write reaches the database, which ALSO refuses it via
    ``assistant_activities_maker_checker_chk``. Both, deliberately: the CHECK
    is the guarantee and this is the readable message.
    """

    code = "fee_run_maker_checker"


class SnapshotMismatchError(FeeRunError):
    """The inputs behind a posted run are no longer what they were."""

    code = "fee_run_snapshot_mismatch"


# ═══════════════════════════════════════════════════════════════════════════
# Results
# ═══════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class PreviewResult:
    run_id: str
    status: str
    engine_version: str
    calculation_snapshot_hash: str
    lines_written: int
    lines_replaced: int
    total_net_fee: Decimal
    skipped: tuple[dict[str, Any], ...]


@dataclass(frozen=True)
class SnapshotVerification:
    """Whether a posted run still reproduces, and which half failed if not."""

    run_id: str
    stored_hash: str
    recomputed_hash_from_stored_inputs: str
    recomputed_hash_from_live_inputs: str | None
    reproduces_from_stored_inputs: bool
    inputs_unchanged_upstream: bool
    line_mismatches: tuple[dict[str, Any], ...]
    live_error: str | None = None

    @property
    def ok(self) -> bool:
        """Reproducibility only. Upstream drift is reported, not failed —
        a corrected balance is a real event, not a broken run."""
        return self.reproduces_from_stored_inputs and not self.line_mismatches


# ═══════════════════════════════════════════════════════════════════════════
# Reading
# ═══════════════════════════════════════════════════════════════════════════


async def get_run(conn, org_id: str, run_id: str) -> dict[str, Any]:
    row = await conn.fetchrow(
        f"""SELECT id::text AS id, org_id::text AS org_id, period_start, period_end,
                   billing_frequency, run_type, status,
                   reverses_run_id::text AS reverses_run_id,
                   calculation_snapshot_hash, engine_version,
                   created_by::text AS created_by,
                   advisor_approved_by::text AS advisor_approved_by, advisor_approved_at,
                   compliance_approved_by::text AS compliance_approved_by,
                   compliance_approved_at, posted_at, created_at
            FROM {T_RUNS} WHERE id = $1::uuid AND org_id = $2::uuid
              AND system_to IS NULL""",
        run_id, org_id,
    )
    if row is None:
        raise FeeRunNotFoundError(f"fee_run {run_id} not found in org {org_id}",
                                  run_id=run_id)
    return dict(row)


async def list_lines(conn, org_id: str, run_id: str) -> list[dict[str, Any]]:
    rows = await conn.fetch(
        f"""SELECT id::text AS id, account_id::text AS account_id,
                   billing_group_id::text AS billing_group_id,
                   household_id::text AS household_id,
                   entity_id::text AS entity_id, advisor_id::text AS advisor_id,
                   product_type, fee_schedule_id::text AS fee_schedule_id,
                   billable_value, excluded_value, valuation_method, gross_fee,
                   discount_amount, credit_amount, minimum_adjustment, net_fee,
                   payer_account_id::text AS payer_account_id, payment_method,
                   currency, calc_detail
            FROM {T_LINES} WHERE fee_run_id = $1::uuid AND org_id = $2::uuid
            ORDER BY account_id, id""",
        run_id, org_id,
    )
    return [dict(r) for r in rows]


# ═══════════════════════════════════════════════════════════════════════════
# DRAFT
# ═══════════════════════════════════════════════════════════════════════════


async def create_run(
    conn, org_id: str, *, period_start: date, period_end: date,
    billing_frequency: str, run_type: str = "SCHEDULED",
    created_by: str | None = None, reverses_run_id: str | None = None,
) -> str:
    """A DRAFT run with no lines. ``org_id`` is the caller's, never a body's."""
    if run_type not in RUN_TYPES:
        raise FeeRunError(f"run_type={run_type!r} is not one of {RUN_TYPES}",
                          run_type=run_type)
    if run_type == "REVERSAL" and reverses_run_id is None:
        raise FeeRunError(
            "a REVERSAL run needs reverses_run_id — fee_runs_reversal_requires_target "
            "refuses the row without it",
        )
    return str(await conn.fetchval(
        f"""INSERT INTO {T_RUNS}
              (org_id, period_start, period_end, billing_frequency, run_type,
               status, created_by, reverses_run_id)
            VALUES ($1::uuid, $2::date, $3::date, $4, $5, 'DRAFT', $6::uuid, $7::uuid)
            RETURNING id""",
        org_id, period_start, period_end, billing_frequency, run_type,
        created_by, reverses_run_id,
    ))


# ═══════════════════════════════════════════════════════════════════════════
# PREVIEW
# ═══════════════════════════════════════════════════════════════════════════


async def in_scope_accounts(conn, org_id: str, *, as_of: date) -> list[dict[str, Any]]:
    """Every current, billable account in the org, with the schedule that
    governs it on ``as_of``.

    Precedence is fee34's :func:`resolve_assignment_for_account`, called once
    per account. It is NOT re-implemented as a bulk join here: the resolver
    carries the losing assignments so "why this schedule" is answerable, and a
    second, faster copy of a precedence rule is a second copy that drifts.

    An account with no assignment is REPORTED as skipped, not billed at zero
    and not silently dropped. ``accounts.is_billable = false`` accounts are
    still passed to the engine, which short-circuits them to zero WITH a trace
    — "not billed" and "billed nothing" are different facts and the run should
    record which one happened.
    """
    rows = await conn.fetch(
        f"""SELECT id::text AS id FROM {T_ACCOUNTS}
            WHERE org_id = $1::uuid AND valid_to IS NULL AND system_to IS NULL
              AND (opened_on IS NULL OR opened_on <= $2::date)
            ORDER BY id""",
        org_id, as_of,
    )
    out = []
    for r in rows:
        resolved = await resolve_assignment_for_account(
            conn, org_id, r["id"], as_of=as_of
        )
        out.append({
            "account_id": r["id"],
            "fee_schedule_id": resolved.fee_schedule_id if resolved else None,
            "assignment": (
                {
                    "assignment_id": resolved.assignment_id,
                    "scope_type": resolved.scope_type,
                    "scope_id": resolved.scope_id,
                    "precedence": resolved.precedence,
                    "schedule_code": resolved.schedule_code,
                    "schedule_version": resolved.schedule_version,
                    "schedule_status": resolved.schedule_status,
                    "losers": list(resolved.losers),
                }
                if resolved else None
            ),
        })
    return out


async def preview_run(
    conn, org_id: str, run_id: str, *, account_ids: Sequence[str] | None = None,
) -> PreviewResult:
    """Calculate every in-scope account, replace the run's lines, hash the inputs.

    Re-runnable. The old lines are DELETEd first and the count is returned, so
    "the preview replaced 12 lines with 12 lines" and "the preview added 12
    lines to the 12 already there" cannot be confused — which they could be,
    silently, before ``fee_run_lines_prevent_posted_mutation`` was fixed to
    return OLD on DELETE (see docs/fee36_part1_fix.sql, finding F36-A).

    Runs inside whatever transaction the caller opened. A preview that deleted
    the old lines and then failed on the fourth account must not leave the run
    with three lines and a stale hash.
    """
    run = await get_run(conn, org_id, run_id)
    _assert_transition(run["status"], "PREVIEW")

    period_start, period_end = run["period_start"], run["period_end"]
    scope = await in_scope_accounts(conn, org_id, as_of=period_end)
    if account_ids is not None:
        wanted = {str(a) for a in account_ids}
        scope = [s for s in scope if s["account_id"] in wanted]

    requests: list[AccountCalcRequest] = []
    provenances: list[dict[str, Any]] = []
    docs: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []

    for entry in scope:
        if entry["fee_schedule_id"] is None:
            skipped.append({
                "account_id": entry["account_id"],
                "reason": (
                    "no fee_assignment resolves for this account on "
                    f"{period_end} and there is no ORG_DEFAULT. The account is "
                    "not billed; it is not billed ZERO"
                ),
            })
            continue
        request, provenance = await load_account_calc_request(
            conn, org_id,
            account_id=entry["account_id"],
            fee_schedule_id=entry["fee_schedule_id"],
            period_start=period_start, period_end=period_end,
        )
        provenance["assignment"] = entry["assignment"]
        requests.append(request)
        provenances.append(provenance)
        docs.append(canonical_inputs(request))

    # ── the ONE call that produces every number in this run ─────────────────
    # calculate_group_fees, not calculate_account_fee in a loop: a
    # HOUSEHOLD- or BILLING_GROUP-scoped minimum is compared against the sum of
    # the group's accounts, and an account cannot see its siblings. Looping
    # would charge the minimum once per account.
    group = calculate_group_fees(requests) if requests else None
    results: tuple[FeeCalcResult, ...] = group.results if group else ()

    digest = snapshot_hash(
        docs, engine_version=ENGINE_VERSION, run_type=run["run_type"],
        period_start=period_start, period_end=period_end,
        billing_frequency=run["billing_frequency"],
    )

    replaced = await _delete_lines(conn, org_id, run_id)

    total = ZERO
    for request, provenance, doc, result in zip(requests, provenances, docs, results):
        await _insert_line(
            conn, org_id, run_id,
            request=request, result=result, provenance=provenance, inputs_doc=doc,
            group_detail=group.group_detail if group else None,
        )
        total += result.amount

    await conn.execute(
        f"""UPDATE {T_RUNS}
            SET status = 'PREVIEW', calculation_snapshot_hash = $3,
                engine_version = $4
            WHERE id = $1::uuid AND org_id = $2::uuid""",
        run_id, org_id, digest, ENGINE_VERSION,
    )
    return PreviewResult(
        run_id=run_id, status="PREVIEW", engine_version=ENGINE_VERSION,
        calculation_snapshot_hash=digest, lines_written=len(results),
        lines_replaced=replaced, total_net_fee=total, skipped=tuple(skipped),
    )


async def _delete_lines(conn, org_id: str, run_id: str) -> int:
    """Remove the run's lines and RETURN HOW MANY WENT.

    ``RETURNING`` rather than parsing the ``DELETE n`` command tag: the tag was
    what made F36-A invisible for as long as it was. A caller that counts rows
    it got back cannot be told "deleted" by a statement that deleted nothing.
    """
    rows = await conn.fetch(
        f"DELETE FROM {T_LINES} WHERE fee_run_id = $1::uuid AND org_id = $2::uuid "
        f"RETURNING id",
        run_id, org_id,
    )
    return len(rows)


async def _insert_line(
    conn, org_id: str, run_id: str, *, request: AccountCalcRequest,
    result: FeeCalcResult, provenance: Mapping[str, Any],
    inputs_doc: Mapping[str, Any], group_detail: Mapping[str, Any] | None,
) -> str:
    """One ``fee_run_lines`` row. Every amount comes from ``result``.

    ``calc_detail`` carries three things, kept separate on purpose:

      ``engine``     the engine's own step-by-step trace
      ``inputs``     the canonical input document the hash is taken over — the
                     preimage, without which the hash could only ever say that
                     something changed and never what
      ``provenance`` the resolved billing group and every credit's basis, so
                     "why is this credit $412.50" is answerable from the run
                     rather than from tables that have since moved

    The component columns (``discount_amount``, ``credit_amount``,
    ``minimum_adjustment``) are read OUT of the engine's trace as the DELTA
    each ordering step moved the running amount by, rather than recomputed.
    Recomputing them here would be a second implementation of the ordering
    policy, and the copy that drifts is the one the invoice prints. They are
    SIGNED: a discount is negative because it reduced the fee.

    Three things these columns deliberately do NOT do:

    * ``excluded_value`` is billable measured against the post-flow-adjustment
      value, not against ``account_value`` — otherwise a day-weighted
      contribution reads as an exclusion.
    * ``minimum_adjustment`` carries the MINIMUM step only. A MAXIMUM cap is a
      real, different event and lives in ``calc_detail``; folding it into a
      column named for the minimum would net two opposite adjustments into one
      number that describes neither.
    * ``gross_fee`` is the engine's ``gross_fee`` — the tiered amount BEFORE
      proration, which is how fee35 defines it. So
      ``gross - discount - credit`` does not equal ``net_fee`` on a prorated
      period, and should not: the proration factor sits between them and is in
      the trace.
    """
    detail = result.calc_detail
    steps = {s.get("step"): s for s in detail.get("steps", []) if isinstance(s, dict)}

    account = request.data.account
    ex_step = steps.get("EXCLUSIONS") or {}
    pre_exclusion = _decimal_or_zero(
        ex_step.get("value_after_flow_adjustment", result.account_value)
    )
    excluded = pre_exclusion - result.billable_value
    discount = _step_delta(steps.get("DISCOUNTS"))
    credit = _step_delta(steps.get("CREDITS"))
    minimum = _step_delta(steps.get("MINIMUM"))

    payload = {
        "engine": detail,
        "inputs": inputs_doc,
        "provenance": dict(provenance),
    }
    if group_detail is not None:
        payload["group"] = dict(group_detail)

    return str(await conn.fetchval(
        f"""INSERT INTO {T_LINES}
              (org_id, fee_run_id, account_id, billing_group_id, household_id,
               entity_id, advisor_id, product_type, fee_schedule_id,
               billable_value, excluded_value, valuation_method, gross_fee,
               discount_amount, credit_amount, minimum_adjustment, net_fee,
               payer_account_id, payment_method, calc_detail, currency)
            VALUES ($1::uuid, $2::uuid, $3::uuid, $4::uuid, $5::uuid, $6::uuid,
                    $7::uuid, $8, $9::uuid, $10, $11, $12, $13, $14, $15, $16,
                    $17, $18::uuid, $19, $20::jsonb, $21)
            RETURNING id""",
        org_id, run_id, account.id, account.billing_group_id, account.household_id,
        provenance.get("owner_entity_id"), provenance.get("advisor_of_record_id"),
        request.schedule.product_type, request.schedule.id,
        result.billable_value, excluded, request.schedule.valuation_method,
        result.gross_fee, discount, credit, minimum, result.amount,
        account.id, DEFAULT_PAYMENT_METHOD, json.dumps(payload), result.currency,
    ))


def _decimal_or_zero(value: Any) -> Decimal:
    if value is None:
        return ZERO
    return Decimal(str(value))


def _step_delta(step: Mapping[str, Any] | None) -> Decimal:
    """What one ordering step moved the running amount by, from the trace.

    ``amount_after - amount_before``, signed. Reading the delta instead of
    re-deriving it means the stored ``discount_amount`` is by construction the
    discount the engine actually applied, including in the cases where the
    engine chose not to apply one.
    """
    if not step:
        return ZERO
    before = step.get("amount_before")
    after = step.get("amount_after")
    if before is None or after is None:
        return ZERO
    return Decimal(str(after)) - Decimal(str(before))


# ═══════════════════════════════════════════════════════════════════════════
# Variance — what an advisor actually reads before approving
# ═══════════════════════════════════════════════════════════════════════════


async def variance_report(conn, org_id: str, run_id: str) -> list[dict[str, Any]]:
    """Each line against the same account's last POSTED line, biggest move first.

    "Prior period" is the most recent POSTED, non-REVERSAL run for the same
    account whose ``period_end`` is strictly before this run's ``period_start``
    — not "the period immediately before", because a quarter can legitimately
    be skipped, and not any POSTED run at all, because a REVERSAL's line is a
    negation and comparing against it would report every account as having
    doubled.

    An account with no prior line comes back with ``prior_net_fee: None`` and
    ``is_new: True`` rather than being omitted or compared against zero. A new
    account is the single most common reason a line has no prior, and
    presenting it as a 100% increase would bury the real movers.

    Sorted by absolute dollar change descending, with new accounts after the
    changed ones (they have no change to rank) and ties broken on account id so
    the order is stable between two calls.
    """
    run = await get_run(conn, org_id, run_id)
    rows = await conn.fetch(
        f"""
        SELECT l.account_id::text AS account_id, l.net_fee, l.gross_fee,
               l.billable_value, l.fee_schedule_id::text AS fee_schedule_id,
               prior.net_fee        AS prior_net_fee,
               prior.billable_value AS prior_billable_value,
               prior.period_start   AS prior_period_start,
               prior.period_end     AS prior_period_end,
               prior.run_id::text   AS prior_run_id
        FROM {T_LINES} l
        LEFT JOIN LATERAL (
            SELECT pl.net_fee, pl.billable_value, pr.period_start, pr.period_end,
                   pr.id AS run_id
            FROM {T_LINES} pl
            JOIN {T_RUNS} pr ON pr.id = pl.fee_run_id
            WHERE pl.org_id = l.org_id
              AND pl.account_id = l.account_id
              AND pr.status = 'POSTED'
              AND pr.run_type <> 'REVERSAL'
              AND pr.system_to IS NULL
              AND pr.period_end < $3::date
            ORDER BY pr.period_end DESC, pr.posted_at DESC
            LIMIT 1
        ) prior ON TRUE
        WHERE l.fee_run_id = $1::uuid AND l.org_id = $2::uuid
        """,
        run_id, org_id, run["period_start"],
    )

    out: list[dict[str, Any]] = []
    for r in rows:
        prior = r["prior_net_fee"]
        change = None if prior is None else r["net_fee"] - prior
        pct = None
        if prior is not None and prior != ZERO:
            pct = (change / prior) * Decimal(100)
        out.append({
            "account_id": r["account_id"],
            "fee_schedule_id": r["fee_schedule_id"],
            "net_fee": r["net_fee"],
            "prior_net_fee": prior,
            "change": change,
            "abs_change": None if change is None else abs(change),
            "pct_change": pct,
            "billable_value": r["billable_value"],
            "prior_billable_value": r["prior_billable_value"],
            "prior_period": (
                None if prior is None
                else f"{r['prior_period_start']}..{r['prior_period_end']}"
            ),
            "prior_run_id": r["prior_run_id"],
            "is_new": prior is None,
        })
    out.sort(key=lambda d: (
        d["abs_change"] is None,
        -(d["abs_change"] or ZERO),
        d["account_id"],
    ))
    return out


# ═══════════════════════════════════════════════════════════════════════════
# Approvals — through assistant_activities
# ═══════════════════════════════════════════════════════════════════════════


def _gate(gate: str) -> dict[str, str]:
    try:
        return dict(APPROVAL_GATES[gate])
    except KeyError:
        raise FeeRunError(
            f"gate={gate!r} is not one of {tuple(APPROVAL_GATES)}", gate=gate
        ) from None


async def propose_approval(
    conn, org_id: str, run_id: str, *, gate: str, proposed_by: str,
    rationale: str | None = None,
) -> str:
    """Open an approval gate. Writes a ``'proposed'`` activity; run unchanged.

    Returns the activity id. The run does NOT advance here — a proposal is a
    request for a second person, and a lifecycle that moved on the proposal
    would make the checker decorative.
    """
    spec = _gate(gate)
    run = await get_run(conn, org_id, run_id)
    if run["status"] != spec["from_status"]:
        raise FeeRunStateError(
            f"fee_run {run_id} is {run['status']}; the {gate} gate opens from "
            f"{spec['from_status']}",
            run_id=run_id, status=run["status"], gate=gate,
        )
    existing = await _open_proposal(conn, org_id, run_id, spec["action_key"])
    if existing:
        return existing["id"]
    return str(await conn.fetchval(
        f"""INSERT INTO {T_ACTIVITIES}
              (org_id, user_id, proposed_by, action_key, title, status, rationale,
               related_type, related_id, payload, reversible)
            VALUES ($1::uuid, $2::uuid, $2::uuid, $3, $4, $5, $6, $7, $8::uuid,
                    $9::jsonb, false)
            RETURNING id""",
        org_id, proposed_by, spec["action_key"], spec["title"], ACTIVITY_PROPOSED,
        rationale, RELATED_TYPE, run_id,
        json.dumps({
            "gate": gate,
            "fee_run_id": run_id,
            "advances_to": spec["advances_to"],
            "period": f"{run['period_start']}..{run['period_end']}",
            "calculation_snapshot_hash": run["calculation_snapshot_hash"],
        }),
    ))


async def _open_proposal(conn, org_id, run_id, action_key):
    row = await conn.fetchrow(
        f"""SELECT id::text AS id, proposed_by::text AS proposed_by, status
            FROM {T_ACTIVITIES}
            WHERE org_id = $1::uuid AND related_type = $2 AND related_id = $3::uuid
              AND action_key = $4 AND status = $5
            ORDER BY created_at DESC LIMIT 1""",
        org_id, RELATED_TYPE, run_id, action_key, ACTIVITY_PROPOSED,
    )
    return dict(row) if row else None


async def approve(
    conn, org_id: str, run_id: str, *, gate: str, approved_by: str,
) -> dict[str, Any]:
    """Close an approval gate and advance the run, in one transaction.

    Refuses when the approver is the proposer — and the database refuses it too
    (``assistant_activities_maker_checker_chk``), so bypassing this function
    does not bypass the rule.

    The activity moves to ``'approved'`` FIRST and the run advances second,
    both inside the caller's transaction. Ordering matters only if it can be
    torn: it cannot, but a run that had advanced with no approved activity
    behind it would be exactly the "bespoke flag" this design exists to avoid,
    so the activity is made true before the status that depends on it.
    """
    spec = _gate(gate)
    run = await get_run(conn, org_id, run_id)
    if run["status"] != spec["from_status"]:
        raise FeeRunStateError(
            f"fee_run {run_id} is {run['status']}; the {gate} gate closes from "
            f"{spec['from_status']}",
            run_id=run_id, status=run["status"], gate=gate,
        )
    _assert_transition(run["status"], spec["advances_to"])

    proposal = await _open_proposal(conn, org_id, run_id, spec["action_key"])
    if proposal is None:
        raise FeeRunStateError(
            f"no open {ACTIVITY_PROPOSED!r} assistant_activities row for the "
            f"{gate} gate on fee_run {run_id}. The gate has to be PROPOSED by "
            f"one person before it can be APPROVED by another — approving "
            f"without a proposal is a single-party approval wearing two hats",
            run_id=run_id, gate=gate,
        )
    if str(proposal["proposed_by"]) == str(approved_by):
        raise MakerCheckerError(
            f"user {approved_by} proposed the {gate} approval of fee_run "
            f"{run_id} and cannot also approve it",
            run_id=run_id, gate=gate, user_id=str(approved_by),
        )

    activity = await conn.fetchrow(
        f"""UPDATE {T_ACTIVITIES}
            SET status = $3, approved_by = $4::uuid, updated_at = now()
            WHERE id = $1::uuid AND org_id = $2::uuid
            RETURNING id::text AS id, status, approved_by::text AS approved_by,
                      proposed_by::text AS proposed_by""",
        proposal["id"], org_id, ACTIVITY_APPROVED, approved_by,
    )
    await conn.execute(
        f"""UPDATE {T_RUNS}
            SET status = $3, {spec['by_column']} = $4::uuid, {spec['at_column']} = now()
            WHERE id = $1::uuid AND org_id = $2::uuid""",
        run_id, org_id, spec["advances_to"], approved_by,
    )
    return {
        "run_id": run_id,
        "status": spec["advances_to"],
        "activity_id": activity["id"],
        "activity_status": activity["status"],
        "proposed_by": activity["proposed_by"],
        "approved_by": activity["approved_by"],
        "authority": (
            "the assistant_activities row above. fee_runs.%s is a denormalised "
            "mirror and is not read to decide anything" % spec["by_column"]
        ),
    }


async def approval_activities(conn, org_id: str, run_id: str) -> list[dict[str, Any]]:
    """Every approval activity for this run — the audit trail, in order."""
    rows = await conn.fetch(
        f"""SELECT id::text AS id, action_key, status, title,
                   proposed_by::text AS proposed_by, approved_by::text AS approved_by,
                   related_type, related_id::text AS related_id, payload,
                   created_at, updated_at
            FROM {T_ACTIVITIES}
            WHERE org_id = $1::uuid AND related_type = $2 AND related_id = $3::uuid
            ORDER BY created_at""",
        org_id, RELATED_TYPE, run_id,
    )
    return [dict(r) for r in rows]


# ═══════════════════════════════════════════════════════════════════════════
# POSTED
# ═══════════════════════════════════════════════════════════════════════════


async def post_run(conn, org_id: str, run_id: str) -> dict[str, Any]:
    """COMPLIANCE_APPROVED -> POSTED. After this the DB refuses every change.

    Both approval gates are re-checked against ``assistant_activities`` rather
    than against ``fee_runs.status`` alone. The status could only have got here
    through :func:`approve`, but "could only have" is an argument about the
    code, and the point of the ledger is that the posting decision rests on the
    ledger.
    """
    run = await get_run(conn, org_id, run_id)
    _assert_transition(run["status"], "POSTED")

    activities = await approval_activities(conn, org_id, run_id)
    approved = {a["action_key"] for a in activities if a["status"] == ACTIVITY_APPROVED}
    missing = [
        g for g, spec in APPROVAL_GATES.items()
        if spec["action_key"] not in approved
    ]
    if missing:
        raise FeeRunStateError(
            f"fee_run {run_id} is {run['status']} but has no approved "
            f"assistant_activities row for: {missing}. The run status is a "
            f"mirror; the ledger is the authority",
            run_id=run_id, missing_gates=missing,
        )
    if not run["calculation_snapshot_hash"]:
        raise FeeRunStateError(
            f"fee_run {run_id} has no calculation_snapshot_hash — it was never "
            f"previewed, and posting it would freeze numbers whose inputs "
            f"nobody can ever check",
            run_id=run_id,
        )

    await conn.execute(
        f"""UPDATE {T_RUNS} SET status = 'POSTED', posted_at = now()
            WHERE id = $1::uuid AND org_id = $2::uuid""",
        run_id, org_id,
    )
    ledger = await post_to_ledger(conn, org_id, run_id)
    return {"run_id": run_id, "status": "POSTED", "ledger": ledger}


# ═══════════════════════════════════════════════════════════════════════════
# GL posting — a STUB, deliberately, and it says so at runtime
# ═══════════════════════════════════════════════════════════════════════════


#: Design doc open question #3 — WHICH BOOKS RIA FEE REVENUE POSTS TO — is
#: unanswered, so nothing is wired. The pieces all exist and were measured:
#: ``posting_templates`` carries a ``MANAGEMENT_FEE`` template for org
#: 00000000-…-0001, ``posting_template_lines`` names ``account_code``/``side``,
#: ``chart_of_accounts`` has ``5000 Management Fee Expense``, and
#: ``journal_entries.vehicle_id`` is NOT NULL.
#:
#: That last column is the whole problem, not a detail. Every existing template
#: posts INSIDE a vehicle's books — an SPV paying its manager. RIA advisory fee
#: revenue is the manager's OWN revenue and has no vehicle, and account 5000 is
#: an EXPENSE account on the paying side. Guessing would either invent a
#: vehicle id or book the firm's revenue as somebody's expense, and both are
#: the kind of error a reconciliation finds a quarter later.
GL_POSTING_DECISION_REQUIRED = (
    "Which books does RIA fee revenue post to? journal_entries.vehicle_id is "
    "NOT NULL and every deployed posting_template posts inside a vehicle's "
    "books; chart_of_accounts has no revenue account for advisory fees "
    "(5000 is Management Fee EXPENSE, the payer's side). Needs an answer "
    "before fee_run_lines can generate journal_entries. Tracked as design doc "
    "open question #3."
)


async def post_to_ledger(conn, org_id: str, run_id: str) -> dict[str, Any]:
    """STUB. Writes NOTHING to journal_entries and returns why.

    Returning a marked no-op rather than raising: the fee run itself is
    complete and correct without a GL entry, and making posting fail would
    block a real billing run on an accounting question. Returning silently
    would let it look done.
    """
    return {
        "posted": False,
        "reason": "not_implemented",
        "decision_required": GL_POSTING_DECISION_REQUIRED,
        "run_id": run_id,
        "journal_entries_written": 0,
    }


# ═══════════════════════════════════════════════════════════════════════════
# REVERSAL
# ═══════════════════════════════════════════════════════════════════════════


async def create_reversal(
    conn, org_id: str, target_run_id: str, *, created_by: str | None = None,
    reason: str | None = None,
) -> str:
    """A REVERSAL run negating every line of a POSTED run. Original untouched.

    Same period, same accounts, same schedules; every amount sign-flipped. The
    original is not mutated and not moved to ``'REVERSED'`` — it cannot be, the
    trigger refuses every UPDATE on a POSTED row. The two runs are linked by
    ``reverses_run_id`` and read in that direction (finding F36-C).

    The reversal is created DRAFT and its lines written immediately, then left
    for the same two approval gates. A reversal that posted itself would be a
    way to un-bill a client without a second signature.
    """
    target = await get_run(conn, org_id, target_run_id)
    if target["status"] not in IMMUTABLE_STATUSES:
        raise FeeRunStateError(
            f"fee_run {target_run_id} is {target['status']}; only a "
            f"{IMMUTABLE_STATUSES} run needs reversing. An unposted run is "
            f"corrected by re-previewing it",
            run_id=target_run_id, status=target["status"],
        )
    if target["run_type"] == "REVERSAL":
        raise FeeRunStateError(
            f"fee_run {target_run_id} is itself a REVERSAL. Reversing a "
            f"reversal re-bills the client and should be an explicit new run, "
            f"not a double negative nobody can read",
            run_id=target_run_id,
        )
    existing = await conn.fetchval(
        f"""SELECT id::text FROM {T_RUNS}
            WHERE org_id = $1::uuid AND reverses_run_id = $2::uuid
              AND system_to IS NULL LIMIT 1""",
        org_id, target_run_id,
    )
    if existing:
        raise FeeRunStateError(
            f"fee_run {target_run_id} is already reversed by {existing}. A "
            f"second reversal would credit the client twice",
            run_id=target_run_id, existing_reversal_id=existing,
        )

    reversal_id = await create_run(
        conn, org_id,
        period_start=target["period_start"], period_end=target["period_end"],
        billing_frequency=target["billing_frequency"], run_type="REVERSAL",
        created_by=created_by, reverses_run_id=target_run_id,
    )

    lines = await list_lines(conn, org_id, target_run_id)
    for line in lines:
        await conn.execute(
            f"""INSERT INTO {T_LINES}
                  (org_id, fee_run_id, account_id, billing_group_id, household_id,
                   entity_id, advisor_id, product_type, fee_schedule_id,
                   billable_value, excluded_value, valuation_method, gross_fee,
                   discount_amount, credit_amount, minimum_adjustment, net_fee,
                   payer_account_id, payment_method, calc_detail, currency)
                VALUES ($1::uuid, $2::uuid, $3::uuid, $4::uuid, $5::uuid, $6::uuid,
                        $7::uuid, $8, $9::uuid, $10, $11, $12, $13, $14, $15, $16,
                        $17, $18::uuid, $19, $20::jsonb, $21)""",
            org_id, reversal_id, line["account_id"], line["billing_group_id"],
            line["household_id"], line["entity_id"], line["advisor_id"],
            line["product_type"], line["fee_schedule_id"],
            # billable_value is NOT negated: the account really did hold that
            # much. Negating it would claim a negative portfolio.
            line["billable_value"], line["excluded_value"], line["valuation_method"],
            -line["gross_fee"], -line["discount_amount"], -line["credit_amount"],
            -line["minimum_adjustment"], -line["net_fee"],
            line["payer_account_id"], line["payment_method"],
            json.dumps({
                "reversal_of_line": line["id"],
                "reverses_run_id": target_run_id,
                "reason": reason,
                "note": (
                    "every amount is the original line's, negated. No fee was "
                    "recalculated: re-running the engine could legitimately "
                    "produce a different number today, and a reversal that did "
                    "not exactly undo what was billed is not a reversal"
                ),
                "original_net_fee": str(line["net_fee"]),
            }),
            line["currency"],
        )

    await conn.execute(
        f"""UPDATE {T_RUNS} SET status = 'PREVIEW',
                engine_version = $3, calculation_snapshot_hash = $4
            WHERE id = $1::uuid AND org_id = $2::uuid""",
        reversal_id, org_id, target["engine_version"],
        target["calculation_snapshot_hash"],
    )
    return reversal_id


async def reversal_balance(conn, org_id: str, target_run_id: str) -> list[dict[str, Any]]:
    """Per account: original net_fee, reversal net_fee, and their sum.

    The sum must be exactly zero for every account. Returned per account rather
    than as one total because two accounts whose errors cancel would net to
    zero overall while both being wrong.
    """
    rows = await conn.fetch(
        f"""
        SELECT COALESCE(o.account_id, r.account_id)::text AS account_id,
               COALESCE(o.net_fee, 0) AS original_net_fee,
               COALESCE(r.net_fee, 0) AS reversal_net_fee,
               COALESCE(o.net_fee, 0) + COALESCE(r.net_fee, 0) AS net
        FROM (SELECT account_id, sum(net_fee) net_fee FROM {T_LINES}
              WHERE fee_run_id = $2::uuid AND org_id = $1::uuid
              GROUP BY account_id) o
        FULL OUTER JOIN (
              SELECT l.account_id, sum(l.net_fee) net_fee FROM {T_LINES} l
              JOIN {T_RUNS} rr ON rr.id = l.fee_run_id
              WHERE rr.reverses_run_id = $2::uuid AND l.org_id = $1::uuid
              GROUP BY l.account_id) r
          ON r.account_id = o.account_id
        ORDER BY 1
        """,
        org_id, target_run_id,
    )
    return [dict(r) for r in rows]


# ═══════════════════════════════════════════════════════════════════════════
# Reproducibility
# ═══════════════════════════════════════════════════════════════════════════


async def verify_snapshot(
    conn, org_id: str, run_id: str, *, check_live: bool = True,
) -> SnapshotVerification:
    """Does this run still reproduce, and have its inputs moved since?

    Two genuinely different questions, answered separately because they fail
    for different reasons and only one of them is a bug:

    **Reproducibility.** Rebuild each account's ``AccountCalcRequest`` from the
    ``calc_detail['inputs']`` document stored on its own line, re-run
    :func:`~services.fee_calc.calculate_group_fees`, and compare to the cent.
    A failure here means the engine no longer produces the same number from the
    same inputs — a real regression. The hash is recomputed over those same
    stored documents and must equal ``calculation_snapshot_hash``, which is
    what proves the stored preimage is the preimage.

    **Drift.** Re-load the inputs from the live tables for the same accounts and
    period and re-hash. A difference means something upstream moved — a
    restated balance, a re-pointed assignment, a back-dated exclusion. That is
    NOT a failure: retroactive corrections are legitimate and this is exactly
    the event the hash exists to surface. It is reported, and
    :attr:`SnapshotVerification.ok` does not depend on it.
    """
    run = await get_run(conn, org_id, run_id)
    lines = await list_lines(conn, org_id, run_id)

    stored_docs: list[dict[str, Any]] = []
    rebuilt: list[AccountCalcRequest] = []
    for line in lines:
        detail = line["calc_detail"]
        if isinstance(detail, str):
            detail = json.loads(detail)
        doc = detail.get("inputs")
        if doc is None:
            raise SnapshotMismatchError(
                f"fee_run_line {line['id']} has no calc_detail['inputs'] — the "
                f"hash's preimage was not stored, so this run's numbers can "
                f"never be re-derived",
                run_id=run_id, line_id=line["id"],
            )
        stored_docs.append(doc)
        rebuilt.append(_request_from_doc(doc))

    recomputed = snapshot_hash(
        stored_docs, engine_version=run["engine_version"] or ENGINE_VERSION,
        run_type=run["run_type"], period_start=run["period_start"],
        period_end=run["period_end"], billing_frequency=run["billing_frequency"],
    )

    mismatches: list[dict[str, Any]] = []
    if rebuilt:
        results = calculate_group_fees(rebuilt).by_account()
        for line in lines:
            got = results.get(line["account_id"])
            if got is None:
                mismatches.append({
                    "account_id": line["account_id"],
                    "reason": "the rebuilt request set produced no result",
                })
            elif got.amount != line["net_fee"]:
                mismatches.append({
                    "account_id": line["account_id"],
                    "stored_net_fee": str(line["net_fee"]),
                    "recomputed_net_fee": str(got.amount),
                    "difference": str(got.amount - line["net_fee"]),
                })

    live_hash: str | None = None
    live_error: str | None = None
    if check_live:
        try:
            live_docs = []
            for line in lines:
                request, _ = await load_account_calc_request(
                    conn, org_id,
                    account_id=line["account_id"],
                    fee_schedule_id=line["fee_schedule_id"],
                    period_start=run["period_start"], period_end=run["period_end"],
                )
                live_docs.append(canonical_inputs(request))
            live_hash = snapshot_hash(
                live_docs, engine_version=run["engine_version"] or ENGINE_VERSION,
                run_type=run["run_type"], period_start=run["period_start"],
                period_end=run["period_end"],
                billing_frequency=run["billing_frequency"],
            )
        except Exception as exc:  # noqa: BLE001
            # An input that no longer LOADS is itself drift, and the loudest
            # kind. Recorded, not raised: the stored run is still valid.
            live_error = f"{type(exc).__name__}: {exc}"

    return SnapshotVerification(
        run_id=run_id,
        stored_hash=run["calculation_snapshot_hash"],
        recomputed_hash_from_stored_inputs=recomputed,
        recomputed_hash_from_live_inputs=live_hash,
        reproduces_from_stored_inputs=(recomputed == run["calculation_snapshot_hash"]),
        inputs_unchanged_upstream=(
            live_hash is not None and live_hash == run["calculation_snapshot_hash"]
        ),
        line_mismatches=tuple(mismatches),
        live_error=live_error,
    )


def _request_from_doc(doc: Mapping[str, Any]) -> AccountCalcRequest:
    """Rebuild an :class:`AccountCalcRequest` from a stored input document.

    The dataclasses' own ``from_row`` does the coercion, so a stored ``"1000"``
    becomes ``Decimal('1000')`` through exactly the same path a database
    ``numeric`` does. Rebuilding by hand with ``Decimal(...)`` calls here would
    be a second coercion boundary, and the two would eventually disagree about
    something like a null.
    """
    from services.fee_calc_inputs import (
        AccountInput, AccountPeriodInput, BillingPeriod, CreditInput,
        DailyBalanceInput, DiscountInput, ExclusionInput, FeeScheduleInput,
        FeeTierInput, FlowInput, PositionInput,
    )

    schedule = FeeScheduleInput.from_row(dict(doc["schedule"]))
    tiers = tuple(FeeTierInput.from_row(dict(t)) for t in doc["tiers"])
    alt = {
        k: (FeeScheduleInput.from_row(dict(v["schedule"])),
            tuple(FeeTierInput.from_row(dict(t)) for t in v["tiers"]))
        for k, v in (doc.get("alt_schedules") or {}).items()
    }
    return AccountCalcRequest(
        data=AccountPeriodInput(
            account=AccountInput.from_row(dict(doc["account"])),
            period=BillingPeriod(**doc["period"]),
            balances=tuple(DailyBalanceInput.from_row(dict(b)) for b in doc["balances"]),
            flows=tuple(FlowInput.from_row(dict(f)) for f in doc["flows"]),
            positions=tuple(PositionInput.from_row(dict(p)) for p in doc["positions"]),
        ),
        schedule=schedule, tiers=tiers,
        exclusions=tuple(ExclusionInput.from_row(dict(x)) for x in doc["exclusions"]),
        discounts=tuple(DiscountInput.from_row(dict(x)) for x in doc["discounts"]),
        credits=tuple(CreditInput.from_row(dict(x)) for x in doc["credits"]),
        alt_schedules=alt,
    )


# ═══════════════════════════════════════════════════════════════════════════
# Internals
# ═══════════════════════════════════════════════════════════════════════════


def _assert_transition(current: str, target: str) -> None:
    allowed = ALLOWED_TRANSITIONS.get(current, ())
    if target not in allowed:
        raise FeeRunStateError(
            f"fee_run cannot go {current} -> {target}; from {current} the only "
            f"legal moves are {allowed or '(none — the run is frozen)'}",
            current_status=current, target_status=target,
        )


__all__ = [
    "ACTIVITY_APPROVED",
    "ACTIVITY_PROPOSED",
    "ALLOWED_TRANSITIONS",
    "APPROVAL_GATES",
    "GL_POSTING_DECISION_REQUIRED",
    "IMMUTABLE_STATUSES",
    "RELATED_TYPE",
    "RUN_STATUSES",
    "RUN_TYPES",
    "FeeRunError",
    "FeeRunNotFoundError",
    "FeeRunStateError",
    "MakerCheckerError",
    "PreviewResult",
    "SnapshotMismatchError",
    "SnapshotVerification",
    "approval_activities",
    "approve",
    "create_reversal",
    "create_run",
    "get_run",
    "in_scope_accounts",
    "list_lines",
    "post_run",
    "post_to_ledger",
    "preview_run",
    "propose_approval",
    "reversal_balance",
    "variance_report",
    "verify_snapshot",
]
