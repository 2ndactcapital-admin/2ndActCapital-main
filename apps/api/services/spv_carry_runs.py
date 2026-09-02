"""SPV carry runs — the database half of the carry engine.

``services.spv_carry`` is the pure waterfall and knows nothing about a
database. This module is everything else: subscribing to the realization
event, reading each investor's cumulative position, resolving their terms
through fee42, writing a DRAFT proposal, and walking it through the same
DRAFT -> PREVIEW -> ADVISOR_APPROVED -> COMPLIANCE_APPROVED -> POSTED
lifecycle that ``services.fee_runs`` already uses.


A TRIGGERED RUN PROPOSES. IT NEVER DISPOSES.
──────────────────────────────────────────────────────────────────────────────
:func:`propose_carry_run` writes ``status='DRAFT'`` and nothing else. It does
not preview, does not approve, does not post, and — deliberately — this module
exposes no function that does more than one of those steps at a time. The
distance between "an event fired" and "money moved" is four separate human
decisions, two of them by different people, and it stays that way whether the
run was started by a person clicking a button or by a distribution posting at
three in the morning.

The event path is *more* constrained than the human one, not less: it enters
through a BPMN Service Task whose action carries a ``required_permission``
which the engine re-checks against the member the trigger runs as
(``workflow_engine._assert_action_permission``), so an automatic trigger cannot
reach further than the person it runs as could.


THE STATED SOURCE FOR CUMULATIVE CAPITAL DOES NOT WORK — [F2]
──────────────────────────────────────────────────────────────────────────────
This sprint was told to read ``v_capital_accounts`` for each investor's
cumulative paid-in capital and distributions-to-date. Measured against the
deployed database, it cannot supply either, for three independent reasons:

  1. Its grain is ``journal_lines.dim_member_series_id``. There is **no
     ``dim_member_series`` table** — no table matching ``dim_%`` exists at all —
     and the column carries no foreign key. There is therefore no join path
     from that id to ``entities.id`` or ``spv_subscriptions.id``, so even a
     populated view could not be attributed to an SPV investor.
  2. The column is NULL in 100% of deployed ``journal_lines`` rows (0 of 2),
     and the view's own ``WHERE`` clause requires
     ``dim_member_series_id IS NOT NULL``. The view returns **zero rows** today
     and will keep returning zero until some GL posting path populates that
     dimension. Nothing does: GL posting is still open question #3
     (``fee_runs.GL_POSTING_DECISION_REQUIRED``), fee43's territory.
  3. It also groups by ``journal_entries.vehicle_id``, and the one deployed SPV
     has ``vehicle_entity_id`` NULL — so the SPV could not be located in it
     regardless.

:func:`capital_account_probe` runs the view for real on every proposal and
records what it found in ``calc_detail``, so this finding is re-measured at
runtime rather than frozen into a comment that quietly goes stale the day the
GL starts posting member-series lines.

What this module reads instead is not a second cumulative-balance table — it is
the **posted transactions themselves**:

    contributions  spv_transaction_allocations, on spv_transactions whose
                   transaction_type category is 'call'
    distributions  the same, on category 'distribution'

both restricted to ``spv_transactions.status = 'posted'`` and allocation
``status = 'active'`` — the exact rows ``spv_allocation.post_transaction``
wrote and ``spv_events`` published. Same posted-only discipline as fee36's
credit basis and fee39's revenue recognition: carry is never computed against
money nobody has actually moved. Flag-driven, not code-driven, for the same
reason ``services.spv_events`` gives.


WHOLE_FUND vs DEAL_BY_DEAL — WHAT IS ACTUALLY DERIVABLE — [F4]
──────────────────────────────────────────────────────────────────────────────
``spv_transactions`` carries **no column referencing an investment, position,
asset or security** (measured). An SPV therefore has no grain BELOW itself: its
whole transaction history *is* one deal's history, and ``spvs.deal_id`` is NOT
NULL. At the SPV grain DEAL_BY_DEAL and WHOLE_FUND are consequently the *same
arithmetic on the same rows*, and this module computes both from the same
scope.

That equivalence holds only for a standalone vehicle. ``spvs.vehicle_type``
admits ``investment_series`` and ``member_series``, and ``master_entity_id``
points at a master vehicle — a genuine WHOLE_FUND calculation across such a
structure would have to net this realization against every sibling series'
cumulative gains, which needs a master-level rollup that has no deployed data
(every deployed SPV is ``standalone_spv`` with ``master_entity_id`` NULL).

So the honest boundary is drawn where it actually is:
:class:`WholeFundScopeError` REFUSES a WHOLE_FUND run on a non-standalone
vehicle rather than silently computing a per-vehicle answer and labelling it
whole-fund. On a standalone SPV it proceeds, and ``calc_detail`` records that
the two bases coincide at this grain and why.


IDEMPOTENCY IS APPLICATION-LEVEL, AND SAYS SO
──────────────────────────────────────────────────────────────────────────────
``spv_carry_runs.domain_event_id`` has a plain btree index, not a unique one
(measured). A re-published event that got a second delivery must not produce a
second DRAFT proposing the same money twice, so :func:`propose_carry_run`
adopts an existing live run for the same event. Exactly like
``services.domain_events``' DELIVERED guard, this is an application-level rule
with no index behind it, and is documented as such rather than presented as a
guarantee.


ORG SCOPE
──────────────────────────────────────────────────────────────────────────────
``org_id`` is always a parameter from an already-authenticated caller or from
the event's own row — never from a request body. Every statement filters on it.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Mapping

from services import spv_fee_terms as SFT
from services.action_registry import REGISTRY, AssistantAction
from services.domain_events import decode_payload
from services.spv_carry import (
    CARRY_BASES,
    CARRY_DEAL_BY_DEAL,
    CARRY_WHOLE_FUND,
    ENGINE_VERSION,
    CarryResult,
    InvestorState,
    SpvCarryError,
    compute_carry,
    terms_from_resolved,
)

logger = logging.getLogger(__name__)

T_RUNS = "public.spv_carry_runs"
T_LINES = "public.spv_carry_run_lines"
T_ACTIVITIES = "public.assistant_activities"

#: ``services.spv_events.EVENT_SPV_REALIZATION`` — imported as a literal rather
#: than from that module to keep this consumer independent of the emitter.
EVENT_TYPE = "spv_realization"

#: ``spv_carry_runs_status_check``, read from the deployed constraint.
RUN_STATUSES = (
    "DRAFT", "PREVIEW", "ADVISOR_APPROVED", "COMPLIANCE_APPROVED",
    "POSTED", "REVERSED",
)

#: The status the DB trigger treats as frozen
#: (``spv_carry_runs_prevent_posted_mutation``). Mirrored here only so this
#: module's errors can say so before the trigger does.
IMMUTABLE_STATUSES = ("POSTED",)

#: Legal moves. Same shape as ``fee_runs.ALLOWED_TRANSITIONS``, including the
#: PREVIEW self-edge — a run may be re-previewed until somebody approves it.
ALLOWED_TRANSITIONS: Mapping[str, tuple[str, ...]] = {
    "DRAFT": ("PREVIEW",),
    "PREVIEW": ("PREVIEW", "ADVISOR_APPROVED"),
    "ADVISOR_APPROVED": ("PREVIEW", "COMPLIANCE_APPROVED"),
    "COMPLIANCE_APPROVED": ("PREVIEW", "POSTED"),
    "POSTED": (),
    "REVERSED": (),
}

#: ``assistant_activities.related_type`` for everything this module writes.
RELATED_TYPE = "spv_carry_run"

#: The two ``assistant_activities.status`` values the maker-checker ledger uses.
ACTIVITY_PROPOSED = "proposed"
ACTIVITY_APPROVED = "approved"

#: One dict per gate so a new gate cannot be half-added. Mirrors
#: ``fee_runs.APPROVAL_GATES`` field for field.
APPROVAL_GATES: Mapping[str, dict[str, str]] = {
    "ADVISOR": {
        "action_key": "spv_carry_run.advisor_approve",
        "advances_to": "ADVISOR_APPROVED",
        "from_status": "PREVIEW",
        "by_column": "advisor_approved_by",
        "at_column": "advisor_approved_at",
        "title": "Advisor approval of SPV carry run",
    },
    "COMPLIANCE": {
        "action_key": "spv_carry_run.compliance_approve",
        "advances_to": "COMPLIANCE_APPROVED",
        "from_status": "ADVISOR_APPROVED",
        "by_column": "compliance_approved_by",
        "at_column": "compliance_approved_at",
        "title": "Compliance approval of SPV carry run",
    },
}

#: The registry key a BPMN Service Task names to subscribe this module to the
#: realization event.
ACTION_KEY = "spv_carry.propose_from_realization"

#: Reused rather than invented. ``public.permissions`` is a CLOSED vocabulary
#: of 28 names (measured) and contains no "manage carry" key; this sprint ships
#: no Part-1 SQL to add one, and referencing a permission that does not exist
#: would make the gate inert. ``manage_billing`` is the key fee33/fee34 already
#: settled on as the fee module's write authority
#: (``fee_schedules.WRITE_PERMISSION``), and deciding what a GP is owed is the
#: same authority as deciding what a client is charged.
ACTION_PERMISSION = "manage_billing"

#: ``transaction_types.category`` values that move capital IN and OUT. Read as
#: flags, never as codes — the same house rule ``services.spv_events`` states.
CATEGORY_CALL = "call"
CATEGORY_DISTRIBUTION = "distribution"

POSTED_STATUS = "posted"
ACTIVE_ALLOCATION_STATUS = "active"

ZERO = Decimal(0)


# ═══════════════════════════════════════════════════════════════════════════
# Errors
# ═══════════════════════════════════════════════════════════════════════════


class CarryRunError(SpvCarryError):
    code = "carry_run_error"


class CarryRunNotFoundError(CarryRunError):
    code = "carry_run_not_found"


class CarryRunStateError(CarryRunError):
    """The run is not in a status from which this move is legal."""

    code = "carry_run_state_error"


class MakerCheckerError(CarryRunError):
    """The approver is the proposer. Refused here AND by
    ``assistant_activities_maker_checker_chk`` in the database."""

    code = "carry_run_maker_checker"


class WholeFundScopeError(CarryRunError):
    """WHOLE_FUND asked for on a vehicle whose whole fund is bigger than it."""

    code = "carry_whole_fund_scope"


class EventNotUsableError(CarryRunError):
    """The domain event does not describe a realization this module can price."""

    code = "carry_event_not_usable"


# ═══════════════════════════════════════════════════════════════════════════
# Cumulative capital — the honest source, plus a live probe of the stated one
# ═══════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class CapitalAccountProbe:
    """What ``v_capital_accounts`` actually returned, measured at run time."""

    rows_for_org: int
    rows_for_vehicle: int
    vehicle_entity_id: str | None
    usable: bool
    reason: str

    def as_detail(self) -> dict[str, Any]:
        return {
            "view": "public.v_capital_accounts",
            "rows_for_org": self.rows_for_org,
            "rows_for_vehicle": self.rows_for_vehicle,
            "vehicle_entity_id": self.vehicle_entity_id,
            "usable": self.usable,
            "reason": self.reason,
        }


async def capital_account_probe(conn, org_id, spv_id) -> CapitalAccountProbe:
    """Ask ``v_capital_accounts`` for this SPV's capital accounts, for real.

    Re-measured on every proposal rather than asserted from a comment. The day
    a GL posting path starts stamping ``dim_member_series_id``, this probe
    starts reporting rows and the finding in the module docstring becomes
    visibly stale instead of silently wrong.
    """
    vehicle_entity_id = await conn.fetchval(
        "SELECT vehicle_entity_id FROM spvs WHERE id = $1::uuid AND org_id = $2::uuid",
        str(spv_id), str(org_id),
    )
    rows_for_org = await conn.fetchval(
        "SELECT count(*) FROM v_capital_accounts WHERE org_id = $1::uuid",
        str(org_id),
    )
    rows_for_vehicle = 0
    if vehicle_entity_id is not None:
        rows_for_vehicle = await conn.fetchval(
            "SELECT count(*) FROM v_capital_accounts "
            "WHERE org_id = $1::uuid AND vehicle_id = $2::uuid",
            str(org_id), str(vehicle_entity_id),
        )

    if rows_for_vehicle:
        reason = (
            "the view returned rows for this vehicle, but its grain is "
            "journal_lines.dim_member_series_id, for which no dim_member_series "
            "table and no foreign key exist — the rows cannot be attributed to "
            "an SPV investor entity"
        )
    elif vehicle_entity_id is None:
        reason = (
            "spvs.vehicle_entity_id is NULL, so this SPV has no vehicle_id to "
            "look up in the view at all"
        )
    else:
        reason = (
            "the view returned no rows for this vehicle: its WHERE requires "
            "journal_lines.dim_member_series_id IS NOT NULL and nothing "
            "populates that dimension (GL posting is fee43's open question #3)"
        )
    # Never usable today under ANY of the three branches — the join path to an
    # investor entity does not exist even when rows do.
    return CapitalAccountProbe(
        rows_for_org=rows_for_org,
        rows_for_vehicle=rows_for_vehicle,
        vehicle_entity_id=None if vehicle_entity_id is None else str(vehicle_entity_id),
        usable=False,
        reason=reason,
    )


async def assert_scope_supported(conn, org_id, spv_id, carry_basis: str) -> dict:
    """Refuse a WHOLE_FUND run whose whole fund is bigger than this vehicle.

    See "WHOLE_FUND vs DEAL_BY_DEAL" in the module docstring.
    """
    if carry_basis not in CARRY_BASES:
        raise CarryRunError(
            f"carry_basis={carry_basis!r} is not one of {list(CARRY_BASES)}",
            carry_basis=carry_basis,
        )
    row = await conn.fetchrow(
        "SELECT vehicle_type, master_entity_id, deal_id FROM spvs "
        "WHERE id = $1::uuid AND org_id = $2::uuid",
        str(spv_id), str(org_id),
    )
    if row is None:
        raise CarryRunError(f"spv {spv_id} not found in org {org_id}", spv_id=str(spv_id))

    standalone = (
        row["vehicle_type"] == "standalone_spv" and row["master_entity_id"] is None
    )
    if carry_basis == CARRY_WHOLE_FUND and not standalone:
        raise WholeFundScopeError(
            f"carry_basis=WHOLE_FUND on spv {spv_id}, which is "
            f"vehicle_type={row['vehicle_type']!r} with "
            f"master_entity_id={row['master_entity_id']}. A whole-fund "
            f"calculation must net this realization against every sibling "
            f"series in the master structure, and no master-level rollup is "
            f"deployed. Computing it per-vehicle and calling it whole-fund "
            f"would understate the hurdle for every investor in the fund",
            spv_id=str(spv_id), vehicle_type=row["vehicle_type"],
            master_entity_id=str(row["master_entity_id"]),
        )
    return {
        "carry_basis": carry_basis,
        "vehicle_type": row["vehicle_type"],
        "master_entity_id": (
            None if row["master_entity_id"] is None else str(row["master_entity_id"])
        ),
        "deal_id": str(row["deal_id"]),
        "scope": "spv",
        "note": (
            "spv_transactions carries no investment/position reference, so an "
            "SPV has no grain below itself and DEAL_BY_DEAL and WHOLE_FUND are "
            "the same rows on a standalone vehicle. Recorded rather than "
            "assumed: a non-standalone vehicle is refused, not approximated."
        ),
    }


async def load_investor_state(
    conn, org_id, spv_id, entity_id, *, exclude_transaction_id=None,
) -> InvestorState:
    """This investor's cumulative paid-in and distributed, from POSTED rows.

    ``exclude_transaction_id`` is the realizing transaction itself. It must be
    excluded: the waterfall's ``cumulative_distributed`` means "before this
    realization", and including the distribution being priced would return its
    own capital to itself and clear its own hurdle.
    """
    row = await conn.fetchrow(
        """
        SELECT
          COALESCE(SUM(a.allocated_amount)
                   FILTER (WHERE tt.category = $4), 0)  AS paid_in,
          COALESCE(SUM(a.allocated_amount)
                   FILTER (WHERE tt.category = $5), 0)  AS distributed,
          count(*)                                      AS rows_read
        FROM spv_transaction_allocations a
        JOIN spv_transactions t   ON t.id = a.transaction_id
        JOIN public.transaction_types tt ON tt.id = t.transaction_type_id
        WHERE a.org_id = $1::uuid
          AND a.spv_id = $2::uuid
          AND a.entity_id = $3::uuid
          AND a.status = $6
          AND t.status = $7
          AND ($8::uuid IS NULL OR t.id <> $8::uuid)
        """,
        str(org_id), str(spv_id), str(entity_id),
        CATEGORY_CALL, CATEGORY_DISTRIBUTION,
        ACTIVE_ALLOCATION_STATUS, POSTED_STATUS,
        None if exclude_transaction_id is None else str(exclude_transaction_id),
    )
    return InvestorState(
        cumulative_paid_in=Decimal(str(row["paid_in"])),
        cumulative_distributed=Decimal(str(row["distributed"])),
        source=(
            "spv_transaction_allocations JOIN transaction_types "
            f"(category IN ({CATEGORY_CALL!r},{CATEGORY_DISTRIBUTION!r})), "
            f"spv_transactions.status={POSTED_STATUS!r}, "
            f"allocation.status={ACTIVE_ALLOCATION_STATUS!r}, "
            f"excluding transaction {exclude_transaction_id}; "
            f"{row['rows_read']} rows read. "
            "v_capital_accounts is NOT the source — see capital_account_probe"
        ),
    )


# ═══════════════════════════════════════════════════════════════════════════
# Reads
# ═══════════════════════════════════════════════════════════════════════════


async def get_run(conn, org_id, run_id) -> dict[str, Any]:
    row = await conn.fetchrow(
        f"""SELECT id::text AS id, org_id::text AS org_id, spv_id::text AS spv_id,
                   domain_event_id::text AS domain_event_id,
                   triggering_transaction_id::text AS triggering_transaction_id,
                   status, carry_basis, calculation_snapshot_hash, engine_version,
                   created_by::text AS created_by,
                   advisor_approved_by::text AS advisor_approved_by,
                   advisor_approved_at,
                   compliance_approved_by::text AS compliance_approved_by,
                   compliance_approved_at, posted_at,
                   reverses_run_id::text AS reverses_run_id, created_at
            FROM {T_RUNS} WHERE id = $1::uuid AND org_id = $2::uuid""",
        str(run_id), str(org_id),
    )
    if row is None:
        raise CarryRunNotFoundError(
            f"spv_carry_run {run_id} not found in org {org_id}", run_id=str(run_id)
        )
    return dict(row)


async def list_lines(conn, org_id, run_id) -> list[dict[str, Any]]:
    rows = await conn.fetch(
        f"""SELECT id::text AS id, entity_id::text AS entity_id,
                   spv_subscription_id::text AS spv_subscription_id,
                   gross_gain_allocated, return_of_capital, preferred_return,
                   gp_catchup, carry_to_gp, net_to_lp, calc_detail, created_at
            FROM {T_LINES}
            WHERE spv_carry_run_id = $1::uuid AND org_id = $2::uuid
            ORDER BY entity_id, id""",
        str(run_id), str(org_id),
    )
    out = []
    for r in rows:
        d = dict(r)
        d["calc_detail"] = decode_payload(d["calc_detail"])
        out.append(d)
    return out


# ═══════════════════════════════════════════════════════════════════════════
# The proposal — DRAFT, and only DRAFT
# ═══════════════════════════════════════════════════════════════════════════


def _snapshot_hash(payload: Mapping[str, Any]) -> str:
    """A stable fingerprint of everything that produced these numbers.

    Same purpose as ``fee_runs.calculation_snapshot_hash``: a POSTED run whose
    inputs nobody can re-derive is a frozen number with no provenance. Sorted
    keys and exact decimal strings, so the same inputs hash the same way on any
    machine and in any Python version.
    """
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


async def _live_run_for_event(conn, org_id, domain_event_id):
    """An existing, not-reversed run for this event — the idempotency guard.

    Application-level; there is no unique index on ``domain_event_id``. See
    "IDEMPOTENCY IS APPLICATION-LEVEL" in the module docstring.
    """
    return await conn.fetchval(
        f"""SELECT id::text FROM {T_RUNS}
            WHERE org_id = $1::uuid AND domain_event_id = $2::uuid
              AND status <> 'REVERSED'
            ORDER BY created_at LIMIT 1""",
        str(org_id), str(domain_event_id),
    )


async def load_event(conn, org_id, domain_event_id) -> dict[str, Any]:
    row = await conn.fetchrow(
        """SELECT id::text AS id, org_id::text AS org_id, event_type,
                  source_type, source_id::text AS source_id, payload, occurred_at
           FROM domain_events WHERE id = $1::uuid AND org_id = $2::uuid""",
        str(domain_event_id), str(org_id),
    )
    if row is None:
        raise EventNotUsableError(
            f"domain_event {domain_event_id} not found in org {org_id}",
            domain_event_id=str(domain_event_id),
        )
    d = dict(row)
    if d["event_type"] != EVENT_TYPE:
        raise EventNotUsableError(
            f"domain_event {domain_event_id} is event_type "
            f"{d['event_type']!r}, not {EVENT_TYPE!r}. This consumer prices "
            f"realizations and nothing else",
            domain_event_id=str(domain_event_id), event_type=d["event_type"],
        )
    d["payload"] = decode_payload(d["payload"])
    return d


async def propose_carry_run(
    conn, org_id, *, domain_event_id, created_by=None,
) -> dict[str, Any]:
    """Price a realization for every allocated investor and write a DRAFT.

    Returns ``{run_id, status, lines, deduped, ...}``. ``status`` is always
    ``'DRAFT'`` — see "A TRIGGERED RUN PROPOSES" in the module docstring. This
    function contains no path that advances a run, by design.

    The per-investor amounts come from the EVENT's own payload
    (``allocations[].allocated_amount``), which ``services.spv_events`` built
    from the posted ``spv_transaction_allocations`` rows. They are not
    re-derived from ownership percentages here: any drift between a
    re-derivation and the posted allocation is a real mispayment, and the
    posted rows are the ones that actually happened.
    """
    event = await load_event(conn, org_id, domain_event_id)
    payload = event["payload"]

    existing = await _live_run_for_event(conn, org_id, domain_event_id)
    if existing is not None:
        logger.info(
            "propose_carry_run: domain_event %s already has live run %s; "
            "adopting it rather than proposing the same money twice",
            domain_event_id, existing,
        )
        run = await get_run(conn, org_id, existing)
        return {
            "run_id": existing, "status": run["status"], "deduped": True,
            "lines": await list_lines(conn, org_id, existing),
        }

    spv_id = payload.get("spv_id")
    txn_id = payload.get("spv_transaction_id") or event["source_id"]
    allocations = payload.get("allocations") or []
    if not spv_id:
        raise EventNotUsableError(
            f"domain_event {domain_event_id} payload carries no spv_id",
            domain_event_id=str(domain_event_id),
        )
    if not allocations:
        raise EventNotUsableError(
            f"domain_event {domain_event_id} carries no per-investor "
            f"allocations. Carry is owed per investor; a realization with no "
            f"allocated split has nobody to owe it to and must be investigated "
            f"rather than posted as a zero run",
            domain_event_id=str(domain_event_id), spv_id=str(spv_id),
        )

    class_label = payload.get("class_label")
    txn_date = payload.get("txn_date")
    as_of = None
    if txn_date:
        from datetime import date as _date
        as_of = _date.fromisoformat(txn_date)

    probe = await capital_account_probe(conn, org_id, spv_id)

    # Terms are resolved per investor — a side letter can move any economic
    # field, so two investors in the same SPV can be on different carry.
    computed: list[tuple[dict, CarryResult, dict]] = []
    basis_seen: set[str] = set()
    for alloc in allocations:
        entity_id = alloc.get("entity_id")
        resolved = await SFT.resolve_terms_for_entity(
            conn, str(org_id), spv_id, entity_id,
            class_label=class_label, as_of=as_of,
        )
        terms = terms_from_resolved(resolved)
        basis_seen.add(terms.carry_basis)
        scope = await assert_scope_supported(conn, org_id, spv_id, terms.carry_basis)
        state = await load_investor_state(
            conn, org_id, spv_id, entity_id, exclude_transaction_id=txn_id,
        )
        result = compute_carry(
            gross_gain_allocated=alloc.get("allocated_amount"),
            state=state,
            terms=terms,
            entity_id=entity_id,
        )
        result.calc_detail["scope"] = scope
        result.calc_detail["capital_account_probe"] = probe.as_detail()
        result.calc_detail["event"] = {
            "domain_event_id": str(event["id"]),
            "spv_transaction_id": str(txn_id),
            "occurred_at": event["occurred_at"].isoformat(),
            "allocation_id": alloc.get("allocation_id"),
            "ownership_pct": alloc.get("ownership_pct"),
        }
        computed.append((alloc, result, scope))

    # One run carries one carry_basis. Two investors whose side letters put
    # them on different bases is a real, reportable condition, not something to
    # resolve by picking the first one.
    if len(basis_seen) > 1:
        raise CarryRunError(
            f"the investors in spv {spv_id} resolve to more than one "
            f"carry_basis ({sorted(basis_seen)}). spv_carry_runs.carry_basis is "
            f"one column on the run; splitting a single realization across two "
            f"bases needs a decision nobody has made",
            spv_id=str(spv_id), bases=sorted(basis_seen),
        )
    carry_basis = basis_seen.pop() if basis_seen else CARRY_DEAL_BY_DEAL

    snapshot = _snapshot_hash({
        "engine_version": ENGINE_VERSION,
        "domain_event_id": str(event["id"]),
        "spv_id": str(spv_id),
        "spv_transaction_id": str(txn_id),
        "carry_basis": carry_basis,
        "lines": sorted(
            (
                {
                    "entity_id": str(a.get("entity_id")),
                    "gross_gain_allocated": str(r.gross_gain_allocated),
                    "terms": r.calc_detail["terms"],
                    "inputs": r.calc_detail["inputs"],
                    "carry_to_gp": str(r.carry_to_gp),
                    "net_to_lp": str(r.net_to_lp),
                }
                for a, r, _ in computed
            ),
            key=lambda d: d["entity_id"],
        ),
    })

    run_id = str(await conn.fetchval(
        f"""INSERT INTO {T_RUNS}
              (org_id, spv_id, domain_event_id, triggering_transaction_id,
               status, carry_basis, calculation_snapshot_hash, engine_version,
               created_by)
            VALUES ($1::uuid, $2::uuid, $3::uuid, $4::uuid, 'DRAFT', $5, $6, $7,
                    $8::uuid)
            RETURNING id""",
        str(org_id), str(spv_id), str(event["id"]), str(txn_id),
        carry_basis, snapshot, ENGINE_VERSION,
        None if created_by is None else str(created_by),
    ))

    for alloc, result, _scope in computed:
        await _insert_line(
            conn, org_id, run_id,
            entity_id=alloc.get("entity_id"),
            subscription_id=alloc.get("subscription_id"),
            result=result,
        )

    logger.info(
        "propose_carry_run: DRAFT %s for spv %s from event %s — %d lines, "
        "carry_to_gp=%s",
        run_id, spv_id, event["id"], len(computed),
        sum((r.carry_to_gp for _, r, _ in computed), ZERO),
    )
    return {
        "run_id": run_id,
        "status": "DRAFT",
        "deduped": False,
        "spv_id": str(spv_id),
        "carry_basis": carry_basis,
        "calculation_snapshot_hash": snapshot,
        "engine_version": ENGINE_VERSION,
        "capital_account_probe": probe.as_detail(),
        "lines": await list_lines(conn, org_id, run_id),
    }


async def _insert_line(conn, org_id, run_id, *, entity_id, subscription_id, result):
    """Write one line. The DB balance CHECK is the backstop, not the plan.

    ``CarryResult.reconciles()`` has already held inside the engine; this
    INSERT would be refused by ``spv_carry_run_lines_balance_check`` if it had
    not. Both layers are real and neither substitutes for the other.
    """
    return await conn.fetchval(
        f"""INSERT INTO {T_LINES}
              (org_id, spv_carry_run_id, entity_id, spv_subscription_id,
               gross_gain_allocated, return_of_capital, preferred_return,
               gp_catchup, carry_to_gp, net_to_lp, calc_detail)
            VALUES ($1::uuid, $2::uuid, $3::uuid, $4::uuid, $5, $6, $7, $8, $9,
                    $10, $11::jsonb)
            RETURNING id""",
        str(org_id), str(run_id), str(entity_id),
        None if subscription_id is None else str(subscription_id),
        result.gross_gain_allocated, result.return_of_capital,
        result.preferred_return, result.gp_catchup, result.carry_to_gp,
        result.net_to_lp, json.dumps(result.calc_detail, default=str),
    )


# ═══════════════════════════════════════════════════════════════════════════
# Lifecycle
# ═══════════════════════════════════════════════════════════════════════════


def _assert_transition(current: str, target: str) -> None:
    allowed = ALLOWED_TRANSITIONS.get(current, ())
    if target not in allowed:
        raise CarryRunStateError(
            f"spv_carry_run cannot move {current} -> {target}; from {current} "
            f"the legal moves are {list(allowed)}",
            current=current, target=target,
        )


async def preview_run(conn, org_id, run_id) -> dict[str, Any]:
    """DRAFT -> PREVIEW (or PREVIEW -> PREVIEW). A human step, never automatic.

    Nothing is recalculated: the lines were computed from the posted
    allocations at proposal time and the snapshot hash was taken then. Preview
    is the moment a person says "these are the numbers I am putting in front of
    an approver", and that is a decision, not arithmetic.
    """
    run = await get_run(conn, org_id, run_id)
    _assert_transition(run["status"], "PREVIEW")
    if not run["calculation_snapshot_hash"]:
        raise CarryRunStateError(
            f"spv_carry_run {run_id} has no calculation_snapshot_hash",
            run_id=str(run_id),
        )
    lines = await list_lines(conn, org_id, run_id)
    if not lines:
        raise CarryRunStateError(
            f"spv_carry_run {run_id} has no lines; there is nothing to preview",
            run_id=str(run_id),
        )
    await conn.execute(
        f"UPDATE {T_RUNS} SET status = 'PREVIEW' "
        f"WHERE id = $1::uuid AND org_id = $2::uuid",
        str(run_id), str(org_id),
    )
    return {
        "run_id": str(run_id),
        "status": "PREVIEW",
        "line_count": len(lines),
        "total_carry_to_gp": sum(
            (Decimal(str(x["carry_to_gp"])) for x in lines), ZERO
        ),
        "total_net_to_lp": sum(
            (Decimal(str(x["net_to_lp"])) for x in lines), ZERO
        ),
    }


def _gate(gate: str) -> dict[str, str]:
    try:
        return dict(APPROVAL_GATES[gate])
    except KeyError:
        raise CarryRunError(
            f"gate={gate!r} is not one of {tuple(APPROVAL_GATES)}", gate=gate
        ) from None


async def _open_proposal(conn, org_id, run_id, action_key):
    row = await conn.fetchrow(
        f"""SELECT id::text AS id, proposed_by::text AS proposed_by, status
            FROM {T_ACTIVITIES}
            WHERE org_id = $1::uuid AND related_type = $2 AND related_id = $3::uuid
              AND action_key = $4 AND status = $5
            ORDER BY created_at DESC LIMIT 1""",
        str(org_id), RELATED_TYPE, str(run_id), action_key, ACTIVITY_PROPOSED,
    )
    return dict(row) if row else None


async def propose_approval(
    conn, org_id, run_id, *, gate: str, proposed_by, rationale: str | None = None,
) -> str:
    """Open an approval gate. Writes a ``'proposed'`` activity; run unchanged.

    The run does NOT advance here — a proposal is a request for a second
    person, and a lifecycle that moved on the proposal would make the checker
    decorative. Identical to ``fee_runs.propose_approval``.
    """
    spec = _gate(gate)
    run = await get_run(conn, org_id, run_id)
    if run["status"] != spec["from_status"]:
        raise CarryRunStateError(
            f"spv_carry_run {run_id} is {run['status']}; the {gate} gate opens "
            f"from {spec['from_status']}",
            run_id=str(run_id), status=run["status"], gate=gate,
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
        str(org_id), str(proposed_by), spec["action_key"], spec["title"],
        ACTIVITY_PROPOSED, rationale, RELATED_TYPE, str(run_id),
        json.dumps({
            "gate": gate,
            "spv_carry_run_id": str(run_id),
            "advances_to": spec["advances_to"],
            "spv_id": run["spv_id"],
            "carry_basis": run["carry_basis"],
            "calculation_snapshot_hash": run["calculation_snapshot_hash"],
        }),
    ))


async def approve(conn, org_id, run_id, *, gate: str, approved_by) -> dict[str, Any]:
    """Close an approval gate and advance the run, in one transaction.

    Refuses when the approver is the proposer — and
    ``assistant_activities_maker_checker_chk`` refuses it too, so bypassing
    this function does not bypass the rule. The activity moves to
    ``'approved'`` FIRST and the run advances second: a run that had advanced
    with no approved activity behind it would be the bespoke flag this design
    exists to avoid.
    """
    spec = _gate(gate)
    run = await get_run(conn, org_id, run_id)
    if run["status"] != spec["from_status"]:
        raise CarryRunStateError(
            f"spv_carry_run {run_id} is {run['status']}; the {gate} gate closes "
            f"from {spec['from_status']}",
            run_id=str(run_id), status=run["status"], gate=gate,
        )
    _assert_transition(run["status"], spec["advances_to"])

    proposal = await _open_proposal(conn, org_id, run_id, spec["action_key"])
    if proposal is None:
        raise CarryRunStateError(
            f"no open {ACTIVITY_PROPOSED!r} assistant_activities row for the "
            f"{gate} gate on spv_carry_run {run_id}. The gate has to be "
            f"PROPOSED by one person before it can be APPROVED by another — "
            f"approving without a proposal is a single-party approval wearing "
            f"two hats",
            run_id=str(run_id), gate=gate,
        )
    if str(proposal["proposed_by"]) == str(approved_by):
        raise MakerCheckerError(
            f"user {approved_by} proposed the {gate} approval of "
            f"spv_carry_run {run_id} and cannot also approve it",
            run_id=str(run_id), gate=gate, user_id=str(approved_by),
        )

    activity = await conn.fetchrow(
        f"""UPDATE {T_ACTIVITIES}
            SET status = $3, approved_by = $4::uuid, updated_at = now()
            WHERE id = $1::uuid AND org_id = $2::uuid
            RETURNING id::text AS id, status, approved_by::text AS approved_by,
                      proposed_by::text AS proposed_by""",
        proposal["id"], str(org_id), ACTIVITY_APPROVED, str(approved_by),
    )
    await conn.execute(
        f"""UPDATE {T_RUNS}
            SET status = $3, {spec['by_column']} = $4::uuid,
                {spec['at_column']} = now()
            WHERE id = $1::uuid AND org_id = $2::uuid""",
        str(run_id), str(org_id), spec["advances_to"], str(approved_by),
    )
    return {
        "run_id": str(run_id),
        "status": spec["advances_to"],
        "activity_id": activity["id"],
        "activity_status": activity["status"],
        "proposed_by": activity["proposed_by"],
        "approved_by": activity["approved_by"],
        "authority": (
            "the assistant_activities row above. spv_carry_runs.%s is a "
            "denormalised mirror and is not read to decide anything"
            % spec["by_column"]
        ),
    }


async def approval_activities(conn, org_id, run_id) -> list[dict[str, Any]]:
    rows = await conn.fetch(
        f"""SELECT id::text AS id, action_key, status, title,
                   proposed_by::text AS proposed_by,
                   approved_by::text AS approved_by,
                   related_type, related_id::text AS related_id, payload,
                   created_at, updated_at
            FROM {T_ACTIVITIES}
            WHERE org_id = $1::uuid AND related_type = $2 AND related_id = $3::uuid
            ORDER BY created_at""",
        str(org_id), RELATED_TYPE, str(run_id),
    )
    return [dict(r) for r in rows]


async def post_run(conn, org_id, run_id) -> dict[str, Any]:
    """COMPLIANCE_APPROVED -> POSTED. After this the DB refuses every change.

    Both gates are re-checked against ``assistant_activities`` rather than
    against ``spv_carry_runs.status`` alone. The status could only have got
    here through :func:`approve`, but "could only have" is an argument about
    the code, and the point of the ledger is that the posting decision rests on
    the ledger.

    Deliberately does NOT post to the general ledger. GL posting is open
    question #3 (see ``fee_runs.GL_POSTING_DECISION_REQUIRED``) and is fee43's
    territory; a carry run reaching POSTED here records that the numbers are
    approved, not that they have been booked.
    """
    run = await get_run(conn, org_id, run_id)
    _assert_transition(run["status"], "POSTED")

    activities = await approval_activities(conn, org_id, run_id)
    approved = {a["action_key"] for a in activities if a["status"] == ACTIVITY_APPROVED}
    missing = [
        g for g, spec in APPROVAL_GATES.items() if spec["action_key"] not in approved
    ]
    if missing:
        raise CarryRunStateError(
            f"spv_carry_run {run_id} is {run['status']} but has no approved "
            f"assistant_activities row for: {missing}. The run status is a "
            f"mirror; the ledger is the authority",
            run_id=str(run_id), missing_gates=missing,
        )
    if not run["calculation_snapshot_hash"]:
        raise CarryRunStateError(
            f"spv_carry_run {run_id} has no calculation_snapshot_hash — "
            f"posting it would freeze numbers whose inputs nobody can check",
            run_id=str(run_id),
        )

    await conn.execute(
        f"""UPDATE {T_RUNS} SET status = 'POSTED', posted_at = now()
            WHERE id = $1::uuid AND org_id = $2::uuid""",
        str(run_id), str(org_id),
    )
    lines = await list_lines(conn, org_id, run_id)
    return {
        "run_id": str(run_id),
        "status": "POSTED",
        "line_count": len(lines),
        "total_carry_to_gp": sum(
            (Decimal(str(x["carry_to_gp"])) for x in lines), ZERO
        ),
        "total_net_to_lp": sum(
            (Decimal(str(x["net_to_lp"])) for x in lines), ZERO
        ),
        "general_ledger": (
            "NOT POSTED. GL posting for carry is fee43's open question #3; "
            "this run records approved numbers, not booked entries."
        ),
    }


# ═══════════════════════════════════════════════════════════════════════════
# The subscription — a BPMN Service Task, through the real registry
# ═══════════════════════════════════════════════════════════════════════════


async def _propose_handler(
    pool=None, user_id=None, org_id=None, run_context=None, workflow_run_id=None, **_
):
    """Registry handler. Invoked by ``workflow_engine._execute_service_task``.

    ``run_context`` is the context ``domain_events.publish_event`` wrote onto
    the ``workflow_runs`` row: it carries ``domain_event_id``, so this handler
    prices the event that actually fired rather than "the most recent one",
    which would be a race the moment two distributions post together.

    Raises on every failure rather than returning a failure dict, so the run
    HOLDs loudly. A carry proposal that silently did not happen is a
    distribution nobody ever computed carry on.
    """
    context = run_context or {}
    if context.get("event_type") not in (None, EVENT_TYPE):
        raise EventNotUsableError(
            f"{ACTION_KEY} was invoked on a run started by event_type "
            f"{context.get('event_type')!r}, not {EVENT_TYPE!r}",
            event_type=context.get("event_type"),
        )
    domain_event_id = context.get("domain_event_id")
    if not domain_event_id:
        raise EventNotUsableError(
            f"{ACTION_KEY} was invoked with no domain_event_id in the workflow "
            f"run context. This action prices ONE named realization; it must "
            f"not fall back to scanning for recent ones",
            workflow_run_id=str(workflow_run_id),
        )
    if pool is None:
        raise CarryRunError(f"{ACTION_KEY} was invoked without a database pool")

    async with pool.acquire() as conn:
        outcome = await propose_carry_run(
            conn, org_id, domain_event_id=domain_event_id, created_by=user_id,
        )

    total_carry = sum(
        (Decimal(str(x["carry_to_gp"])) for x in outcome["lines"]), ZERO
    )
    return {
        "data": {
            "spv_carry_run_id": outcome["run_id"],
            "status": outcome["status"],
            "deduped": outcome["deduped"],
            "line_count": len(outcome["lines"]),
            "total_carry_to_gp": str(total_carry),
            "domain_event_id": str(domain_event_id),
            "workflow_run_id": None if workflow_run_id is None else str(workflow_run_id),
        },
        "render": None,
        "text": (
            f"Proposed SPV carry run {outcome['run_id']} in "
            f"{outcome['status']} — {len(outcome['lines'])} investor line(s), "
            f"{total_carry} to the GP. Awaiting preview and two approvals; "
            f"nothing is posted."
        ),
    }


def register_actions() -> None:
    REGISTRY.register(
        AssistantAction(
            key=ACTION_KEY,
            module="spv_carry",
            description=(
                "Price the carry waterfall for every investor allocated a share "
                "of a realized SPV distribution, and record it as a DRAFT "
                "spv_carry_run awaiting advisor and compliance approval. "
                "Proposes only — it never posts, and it moves no money."
            ),
            # WRITE, honestly: it inserts a run and its lines. Classifying it
            # read to make the tier default fall out more conveniently would be
            # a lie encoded in the catalogue.
            access_type="write",
            required_permission=ACTION_PERMISSION,
            default_autonomy="confirm",
            reversible=False,  # a DRAFT is discarded, not undone
            render_target="inline",
            handler=_propose_handler,
            params_schema={"type": "object", "properties": {}, "required": []},
            # Opt in to real invocation from a BPMN Service Task. The engine
            # re-checks required_permission against the member the run belongs
            # to, so this does not widen anyone's reach.
            workflow_invocable=True,
        )
    )
