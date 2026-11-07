"""Block trading, allocation and DVP settlement (rep9) — manual-first.

Approved account instructions (rep2) are aggregated into a BLOCK: one
security, one side, one executor, one execute-by date. The pre-trade
allocation is computed under the firm's APPROVED allocation policy and FIXED
when a second person approves the block. The block is executed by a broker /
desk / trader of the adviser's choice — always through the MANUAL route (print
the order execution sheet, give it to the broker, enter the fills by hand); a
FIX router interface exists (services.execution_routers) but none is connected.

LIFECYCLE (enforced here first, then again by public.trade_blocks_guard)
──────────────────────────────────────────────────────────────────────────────
  draft ─submit→ pending_approval ─approve (human task, maker ≠ checker)→ approved
        ←reject─┘                    (the allocation is FIXED here)
  approved ─mark sent (person, channel)→ sent ─fill→ partially_filled ─fill→ filled
  filled / partially_filled ─allocate (person)→ allocated ─every allocation
  settled or cancelled→ settled ─close (person)→ closed
  cancelled from draft / pending_approval / approved / sent (no fills), with a reason.

ALLOCATION (services.block_allocation): average price computed from the block
fills; a full fill gives every account its fixed quantity; a partial fill is
apportioned pro rata with deterministic rounding, the minimum allocation
respected (zero with the reason). Each account's share is booked on ITS OWN
instruction as a rep2 fill at the average price, with its own instructed lots
(sells) — then rep2's reconcile / close apply unchanged. The unfilled
remainder follows the block's if-not-filled rule: cancel → each instruction is
completed short; carry → each instruction keeps its remainder working (it may
join the next block); ask → a to-do for a person to carry or cancel it.

SETTLEMENT: an account at the integrated custodian receives versus payment
(the custodian desk is instructed, as today); an account at another custodian
gets rep7b's DVP settlement instruction, rewritten from the block's trade date
(T+1, weekends only — no holiday calendar). Status per allocation: pending →
affirmed → settled, or failed (with the reason; a to-do opens with the
allocation and the counterparty). The block settles when every allocation is
settled or cancelled, and only then may a person close it.

CORRECTIONS: once booked, an allocation changes ONLY through an
allocation_corrections row with a reason, approved by a second person (a
human task) — the database refuses anything else; the original state is kept
(system axis). rep2's fill rows are immutable, so a correction is carried on
the block allocation (the block's book of record and its settlement) and on
the instruction's timeline as an event; the instruction reconciles against the
corrected allocation.

LOGGING: every action writes a block_events row + an audit_log row in its own
transaction and, after commit, one domain_events row ``blocks.<action>``.
Every state change archives the row first (system axis; the id is stable).

THE CUSTODY CLIFF. Nothing here places a trade or moves money; nothing is sent
electronically. Every action is a person's (agents are refused).
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal

from services import authority as AU
from services import block_allocation as BA
from services import execution as X
from services import execution_routers as RT
from services import human_tasks as HT

log = logging.getLogger(__name__)

T_EA = "public.execution_accounts"
T_POL = "public.allocation_policies"
T_BLK = "public.trade_blocks"
T_ALLOC = "public.block_allocations"
T_FILL = "public.block_fills"
T_CORR = "public.allocation_corrections"
T_CHG = "public.block_charges"
T_EVT = "public.block_events"

VIEW = "view_blocks"
MANAGE = "manage_blocks"
APPROVE = "approve_blocks"
TASK_BLOCK = "block_approval"
TASK_POLICY = "allocation_policy_approval"
TASK_CORRECTION = "block_allocation_correction"
SUBJECT_BLOCK = "trade_block"
SUBJECT_POLICY = "allocation_policy"
SUBJECT_CORRECTION = "allocation_correction"
EVENT_SOURCE = "block_event"
TODO_FAIL = "block_settlement_failed"
TODO_REMAINDER = "block_remainder"
TODO_EXPIRY = "block_not_filled"

BLOCK_EXECUTORS = ("broker", "trader", "custodian_desk")
OPEN_STATUSES = ("draft", "pending_approval", "approved", "sent", "partially_filled")
LIVE_STATUSES = OPEN_STATUSES + ("filled", "allocated", "settled")
STATUS_LABELS = {
    "draft": "Draft", "pending_approval": "Awaiting approval", "approved": "Approved — allocation fixed",
    "sent": "Sent", "partially_filled": "Partially filled", "filled": "Filled", "allocated": "Allocated",
    "settled": "Settled", "closed": "Closed", "cancelled": "Cancelled",
}
ALLOCATION_STATUS_LABELS = {"planned": "Planned (preview)", "fixed": "Fixed at approval", "allocated": "Allocated",
                            "cancelled": "Cancelled"}
SETTLEMENT_STATUS_LABELS = {"pending": "Pending", "affirmed": "Affirmed", "settled": "Settled",
                            "failed": "Failed", "cancelled": "Cancelled (nothing to settle)"}
SETTLEMENT_EDGES = {"pending": ("affirmed", "settled", "failed"), "affirmed": ("settled", "failed"),
                    "failed": ("pending", "affirmed", "settled")}
SETTLEMENT_KIND_LABELS = {"integrated": "Integrated custodian — receive versus payment",
                          "dvp": "Another custodian — delivery versus payment (DVP)"}
SETTLEMENT_METHOD_LABELS = {"manual": "Confirmed manually", "custodian_data": "Matched to custodian / held-away data"}
METHOD_LABELS = {"pro_rata_requested": "Pro rata on the quantity each account requested"}
ROUNDING_LABELS = {"largest_remainder_then_account": "Round down to each account's unit; residual by the largest "
                                                     "remainder, ties by account id"}
PARTIAL_LABELS = {"pro_rata_allocated": "A partial fill is shared pro rata on the fixed allocations"}
POLICY_STATUS_LABELS = {"draft": "Awaiting a second person's approval", "approved": "Approved", "rejected": "Rejected"}
CORRECTION_STATUS_LABELS = POLICY_STATUS_LABELS
CHARGE_KIND_LABELS = {"commission": "Commission", "fee": "Fee", "other": "Other charge"}
REFERENCE_SOURCE_LABELS = {"security_data": "Security-data layer", "adviser": "Typed by the adviser"}
EXECUTION_ACCOUNT_STATUS_LABELS = {"active": "Active", "retired": "Retired"}
ACTION_LABELS = {
    "create": "Built", "submit": "Submitted for approval", "approve": "Approved — allocation fixed",
    "reject": "Rejected", "mark_sent": "Marked sent", "fill": "Fill entered", "allocate": "Allocated",
    "settlement": "Settlement status", "settlement_fail": "Settlement failed", "correction_request":
    "Correction requested", "correction_approve": "Correction approved", "correction_reject": "Correction rejected",
    "charge": "Charge entered", "charge_close": "Charge closed", "close": "Closed", "cancel": "Cancelled",
    "sheet": "Order execution sheet generated", "expire": "Expired", "carry": "Carried to the next day",
    "ask": "Not filled — decision requested",
}
REMAINDER_LABELS = {"cancel": "Remainder cancelled — each instruction completed short",
                    "carry": "Remainder carried — each instruction keeps it working",
                    "ask": "Remainder awaiting a decision (to-do opened)"}
# Open for the firm and its counsel — shown as notes, never decided by the code.
COMPLIANCE_NOTES = (
    "The written aggregation and allocation policy, and its disclosure in Form ADV, are the firm's (with counsel). "
    "The policy text here is the firm's own words; the platform enforces only its mechanics.",
    "Best-execution review of each executing broker and of aggregating orders is the firm's.",
    "Trade-away commissions (charged by the executing broker on top of the custodian's) are recorded per block; "
    "whether and how they are disclosed or allocated is the firm's decision.",
    "Each receiving custodian must accept DVP settlement from the executing broker (a DVP agreement / standing "
    "instruction on file); recording the receiving details does not establish it.",
    "Whether Altruist accepts DVP deliveries in is not established on record.",
)


class BlockError(Exception):
    """A request the rules refuse (422)."""


class BlockNotFound(BlockError, LookupError):
    pass


class BlockConflict(BlockError):
    """The state does not allow it (409)."""


class MakerCheckerViolation(PermissionError):
    pass


@dataclass
class EventRef:
    id: str
    org_id: str
    action: str
    block_id: str
    payload: dict = field(default_factory=dict)
    actor_id: str | None = None


def _dumps(v) -> str:
    return json.dumps(v, default=lambda o: str(o) if isinstance(o, (Decimal, uuid.UUID)) else o.isoformat()
                      if isinstance(o, (date, datetime)) else str(o))


def _loads(v):
    if v is None or isinstance(v, (dict, list)):
        return v
    try:
        return json.loads(v)
    except (TypeError, ValueError):
        return None


def _uuid(v, what="block") -> str:
    try:
        return str(uuid.UUID(str(v)))
    except (TypeError, ValueError):
        raise BlockNotFound(f"{what} not found") from None


def _dec(v) -> Decimal | None:
    if v is None:
        return None
    return v if isinstance(v, Decimal) else Decimal(str(v))


def _num(v):
    return None if v is None else str(v)


def _iso(v):
    return v.isoformat() if v is not None else None


async def _human(conn, actor_id, org_id) -> dict:
    try:
        return await HT.assert_human(conn, actor_id, org_id)
    except HT.AgentActorRefused as exc:
        raise BlockError(str(exc)) from exc


def _need(row: dict, statuses: tuple[str, ...], what: str) -> None:
    if row["status"] not in statuses:
        raise BlockConflict(f"cannot {what} a block that is {STATUS_LABELS.get(row['status'], row['status']).lower()} "
                            f"(allowed from: {', '.join(STATUS_LABELS[s].lower() for s in statuses)})")


# ── Writing primitives ──────────────────────────────────────────────────────
async def _archive(conn, table: str, row_id) -> None:
    got = await conn.fetchval(
        f"""INSERT INTO {table}
            SELECT (jsonb_populate_record(NULL::{table}, to_jsonb(t) || jsonb_build_object(
                'id', gen_random_uuid(), 'archived_from_id', t.id, 'system_to', now()))).*
            FROM {table} t WHERE t.id = $1::uuid AND t.system_to IS NULL RETURNING id""", str(row_id))
    if got is None:
        raise BlockConflict("the row is not live — refusing to change it")


async def _update(conn, table: str, row_id, fields: dict) -> dict:
    """Archive the live state (system axis), then change the live row in place
    (its id is stable: allocations, fills, events and tasks point at it)."""
    await _archive(conn, table, row_id)
    cols = list(fields)
    sets = ", ".join(f"{c} = ${i + 2}" for i, c in enumerate(cols))
    extra = ", updated_at = now()" if table in (T_BLK, T_ALLOC, T_EA) else ""
    row = await conn.fetchrow(f"UPDATE {table} SET {sets}{extra}, system_from = now() WHERE id = $1::uuid "
                              "AND system_to IS NULL RETURNING *", str(row_id), *[fields[c] for c in cols])
    if row is None:
        raise BlockConflict("the row changed underneath this request — reload and retry")
    return dict(row)


async def _audit(conn, org_id, user_id, action: str, resource_type: str, resource_id, payload: dict) -> None:
    await conn.execute(
        "INSERT INTO audit_log (org_id, user_id, action, resource_type, resource_id, payload) "
        "VALUES ($1::uuid, $2::uuid, $3, $4, $5::uuid, $6::jsonb)",
        str(org_id), str(user_id) if user_id else None, f"blocks.{action}", resource_type, str(resource_id),
        _dumps(payload))


async def _event(conn, *, org_id, block_id, action: str, from_status, to_status: str, actor_id,
                 detail: dict | None = None, allocation_id=None) -> EventRef:
    detail = detail or {}
    eid = await conn.fetchval(
        f"INSERT INTO {T_EVT} (org_id, block_id, allocation_id, action, from_status, to_status, actor_id, detail) "
        "VALUES ($1::uuid, $2::uuid, $3::uuid, $4, $5, $6, $7::uuid, $8::jsonb) RETURNING id",
        str(org_id), str(block_id), str(allocation_id) if allocation_id else None, action, from_status, to_status,
        str(actor_id) if actor_id else None, _dumps(detail))
    payload = {"block_id": str(block_id), "from_status": from_status, "to_status": to_status, **detail}
    if allocation_id:
        payload["allocation_id"] = str(allocation_id)
    await _audit(conn, org_id, actor_id, action, "trade_block", block_id, {**payload, "event_id": str(eid)})
    return EventRef(str(eid), str(org_id), action, str(block_id), payload, str(actor_id) if actor_id else None)


async def publish(pool, refs: list[EventRef]) -> None:
    from services.domain_events import publish_event

    for r in refs:
        try:
            await publish_event(pool, r.org_id, f"blocks.{r.action}", EVENT_SOURCE, r.id, json.loads(_dumps(r.payload)),
                                created_by=r.actor_id)
        except Exception:  # noqa: BLE001 — a notification; the write stands
            log.exception("blocks.%s event not published", r.action)


async def _todo(conn, org_id, recipients, *, source: str, related_type: str, related_id, title: str, detail: str,
                action_key: str) -> list[str]:
    out = []
    for uid in sorted({str(u) for u in recipients if u}):
        await conn.execute(
            "INSERT INTO member_todos (org_id, user_id, kind, category, source, related_type, related_id, title, "
            "detail, action_key, priority, status) VALUES ($1::uuid, $2::uuid, 'actual', 'execution', $3, $4, "
            "$5::uuid, $6, $7, $8, 15, 'open')",
            str(org_id), uid, source, related_type, str(related_id), title, detail, action_key)
        out.append(uid)
    return out


async def _managers(conn, org_id) -> set[str]:
    return {str(r["id"]) for r in await conn.fetch(
        """SELECT DISTINCT u.id FROM users u JOIN user_roles ur ON ur.user_id = u.id
           JOIN roles r ON r.id = ur.role_id AND r.org_id = u.org_id
           JOIN role_permissions rp ON rp.role_id = ur.role_id
           JOIN permissions p ON p.id = rp.permission_id
           WHERE u.org_id = $1::uuid AND p.name = $2 AND u.is_active
             AND COALESCE(u.status, 'active') = 'active'""", str(org_id), MANAGE)}


# ── Reading ─────────────────────────────────────────────────────────────────
async def load(conn, org_id, block_id, *, for_update: bool = False) -> dict:
    row = await conn.fetchrow(f"SELECT * FROM {T_BLK} WHERE id = $1::uuid AND org_id = $2::uuid AND system_to IS NULL"
                              + (" FOR UPDATE" if for_update else ""), _uuid(block_id), str(org_id))
    if row is None:
        raise BlockNotFound("block not found")
    return dict(row)


async def allocations_of(conn, block_id) -> list[dict]:
    return [dict(r) for r in await conn.fetch(
        f"""SELECT al.*, a.account_number_masked, a.custodian_code
            FROM {T_ALLOC} al JOIN public.accounts a ON a.id = al.account_id
            WHERE al.block_id = $1::uuid AND al.system_to IS NULL
            ORDER BY al.account_id, al.id""", str(block_id))]


async def _allocation(conn, block_id, allocation_id) -> dict | None:
    """One live allocation of THIS block (None when the id is another block's)."""
    aid = _uuid(allocation_id, "allocation")
    for a in await allocations_of(conn, block_id):
        if str(a["id"]) == aid:
            return a
    return None


async def fills_of(conn, block_id) -> list[dict]:
    return [dict(r) for r in await conn.fetch(
        f"SELECT * FROM {T_FILL} WHERE block_id = $1::uuid AND valid_to IS NULL AND system_to IS NULL "
        "ORDER BY filled_at, created_at", str(block_id))]


async def load_account(conn, org_id, account_id) -> dict:
    row = await conn.fetchrow(
        "SELECT id, org_id, account_number_masked, account_number_hash, custodian_code, registration_type, "
        "tax_status, household_id, is_held_away, is_discretionary, closed_on FROM public.accounts "
        "WHERE id = $1::uuid AND org_id = $2::uuid AND system_to IS NULL AND valid_to IS NULL",
        _uuid(account_id, "account"), str(org_id))
    if row is None:
        raise BlockNotFound("account not found")
    return await AU.attach(conn, org_id, dict(row))


async def account_unit(conn, account: dict) -> tuple[Decimal, str]:
    """(quantity unit, the capability value behind it): fractional only when the
    account's custodian has fractional_shares as an evidenced 'yes'."""
    from services.custodian_capabilities import available, capability
    from services.custody.registry import trade_custodian_key

    key = trade_custodian_key(account.get("custodian_code")) or "manual"
    cap = await capability(conn, key, "fractional_shares")
    return BA.unit_for(available(cap)), cap.value


async def automated_route_available(conn, account: dict) -> tuple[bool, str]:
    """An automated route exists for this account only when it is managed at
    its integrated custodian and that custodian's equity_trading_api is an
    evidenced 'yes' (rep2's capability rule). Another custodian / advise are
    never an API (rep7b)."""
    from services.custodian_capabilities import available, capability
    from services.custody.registry import trade_custodian_key

    e = AU.effective(account)
    if e["authority"] != "managed" or e["custody"] != "integrated":
        return False, "no custodian API for this account's authority / custody (manual only)"
    key = trade_custodian_key(account.get("custodian_code")) or "manual"
    cap = await capability(conn, key, "equity_trading_api")
    return available(cap), f"{key} equity_trading_api is '{cap.value}'"


async def _advice_only(conn, ins: dict) -> bool:
    if ins.get("source") != "proposal" or not ins.get("source_ref"):
        return False
    v = await conn.fetchval("SELECT advice_only FROM public.rebalance_proposals WHERE id = $1::uuid "
                            "AND system_to IS NULL", str(ins["source_ref"]))
    return bool(v)


async def _open_allocation(conn, instruction_id) -> str | None:
    v = await conn.fetchval(f"SELECT block_id FROM {T_ALLOC} WHERE instruction_id = $1::uuid AND system_to IS NULL "
                            "AND status IN ('planned', 'fixed') LIMIT 1", str(instruction_id))
    return str(v) if v else None


async def _carried(conn, instruction_id) -> bool:
    return bool(await conn.fetchval(f"SELECT 1 FROM {T_ALLOC} WHERE instruction_id = $1::uuid AND system_to IS NULL "
                                    "AND status = 'allocated' LIMIT 1", str(instruction_id)))


async def _filled_qty(conn, instruction_id) -> Decimal:
    return _dec(await conn.fetchval("SELECT COALESCE(sum(quantity), 0) FROM public.execution_fills WHERE "
                                    "instruction_id = $1::uuid AND valid_to IS NULL AND system_to IS NULL",
                                    str(instruction_id)))


def eligibility(ins: dict, acct: dict, *, advice_only: bool, open_block: str | None, carried: bool,
                remaining: Decimal, today: date) -> str | None:
    """The plain reason an instruction cannot join a block, or None (Task 1b)."""
    if open_block:
        return f"already in block {open_block}"
    if ins.get("option_terms"):
        return "an option leg is executed on its own (rep8), never in a block"
    if ins["quantity"] is None:
        return "a notional instruction cannot be blocked — the allocation needs a share quantity"
    e = AU.effective(acct)
    if e["authority"] != "managed":
        return (f"authority {AU.AUTHORITY_SHORT[e['authority']]} — advise and read-only accounts are never in a "
                "block (the member or another adviser executes, or nobody trades)")
    if e["custody"] not in ("integrated", "other_custodian"):
        return f"custody {AU.CUSTODY_SHORT[e['custody']]} — a block settles only to an integrated or another custodian"
    if not acct.get("is_discretionary") or advice_only:
        return "non-discretionary (advice only) — the member decides and executes; never in a block"
    if ins["executor_type"] not in BLOCK_EXECUTORS:
        return (f"executor {X.EXECUTOR_LABELS[ins['executor_type']].lower()} — blocks are for a broker, trader or "
                "custodian desk; a member or another adviser executes on their own (letter of instruction)")
    if ins["status"] == "approved":
        pass
    elif ins["status"] in ("sent", "partially_filled") and carried:
        pass
    else:
        return (f"status {X.STATUS_LABELS.get(ins['status'], ins['status']).lower()} — only an approved instruction, "
                "or the carried remainder of one, joins a block")
    if ins["execute_by"] < today:
        return "its execute-by date has passed"
    if remaining <= 0:
        return "nothing left to trade on this instruction"
    return None


async def _member(conn, org_id, ins: dict, today: date) -> dict:
    acct = await load_account(conn, org_id, ins["account_id"])
    filled = await _filled_qty(conn, ins["id"])
    remaining = (_dec(ins["quantity"]) - filled) if ins["quantity"] is not None else Decimal(0)
    unit, frac_value = await account_unit(conn, acct)
    why = eligibility(ins, acct, advice_only=await _advice_only(conn, ins),
                      open_block=await _open_allocation(conn, ins["id"]), carried=await _carried(conn, ins["id"]),
                      remaining=remaining, today=today)
    e = AU.effective(acct)
    dvp = e["custody"] == "other_custodian"
    rc = acct.get("receiving") or {}
    return {"instruction": ins, "account": acct, "remaining": remaining, "filled": filled, "unit": unit,
            "fractional_value": frac_value, "reason": why,
            "settlement_kind": "dvp" if dvp else "integrated",
            "receiving_custodian": (rc.get("custodian_name") if dvp else acct.get("custodian_code")) or "",
            "receiving": rc if dvp else None}


def _member_out(m: dict, sec_name: str | None = None) -> dict:
    ins, acct = m["instruction"], m["account"]
    return {"instruction_id": str(ins["id"]), "account_id": str(acct["id"]),
            "account_number_masked": acct["account_number_masked"], "custodian_code": acct["custodian_code"],
            "global_security_id": str(ins["global_security_id"]), "security_name": sec_name, "side": ins["side"],
            "executor_type": ins["executor_type"], "execute_by": _iso(ins["execute_by"]),
            "quantity": _num(ins["quantity"]), "remaining": _num(m["remaining"]), "status": ins["status"],
            "unit": _num(m["unit"]), "fractional": m["unit"] < 1, "settlement_kind": m["settlement_kind"],
            "receiving_custodian": m["receiving_custodian"], "authority": AU.authority_out(acct)["authority"],
            "custody": AU.authority_out(acct)["custody"], "reason": m["reason"]}


async def candidates(conn, org_id, today: date | None = None) -> dict:
    """Every live quantity instruction a block could take, split into
    eligible / ineligible (with the plain reason), plus suggested groupings
    (security / side / executor / execute-by) of two or more."""
    today = today or date.today()
    rows = await conn.fetch(
        f"SELECT i.*, s.name AS security_name FROM {X.T_INS} i JOIN portfolio.securities_global s "
        "ON s.id = i.global_security_id WHERE i.org_id = $1::uuid AND i.system_to IS NULL "
        "AND i.status = ANY($2::text[]) ORDER BY i.execute_by, i.global_security_id, i.side, i.account_id",
        str(org_id), ["approved", "sent", "partially_filled"])
    eligible, ineligible = [], []
    for r in rows:
        d = dict(r)
        m = await _member(conn, org_id, d, today)
        out = _member_out(m, d["security_name"])
        (ineligible if m["reason"] else eligible).append(out)
    groups: dict[tuple, list] = {}
    for e in eligible:
        groups.setdefault((e["global_security_id"], e["side"], e["executor_type"], e["execute_by"]), []).append(e)
    suggestions = [{"global_security_id": k[0], "security_name": v[0]["security_name"], "side": k[1],
                    "executor_type": k[2], "execute_by": k[3], "instruction_ids": [x["instruction_id"] for x in v],
                    "accounts": len(v), "total": str(sum((Decimal(x["remaining"]) for x in v), Decimal(0)))}
                   for k, v in groups.items() if len(v) >= 2]
    return {"eligible": eligible, "ineligible": ineligible, "suggestions": suggestions}


# ── Allocation policy ───────────────────────────────────────────────────────
async def current_policy(conn, org_id) -> dict | None:
    row = await conn.fetchrow(f"SELECT * FROM {T_POL} WHERE org_id = $1::uuid AND status = 'approved' "
                              "AND valid_to IS NULL AND system_to IS NULL", str(org_id))
    return dict(row) if row else None


def policy_out(r: dict | None) -> dict | None:
    if not r:
        return None
    return {"id": str(r["id"]), "version_no": r["version_no"], "method": r["method"],
            "minimum_allocation": _num(r["minimum_allocation"]), "rounding_rule": r["rounding_rule"],
            "partial_fill_rule": r["partial_fill_rule"], "policy_text": r["policy_text"],
            "adv_reference": r["adv_reference"], "reason": r["reason"], "status": r["status"],
            "maker_id": str(r["maker_id"]), "approved_by": str(r["approved_by"]) if r["approved_by"] else None,
            "approval_task_id": str(r["approval_task_id"]) if r["approval_task_id"] else None,
            "decided_at": _iso(r["decided_at"]), "decision_note": r["decision_note"],
            "valid_to": _iso(r["valid_to"]), "superseded_reason": r["superseded_reason"],
            "created_at": _iso(r["created_at"]), "in_effect": r["status"] == "approved" and r["valid_to"] is None}


async def list_policies(conn, org_id) -> list[dict]:
    return [policy_out(dict(r)) for r in await conn.fetch(
        f"SELECT * FROM {T_POL} WHERE org_id = $1::uuid AND system_to IS NULL ORDER BY version_no DESC", str(org_id))]


async def request_policy(pool, *, org_id, actor_id, minimum_allocation: Decimal, policy_text: str,
                         adv_reference: str | None, reason: str, method: str = "pro_rata_requested",
                         rounding_rule: str = "largest_remainder_then_account",
                         partial_fill_rule: str = "pro_rata_allocated") -> dict:
    """A new policy VERSION, a draft until a second person approves it."""
    if method not in BA.METHODS:
        raise BlockError(f"method is one of {list(BA.METHODS)} (others are added when the firm writes them)")
    if rounding_rule not in BA.ROUNDING_RULES or partial_fill_rule not in BA.PARTIAL_RULES:
        raise BlockError("unknown rounding or partial-fill rule")
    if not isinstance(minimum_allocation, Decimal) or minimum_allocation < 0:
        raise BlockError("the minimum allocation is a decimal of zero or more shares")
    if not (policy_text or "").strip():
        raise BlockError("the written policy text is the firm's own words — it cannot be empty")
    if not (reason or "").strip():
        raise BlockError("a policy version needs a reason")
    async with pool.acquire() as conn:
        actor = await _human(conn, actor_id, org_id)
        if await conn.fetchval(f"SELECT 1 FROM {T_POL} WHERE org_id = $1::uuid AND status = 'draft' "
                               "AND system_to IS NULL", str(org_id)):
            raise BlockConflict("a policy version is already awaiting approval — decide it first")
        version = int(await conn.fetchval(f"SELECT COALESCE(max(version_no), 0) + 1 FROM {T_POL} WHERE "
                                          "org_id = $1::uuid AND system_to IS NULL", str(org_id)))
    pid = uuid.uuid4()
    task = await HT.open_task(
        pool, org_id=org_id, kind=TASK_POLICY, subject_type=SUBJECT_POLICY, subject_id=pid, maker_id=actor["id"],
        eligible_permission=APPROVE, title=f"Approve allocation policy version {version}",
        payload={"version_no": version, "minimum_allocation": str(minimum_allocation), "method": method},
        action_path="/admin/allocation-policy")
    try:
        async with pool.acquire() as conn:
            async with conn.transaction():
                row = await conn.fetchrow(
                    f"""INSERT INTO {T_POL} (id, org_id, version_no, method, minimum_allocation, rounding_rule,
                            partial_fill_rule, policy_text, adv_reference, reason, status, maker_id, approval_task_id)
                        VALUES ($1, $2::uuid, $3, $4, $5, $6, $7, $8, $9, $10, 'draft', $11::uuid, $12::uuid)
                        RETURNING *""",
                    pid, str(org_id), version, method, minimum_allocation, rounding_rule, partial_fill_rule,
                    policy_text.strip(), (adv_reference or "").strip() or None, reason.strip(), str(actor["id"]),
                    task["id"])
                await _audit(conn, org_id, actor["id"], "policy_requested", "allocation_policy", pid,
                             {"version_no": version, "task_id": task["id"]})
    except Exception:
        await _withdraw(pool, task["id"], "the policy version could not be recorded")
        raise
    return {**policy_out(dict(row)), "task": task}


async def _withdraw(pool, task_id, reason: str) -> None:
    from services.workflow_engine import cancel_user_task

    try:
        await cancel_user_task(pool, uuid.UUID(str(task_id)), None, reason)
    except Exception:  # noqa: BLE001 — the task may already be decided
        log.info("task %s was not open to withdraw", task_id)


async def _decide_change(pool, *, table: str, subject: str, org_id, actor_id, row_id, outcome: str,
                         note: str | None) -> dict:
    """approve / reject a policy version or a correction through its task."""
    if outcome not in ("approve", "reject"):
        raise BlockError("outcome is approve or reject")
    if outcome == "reject" and not (note or "").strip():
        raise BlockError("a rejection needs a note saying why")
    async with pool.acquire() as conn:
        actor = await _human(conn, actor_id, org_id)
        row = await conn.fetchrow(f"SELECT * FROM {table} WHERE id = $1::uuid AND org_id = $2::uuid "
                                  "AND system_to IS NULL", _uuid(row_id, subject.replace("_", " ")), str(org_id))
    if row is None:
        raise BlockNotFound(f"{subject.replace('_', ' ')} not found")
    if row["status"] != "draft":
        raise BlockConflict(f"the {subject.replace('_', ' ')} is {row['status']}, not awaiting approval")
    if actor["id"] == row["maker_id"]:
        raise MakerCheckerViolation(f"maker-checker: you asked for this {subject.replace('_', ' ')}, so a different "
                                    f"holder of {APPROVE} must {outcome} it")
    try:
        done = await HT.complete_task(pool, org_id=org_id, task_id=row["approval_task_id"], actor_id=actor["id"],
                                      outcome=outcome, note=note)
    except HT.MakerCheckerError as exc:
        raise MakerCheckerViolation(str(exc)) from exc
    except HT.HumanTaskPermissionError as exc:
        raise PermissionError(str(exc)) from exc
    except HT.HumanTaskConflict as exc:
        raise BlockConflict(str(exc)) from exc
    async with pool.acquire() as conn:
        # the engine applied the outcome through on_complete; re-read the row (never trust a return value's shape)
        return dict(await conn.fetchrow(f"SELECT * FROM {table} WHERE id = $1::uuid AND system_to IS NULL",
                                        str(row["id"])))


async def decide_policy(pool, *, org_id, actor_id, policy_id, outcome: str, note: str | None = None) -> dict:
    return policy_out(await _decide_change(pool, table=T_POL, subject=SUBJECT_POLICY, org_id=org_id,
                                           actor_id=actor_id, row_id=policy_id, outcome=outcome, note=note))


async def _apply_policy(pool, task: dict, outcome: str, actor_id, note) -> dict | None:
    org_id = task["org_id"]
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(f"SELECT * FROM {T_POL} WHERE id = $1::uuid AND org_id = $2::uuid "
                                      "AND system_to IS NULL FOR UPDATE", task["subject_id"], str(org_id))
            if row is None or row["status"] != "draft":
                return policy_out(dict(row)) if row else None
            checker = uuid.UUID(str(actor_id))
            if checker == row["maker_id"]:
                raise MakerCheckerViolation("maker-checker: the person who wrote a policy version cannot approve it")
            status = "approved" if outcome == "approve" else "rejected"
            if status == "approved":
                prev = await conn.fetchrow(f"SELECT id FROM {T_POL} WHERE org_id = $1::uuid AND status = 'approved' "
                                           "AND valid_to IS NULL AND system_to IS NULL FOR UPDATE", str(org_id))
                if prev:
                    await conn.execute(f"UPDATE {T_POL} SET valid_to = now(), superseded_reason = $2 WHERE id = $1",
                                       prev["id"], f"superseded by version {row['version_no']}")
            await _archive(conn, T_POL, row["id"])
            new = dict(await conn.fetchrow(
                f"UPDATE {T_POL} SET status = $2, approved_by = $3, decided_at = now(), decision_note = $4, "
                "system_from = now() WHERE id = $1 AND system_to IS NULL RETURNING *", row["id"], status, checker,
                note))
            await _audit(conn, org_id, checker, f"policy_{status}", "allocation_policy", row["id"],
                         {"task_id": task["id"], "note": note, "version_no": row["version_no"]})
    return policy_out(new)


async def _on_policy(pool, task, outcome, actor_id, note):
    return await _apply_policy(pool, task, outcome, actor_id, note)


HT.register_kind(TASK_POLICY, label="Allocation policy approval", outcomes=("approve", "reject"), maker_checker=True,
                 on_complete=_on_policy)


# ── Executing (omnibus) accounts ────────────────────────────────────────────
def execution_account_out(r: dict | None) -> dict | None:
    if not r:
        return None
    return {"id": str(r["id"]), "broker_name": r["broker_name"], "account_reference_masked":
            r["account_reference_masked"], "dvp_capable": r["dvp_capable"], "contact": r["contact"],
            "notes": r["notes"], "status": r["status"], "created_at": _iso(r["created_at"])}


async def list_execution_accounts(conn, org_id) -> list[dict]:
    return [execution_account_out(dict(r)) for r in await conn.fetch(
        f"SELECT * FROM {T_EA} WHERE org_id = $1::uuid AND system_to IS NULL ORDER BY status, broker_name",
        str(org_id))]


async def _execution_account(conn, org_id, ea_id) -> dict:
    row = await conn.fetchrow(f"SELECT * FROM {T_EA} WHERE id = $1::uuid AND org_id = $2::uuid AND system_to IS NULL",
                              _uuid(ea_id, "executing account"), str(org_id))
    if row is None:
        raise BlockNotFound("executing account not found")
    return dict(row)


def _mask(last4: str | None) -> str:
    try:
        m = AU.mask_last4(last4)
    except AU.AuthorityError as exc:
        raise BlockError("the executing account is given as its last four characters only — never the full "
                         "number") from exc
    if not m:
        raise BlockError("give the executing account's last four characters (it is stored masked)")
    return m


async def create_execution_account(pool, *, org_id, actor_id, broker_name: str, reference_last4: str,
                                   dvp_capable: bool, contact: str | None = None, notes: str | None = None) -> dict:
    if not (broker_name or "").strip():
        raise BlockError("name the broker")
    masked = _mask(reference_last4)
    async with pool.acquire() as conn:
        actor = await _human(conn, actor_id, org_id)
        async with conn.transaction():
            row = await conn.fetchrow(
                f"INSERT INTO {T_EA} (org_id, broker_name, account_reference_masked, dvp_capable, contact, notes, "
                "created_by) VALUES ($1::uuid, $2, $3, $4, $5, $6, $7::uuid) RETURNING *",
                str(org_id), broker_name.strip(), masked, bool(dvp_capable), contact, notes, str(actor["id"]))
            await _audit(conn, org_id, actor["id"], "execution_account_created", "execution_account", row["id"],
                         {"broker_name": row["broker_name"], "account_reference_masked": masked,
                          "dvp_capable": bool(dvp_capable)})
    return execution_account_out(dict(row))


async def update_execution_account(pool, *, org_id, actor_id, account_id, changes: dict) -> dict:
    allowed = {"broker_name", "dvp_capable", "contact", "notes", "status"}
    bad = set(changes) - allowed
    if bad:
        raise BlockError(f"not editable: {sorted(bad)}")
    if "status" in changes and changes["status"] not in EXECUTION_ACCOUNT_STATUS_LABELS:
        raise BlockError("status is active or retired")
    if "broker_name" in changes and not (changes["broker_name"] or "").strip():
        raise BlockError("name the broker")
    async with pool.acquire() as conn:
        actor = await _human(conn, actor_id, org_id)
        async with conn.transaction():
            cur = await _execution_account(conn, org_id, account_id)
            if cur["status"] == "retired":
                raise BlockConflict("a retired executing account is not changed — add a new one")
            new = await _update(conn, T_EA, cur["id"], changes)
            await _audit(conn, org_id, actor["id"], "execution_account_changed", "execution_account", cur["id"],
                         {"before": {k: cur[k] for k in changes}, "after": {k: new[k] for k in changes}})
    return execution_account_out(new)


# ── Price guidance: a reference price is never invented ─────────────────────
async def reference_price(conn, org_id, global_security_id, *, typed: Decimal | None, typed_at: datetime | None,
                          today: date) -> dict:
    """{price, source, provider, as_of, missing}: the adviser's typed price
    (source 'adviser', as of the time they give), else the security-data
    layer's (marketdata1: provider + as-of), else nothing — with what is missing."""
    if typed is not None:
        if typed <= 0:
            raise BlockError("a reference price must be above zero")
        return {"price": typed.quantize(BA.PRICE_Q), "source": "adviser", "provider": "adviser",
                "as_of": (typed_at.date() if typed_at else today), "missing": None}
    from services import security_data as SD

    r = await SD.get(conn, org_id, "eod_price", str(global_security_id), as_of=today, purpose="internal")
    if r.found and r.value is not None:
        return {"price": Decimal(str(r.value)).quantize(BA.PRICE_Q), "source": "security_data",
                "provider": r.provider_key, "as_of": r.as_of, "missing": None}
    return {"price": None, "source": None, "provider": None, "as_of": None, "missing": r.missing or SD.NOT_PRICED}


# ── Build (preview + draft) ─────────────────────────────────────────────────
async def _plan(conn, org_id, instruction_ids: list, policy: dict | None, today: date) -> dict:
    ids = list(dict.fromkeys(_uuid(i, "instruction") for i in instruction_ids))
    if not ids:
        raise BlockError("choose the approved instructions to block")
    members, refused, loaded = [], [], []
    for iid in ids:
        ins = await X.load(conn, org_id, iid)
        loaded.append(ins)
        m = await _member(conn, org_id, ins, today)
        if m["reason"]:
            refused.append({**_member_out(m), "reason": m["reason"]})
        else:
            members.append(m)
    ok = members
    if ok:
        secs = {str(m["instruction"]["global_security_id"]) for m in ok}
        sides = {m["instruction"]["side"] for m in ok}
        if len(secs) > 1:
            raise BlockConflict("a block is one security — these instructions name more than one")
        if len(sides) > 1:
            raise BlockConflict("opposite sides of the same security are never netted across accounts — build a buy "
                                "block and a sell block separately")
        execs = {m["instruction"]["executor_type"] for m in ok}
        if len(execs) > 1:
            raise BlockConflict(f"a block has one executor — these instructions name {sorted(execs)}")
        bys = {m["instruction"]["execute_by"] for m in ok}
        if len(bys) > 1:
            raise BlockConflict(f"a block has one execute-by window — these instructions are due on "
                                f"{sorted(d.isoformat() for d in bys)}")
    minimum = _dec(policy["minimum_allocation"]) if policy else Decimal(0)
    alloc, ref2 = BA.fixed_allocation([(str(m["instruction"]["id"]), str(m["account"]["id"]), m["remaining"],
                                        m["unit"]) for m in ok], minimum)
    final_members = []
    for m in ok:
        k = str(m["instruction"]["id"])
        if k in ref2:
            refused.append({**_member_out(m), "reason": ref2[k]})
        else:
            m["allocated"] = alloc[k]
            final_members.append(m)
    first = final_members[0]["instruction"] if final_members else None
    # rep9 fix 1 — what the cross-trade check reads: the chosen instructions' own security / side / window, even when
    # every one of them is refused (below the minimum, ineligible) — so the flag is reported alongside any refusal
    b = ok[0]["instruction"] if ok else (loaded[0] if loaded else None)
    basis = ({"global_security_id": str(b["global_security_id"]), "side": b["side"], "execute_by": b["execute_by"]}
             if b is not None and b.get("global_security_id") and b.get("side") else None)
    return {"members": final_members, "refused": refused, "basis": basis,
            "global_security_id": str(first["global_security_id"]) if first else None,
            "side": first["side"] if first else None, "executor_type": first["executor_type"] if first else None,
            "execute_by": first["execute_by"] if first else None,
            "total": sum((m["allocated"] for m in final_members), Decimal(0))}


async def _crosses(conn, org_id, global_security_id, side: str, execute_by: date, exclude=None) -> list[str]:
    # rep13 — the opposite DIRECTION: a buy / cover crosses a sell / short, and the reverse
    other = ["sell", "sell_short"] if side in ("buy", "buy_to_cover") else ["buy", "buy_to_cover"]
    rows = await conn.fetch(
        f"SELECT id FROM {T_BLK} WHERE org_id = $1::uuid AND system_to IS NULL AND global_security_id = $2::uuid "
        "AND side = ANY($3::text[]) AND execute_by = $4 AND status = ANY($5::text[]) AND id <> COALESCE($6::uuid, "
        "'00000000-0000-0000-0000-000000000000'::uuid)",
        str(org_id), str(global_security_id), other, execute_by, list(LIVE_STATUSES),
        str(exclude) if exclude else None)
    return [str(r["id"]) for r in rows]


async def preview(conn, org_id, instruction_ids: list, *, typed_reference: Decimal | None = None,
                  typed_at: datetime | None = None, today: date | None = None) -> dict:
    """The allocation a block of these instructions would fix under the
    current policy — nothing is written."""
    today = today or date.today()
    policy = await current_policy(conn, org_id)
    plan = await _plan(conn, org_id, instruction_ids, policy, today)
    out = {"policy": policy_out(policy), "policy_missing": policy is None, "refused": plan["refused"],
           "allocations": [{**_member_out(m), "allocated_quantity": str(m["allocated"])} for m in plan["members"]],
           "total_quantity": str(plan["total"]), "side": plan["side"], "executor_type": plan["executor_type"],
           "execute_by": _iso(plan["execute_by"]), "global_security_id": plan["global_security_id"],
           "crosses": [], "reference": None}
    if plan["basis"]:
        bs = plan["basis"]
        out["crosses"] = await _crosses(conn, org_id, bs["global_security_id"], bs["side"], bs["execute_by"])
    if plan["members"]:
        ref = await reference_price(conn, org_id, plan["global_security_id"], typed=typed_reference,
                                    typed_at=typed_at, today=today)
        out["reference"] = {**ref, "price": _num(ref["price"]), "as_of": _iso(ref["as_of"])}
    return out


async def build_block(pool, *, org_id, actor_id, instruction_ids: list, route: str = "manual",
                      manual_reason: str | None = None, execution_account_id=None, executor_name: str | None = None,
                      executor_contact: str | None = None, price_kind: str = "market",
                      limit_price: Decimal | None = None, range_low: Decimal | None = None,
                      range_high: Decimal | None = None, reference_price_typed: Decimal | None = None,
                      reference_at: datetime | None = None, if_not_filled: str = "ask",
                      cross_reason: str | None = None, firm_contact: str | None = None,
                      notes: str | None = None) -> dict:
    """A DRAFT block over approved account instructions, its allocation
    computed under the approved policy (fixed only when a second person
    approves it). Refuses — with every reason — rather than leaving anyone out
    silently."""
    why = RT.route_refusal(route)
    if why:
        raise BlockConflict(why)
    if if_not_filled not in X.IF_NOT_FILLED_LABELS:
        raise BlockError(f"if not filled is one of {list(X.IF_NOT_FILLED_LABELS)}")
    if price_kind not in X.PRICE_KIND_LABELS:
        raise BlockError(f"price kind is one of {list(X.PRICE_KIND_LABELS)}")
    today = date.today()
    async with pool.acquire() as conn:
        actor = await _human(conn, actor_id, org_id)
        policy = await current_policy(conn, org_id)
        if policy is None:
            raise BlockConflict("no approved allocation policy — the firm writes its policy in Settings → Allocation "
                                "policy and a second person approves it before any block is built")
        plan = await _plan(conn, org_id, instruction_ids, policy, today)
        # rep9 fix 1 — the cross-trade check runs BEFORE any other refusal is raised and is reported alongside it
        # (it used to run last, so a below-minimum or ineligible pick hid an opposite-side block in the same window)
        bs = plan["basis"]
        crosses = (await _crosses(conn, org_id, bs["global_security_id"], bs["side"], bs["execute_by"])
                   if bs else [])
        cross_why = (f"cross-trade risk: a {('sell' if bs['side'] == 'buy' else 'buy')} block of the same security in "
                     f"the same window exists ({', '.join(crosses)}) — the two are never netted; give a reason to "
                     "build this block alongside it") if crosses and not (cross_reason or "").strip() else None
        if plan["refused"]:
            raise BlockConflict("not every instruction can join this block: " + "; ".join(
                f"{r['account_number_masked']}: {r['reason']}" for r in plan["refused"])
                + (f"; and {cross_why}" if cross_why else ""))
        if not plan["members"]:
            raise BlockError("no eligible instruction was chosen")
        executor = plan["executor_type"]
        ea = None
        if executor in ("broker", "trader"):
            if not execution_account_id:
                raise BlockError("a broker / trader block executes in the firm's executing (omnibus) account — "
                                 "choose one (Settings → Executing accounts)")
            ea = await _execution_account(conn, org_id, execution_account_id)
            if ea["status"] != "active":
                raise BlockConflict("that executing account is retired")
            if any(m["settlement_kind"] == "dvp" for m in plan["members"]) and not ea["dvp_capable"]:
                raise BlockConflict(f"{ea['broker_name']} {ea['account_reference_masked']} is not recorded as "
                                    "DVP-capable, and this block settles DVP to another custodian")
        elif execution_account_id:
            raise BlockError("a custodian-desk block executes at the custodian — no executing account")
        auto = RT.any_connected()
        auto_notes = []
        if executor == "custodian_desk":
            avail = [await automated_route_available(conn, m["account"]) for m in plan["members"]]
            auto = auto or all(a for a, _ in avail)
            auto_notes = sorted({n for _, n in avail})
        if auto and route == "manual" and not (manual_reason or "").strip():
            raise BlockConflict("an automated route is available for this block (" + "; ".join(auto_notes) + ") — "
                                "say why you choose the manual route; that choice is recorded")
        if cross_why:
            raise BlockConflict(cross_why)
        ref = await reference_price(conn, org_id, plan["global_security_id"], typed=reference_price_typed,
                                    typed_at=reference_at, today=today)
        if price_kind == "range" and ref["price"] is None:
            raise BlockConflict(f"a range needs a reference price: the security-data layer has none ({ref['missing']}) "
                                "— type one")
        async with conn.transaction():
            blk = dict(await conn.fetchrow(
                f"""INSERT INTO {T_BLK} (org_id, global_security_id, side, total_quantity, executor_type,
                        executor_name, executor_contact, execution_account_id, route, automated_route_available,
                        manual_reason, price_kind, limit_price, range_low, range_high, reference_price, reference_at,
                        reference_source, reference_provider, reference_as_of, execute_by, if_not_filled, policy_id,
                        cross_flag, cross_reason, firm_contact, notes, status, maker_id, maker_ids)
                    VALUES ($1::uuid, $2::uuid, $3, $4, $5, $6, $7, $8::uuid, $9, $10, $11, $12, $13, $14, $15, $16,
                            $17, $18, $19, $20, $21, $22, $23::uuid, $24, $25, $26, $27, 'draft', $28::uuid,
                            ARRAY[$28::uuid]) RETURNING *""",
                str(org_id), plan["global_security_id"], plan["side"], plan["total"], executor, executor_name,
                executor_contact, str(ea["id"]) if ea else None, route, bool(auto),
                (manual_reason or "").strip() or None, price_kind, limit_price, range_low, range_high, ref["price"],
                reference_at if ref["source"] == "adviser" else None, ref["source"], ref["provider"], ref["as_of"],
                plan["execute_by"], if_not_filled, str(policy["id"]), bool(crosses),
                (cross_reason or "").strip() or None if crosses else None, firm_contact, notes, str(actor["id"])))
            for m in plan["members"]:
                await conn.execute(
                    f"""INSERT INTO {T_ALLOC} (org_id, block_id, instruction_id, account_id, requested_quantity,
                            allocated_quantity, quantity_unit, settlement_kind, receiving_custodian, status, created_by)
                        VALUES ($1::uuid, $2::uuid, $3::uuid, $4::uuid, $5, $6, $7, $8, $9, 'planned', $10::uuid)""",
                    str(org_id), str(blk["id"]), str(m["instruction"]["id"]), str(m["account"]["id"]), m["remaining"],
                    m["allocated"], m["unit"], m["settlement_kind"], m["receiving_custodian"] or "not recorded",
                    str(actor["id"]))
            ref_ev = await _event(conn, org_id=org_id, block_id=blk["id"], action="create", from_status=None,
                                  to_status="draft", actor_id=actor["id"],
                                  detail={"instructions": [str(m["instruction"]["id"]) for m in plan["members"]],
                                          "total_quantity": str(plan["total"]), "policy_version": policy["version_no"],
                                          "route": route, "automated_route_available": bool(auto),
                                          "manual_reason": manual_reason, "crosses": crosses,
                                          "reference": {"source": ref["source"], "provider": ref["provider"],
                                                        "as_of": _iso(ref["as_of"])}})
    await publish(pool, [ref_ev])
    return blk


# ── Approval (a governed human task; the allocation is FIXED here) ──────────
async def submit(pool, *, org_id, block_id, actor_id) -> dict:
    async with pool.acquire() as conn:
        actor = await _human(conn, actor_id, org_id)
        row = await load(conn, org_id, block_id)
        _need(row, ("draft",), "submit")
        if row["execute_by"] < date.today():
            raise BlockConflict("the execute-by date has passed — cancel this block and build a new one")
        sec = await X._security(conn, row["global_security_id"])
    task = await HT.open_task(
        pool, org_id=org_id, kind=TASK_BLOCK, subject_type=SUBJECT_BLOCK, subject_id=row["id"], maker_id=actor["id"],
        eligible_permission=APPROVE,
        title=f"Approve block: {X.SIDE_LABELS[row['side']]} {row['total_quantity']} shares of {sec['name']}",
        payload={"side": row["side"], "total_quantity": str(row["total_quantity"]), "security": sec["name"],
                 "executor_type": row["executor_type"]},
        action_path=f"/portfolio/blocks/{row['id']}")
    try:
        async with pool.acquire() as conn:
            async with conn.transaction():
                cur = await load(conn, org_id, block_id, for_update=True)
                _need(cur, ("draft",), "submit")
                makers = list(cur["maker_ids"])
                if actor["id"] not in makers:
                    makers.append(actor["id"])
                new = await _update(conn, T_BLK, cur["id"], {"status": "pending_approval",
                                                              "approval_task_id": uuid.UUID(task["id"]),
                                                              "maker_ids": makers})
                ref = await _event(conn, org_id=org_id, block_id=cur["id"], action="submit", from_status="draft",
                                   to_status="pending_approval", actor_id=actor["id"],
                                   detail={"task_id": task["id"], "notified": task.get("notified", [])})
    except Exception:
        await HT.cancel_task(pool, org_id=org_id, task_id=task["id"], actor_id=actor["id"],
                             reason="the block could not be submitted")
        raise
    await publish(pool, [ref])
    return new


async def _still_eligible(conn, org_id, block: dict) -> list[str]:
    """Every allocation's instruction still eligible (re-read, never trusted)."""
    bad = []
    today = date.today()
    for a in await allocations_of(conn, block["id"]):
        if a["status"] != "planned":
            continue
        ins = await X.load(conn, org_id, a["instruction_id"])
        acct = await load_account(conn, org_id, a["account_id"])
        filled = await _filled_qty(conn, ins["id"])
        remaining = _dec(ins["quantity"]) - filled if ins["quantity"] is not None else Decimal(0)
        open_other = await conn.fetchval(f"SELECT block_id FROM {T_ALLOC} WHERE instruction_id = $1 AND system_to IS "
                                         "NULL AND status IN ('planned', 'fixed') AND block_id <> $2 LIMIT 1",
                                         ins["id"], block["id"])
        why = eligibility(ins, acct, advice_only=await _advice_only(conn, ins),
                          open_block=str(open_other) if open_other else None, carried=await _carried(conn, ins["id"]),
                          remaining=remaining, today=today)
        if why is None and remaining != _dec(a["requested_quantity"]):
            why = f"its remaining quantity changed ({a['requested_quantity']} → {remaining}) — rebuild the block"
        if why:
            bad.append(f"{a['account_number_masked']}: {why}")
    return bad


async def _apply_block_outcome(pool, org_id, task: dict, outcome: str, actor_id, note) -> dict | None:
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(f"SELECT * FROM {T_BLK} WHERE approval_task_id = $1::uuid AND system_to IS NULL "
                                      "AND org_id = $2::uuid FOR UPDATE", task["id"], str(org_id))
            if row is None or row["status"] != "pending_approval":
                return dict(row) if row else None
            row = dict(row)
            if outcome == "approve":
                if uuid.UUID(str(actor_id)) in row["maker_ids"]:
                    raise MakerCheckerViolation("maker-checker: a maker of this block cannot approve it")
                new = await _update(conn, T_BLK, row["id"], {"status": "approved",
                                                              "approved_by": uuid.UUID(str(actor_id)),
                                                              "approved_at": datetime.now(timezone.utc)})
                fixed = []
                for a in await allocations_of(conn, row["id"]):
                    if a["status"] == "planned":
                        await _update(conn, T_ALLOC, a["id"], {"status": "fixed"})
                        fixed.append({"allocation_id": str(a["id"]), "account": a["account_number_masked"],
                                      "allocated_quantity": str(a["allocated_quantity"])})
                ref = await _event(conn, org_id=org_id, block_id=row["id"], action="approve",
                                   from_status="pending_approval", to_status="approved", actor_id=actor_id,
                                   detail={"task_id": task["id"], "note": note, "fixed": fixed})
            else:
                new = await _update(conn, T_BLK, row["id"], {"status": "draft", "approval_task_id": None})
                ref = await _event(conn, org_id=org_id, block_id=row["id"], action="reject",
                                   from_status="pending_approval", to_status="draft", actor_id=actor_id,
                                   detail={"task_id": task["id"], "note": note})
    await publish(pool, [ref])
    return new


async def _on_block(pool, task, outcome, actor_id, note):
    return await _apply_block_outcome(pool, task["org_id"], task, outcome, actor_id, note)


async def _block_precheck(pool, task, outcome, actor_id):
    if outcome != "approve":
        return
    async with pool.acquire() as conn:
        row = await conn.fetchrow(f"SELECT * FROM {T_BLK} WHERE approval_task_id = $1::uuid AND system_to IS NULL",
                                  task["id"])
        if row is None:
            return
        bad = await _still_eligible(conn, row["org_id"], dict(row))
    if bad:
        raise HT.HumanTaskConflict("the block cannot be approved as built: " + "; ".join(bad))


HT.register_kind(TASK_BLOCK, label="Block approval", outcomes=("approve", "reject"), maker_checker=True,
                 on_complete=_on_block)
HT.register_precheck(TASK_BLOCK, _block_precheck)


async def decide(pool, *, org_id, block_id, actor_id, outcome: str, note: str | None = None) -> dict:
    """Approve / reject through the block's open task. Maker-checker twice
    over (maker_ids here, the engine's maker, and the DB CHECK)."""
    if outcome not in ("approve", "reject"):
        raise BlockError("outcome is approve or reject")
    async with pool.acquire() as conn:
        actor = await _human(conn, actor_id, org_id)
        row = await load(conn, org_id, block_id)
    _need(row, ("pending_approval",), "approve or reject")
    if actor["id"] in row["maker_ids"]:
        raise MakerCheckerViolation("maker-checker: you built or submitted this block, so a different holder of "
                                    f"{APPROVE} must {outcome} it")
    try:
        done = await HT.complete_task(pool, org_id=org_id, task_id=row["approval_task_id"], actor_id=actor["id"],
                                      outcome=outcome, note=note)
    except HT.MakerCheckerError as exc:
        raise MakerCheckerViolation(str(exc)) from exc
    except HT.HumanTaskPermissionError as exc:
        raise PermissionError(str(exc)) from exc
    except HT.HumanTaskConflict as exc:
        raise BlockConflict(str(exc)) from exc
    result = done.get("subject_result")
    if result is None:
        async with pool.acquire() as conn:
            result = await load(conn, org_id, block_id)
    return result


# ── Sent, fills ─────────────────────────────────────────────────────────────
async def mark_sent(pool, *, org_id, block_id, actor_id, channel: str, note: str | None = None) -> dict:
    """approved → sent, and each account instruction with it (rep2 'sent',
    channel and person recorded on both)."""
    if channel not in X.SENT_CHANNEL_LABELS:
        raise BlockError(f"channel must be one of {list(X.SENT_CHANNEL_LABELS)}")
    now = datetime.now(timezone.utc)
    refs, xrefs = [], []
    async with pool.acquire() as conn:
        actor = await _human(conn, actor_id, org_id)
        async with conn.transaction():
            row = await load(conn, org_id, block_id, for_update=True)
            _need(row, ("approved",), "mark sent")
            new = await _update(conn, T_BLK, row["id"], {"status": "sent", "sent_by": actor["id"], "sent_at": now,
                                                          "sent_channel": channel, "sent_note": note})
            for a in await allocations_of(conn, row["id"]):
                if a["status"] != "fixed":
                    continue
                ins = await X.load(conn, org_id, a["instruction_id"], for_update=True)
                if ins["status"] == "approved":
                    await X._update(conn, ins["id"], {"status": "sent", "sent_by": actor["id"], "sent_at": now,
                                                      "sent_channel": channel,
                                                      "sent_note": f"sent in block {row['id']}"})
                    xrefs.append(await X._event(conn, org_id=org_id, instruction_id=ins["id"], action="mark_sent",
                                                from_status="approved", to_status="sent", actor_id=actor["id"],
                                                detail={"channel": channel, "block_id": str(row["id"])}))
            refs.append(await _event(conn, org_id=org_id, block_id=row["id"], action="mark_sent",
                                     from_status="approved", to_status="sent", actor_id=actor["id"],
                                     detail={"channel": channel, "note": note}))
    await publish(pool, refs)
    await X.publish(pool, xrefs)
    return new


async def enter_fill(pool, *, org_id, block_id, actor_id, quantity: Decimal, price: Decimal, filled_at: datetime,
                     venue: str | None = None, note: str | None = None) -> dict:
    """A fill a person reports against the BLOCK. The block's average price is
    recomputed from every fill — never entered."""
    if not isinstance(quantity, Decimal) or not isinstance(price, Decimal):
        raise BlockError("fill quantity and price must be Decimal")
    if quantity <= 0 or price <= 0:
        raise BlockError("fill quantity and price must be greater than zero")
    if filled_at.tzinfo is None:
        raise BlockError("the fill time needs a time zone")
    if filled_at > datetime.now(timezone.utc):
        raise BlockError("the fill time is in the future")
    async with pool.acquire() as conn:
        actor = await _human(conn, actor_id, org_id)
        async with conn.transaction():
            row = await load(conn, org_id, block_id, for_update=True)
            _need(row, ("sent", "partially_filled"), "enter a fill on")
            prior = await fills_of(conn, row["id"])
            total = sum((_dec(f["quantity"]) for f in prior), Decimal(0)) + quantity
            if total > _dec(row["total_quantity"]):
                raise BlockError(f"this fill would take the block to {total}, more than its {row['total_quantity']}")
            fid = await conn.fetchval(
                f"INSERT INTO {T_FILL} (org_id, block_id, quantity, price, filled_at, venue, note, entered_by) "
                "VALUES ($1::uuid, $2::uuid, $3, $4, $5, $6, $7, $8::uuid) RETURNING id",
                str(org_id), str(row["id"]), quantity, price, filled_at, venue, note, str(actor["id"]))
            avg = BA.average_price([(_dec(f["quantity"]), _dec(f["price"])) for f in prior] + [(quantity, price)])
            to_status = "filled" if total == _dec(row["total_quantity"]) else "partially_filled"
            new = await _update(conn, T_BLK, row["id"], {"status": to_status, "filled_quantity": total,
                                                          "average_price": avg})
            ref = await _event(conn, org_id=org_id, block_id=row["id"], action="fill", from_status=row["status"],
                               to_status=to_status, actor_id=actor["id"],
                               detail={"fill_id": str(fid), "quantity": str(quantity), "price": str(price),
                                       "filled_at": filled_at.isoformat(), "venue": venue,
                                       "total_quantity": str(total), "average_price": str(avg)})
    await publish(pool, [ref])
    return new


# ── Allocation (a person's act) ─────────────────────────────────────────────
async def allocate(pool, *, org_id, block_id, actor_id, note: str | None = None) -> dict:
    """Book each account's share of the fills on its own instruction at the
    average price (rep2 fill + its own lots), write each DVP allocation's
    settlement instruction, and apply the if-not-filled rule to any
    remainder. ONE transaction: every account or none."""
    refs, xrefs = [], []
    async with pool.acquire() as conn:
        actor = await _human(conn, actor_id, org_id)
        async with conn.transaction():
            row = await load(conn, org_id, block_id, for_update=True)
            _need(row, ("filled", "partially_filled"), "allocate")
            policy = dict(await conn.fetchrow(f"SELECT * FROM {T_POL} WHERE id = $1::uuid", row["policy_id"]))
            fills = await fills_of(conn, row["id"])
            filled = sum((_dec(f["quantity"]) for f in fills), Decimal(0))
            avg = BA.average_price([(_dec(f["quantity"]), _dec(f["price"])) for f in fills])
            last = max(f["filled_at"] for f in fills)
            trade_date = last.astimezone(timezone.utc).date()
            allocs = [a for a in await allocations_of(conn, row["id"]) if a["status"] == "fixed"]
            slots = [BA.Slot(str(a["id"]), str(a["account_id"]), _dec(a["allocated_quantity"]),
                             _dec(a["quantity_unit"])) for a in allocs]
            try:
                shares = BA.allocate_fill(slots, filled, _dec(policy["minimum_allocation"]))
            except BA.AllocationError as exc:
                raise BlockConflict(f"the fills cannot be allocated under policy version {policy['version_no']}: "
                                    f"{exc}") from exc
            partial = filled < _dec(row["total_quantity"])
            rule = row["if_not_filled"]
            booked = []
            for a in allocs:
                sh = shares[str(a["id"])]
                ins = await X.load(conn, org_id, a["instruction_id"], for_update=True)
                if ins["status"] not in ("sent", "partially_filled"):
                    raise BlockConflict(f"{a['account_number_masked']}: its instruction is "
                                        f"{X.STATUS_LABELS[ins['status']].lower()}, not working — cannot book")
                acct = await load_account(conn, org_id, a["account_id"])
                prior = await _filled_qty(conn, ins["id"])
                fields = {"status": "allocated", "final_quantity": sh.quantity, "zero_reason": sh.zero_reason,
                          "settlement_status": "pending" if sh.quantity > 0 else "cancelled"}
                if sh.quantity > 0:
                    fields["average_price"] = avg
                    lots = []
                    if ins["side"] == "sell":
                        own = [(str(x["tax_lot_id"]), _dec(x["quantity"])) for x in await X._lots(conn, ins["id"])]
                        lots = BA.relieve_lots(own, sh.quantity, _dec(a["requested_quantity"]),
                                               _dec(a["quantity_unit"]))
                    complete_all = prior + sh.quantity == _dec(ins["quantity"])
                    short = (not complete_all) and rule == "cancel"
                    fill_id = await conn.fetchval(
                        f"INSERT INTO {X.T_FILL} (org_id, instruction_id, quantity, price, filled_at, lots, completes, "
                        "note, entered_by) VALUES ($1::uuid, $2::uuid, $3, $4, $5, $6::jsonb, $7, $8, $9::uuid) "
                        "RETURNING id", str(org_id), str(ins["id"]), sh.quantity, avg, last,
                        _dumps([{"tax_lot_id": k, "quantity": str(q)} for k, q in lots]), bool(short),
                        f"block {row['id']} allocation at the average price", str(actor["id"]))
                    to_ins = "filled" if (complete_all or short) else "partially_filled"
                    if to_ins != ins["status"]:
                        await X._update(conn, ins["id"], {"status": to_ins})
                    xrefs.append(await X._event(
                        conn, org_id=org_id, instruction_id=ins["id"], action="fill", from_status=ins["status"],
                        to_status=to_ins, actor_id=actor["id"],
                        detail={"fill_id": str(fill_id), "quantity": str(sh.quantity), "price": str(avg),
                                "filled_at": last.isoformat(), "lots": len(lots), "block_id": str(row["id"]),
                                "allocation_id": str(a["id"]), "average_price": True,
                                "remainder_cancelled": bool(short)}))
                    if a["settlement_kind"] == "dvp":
                        await conn.execute("UPDATE public.instruction_settlements SET valid_to = now() WHERE "
                                           "instruction_id = $1::uuid AND valid_to IS NULL AND system_to IS NULL",
                                           str(ins["id"]))
                        st = await AU.write_settlement(conn, org_id=org_id, account=acct, instruction=ins,
                                                       actor_id=actor["id"], trade_date=trade_date)
                        fields["settlement_id"] = st["id"]
                else:
                    # nothing for this account in this block (the minimum allocation): its instruction follows the
                    # block's if-not-filled rule like any other remainder
                    if rule == "cancel":
                        if prior > 0:
                            await X._update(conn, ins["id"], {"status": "filled"})
                            to_ins = "filled"
                        else:
                            await X._update(conn, ins["id"], {"status": "cancelled", "cancelled_by": actor["id"],
                                                              "cancelled_at": datetime.now(timezone.utc),
                                                              "cancel_reason": f"block {row['id']}: {sh.zero_reason}"})
                            to_ins = "cancelled"
                        xrefs.append(await X._event(
                            conn, org_id=org_id, instruction_id=ins["id"],
                            action="cancel" if to_ins == "cancelled" else "fill", from_status=ins["status"],
                            to_status=to_ins, actor_id=actor["id"],
                            detail={"block_id": str(row["id"]), "allocation_id": str(a["id"]), "quantity": "0",
                                    "reason": sh.zero_reason}))
                new_a = await _update(conn, T_ALLOC, a["id"], fields)
                booked.append({"allocation_id": str(a["id"]), "account": a["account_number_masked"],
                               "final_quantity": str(sh.quantity), "zero_reason": sh.zero_reason,
                               "settlement_id": str(new_a["settlement_id"]) if new_a["settlement_id"] else None})
            handling = REMAINDER_LABELS[rule] if partial else None
            new = await _update(conn, T_BLK, row["id"], {"status": "allocated", "allocated_by": actor["id"],
                                                          "allocated_at": datetime.now(timezone.utc),
                                                          "average_price": avg, "remainder_handling": handling})
            todo = []
            if partial and rule == "ask":
                todo = await _todo(conn, org_id, set(map(str, row["maker_ids"])) | await _managers(conn, org_id),
                                   source=TODO_REMAINDER, related_type="trade_block", related_id=row["id"],
                                   title=f"Block partially filled ({filled} of {row['total_quantity']}): carry or "
                                         "cancel the remainder?",
                                   detail="Each account's unfilled remainder is still working on its instruction. "
                                          "Carry it into a new block, or cancel the instructions.",
                                   action_key=f"/portfolio/blocks/{row['id']}")
            refs.append(await _event(conn, org_id=org_id, block_id=row["id"], action="allocate",
                                     from_status=row["status"], to_status="allocated", actor_id=actor["id"],
                                     detail={"filled": str(filled), "average_price": str(avg),
                                             "trade_date": trade_date.isoformat(), "allocations": booked,
                                             "partial": partial, "remainder": handling, "notified": todo,
                                             "policy_version": policy["version_no"], "note": note}))
    await publish(pool, refs)
    await X.publish(pool, xrefs)
    return new


# ── Settlement ──────────────────────────────────────────────────────────────
async def _maybe_settle_block(conn, org_id, block: dict, actor_id) -> EventRef | None:
    allocs = await allocations_of(conn, block["id"])
    if block["status"] == "allocated" and allocs and all(a["settlement_status"] in ("settled", "cancelled")
                                                         for a in allocs if a["status"] != "cancelled"):
        await _update(conn, T_BLK, block["id"], {"status": "settled", "settled_at": datetime.now(timezone.utc)})
        return await _event(conn, org_id=org_id, block_id=block["id"], action="settlement", from_status="allocated",
                            to_status="settled", actor_id=actor_id, detail={"all_allocations": "settled or cancelled"})
    return None


async def set_settlement(pool, *, org_id, block_id, allocation_id, actor_id, status: str, note: str | None = None,
                         fail_reason: str | None = None, method: str | None = None,
                         reference: str | None = None) -> dict:
    """One allocation's settlement status, entered by a person. 'failed'
    needs the reason and opens a to-do with the allocation and the
    counterparty; 'settled' needs a note (manual) or the reference it was
    matched to (custodian data)."""
    if status not in ("affirmed", "settled", "failed", "pending"):
        raise BlockError("status is pending, affirmed, settled or failed")
    if status == "failed" and not (fail_reason or "").strip():
        raise BlockError("a failed settlement needs the reason")
    if status == "settled":
        method = method or "manual"
        if method not in SETTLEMENT_METHOD_LABELS:
            raise BlockError(f"method is one of {list(SETTLEMENT_METHOD_LABELS)}")
        if method == "manual" and not (note or "").strip():
            raise BlockError("a manual settlement confirmation needs a note saying how it was confirmed")
        if method == "custodian_data" and not (reference or "").strip():
            raise BlockError("matching to custodian data needs the reference it was matched to")
    refs = []
    async with pool.acquire() as conn:
        actor = await _human(conn, actor_id, org_id)
        async with conn.transaction():
            row = await load(conn, org_id, block_id, for_update=True)
            _need(row, ("allocated",), "record settlement on")
            a = await _allocation(conn, row["id"], allocation_id)
            if a is None:
                raise BlockNotFound("allocation not found in this block")
            cur = a["settlement_status"]
            if status not in SETTLEMENT_EDGES.get(cur, ()):
                raise BlockConflict(f"settlement {SETTLEMENT_STATUS_LABELS.get(cur, cur).lower()} cannot become "
                                    f"{SETTLEMENT_STATUS_LABELS[status].lower()}")
            fields = {"settlement_status": status, "settlement_updated_by": actor["id"],
                      "settlement_updated_at": datetime.now(timezone.utc), "settlement_note": note}
            if status == "failed":
                fields["fail_reason"] = fail_reason.strip()
            if status == "settled":
                fields["settlement_method"] = method
                fields["settlement_reference"] = (reference or "").strip() or None
            await _update(conn, T_ALLOC, a["id"], fields)
            notified = []
            if status == "failed":
                counterparty = (f"{row['executor_name'] or X.EXECUTOR_LABELS[row['executor_type']]} → "
                                f"{a['receiving_custodian']}")
                notified = await _todo(
                    conn, org_id, set(map(str, row["maker_ids"])) | await _managers(conn, org_id),
                    source=TODO_FAIL, related_type="block_allocation", related_id=a["id"],
                    title=f"Settlement failed: {a['account_number_masked']} ({a['final_quantity']} shares)",
                    detail=f"Block {row['id']}, allocation {a['id']}; counterparty {counterparty}. Reason: "
                           f"{fail_reason.strip()}. Re-instruct the settlement and record its new status.",
                    action_key=f"/portfolio/blocks/{row['id']}")
                refs.append(await _event(conn, org_id=org_id, block_id=row["id"], action="settlement_fail",
                                         from_status=row["status"], to_status=row["status"], actor_id=actor["id"],
                                         allocation_id=a["id"], detail={"reason": fail_reason.strip(),
                                                                        "counterparty": counterparty,
                                                                        "notified": notified}))
            else:
                refs.append(await _event(conn, org_id=org_id, block_id=row["id"], action="settlement",
                                         from_status=row["status"], to_status=row["status"], actor_id=actor["id"],
                                         allocation_id=a["id"], detail={"from": cur, "to": status, "method": method,
                                                                        "reference": reference, "note": note}))
            done = await _maybe_settle_block(conn, org_id, row, actor["id"])
            if done:
                refs.append(done)
            new = await load(conn, org_id, block_id)
    await publish(pool, refs)
    return new


async def match_settlement(pool, *, org_id, block_id, allocation_id, actor_id, record_kind: str, record_id,
                           note: str | None = None) -> dict:
    """Settled, matched from the custodian feed (a portfolio.transactions row
    of the account) or a held-away import (a rep7 held_away_records
    transaction): same account, security, side and quantity."""
    if record_kind not in ("transaction", "held_away_record"):
        raise BlockError("record kind is transaction or held_away_record")
    async with pool.acquire() as conn:
        await _human(conn, actor_id, org_id)
        row = await load(conn, org_id, block_id)
        a = await _allocation(conn, row["id"], allocation_id)
        if a is None:
            raise BlockNotFound("allocation not found in this block")
        rid = _uuid(record_id, "record")
        if record_kind == "transaction":
            rec = await conn.fetchrow(
                "SELECT x.id, x.transaction_type_code AS side, x.quantity, p.account_id, a.global_security_id "
                "FROM portfolio.transactions x JOIN portfolio.positions p ON p.id = x.position_id "
                "JOIN portfolio.assets a ON a.id = p.asset_id AND a.system_to IS NULL "
                "WHERE x.id = $1::uuid AND x.org_id = $2::uuid AND x.system_to IS NULL", rid, str(org_id))
        else:
            rec = await conn.fetchrow(
                """SELECT r.id, t.transaction_type_code AS side, t.quantity, r.account_id, a.global_security_id
                   FROM public.held_away_records r
                   JOIN portfolio.transactions t ON t.id = r.record_id AND t.system_to IS NULL
                   JOIN portfolio.positions p ON p.id = t.position_id
                   JOIN portfolio.assets a ON a.id = p.asset_id AND a.system_to IS NULL
                   WHERE r.id = $1::uuid AND r.org_id = $2::uuid AND r.system_to IS NULL
                     AND r.record_type = 'transaction'""", rid, str(org_id))
    if rec is None:
        raise BlockNotFound(f"{record_kind.replace('_', ' ')} not found")
    if str(rec["account_id"]) != str(a["account_id"]):
        raise BlockConflict("that record is another account's")
    if str(rec["global_security_id"]) != str(row["global_security_id"]) or (rec["side"] or "") != row["side"]:
        raise BlockConflict("that record is a different security or side")
    if rec["quantity"] is None or abs(_dec(rec["quantity"])) != _dec(a["final_quantity"]):
        raise BlockConflict(f"that record is {rec['quantity']} shares; the allocation is {a['final_quantity']}")
    return await set_settlement(pool, org_id=org_id, block_id=block_id, allocation_id=allocation_id,
                                actor_id=actor_id, status="settled", method="custodian_data",
                                reference=f"{record_kind}:{rec['id']}", note=note or "matched to custodian data")


async def close(pool, *, org_id, block_id, actor_id, note: str | None = None) -> dict:
    """settled → closed. Refused, naming them, while any allocation is not
    settled or cancelled."""
    async with pool.acquire() as conn:
        actor = await _human(conn, actor_id, org_id)
        async with conn.transaction():
            row = await load(conn, org_id, block_id, for_update=True)
            open_ = [f"{a['account_number_masked']} ({SETTLEMENT_STATUS_LABELS.get(a['settlement_status'], 'not booked')})"
                     for a in await allocations_of(conn, row["id"])
                     if a["status"] != "cancelled" and a["settlement_status"] not in ("settled", "cancelled")]
            if open_:
                raise BlockConflict("the block closes only when every allocation is settled or cancelled — still "
                                    "open: " + ", ".join(open_))
            _need(row, ("settled",), "close")
            new = await _update(conn, T_BLK, row["id"], {"status": "closed", "closed_by": actor["id"],
                                                          "closed_at": datetime.now(timezone.utc)})
            ref = await _event(conn, org_id=org_id, block_id=row["id"], action="close", from_status="settled",
                               to_status="closed", actor_id=actor["id"], detail={"note": note})
    await publish(pool, [ref])
    return new


async def cancel(pool, *, org_id, block_id, actor_id, reason: str) -> dict:
    """draft / pending / approved / sent with no fills → cancelled. The
    allocations are cancelled; each account instruction stays as it is (sent
    ones keep working on their own route) — never silently cancelled."""
    if not (reason or "").strip():
        raise BlockError("a cancellation needs a reason")
    async with pool.acquire() as conn:
        actor = await _human(conn, actor_id, org_id)
        row = await load(conn, org_id, block_id)
    _need(row, ("draft", "pending_approval", "approved", "sent"), "cancel")
    if row["status"] == "pending_approval" and row["approval_task_id"]:
        try:
            await HT.cancel_task(pool, org_id=org_id, task_id=row["approval_task_id"], actor_id=actor["id"],
                                 reason=f"block cancelled: {reason}")
        except HT.HumanTaskConflict:
            pass
    return await _cancel_tx(pool, org_id, block_id, actor["id"], reason.strip(), "cancel")


async def _cancel_tx(pool, org_id, block_id, actor_id, reason: str, action: str) -> dict:
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await load(conn, org_id, block_id, for_update=True)
            _need(row, ("draft", "pending_approval", "approved", "sent"), "cancel")
            if await fills_of(conn, row["id"]):
                raise BlockConflict("a block with fills is allocated, never cancelled")
            for a in await allocations_of(conn, row["id"]):
                if a["status"] in ("planned", "fixed"):
                    await _update(conn, T_ALLOC, a["id"], {"status": "cancelled"})
            new = await _update(conn, T_BLK, row["id"], {"status": "cancelled", "cancelled_by": actor_id,
                                                          "cancelled_at": datetime.now(timezone.utc),
                                                          "cancel_reason": reason})
            ref = await _event(conn, org_id=org_id, block_id=row["id"], action=action, from_status=row["status"],
                               to_status="cancelled", actor_id=actor_id, detail={"reason": reason})
    await publish(pool, [ref])
    return new


# ── Corrections (reason + a second person; the original kept) ───────────────
async def request_correction(pool, *, org_id, block_id, actor_id, changes: list[dict], reason: str) -> dict:
    """``changes`` = [{allocation_id, quantity}] — the new final quantities.
    The block total never changes (what was filled was filled); each quantity
    is in its account's unit, within its fixed allocation, zero or at least the
    policy minimum."""
    if not (reason or "").strip():
        raise BlockError("a correction needs a reason")
    if not changes:
        raise BlockError("name the allocations to correct")
    async with pool.acquire() as conn:
        actor = await _human(conn, actor_id, org_id)
        row = await load(conn, org_id, block_id)
        _need(row, ("allocated",), "correct the allocation of")
        policy = dict(await conn.fetchrow(f"SELECT * FROM {T_POL} WHERE id = $1::uuid", row["policy_id"]))
        allocs = {str(a["id"]): a for a in await allocations_of(conn, row["id"]) if a["status"] == "allocated"}
        if await conn.fetchval(f"SELECT 1 FROM {T_CORR} WHERE block_id = $1 AND status = 'draft' AND system_to IS NULL",
                               row["id"]):
            raise BlockConflict("a correction for this block is already awaiting approval — decide it first")
        out, before_sum, after_sum = [], Decimal(0), Decimal(0)
        seen = set()
        for c in changes:
            aid = _uuid(c.get("allocation_id"), "allocation")
            if aid in seen:
                raise BlockError("an allocation is listed twice")
            seen.add(aid)
            a = allocs.get(aid)
            if a is None:
                raise BlockNotFound("allocation not found in this block")
            q = c.get("quantity")
            if not isinstance(q, Decimal) or q < 0:
                raise BlockError("each corrected quantity is a decimal of zero or more")
            if a["settlement_status"] == "settled":
                raise BlockConflict(f"{a['account_number_masked']} has settled — a settled allocation is corrected by "
                                    "a new trade, not by re-allocation")
            if not BA.on_unit(q, _dec(a["quantity_unit"])):
                raise BlockError(f"{a['account_number_masked']} trades whole shares only")
            if q > _dec(a["allocated_quantity"]):
                raise BlockError(f"{a['account_number_masked']}: {q} is more than its fixed allocation "
                                 f"{a['allocated_quantity']}")
            if 0 < q < _dec(policy["minimum_allocation"]):
                raise BlockError(f"{a['account_number_masked']}: {q} is below the policy's minimum allocation")
            if _dec(a["final_quantity"]) == 0 and q > 0:
                raise BlockConflict(f"{a['account_number_masked']} received nothing in this block (its instruction "
                                    "followed the remainder rule) — it cannot take shares in a correction")
            before_sum += _dec(a["final_quantity"])
            after_sum += q
            out.append({"allocation_id": aid, "account": a["account_number_masked"],
                        "before": str(a["final_quantity"]), "after": str(q)})
        if before_sum != after_sum:
            raise BlockError(f"a correction moves shares between allocations; the total must stay {before_sum} "
                             f"(it would be {after_sum})")
    cid = uuid.uuid4()
    task = await HT.open_task(
        pool, org_id=org_id, kind=TASK_CORRECTION, subject_type=SUBJECT_CORRECTION, subject_id=cid,
        maker_id=actor["id"], eligible_permission=APPROVE, title=f"Approve allocation correction on block {row['id']}",
        payload={"block_id": str(row["id"]), "changes": out, "reason": reason.strip()},
        action_path=f"/portfolio/blocks/{row['id']}")
    try:
        async with pool.acquire() as conn:
            async with conn.transaction():
                crow = await conn.fetchrow(
                    f"INSERT INTO {T_CORR} (id, org_id, block_id, changes, reason, status, maker_id, approval_task_id) "
                    "VALUES ($1, $2::uuid, $3::uuid, $4::jsonb, $5, 'draft', $6::uuid, $7::uuid) RETURNING *",
                    cid, str(org_id), str(row["id"]), _dumps(out), reason.strip(), str(actor["id"]), task["id"])
                ref = await _event(conn, org_id=org_id, block_id=row["id"], action="correction_request",
                                   from_status=row["status"], to_status=row["status"], actor_id=actor["id"],
                                   detail={"correction_id": str(cid), "changes": out, "reason": reason.strip(),
                                           "task_id": task["id"]})
    except Exception:
        await _withdraw(pool, task["id"], "the correction could not be recorded")
        raise
    await publish(pool, [ref])
    return {**correction_out(dict(crow)), "task": task}


def correction_out(r: dict | None) -> dict | None:
    if not r:
        return None
    return {"id": str(r["id"]), "block_id": str(r["block_id"]), "changes": _loads(r["changes"]) or [],
            "reason": r["reason"], "status": r["status"], "maker_id": str(r["maker_id"]),
            "approved_by": str(r["approved_by"]) if r["approved_by"] else None,
            "approval_task_id": str(r["approval_task_id"]) if r["approval_task_id"] else None,
            "decided_at": _iso(r["decided_at"]), "decision_note": r["decision_note"], "created_at": _iso(r["created_at"])}


async def decide_correction(pool, *, org_id, actor_id, correction_id, outcome: str, note: str | None = None) -> dict:
    return correction_out(await _decide_change(pool, table=T_CORR, subject=SUBJECT_CORRECTION, org_id=org_id,
                                               actor_id=actor_id, row_id=correction_id, outcome=outcome, note=note))


async def _apply_correction(pool, task: dict, outcome: str, actor_id, note) -> dict | None:
    org_id = task["org_id"]
    refs, xrefs = [], []
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(f"SELECT * FROM {T_CORR} WHERE id = $1::uuid AND org_id = $2::uuid "
                                      "AND system_to IS NULL FOR UPDATE", task["subject_id"], str(org_id))
            if row is None or row["status"] != "draft":
                return correction_out(dict(row)) if row else None
            checker = uuid.UUID(str(actor_id))
            if checker == row["maker_id"]:
                raise MakerCheckerViolation("maker-checker: the person who asked for a correction cannot approve it")
            status = "approved" if outcome == "approve" else "rejected"
            await _archive(conn, T_CORR, row["id"])
            new = dict(await conn.fetchrow(
                f"UPDATE {T_CORR} SET status = $2, approved_by = $3, decided_at = now(), decision_note = $4, "
                "system_from = now() WHERE id = $1 AND system_to IS NULL RETURNING *", row["id"], status, checker,
                note))
            blk = await load(conn, org_id, row["block_id"], for_update=True)
            if status == "approved":
                if blk["status"] != "allocated":
                    raise HT.HumanTaskConflict(f"the block is {blk['status']} — the correction no longer applies")
                for ch in _loads(row["changes"]) or []:
                    a = await conn.fetchrow(f"SELECT * FROM {T_ALLOC} WHERE id = $1::uuid AND system_to IS NULL",
                                            ch["allocation_id"])
                    if a is None or _dec(a["final_quantity"]) != Decimal(ch["before"]):
                        raise HT.HumanTaskConflict("an allocation changed since the correction was asked for — ask "
                                                   "again")
                    if a["settlement_status"] == "settled":
                        raise HT.HumanTaskConflict("an allocation in the correction has settled since it was asked for")
                    await _update(conn, T_ALLOC, a["id"], {"final_quantity": Decimal(ch["after"]),
                                                           "correction_id": row["id"]})
                    ins = await X.load(conn, org_id, a["instruction_id"])
                    xrefs.append(await X._event(conn, org_id=org_id, instruction_id=ins["id"], action="edit",
                                                from_status=ins["status"], to_status=ins["status"], actor_id=checker,
                                                detail={"block_correction": str(row["id"]),
                                                        "block_id": str(row["block_id"]),
                                                        "allocation_id": str(a["id"]), "before": ch["before"],
                                                        "after": ch["after"], "reason": row["reason"]}))
            refs.append(await _event(conn, org_id=org_id, block_id=row["block_id"],
                                     action="correction_approve" if status == "approved" else "correction_reject",
                                     from_status=blk["status"], to_status=blk["status"], actor_id=checker,
                                     detail={"correction_id": str(row["id"]), "note": note,
                                             "changes": _loads(row["changes"])}))
    await publish(pool, refs)
    await X.publish(pool, xrefs)
    return correction_out(new)


async def _on_correction(pool, task, outcome, actor_id, note):
    return await _apply_correction(pool, task, outcome, actor_id, note)


async def _correction_precheck(pool, task, outcome, actor_id):
    if outcome != "approve":
        return
    async with pool.acquire() as conn:
        row = await conn.fetchrow(f"SELECT c.status, b.status AS block_status FROM {T_CORR} c JOIN {T_BLK} b "
                                  "ON b.id = c.block_id AND b.system_to IS NULL WHERE c.id = $1::uuid "
                                  "AND c.system_to IS NULL", task["subject_id"])
    if row is not None and row["block_status"] != "allocated":
        raise HT.HumanTaskConflict(f"the block is {row['block_status']} — the correction no longer applies")


HT.register_kind(TASK_CORRECTION, label="Allocation correction", outcomes=("approve", "reject"), maker_checker=True,
                 on_complete=_on_correction)
HT.register_precheck(TASK_CORRECTION, _correction_precheck)


# ── Charges (commissions / fees, adviser-entered) ───────────────────────────
async def add_charge(pool, *, org_id, block_id, actor_id, kind: str, amount: Decimal, currency_code: str = "USD",
                     payee: str | None = None, trade_away: bool = False, note: str | None = None) -> dict:
    if kind not in CHARGE_KIND_LABELS:
        raise BlockError(f"kind is one of {list(CHARGE_KIND_LABELS)}")
    if not isinstance(amount, Decimal) or amount < 0:
        raise BlockError("the amount is a decimal of zero or more")
    async with pool.acquire() as conn:
        actor = await _human(conn, actor_id, org_id)
        async with conn.transaction():
            row = await load(conn, org_id, block_id)
            if row["status"] in ("draft", "pending_approval", "cancelled", "closed"):
                raise BlockConflict("charges are entered once the block is approved and before it is closed")
            ch = await conn.fetchrow(
                f"INSERT INTO {T_CHG} (org_id, block_id, kind, amount, currency_code, payee, trade_away, note, "
                "entered_by) VALUES ($1::uuid, $2::uuid, $3, $4, $5, $6, $7, $8, $9::uuid) RETURNING *",
                str(org_id), str(row["id"]), kind, amount, currency_code.upper(), payee, bool(trade_away), note,
                str(actor["id"]))
            ref = await _event(conn, org_id=org_id, block_id=row["id"], action="charge", from_status=row["status"],
                               to_status=row["status"], actor_id=actor["id"],
                               detail={"charge_id": str(ch["id"]), "kind": kind, "amount": str(amount),
                                       "trade_away": bool(trade_away)})
    await publish(pool, [ref])
    return charge_out(dict(ch))


async def close_charge(pool, *, org_id, block_id, charge_id, actor_id, reason: str) -> dict:
    if not (reason or "").strip():
        raise BlockError("closing a charge needs a reason")
    async with pool.acquire() as conn:
        actor = await _human(conn, actor_id, org_id)
        async with conn.transaction():
            row = await load(conn, org_id, block_id)
            ch = await conn.fetchrow(f"UPDATE {T_CHG} SET valid_to = now(), closed_reason = $3 WHERE id = $1::uuid "
                                     "AND block_id = $2::uuid AND valid_to IS NULL AND system_to IS NULL RETURNING *",
                                     _uuid(charge_id, "charge"), str(row["id"]), reason.strip())
            if ch is None:
                raise BlockNotFound("charge not found (or already closed)")
            ref = await _event(conn, org_id=org_id, block_id=row["id"], action="charge_close",
                               from_status=row["status"], to_status=row["status"], actor_id=actor["id"],
                               detail={"charge_id": str(ch["id"]), "reason": reason.strip()})
    await publish(pool, [ref])
    return charge_out(dict(ch))


def charge_out(r: dict) -> dict:
    return {"id": str(r["id"]), "kind": r["kind"], "amount": str(r["amount"]), "currency_code": r["currency_code"],
            "payee": r["payee"], "trade_away": r["trade_away"], "note": r["note"], "closed_reason": r["closed_reason"],
            "valid_to": _iso(r["valid_to"]), "created_at": _iso(r["created_at"])}


# ── Order execution sheets (manual route, ALWAYS offered) ───────────────────
def _sec_out(sec: dict) -> dict:
    ids = sec.get("identifiers") or {}
    return {"name": sec["name"], "ticker": ids.get("ticker"), "cusip": ids.get("cusip")}


async def _sheet_dict(conn, org_id, block_id) -> dict:
    d = await detail(conn, org_id, block_id)
    ea = d.get("execution_account")
    lot_lines = []
    for a in d["allocations"]:
        for lot in a.get("lots") or []:
            lot_lines.append(f"{a['account_number_masked']} delivers its lot acquired "
                             f"{lot.get('acquired_date') or 'date not recorded'}: {lot['quantity']} shares")
    return {**d, "kind_label": "Block", "side_label": X.SIDE_LABELS[d["side"]],
            "if_not_filled_label": X.IF_NOT_FILLED_LABELS[d["if_not_filled"]],
            "executor_label": X.EXECUTOR_LABELS[d["executor_type"]], "execution_account": ea,
            "policy_version": (d.get("policy") or {}).get("version_no"), "lot_lines": lot_lines}


async def block_sheet(pool, *, org_id, block_id, actor_id) -> tuple[bytes, str]:
    from services.order_sheet import build_sheet

    async with pool.acquire() as conn:
        actor = await _human(conn, actor_id, org_id)
        d = await _sheet_dict(conn, org_id, block_id)
        if d["status"] in ("cancelled",):
            raise BlockConflict("a cancelled block has no order execution sheet")
        org_name = await conn.fetchval("SELECT name FROM organizations WHERE id = $1::uuid", str(org_id))
    pdf = build_sheet(d, org_name=org_name or "", prepared_by=X._who_name(actor))
    async with pool.acquire() as conn:
        async with conn.transaction():
            ref = await _event(conn, org_id=org_id, block_id=d["id"], action="sheet", from_status=d["status"],
                               to_status=d["status"], actor_id=actor["id"],
                               detail={"route": d["route"], "automated_route_available": d["automated_route_available"],
                                       "manual_reason": d["manual_reason"]})
    await publish(pool, [ref])
    return pdf, f"order-execution-sheet-{d['id'][:8]}.pdf"


async def instruction_sheet_dict(conn, org_id, instruction_id, today: date | None = None) -> dict:
    """The same sheet for ONE account instruction (broker / desk / trader): a
    single allocation, so manual is offered on every trade the platform
    proposes."""
    today = today or date.today()
    d = await X.detail(conn, org_id, instruction_id)
    if d["executor_type"] not in BLOCK_EXECUTORS:
        raise BlockError(f"an order execution sheet is for a broker, trader or custodian desk; this instruction's "
                         f"executor is {X.EXECUTOR_LABELS[d['executor_type']].lower()} (use its letter of instruction)")
    if d["quantity"] is None:
        raise BlockError("a notional instruction has no share quantity for an order execution sheet")
    acct = await load_account(conn, org_id, d["account_id"])
    e = AU.effective(acct)
    dvp = e["custody"] == "other_custodian"
    if d.get("reference_price"):
        ref = {"price": d["reference_price"], "source": "adviser", "provider": "adviser",
               "as_of": (d.get("reference_at") or "")[:10] or None}
    else:
        r = await reference_price(conn, org_id, d["global_security_id"], typed=None, typed_at=None, today=today)
        ref = {**r, "price": _num(r["price"]), "as_of": _iso(r["as_of"])}
    st = d.get("settlement")
    alloc = {"account_number_masked": d["account"]["account_number_masked"], "allocated_quantity": d["quantity"],
             "final_quantity": None, "status": "fixed", "settlement_kind": "dvp" if dvp else "integrated",
             "receiving_custodian": ((acct.get("receiving") or {}).get("custodian_name") if dvp else
                                     acct.get("custodian_code")) or "",
             "settlement": st, "receiving": acct.get("receiving") if dvp else None}
    executing = ("Executed directly for this account (no block)" if d["executor_type"] != "custodian_desk" else
                 f"Custodian trading desk — {acct.get('custodian_code')}")
    lot_lines = [f"{alloc['account_number_masked']} delivers its lot acquired {x.get('acquired_date') or 'date not '
                 'recorded'}: {x['quantity']} shares" for x in d.get("lots") or []]
    return {"id": d["id"], "kind_label": "Instruction", "security": d["security"], "side": d["side"],
            "side_label": X.SIDE_LABELS[d["side"]], "total_quantity": d["quantity"], "price_kind": d["price_kind"],
            "limit_price": d["limit_price"], "range_low": d["range_low"], "range_high": d["range_high"],
            "reference": ref, "execute_by": d["execute_by"],
            "if_not_filled_label": X.IF_NOT_FILLED_LABELS[d["if_not_filled"]],
            "executor_type": d["executor_type"], "executor_label": X.EXECUTOR_LABELS[d["executor_type"]],
            "executor_name": d["executor_name"], "executor_contact": d["executor_contact"],
            "executing_text": executing, "allocations": [alloc], "lot_lines": lot_lines, "account": acct}


async def instruction_sheet(pool, *, org_id, instruction_id, actor_id, manual_reason: str | None = None
                            ) -> tuple[bytes, str]:
    """PDF on demand for one instruction. When an automated route IS available
    for its account, the adviser says why they choose manual; the choice (and
    the reason) is recorded on the instruction's timeline."""
    from services.order_sheet import build_sheet

    async with pool.acquire() as conn:
        actor = await _human(conn, actor_id, org_id)
        d = await instruction_sheet_dict(conn, org_id, instruction_id)
        auto, auto_note = await automated_route_available(conn, d["account"])
        if auto and not (manual_reason or "").strip():
            raise BlockConflict(f"an automated route is available for this account ({auto_note}) — say why you choose "
                                "the manual route; that choice is recorded")
        org_name = await conn.fetchval("SELECT name FROM organizations WHERE id = $1::uuid", str(org_id))
        status = (await X.load(conn, org_id, instruction_id))["status"]
    pdf = build_sheet(d, org_name=org_name or "", prepared_by=X._who_name(actor))
    async with pool.acquire() as conn:
        async with conn.transaction():
            ref = await X._event(conn, org_id=org_id, instruction_id=d["id"], action="letter", from_status=status,
                                 to_status=status, actor_id=actor["id"],
                                 detail={"document": "order_execution_sheet", "route": "manual",
                                         "automated_route_available": bool(auto), "automated_route": auto_note,
                                         "manual_reason": (manual_reason or "").strip() or None})
    await X.publish(pool, [ref])
    return pdf, f"order-execution-sheet-{d['id'][:8]}.pdf"


# ── Lists, detail, actions ──────────────────────────────────────────────────
def block_out(r: dict) -> dict:
    return {
        "id": str(r["id"]), "status": r["status"], "global_security_id": str(r["global_security_id"]),
        "side": r["side"], "total_quantity": _num(r["total_quantity"]), "executor_type": r["executor_type"],
        "executor_name": r["executor_name"], "executor_contact": r["executor_contact"],
        "execution_account_id": str(r["execution_account_id"]) if r["execution_account_id"] else None,
        "route": r["route"], "automated_route_available": r["automated_route_available"],
        "manual_reason": r["manual_reason"], "price_kind": r["price_kind"], "limit_price": _num(r["limit_price"]),
        "range_low": _num(r["range_low"]), "range_high": _num(r["range_high"]),
        "reference": {"price": _num(r["reference_price"]), "source": r["reference_source"],
                      "provider": r["reference_provider"], "as_of": _iso(r["reference_as_of"])},
        "execute_by": _iso(r["execute_by"]), "if_not_filled": r["if_not_filled"],
        "policy_id": str(r["policy_id"]), "cross_flag": r["cross_flag"], "cross_reason": r["cross_reason"],
        "firm_contact": r["firm_contact"], "notes": r["notes"], "maker_id": str(r["maker_id"]),
        "maker_ids": [str(m) for m in r["maker_ids"]],
        "approval_task_id": str(r["approval_task_id"]) if r["approval_task_id"] else None,
        "approved_by": str(r["approved_by"]) if r["approved_by"] else None, "approved_at": _iso(r["approved_at"]),
        "sent_channel": r["sent_channel"], "sent_at": _iso(r["sent_at"]),
        "filled_quantity": _num(r["filled_quantity"]), "average_price": _num(r["average_price"]),
        "allocated_at": _iso(r["allocated_at"]), "remainder_handling": r["remainder_handling"],
        "settled_at": _iso(r["settled_at"]), "closed_at": _iso(r["closed_at"]), "cancel_reason": r["cancel_reason"],
        "created_at": _iso(r["created_at"]),
    }


def allocation_out(a: dict, settlement: dict | None = None, lots: list | None = None) -> dict:
    return {"id": str(a["id"]), "instruction_id": str(a["instruction_id"]), "account_id": str(a["account_id"]),
            "account_number_masked": a["account_number_masked"], "custodian_code": a.get("custodian_code"),
            "requested_quantity": _num(a["requested_quantity"]), "allocated_quantity": _num(a["allocated_quantity"]),
            "quantity_unit": _num(a["quantity_unit"]), "fractional": _dec(a["quantity_unit"]) < 1,
            "final_quantity": _num(a["final_quantity"]), "average_price": _num(a["average_price"]),
            "zero_reason": a["zero_reason"], "settlement_kind": a["settlement_kind"],
            "receiving_custodian": a["receiving_custodian"], "settlement_id": str(a["settlement_id"])
            if a["settlement_id"] else None, "settlement": settlement, "settlement_status": a["settlement_status"],
            "settlement_method": a["settlement_method"], "settlement_reference": a["settlement_reference"],
            "settlement_note": a["settlement_note"], "fail_reason": a["fail_reason"],
            "correction_id": str(a["correction_id"]) if a["correction_id"] else None, "status": a["status"],
            "lots": lots or []}


async def list_blocks(conn, org_id, *, status: str | None = None, limit: int = 200) -> list[dict]:
    args: list = [str(org_id)]
    sql = (f"SELECT b.*, s.name AS security_name, (SELECT count(*) FROM {T_ALLOC} al WHERE al.block_id = b.id "
           "AND al.system_to IS NULL AND al.status <> 'cancelled') AS accounts "
           f"FROM {T_BLK} b JOIN portfolio.securities_global s ON s.id = b.global_security_id "
           "WHERE b.org_id = $1::uuid AND b.system_to IS NULL")
    if status:
        if status == "open":
            args.append(list(LIVE_STATUSES))
            sql += f" AND b.status = ANY(${len(args)}::text[])"
        elif status in STATUS_LABELS:
            args.append(status)
            sql += f" AND b.status = ${len(args)}"
        else:
            raise BlockError(f"unknown status {status!r}")
    args.append(max(1, min(int(limit), 1000)))
    sql += f" ORDER BY b.execute_by, b.created_at DESC LIMIT ${len(args)}"
    return [{**block_out(dict(r)), "security_name": r["security_name"], "accounts": int(r["accounts"])}
            for r in await conn.fetch(sql, *args)]


async def detail(conn, org_id, block_id) -> dict:
    row = await load(conn, org_id, block_id)
    sec = await X._security(conn, row["global_security_id"])
    out = block_out(row)
    out["security"] = _sec_out(sec)
    pol = await conn.fetchrow(f"SELECT * FROM {T_POL} WHERE id = $1::uuid", row["policy_id"])
    out["policy"] = policy_out(dict(pol)) if pol else None
    if row["execution_account_id"]:
        ea = await conn.fetchrow(f"SELECT * FROM {T_EA} WHERE id = $1::uuid AND system_to IS NULL",
                                 row["execution_account_id"])
        out["execution_account"] = execution_account_out(dict(ea)) if ea else None
    else:
        out["execution_account"] = None
    allocs = []
    for a in await allocations_of(conn, row["id"]):
        st = None
        if a["settlement_id"]:
            srow = await conn.fetchrow("SELECT * FROM public.instruction_settlements WHERE id = $1::uuid",
                                       a["settlement_id"])
            st = AU.settlement_out(dict(srow)) if srow else None
        elif a["settlement_kind"] == "dvp":
            acct = await load_account(conn, org_id, a["account_id"])
            rc = acct.get("receiving") or {}
            st = {"receiving_custodian_name": rc.get("custodian_name"),
                  "receiving_participant_number": rc.get("participant_number"),
                  "receiving_agent_bank": rc.get("agent_bank"), "receiving_account_masked": rc.get("account_masked"),
                  "settlement_date": None, "calendar_note": AU.CALENDAR_NOTE, "expected": True}
        lots = []
        if row["side"] == "sell":
            lots = [{"tax_lot_id": str(x["tax_lot_id"]), "quantity": str(x["quantity"]),
                     "acquired_date": _iso(x["acquired_date"])} for x in await X._lots(conn, a["instruction_id"])]
        allocs.append(allocation_out(a, st, lots))
    out["allocations"] = allocs
    fills = await fills_of(conn, row["id"])
    events = [dict(e) for e in await conn.fetch(f"SELECT * FROM {T_EVT} WHERE block_id = $1 AND org_id = $2::uuid "
                                                "ORDER BY created_at, id", row["id"], str(org_id))]
    tasks = await HT.list_tasks(conn, org_id, kind=TASK_BLOCK, subject_type=SUBJECT_BLOCK, subject_id=row["id"],
                                limit=20)
    corrections = [correction_out(dict(c)) for c in await conn.fetch(
        f"SELECT * FROM {T_CORR} WHERE block_id = $1 AND system_to IS NULL ORDER BY created_at", row["id"])]
    ctasks = []
    for c in corrections:
        if c["approval_task_id"]:
            try:
                ctasks.append(await HT.get_task(conn, org_id, c["approval_task_id"]))
            except Exception:  # noqa: BLE001
                pass
    charges = [charge_out(dict(c)) for c in await conn.fetch(
        f"SELECT * FROM {T_CHG} WHERE block_id = $1 AND system_to IS NULL ORDER BY created_at", row["id"])]
    names = await X._names(conn, [e["actor_id"] for e in events] + [f["entered_by"] for f in fills]
                           + [row["maker_id"], row["approved_by"]] + [t["completed_by"] for t in tasks])
    out["fills"] = [{"id": str(f["id"]), "quantity": str(f["quantity"]), "price": str(f["price"]),
                     "filled_at": _iso(f["filled_at"]), "venue": f["venue"], "note": f["note"],
                     "entered_by_name": names.get(str(f["entered_by"]))} for f in fills]
    out["timeline"] = [{"id": str(e["id"]), "action": e["action"], "from_status": e["from_status"],
                        "to_status": e["to_status"], "actor_name": names.get(str(e["actor_id"])) if e["actor_id"]
                        else "System (execute-by)", "allocation_id": str(e["allocation_id"]) if e["allocation_id"]
                        else None, "detail": _loads(e["detail"]) or {}, "at": _iso(e["created_at"])} for e in events]
    for t in tasks:
        t["completed_by_name"] = names.get(t["completed_by"] or "")
    out["tasks"] = tasks
    out["correction_tasks"] = ctasks
    out["corrections"] = corrections
    out["charges"] = charges
    out["maker_name"] = names.get(str(row["maker_id"]))
    out["history_versions"] = int(await conn.fetchval(f"SELECT count(*) FROM {T_BLK} WHERE archived_from_id = $1",
                                                      row["id"]))
    out["allocation_history"] = [
        {"allocation_id": str(h["archived_from_id"]), "final_quantity": _num(h["final_quantity"]),
         "status": h["status"], "settlement_status": h["settlement_status"], "correction_id":
         str(h["correction_id"]) if h["correction_id"] else None, "until": _iso(h["system_to"])}
        for h in await conn.fetch(f"SELECT * FROM {T_ALLOC} WHERE block_id = $1 AND system_to IS NOT NULL "
                                  "ORDER BY system_to", row["id"])]
    return out


def vocabularies() -> dict:
    def pairs(d):
        return [{"key": k, "label": v} for k, v in d.items()]

    return {
        "statuses": pairs(STATUS_LABELS), "open_statuses": list(OPEN_STATUSES),
        "allocation_statuses": pairs(ALLOCATION_STATUS_LABELS),
        "settlement_statuses": pairs(SETTLEMENT_STATUS_LABELS), "settlement_kinds": pairs(SETTLEMENT_KIND_LABELS),
        "settlement_methods": pairs(SETTLEMENT_METHOD_LABELS), "methods": pairs(METHOD_LABELS),
        "rounding_rules": pairs(ROUNDING_LABELS), "partial_fill_rules": pairs(PARTIAL_LABELS),
        "policy_statuses": pairs(POLICY_STATUS_LABELS), "correction_statuses": pairs(CORRECTION_STATUS_LABELS),
        "charge_kinds": pairs(CHARGE_KIND_LABELS), "reference_sources": pairs(REFERENCE_SOURCE_LABELS),
        "routes": pairs(RT.ROUTE_LABELS), "routers": RT.routers(), "default_route": RT.DEFAULT_ROUTE,
        "executor_types": [{"key": k, "label": X.EXECUTOR_LABELS[k]} for k in BLOCK_EXECUTORS],
        "sides": pairs(X.SIDE_LABELS), "price_kinds": pairs(X.PRICE_KIND_LABELS),
        "if_not_filled": pairs(X.IF_NOT_FILLED_LABELS), "sent_channels": pairs(X.SENT_CHANNEL_LABELS),
        "actions": pairs(ACTION_LABELS), "remainder": pairs(REMAINDER_LABELS),
        "execution_account_statuses": pairs(EXECUTION_ACCOUNT_STATUS_LABELS),
        "compliance_notes": list(COMPLIANCE_NOTES), "price_rounding": BA.PRICE_ROUNDING_NOTE,
        "calendar_note": AU.CALENDAR_NOTE, "editable": [], "inline_editable": [],
    }


def actions_for(d: dict, *, can_write: bool, can_approve: bool, user_id, task_open: bool) -> list[str]:
    """The SERVER's list of actions this caller may take on this block now.
    The screen renders a control only for an action listed here."""
    s = d["status"]
    makers = {str(m) for m in d.get("maker_ids") or []}
    acts: list[str] = []
    if can_write:
        if s == "draft":
            acts += ["submit", "cancel"]
        elif s == "pending_approval":
            acts += ["cancel"]
        elif s == "approved":
            acts += ["mark_sent", "cancel", "charge"]
        elif s == "sent":
            acts += ["fill", "cancel", "charge"]
        elif s == "partially_filled":
            acts += ["fill", "allocate", "charge"]
        elif s == "filled":
            acts += ["allocate", "charge"]
        elif s == "allocated":
            acts += ["settlement", "charge"]
            if not any(c["status"] == "draft" for c in d.get("corrections") or []):
                acts.append("correct")
        elif s == "settled":
            acts += ["close", "charge"]
    if can_approve and s == "pending_approval" and task_open and str(user_id) not in makers:
        acts += ["approve", "reject"]
    if can_approve and any(c["status"] == "draft" and c["maker_id"] != str(user_id)
                           for c in d.get("corrections") or []):
        acts.append("decide_correction")
    if s != "cancelled":
        acts.append("sheet")
    return acts


# ── Execute-by handling (the only automatic step, always logged) ────────────
async def expire_due(pool, *, today: date | None = None, org_id=None) -> dict:
    """Overdue blocks: not yet working (draft / awaiting approval / approved)
    → cancelled; working with no fills → the block's if-not-filled rule
    (cancel → cancelled, carry → execute-by moves to today, ask → a to-do);
    working WITH fills → a to-do to allocate (allocation is a person's act),
    or carry. Logged with no actor. Never a test org in the cross-org sweep."""
    today = today or date.today()
    out = {"cancelled": [], "carried": [], "asked": []}
    async with pool.acquire() as conn:
        args: list = [today, list(OPEN_STATUSES)]
        sql = f"SELECT id, org_id FROM {T_BLK} WHERE system_to IS NULL AND status = ANY($2::text[]) AND execute_by < $1"
        if org_id:
            args.append(str(org_id))
            sql += " AND org_id = $3::uuid"
        else:
            from services import orgs as ORGS

            sql += " AND " + ORGS.not_test_org_sql(f"{T_BLK}.org_id")
        due = await conn.fetch(sql + " ORDER BY execute_by, id", *args)
    for d in due:
        oid, bid = str(d["org_id"]), str(d["id"])
        refs, task_to_cancel = [], None
        async with pool.acquire() as conn:
            async with conn.transaction():
                row = await load(conn, oid, bid, for_update=True)
                if row["status"] not in OPEN_STATUSES or row["execute_by"] >= today:
                    continue
                working = row["status"] in ("sent", "partially_filled")
                has_fills = row["status"] == "partially_filled"
                rule = row["if_not_filled"]
                if working and rule == "carry":
                    await _update(conn, T_BLK, bid, {"execute_by": today})
                    refs.append(await _event(conn, org_id=oid, block_id=bid, action="carry", from_status=row["status"],
                                             to_status=row["status"], actor_id=None,
                                             detail={"from_execute_by": row["execute_by"], "to_execute_by": today}))
                    out["carried"].append(bid)
                elif working and (rule == "ask" or has_fills):
                    already = await conn.fetchval(f"SELECT 1 FROM {T_EVT} WHERE block_id = $1::uuid AND action = 'ask' "
                                                  "AND detail->>'execute_by' = $2", bid, row["execute_by"].isoformat())
                    if already:
                        continue
                    notified = await _todo(
                        conn, oid, set(map(str, row["maker_ids"])) | await _managers(conn, oid), source=TODO_EXPIRY,
                        related_type="trade_block", related_id=bid,
                        title=f"Block not filled by {row['execute_by'].isoformat()}: "
                              + ("allocate what was filled" if has_fills else "carry or cancel?"),
                        detail=("The block is partially filled past its execute-by date. Allocate the fills (the "
                                "remainder follows the block's rule)." if has_fills else
                                "The block is still working past its execute-by date. Carry it or cancel it."),
                        action_key=f"/portfolio/blocks/{bid}")
                    refs.append(await _event(conn, org_id=oid, block_id=bid, action="ask", from_status=row["status"],
                                             to_status=row["status"], actor_id=None,
                                             detail={"execute_by": row["execute_by"].isoformat(),
                                                     "notified": notified}))
                    out["asked"].append(bid)
                else:
                    reason = (f"not filled by the execute-by date {row['execute_by'].isoformat()} (if not filled: "
                              f"{X.IF_NOT_FILLED_LABELS[rule].lower()})" if working else
                              f"not approved and sent by the execute-by date {row['execute_by'].isoformat()}")
                    if row["status"] == "pending_approval":
                        task_to_cancel = row["approval_task_id"]
                    for a in await allocations_of(conn, bid):
                        if a["status"] in ("planned", "fixed"):
                            await _update(conn, T_ALLOC, a["id"], {"status": "cancelled"})
                    await _update(conn, T_BLK, bid, {"status": "cancelled", "cancelled_at": datetime.now(timezone.utc),
                                                     "cancel_reason": reason})
                    refs.append(await _event(conn, org_id=oid, block_id=bid, action="expire",
                                             from_status=row["status"], to_status="cancelled", actor_id=None,
                                             detail={"reason": reason}))
                    out["cancelled"].append(bid)
        if task_to_cancel:
            await _withdraw(pool, task_to_cancel, "the block expired at its execute-by date")
        await publish(pool, refs)
    return out


async def expire_tick(pool, *, today: date | None = None) -> dict:
    """The cron entrypoint's step, in platform scope."""
    from services.database import reset_rls_context, set_rls_context

    tokens = set_rls_context(None, True)
    try:
        return await expire_due(pool, today=today)
    finally:
        reset_rls_context(tokens)


# ── For rep2's instruction screens ──────────────────────────────────────────
async def block_of_instruction(conn, org_id, instruction_id) -> dict | None:
    row = await conn.fetchrow(
        f"SELECT al.block_id, al.status, al.final_quantity, b.status AS block_status FROM {T_ALLOC} al "
        f"JOIN {T_BLK} b ON b.id = al.block_id AND b.system_to IS NULL WHERE al.instruction_id = $1::uuid "
        "AND al.system_to IS NULL AND al.org_id = $2::uuid ORDER BY al.created_at DESC LIMIT 1",
        str(instruction_id), str(org_id))
    if row is None:
        return None
    return {"block_id": str(row["block_id"]), "allocation_status": row["status"], "block_status": row["block_status"],
            "final_quantity": _num(row["final_quantity"]), "open": row["status"] in ("planned", "fixed")}
