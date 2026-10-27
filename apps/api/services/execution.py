"""Execution instructions — the path for every trade that is not sent automatically (rep2).

In this build that is EVERY trade (services.custody.trading routes them all to
the manual adapter). An instruction records what a person is asked to do —
account, security, side, quantity or notional, price guidance, specific tax
lots, who executes it and by when — and what people report: that it was
approved, sent (and by which channel), filled (quantity, price, time, lots),
reconciled against custodian data or confirmed manually, and closed.

STATUS FLOW (enforced here first, then again by the database trigger)
──────────────────────────────────────────────────────────────────────────────
  draft ─submit→ pending_approval ─approve (human task, maker ≠ checker)→ approved
        ←reject─┘
  approved ─mark sent (person, channel)→ sent ─fill→ partially_filled ─fill→ filled
  filled ─reconcile (custodian data, or manual with a note)→ reconciled ─close→ closed
  cancelled / expired from any open state, with a reason.

Every transition is a governed human task (approval — services.human_tasks)
or a logged human action, EXCEPT what happens at ``execute_by``: the
instruction's own ``if_not_filled`` policy is applied by :func:`expire_due`
(cancel → expired; carry → working again today; ask → a todo for a person),
and that is logged with no actor.

LOGGING. Every action writes, in its own transaction: an
``execution_instruction_events`` row (append-only) and an ``audit_log`` row;
after commit, one ``domain_events`` row ``execution.<action>`` (source_type
``execution_instruction_event``). The instruction row is archived on the
SYSTEM axis before every change (its id is stable: lots, fills, events and
the approval task all point at it).

DECIMAL THROUGHOUT. Quantities and prices are ``Decimal`` from the request
model (a binary float is refused at the boundary) to ``numeric`` columns and
back; nothing here converts through float.

THE CUSTODY CLIFF. Nothing in this module places a trade or moves money.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any

from services import human_tasks as HT

T_INS = "public.execution_instructions"
T_LOT = "public.execution_instruction_lots"
T_FILL = "public.execution_fills"
T_EVT = "public.execution_instruction_events"

VIEW = "view_execution"
MANAGE = "manage_execution"
APPROVE = "approve_execution"
TASK_KIND = "execution_approval"
EVENT_SOURCE_TYPE = "execution_instruction_event"
TODO_SOURCE_ASK = "execution_not_filled"

STATUS_LABELS = {
    "draft": "Draft", "pending_approval": "Awaiting approval", "approved": "Approved", "sent": "Sent",
    "partially_filled": "Partially filled", "filled": "Filled", "reconciled": "Reconciled", "closed": "Closed",
    "cancelled": "Cancelled", "expired": "Expired",
}
OPEN_STATUSES = ("draft", "pending_approval", "approved", "sent", "partially_filled")
WORKING_STATUSES = ("approved", "sent", "partially_filled")
EXECUTOR_LABELS = {
    "custodian_desk": "Custodian trading desk", "broker": "Broker", "member": "Member",
    "other_adviser": "Other adviser", "trader": "Trader",
}
LETTER_EXECUTORS = ("member", "other_adviser")
SIDE_LABELS = {"buy": "Buy", "sell": "Sell"}
PRICE_KIND_LABELS = {"market": "At market", "limit": "Limit", "range": "Acceptable range around a reference price"}
IF_NOT_FILLED_LABELS = {"cancel": "Cancel it", "carry": "Carry to the next day", "ask": "Ask me"}
SOURCE_LABELS = {"manual": "Entered manually", "proposal": "From a proposal"}
SENT_CHANNEL_LABELS = {
    "email": "Email", "phone": "Phone", "custodian_portal": "Custodian portal", "secure_message": "Secure message",
    "letter": "Letter of instruction", "in_person": "In person", "other": "Other",
}
RECONCILE_METHOD_LABELS = {"custodian_data": "Matched to custodian data", "manual": "Confirmed manually"}
ACTION_LABELS = {
    "create": "Created", "edit": "Edited", "submit": "Submitted for approval", "approve": "Approved",
    "reject": "Rejected", "mark_sent": "Marked sent", "fill": "Fill entered", "reconcile": "Reconciled",
    "close": "Closed", "cancel": "Cancelled", "expire": "Expired", "carry": "Carried to the next day",
    "ask": "Not filled — decision requested", "letter": "Letter of instruction generated",
}
DOCUMENT_LABELS = {"letter_of_instruction": "Letter of instruction",
                   "dvp_settlement_instruction": "DVP settlement instruction"}
# Fields a draft edit may change (everything else is lifecycle).
EDITABLE_FIELDS = ("quantity", "notional", "price_kind", "limit_price", "range_low", "range_high", "reference_price",
                   "reference_at", "wash_sale_notes", "executor_type", "executor_name", "executor_contact",
                   "execute_by", "if_not_filled", "notes")


class ExecutionError(Exception):
    """A request the rules refuse (422)."""


class ExecutionNotFound(ExecutionError):
    pass


class ExecutionConflict(ExecutionError):
    """An illegal transition for the instruction's current status (409)."""


class MakerCheckerViolation(ExecutionError):
    pass


@dataclass
class EventRef:
    id: str
    org_id: str
    action: str
    instruction_id: str
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


def _uuid(v, what="instruction") -> str:
    try:
        return str(uuid.UUID(str(v)))
    except (TypeError, ValueError):
        raise ExecutionNotFound(f"{what} not found") from None


def _dec(v) -> Decimal | None:
    if v is None:
        return None
    return v if isinstance(v, Decimal) else Decimal(str(v))


# ── Reading ─────────────────────────────────────────────────────────────────
async def load(conn, org_id, instruction_id, *, for_update: bool = False) -> dict:
    row = await conn.fetchrow(
        f"SELECT * FROM {T_INS} WHERE id = $1::uuid AND org_id = $2::uuid AND system_to IS NULL"
        + (" FOR UPDATE" if for_update else ""), _uuid(instruction_id), str(org_id))
    if row is None:
        raise ExecutionNotFound("instruction not found")
    return dict(row)


async def load_account(conn, org_id, account_id) -> dict:
    row = await conn.fetchrow(
        "SELECT id, org_id, account_number_masked, account_number_hash, custodian_code, custodian_account_id, "
        "registration_type, tax_status, primary_entity_id, household_id, is_held_away, closed_on, base_currency "
        "FROM public.accounts WHERE id = $1::uuid AND org_id = $2::uuid AND system_to IS NULL AND valid_to IS NULL",
        _uuid(account_id, "account"), str(org_id))
    if row is None:
        raise ExecutionNotFound("account not found")
    from services import authority as AU      # rep7b — the effective authority / custody rides on the account

    return await AU.attach(conn, org_id, dict(row))


async def _security(conn, global_security_id) -> dict:
    row = await conn.fetchrow(
        "SELECT id, name, short_name, security_type, currency_code FROM portfolio.securities_global WHERE id = $1::uuid "
        "AND system_to IS NULL", _uuid(global_security_id, "security"))
    if row is None:
        raise ExecutionNotFound("security not found in the security master")
    ids = await conn.fetch(
        "SELECT id_type, id_value FROM portfolio.securities_global_identifiers WHERE global_security_id = $1 "
        "AND valid_to IS NULL AND system_to IS NULL ORDER BY is_primary DESC, id_type", row["id"])
    return {**dict(row), "identifiers": {r["id_type"]: r["id_value"] for r in ids}}


async def search_securities(conn, q: str, limit: int = 20) -> list[dict]:
    q = (q or "").strip()
    if len(q) < 2:
        return []
    rows = await conn.fetch(
        """SELECT DISTINCT s.id, s.name, s.security_type,
                  (SELECT id_value FROM portfolio.securities_global_identifiers t WHERE t.global_security_id = s.id
                    AND t.id_type = 'ticker' AND t.system_to IS NULL AND t.valid_to IS NULL LIMIT 1) AS ticker,
                  (SELECT id_value FROM portfolio.securities_global_identifiers c WHERE c.global_security_id = s.id
                    AND c.id_type = 'cusip' AND c.system_to IS NULL AND c.valid_to IS NULL LIMIT 1) AS cusip
           FROM portfolio.securities_global s
           LEFT JOIN portfolio.securities_global_identifiers i ON i.global_security_id = s.id
                AND i.system_to IS NULL AND i.valid_to IS NULL
           WHERE s.system_to IS NULL AND s.valid_to IS NULL AND s.merged_into_id IS NULL
             AND (s.name ILIKE '%' || $1 || '%' OR upper(i.id_value) = upper($1))
           ORDER BY s.name LIMIT $2""", q, max(1, min(limit, 50)))
    return [{"id": str(r["id"]), "name": r["name"], "security_type": r["security_type"], "ticker": r["ticker"],
             "cusip": r["cusip"]} for r in rows]


async def list_accounts(conn, org_id, limit: int = 500) -> list[dict]:
    rows = await conn.fetch(
        "SELECT id, account_number_masked, custodian_code, registration_type, is_held_away FROM public.accounts "
        "WHERE org_id = $1::uuid AND system_to IS NULL AND valid_to IS NULL AND closed_on IS NULL "
        "ORDER BY custodian_code, account_number_masked LIMIT $2", str(org_id), limit)
    return [{"id": str(r["id"]), "account_number_masked": r["account_number_masked"],
             "custodian_code": r["custodian_code"], "registration_type": r["registration_type"],
             "is_held_away": r["is_held_away"]} for r in rows]


_LOT_SQL = """
SELECT tl.id, tl.acquired_date, tl.quantity, tl.adjusted_cost, tl.original_cost, tl.term, tl.status,
       p.account_id, a.global_security_id
FROM portfolio.tax_lots tl
JOIN portfolio.positions p ON p.id = tl.position_id AND p.system_to IS NULL
JOIN portfolio.assets a ON a.id = p.asset_id AND a.system_to IS NULL
WHERE tl.org_id = $1::uuid AND tl.status = 'open' AND tl.system_to IS NULL AND tl.valid_to IS NULL
  AND p.account_id = $2::uuid
"""


async def account_lots(conn, org_id, account_id, global_security_id=None) -> list[dict]:
    """Open tax lots of THIS account (lot → position.account_id), optionally
    in one security (position → asset.global_security_id)."""
    args = [str(org_id), _uuid(account_id, "account")]
    sql = _LOT_SQL
    if global_security_id:
        args.append(_uuid(global_security_id, "security"))
        sql += " AND a.global_security_id = $3::uuid"
    sql += " ORDER BY tl.acquired_date NULLS LAST, tl.id"
    return [{"id": str(r["id"]), "acquired_date": r["acquired_date"].isoformat() if r["acquired_date"] else None,
             "quantity": r["quantity"], "adjusted_cost": r["adjusted_cost"], "original_cost": r["original_cost"],
             "term": r["term"], "global_security_id": str(r["global_security_id"])}
            for r in await conn.fetch(sql, *args)]


async def _validate_lots(conn, org_id, account_id, security_id, side: str, lots: list[tuple[str, Decimal]],
                         quantity: Decimal | None) -> None:
    """Lot instructions only for open lots of THIS account in THIS security,
    sells only, no lot twice, never more than the lot holds or the sale."""
    if not lots:
        return
    if side != "sell":
        raise ExecutionError("lot instructions apply to sells only")
    seen = set()
    total = Decimal(0)
    for lot_id, qty in lots:
        lid = _uuid(lot_id, "tax lot")
        if lid in seen:
            raise ExecutionError("a tax lot is listed twice")
        seen.add(lid)
        row = await conn.fetchrow(_LOT_SQL + " AND tl.id = $3::uuid AND a.global_security_id = $4::uuid",
                                  str(org_id), str(account_id), lid, str(security_id))
        if row is None:
            raise ExecutionError(f"tax lot {lid} is not an open lot of this account in this security")
        if qty is None or qty <= 0:
            raise ExecutionError("a lot quantity must be greater than zero")
        if row["quantity"] is not None and qty > _dec(row["quantity"]):
            raise ExecutionError(f"tax lot {lid} holds {row['quantity']}, less than the {qty} instructed")
        total += qty
    if quantity is not None and total > quantity:
        raise ExecutionError("the lots add up to more than the quantity to sell")


def _filled(fills: list[dict]) -> tuple[Decimal, Decimal]:
    qty = sum((_dec(f["quantity"]) for f in fills), Decimal(0))
    notional = sum((_dec(f["quantity"]) * _dec(f["price"]) for f in fills), Decimal(0))
    return qty, notional


async def _fills(conn, instruction_id) -> list[dict]:
    return [dict(r) for r in await conn.fetch(
        f"SELECT * FROM {T_FILL} WHERE instruction_id = $1::uuid AND valid_to IS NULL AND system_to IS NULL "
        "ORDER BY filled_at, created_at", str(instruction_id))]


async def _lots(conn, instruction_id) -> list[dict]:
    return [dict(r) for r in await conn.fetch(
        f"""SELECT l.id, l.tax_lot_id, l.quantity, l.created_at, tl.acquired_date, tl.quantity AS lot_quantity,
                   tl.adjusted_cost, tl.term
            FROM {T_LOT} l JOIN portfolio.tax_lots tl ON tl.id = l.tax_lot_id
            WHERE l.instruction_id = $1::uuid AND l.valid_to IS NULL AND l.system_to IS NULL
            ORDER BY tl.acquired_date NULLS LAST, l.tax_lot_id""", str(instruction_id))]


# ── Writing primitives ──────────────────────────────────────────────────────
async def _archive(conn, instruction_id) -> str:
    archived = await conn.fetchval(
        f"""INSERT INTO {T_INS}
            SELECT (jsonb_populate_record(NULL::{T_INS}, to_jsonb(t) || jsonb_build_object(
                'id', gen_random_uuid(), 'archived_from_id', t.id, 'system_to', now()))).*
            FROM {T_INS} t WHERE t.id = $1::uuid AND t.system_to IS NULL
            RETURNING id""", str(instruction_id))
    if archived is None:
        raise ExecutionConflict("instruction is not live — refusing to change it")
    return str(archived)


_UPDATABLE = set(EDITABLE_FIELDS) | {
    "status", "maker_ids", "approval_task_id", "approved_by", "approved_at", "sent_by", "sent_at", "sent_channel",
    "sent_note", "reconciled_by", "reconciled_at", "reconcile_method", "reconcile_reference", "reconcile_note",
    "closed_by", "closed_at", "cancelled_by", "cancelled_at", "cancel_reason", "expired_at", "expiry_reason"}


async def _update(conn, instruction_id, fields: dict) -> dict:
    bad = set(fields) - _UPDATABLE
    if bad:
        raise ExecutionError(f"not updatable: {sorted(bad)}")
    await _archive(conn, instruction_id)
    cols = list(fields)
    sets = ", ".join(f"{c} = ${i + 2}" for i, c in enumerate(cols))
    row = await conn.fetchrow(
        f"UPDATE {T_INS} SET {sets}, updated_at = now(), system_from = now() "
        "WHERE id = $1::uuid AND system_to IS NULL RETURNING *", str(instruction_id), *[fields[c] for c in cols])
    if row is None:
        raise ExecutionConflict("instruction changed underneath this request — reload and retry")
    return dict(row)


async def _event(conn, *, org_id, instruction_id, action: str, from_status, to_status: str, actor_id,
                 detail: dict | None = None) -> EventRef:
    """The event row + its audit row, inside the caller's transaction."""
    detail = detail or {}
    eid = await conn.fetchval(
        f"INSERT INTO {T_EVT} (org_id, instruction_id, action, from_status, to_status, actor_id, detail) "
        "VALUES ($1::uuid, $2::uuid, $3, $4, $5, $6::uuid, $7::jsonb) RETURNING id",
        str(org_id), str(instruction_id), action, from_status, to_status, str(actor_id) if actor_id else None,
        _dumps(detail))
    payload = {"instruction_id": str(instruction_id), "from_status": from_status, "to_status": to_status, **detail}
    await conn.execute(
        "INSERT INTO audit_log (org_id, user_id, action, resource_type, resource_id, payload) "
        "VALUES ($1::uuid, $2::uuid, $3, 'execution_instruction', $4::uuid, $5::jsonb)",
        str(org_id), str(actor_id) if actor_id else None, f"execution.{action}", str(instruction_id),
        _dumps({**payload, "event_id": str(eid)}))
    return EventRef(str(eid), str(org_id), action, str(instruction_id), payload, str(actor_id) if actor_id else None)


async def publish(pool, refs: list[EventRef]) -> list[dict]:
    """After commit: one domain event per action (publish_event never raises)."""
    from services.domain_events import publish_event

    out = []
    for r in refs:
        out.append(await publish_event(pool, r.org_id, f"execution.{r.action}", EVENT_SOURCE_TYPE, r.id,
                                       json.loads(_dumps(r.payload)), created_by=r.actor_id))
    return out


async def _human(conn, actor_id, org_id) -> dict:
    """Every instruction action is a person's (agents are refused)."""
    try:
        return await HT.assert_human(conn, actor_id, org_id)
    except HT.AgentActorRefused as exc:
        raise ExecutionError(str(exc)) from exc


def _need(row: dict, statuses: tuple[str, ...], what: str) -> None:
    if row["status"] not in statuses:
        raise ExecutionConflict(
            f"cannot {what} an instruction that is {STATUS_LABELS.get(row['status'], row['status']).lower()} "
            f"(allowed from: {', '.join(STATUS_LABELS[s].lower() for s in statuses)})")


# ── Create (called by the trade adapters) ───────────────────────────────────
def _is_dvp(acct: dict, executor_type: str) -> bool:
    """rep7b — a managed account at another custodian, executed by a broker or
    trader: a manual DVP settlement instruction (never a custodian API)."""
    from services import authority as AU

    e = AU.effective(acct)
    return e["authority"] == "managed" and e["custody"] == "other_custodian" and executor_type in AU.DVP_EXECUTORS


async def _verify_upstream(conn, org_id, acct: dict, trade_list, upstream: dict) -> dict:
    """rep5 (Task 1d): an instruction list ALREADY approved by an upstream
    maker-checker — a rebalance proposal. Re-read here, never trusted from the
    caller: the proposal is this org's, this account's, approved, made by a
    PERSON who is not its checker, and its approval task was completed
    'approve' by that checker. The database's insert guard re-checks it."""
    if trade_list.source != "proposal" or str(trade_list.source_ref) != str(upstream.get("proposal_id")):
        raise ExecutionError("an upstream approval is a rebalance proposal named as the trade list's source")
    p = await conn.fetchrow(
        "SELECT id, account_id, status, maker_kind, maker_id, checker_id, decided_at, approval_task_id "
        "FROM public.rebalance_proposals WHERE id = $1::uuid AND org_id = $2::uuid AND system_to IS NULL",
        _uuid(upstream["proposal_id"], "proposal"), str(org_id))
    if p is None or str(p["account_id"]) != str(acct["id"]):
        raise ExecutionError("the upstream proposal is not this account's")
    if p["status"] != "approved" or p["maker_kind"] != "user" or p["maker_id"] is None or p["checker_id"] is None:
        raise ExecutionError("only an approved proposal made by a person carries its approval to instructions")
    if str(p["maker_id"]) != str(upstream.get("maker_id")) or str(p["checker_id"]) != str(upstream.get("checker_id")):
        raise ExecutionError("the upstream maker / checker do not match the proposal's")
    if p["maker_id"] == p["checker_id"]:
        raise MakerCheckerViolation("maker-checker: the proposal's maker cannot be its checker")
    task = await HT.get_task(conn, org_id, p["approval_task_id"]) if p["approval_task_id"] else None
    if not task or task["status"] != "completed" or task["outcome"] != "approve" \
            or task["completed_by"] != str(p["checker_id"]):
        raise ExecutionError("the proposal's approval task is not completed 'approve' by its checker")
    return {"maker_id": p["maker_id"], "checker_id": p["checker_id"], "approved_at": p["decided_at"],
            "task_id": str(p["approval_task_id"]), "proposal_id": str(p["id"])}


async def create_instructions(pool, *, org_id, actor_id, account: dict, trade_list, routed_via: str,
                              routing_reason: str, upstream: dict | None = None) -> list[dict]:
    """One DRAFT instruction per trade, ONE transaction. Called by
    services.custody.trading's ManualTradeAdapter — never directly by a route.

    rep5 (additive): ``upstream`` = ``{"proposal_id", "maker_id", "checker_id"}``
    of an APPROVED rebalance proposal (:func:`_verify_upstream` re-reads it).
    Then each instruction starts APPROVED: maker = the proposal's maker,
    approved_by = its checker (so makers_chk / maker_checker_chk / approved_chk
    hold exactly as for a rep2 approval), with the lots inserted in the same
    transaction. Everything after (mark sent, fills, reconcile, close) is
    rep2's flow, unchanged."""
    if trade_list.execute_by < date.today():
        raise ExecutionError("execute-by date is in the past")
    out, refs = [], []
    async with pool.acquire() as conn:
        actor = await _human(conn, actor_id, org_id)
        acct = await load_account(conn, org_id, trade_list.account_id)
        if str(acct["id"]) != str(account["id"]):
            raise ExecutionError("the trade list names a different account")
        from services.held_away import refuse      # rep7 / rep7b — the authority × custody and the executor decide

        refuse(acct, "an execution instruction", ExecutionConflict, path="instructions",
               executor=trade_list.executor_type)
        up = await _verify_upstream(conn, org_id, acct, trade_list, upstream) if upstream else None
        maker = up["maker_id"] if up else actor["id"]
        status = "approved" if up else "draft"
        dvp = _is_dvp(acct, trade_list.executor_type)
        if dvp and not ((acct.get("receiving") or {}).get("custodian_name")
                        and (acct.get("receiving") or {}).get("participant_number")):
            raise ExecutionConflict(f"{acct['account_number_masked']}: record the receiving custodian's name and DTC / "
                                    "participant number (an approved custody change) before a DVP settlement "
                                    "instruction")
        trade_date = date.today()
        async with conn.transaction():
            for t in trade_list.trades:
                await _security(conn, t.global_security_id)
                lots = [(str(li.tax_lot_id), li.quantity) for li in t.lot_instructions]
                await _validate_lots(conn, org_id, acct["id"], t.global_security_id, t.side, lots, t.quantity)
                pg = t.price_guidance
                row = await conn.fetchrow(
                    f"""INSERT INTO {T_INS} (org_id, account_id, global_security_id, side, quantity, notional,
                            price_kind, limit_price, range_low, range_high, reference_price, reference_at,
                            wash_sale_notes, executor_type, executor_name, executor_contact, execute_by,
                            if_not_filled, source, source_ref, routed_via, routing_reason, notes, status,
                            maker_id, maker_ids, approved_by, approved_at)
                        VALUES ($1::uuid, $2::uuid, $3::uuid, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15,
                                $16, $17, $18, $19, $20::uuid, $21, $22, $23, $24, $25::uuid, ARRAY[$25::uuid],
                                $26::uuid, $27)
                        RETURNING *""",
                    str(org_id), str(acct["id"]), str(t.global_security_id), t.side, t.quantity, t.notional,
                    pg.kind, pg.limit_price, pg.range_low, pg.range_high, pg.reference_price, pg.reference_at,
                    t.wash_sale_notes, trade_list.executor_type, trade_list.executor_name,
                    trade_list.executor_contact, trade_list.execute_by, trade_list.if_not_filled,
                    trade_list.source, str(trade_list.source_ref) if trade_list.source_ref else None,
                    routed_via, routing_reason, trade_list.notes, status, str(maker),
                    str(up["checker_id"]) if up else None, up["approved_at"] if up else None)
                for lid, q in lots:
                    await conn.execute(
                        f"INSERT INTO {T_LOT} (org_id, instruction_id, tax_lot_id, quantity, created_by) "
                        "VALUES ($1::uuid, $2, $3::uuid, $4, $5::uuid)", str(org_id), row["id"], lid, q,
                        str(actor["id"]))
                detail = {"routed_via": routed_via, "routing_reason": routing_reason, "lots": len(lots),
                          "source": trade_list.source}
                if dvp:   # rep7b — managed at another custodian: the manual DVP settlement detail, same transaction
                    from services import authority as AU

                    st = await AU.write_settlement(conn, org_id=org_id, account=acct, instruction=dict(row),
                                                   actor_id=actor["id"], trade_date=trade_date)
                    detail["settlement"] = {"type": "dvp", "settlement_date": st["settlement_date"].isoformat(),
                                            "receiving_custodian": st["receiving_custodian_name"]}
                if up:
                    detail["upstream"] = {"proposal_id": up["proposal_id"], "task_id": up["task_id"],
                                          "maker_id": str(up["maker_id"]), "checker_id": str(up["checker_id"])}
                refs.append(await _event(conn, org_id=org_id, instruction_id=row["id"], action="create",
                                         from_status=None, to_status=status, actor_id=actor["id"],
                                         detail=detail))
                out.append(dict(row))
    await publish(pool, refs)
    return out


# ── Draft edit ──────────────────────────────────────────────────────────────
def _merged_amount(row: dict, changes: dict) -> tuple[Decimal | None, Decimal | None]:
    q = changes["quantity"] if "quantity" in changes else row["quantity"]
    n = changes["notional"] if "notional" in changes else row["notional"]
    return _dec(q), _dec(n)


async def edit_instruction(pool, *, org_id, instruction_id, actor_id, changes: dict,
                           lots: list[tuple[str, Decimal]] | None = None) -> dict:
    """Edit a DRAFT. Anything after submission is frozen (the DB agrees)."""
    unknown = set(changes) - set(EDITABLE_FIELDS)
    if unknown:
        raise ExecutionError(f"not editable: {sorted(unknown)}")
    async with pool.acquire() as conn:
        actor = await _human(conn, actor_id, org_id)
        async with conn.transaction():
            row = await load(conn, org_id, instruction_id, for_update=True)
            _need(row, ("draft",), "edit")
            q, n = _merged_amount(row, changes)
            if (q is None) == (n is None) or (q is not None and q <= 0) or (n is not None and n <= 0):
                raise ExecutionError("give exactly one of quantity or notional, above zero")
            if "execute_by" in changes and changes["execute_by"] < date.today():
                raise ExecutionError("execute-by date is in the past")
            if lots is not None:
                await _validate_lots(conn, org_id, row["account_id"], row["global_security_id"], row["side"],
                                     lots, q)
            elif q is not None:
                current = await _lots(conn, row["id"])
                if sum((_dec(x["quantity"]) for x in current), Decimal(0)) > q:
                    raise ExecutionError("the existing lots add up to more than the new quantity")
            makers = list(row["maker_ids"])
            if actor["id"] not in makers:
                makers.append(actor["id"])
            before = {k: row[k] for k in changes}
            new = await _update(conn, row["id"], {**changes, "maker_ids": makers})
            if lots is not None:
                await conn.execute(f"UPDATE {T_LOT} SET valid_to = now() WHERE instruction_id = $1 "
                                   "AND valid_to IS NULL AND system_to IS NULL", row["id"])
                for lid, lq in lots:
                    await conn.execute(
                        f"INSERT INTO {T_LOT} (org_id, instruction_id, tax_lot_id, quantity, created_by) "
                        "VALUES ($1::uuid, $2, $3::uuid, $4, $5::uuid)", str(org_id), row["id"], lid, lq,
                        str(actor["id"]))
            ref = await _event(conn, org_id=org_id, instruction_id=row["id"], action="edit", from_status="draft",
                               to_status="draft", actor_id=actor["id"],
                               detail={"fields": sorted(changes), "before": before,
                                       "after": {k: new[k] for k in changes},
                                       "lots_replaced": lots is not None})
    await publish(pool, [ref])
    return new


# ── Approval (a governed human task) ────────────────────────────────────────
def _task_title(row: dict, sec_name: str) -> str:
    amt = f"{row['quantity']} shares" if row["quantity"] is not None else f"{row['notional']} notional"
    return f"Approve instruction: {SIDE_LABELS[row['side']]} {amt} of {sec_name}"


async def submit(pool, *, org_id, instruction_id, actor_id) -> dict:
    """draft → pending_approval; opens the approval task (maker = submitter)."""
    async with pool.acquire() as conn:
        actor = await _human(conn, actor_id, org_id)
        row = await load(conn, org_id, instruction_id)
        _need(row, ("draft",), "submit")
        if row["execute_by"] < date.today():
            raise ExecutionError("execute-by date is in the past — edit it before submitting")
        sec = await _security(conn, row["global_security_id"])
        acct = await load_account(conn, org_id, row["account_id"])
    why = _approval_refusal(acct, row)
    if why:
        raise ExecutionConflict(why)
    task = await HT.open_task(
        pool, org_id=org_id, kind=TASK_KIND, subject_type="execution_instruction", subject_id=row["id"],
        maker_id=actor["id"], eligible_permission=APPROVE, title=_task_title(row, sec["name"]),
        payload={"account": acct["account_number_masked"], "side": row["side"],
                 "quantity": str(row["quantity"]) if row["quantity"] is not None else None,
                 "notional": str(row["notional"]) if row["notional"] is not None else None,
                 "security": sec["name"], "executor_type": row["executor_type"]},
        action_path=f"/portfolio/instructions/{row['id']}")
    try:
        async with pool.acquire() as conn:
            async with conn.transaction():
                cur = await load(conn, org_id, instruction_id, for_update=True)
                _need(cur, ("draft",), "submit")
                makers = list(cur["maker_ids"])
                if actor["id"] not in makers:
                    makers.append(actor["id"])
                new = await _update(conn, cur["id"], {"status": "pending_approval", "approval_task_id":
                                                      uuid.UUID(task["id"]), "maker_ids": makers})
                ref = await _event(conn, org_id=org_id, instruction_id=cur["id"], action="submit",
                                   from_status="draft", to_status="pending_approval", actor_id=actor["id"],
                                   detail={"task_id": task["id"], "workflow_run_id": task["run_id"],
                                           "notified": task.get("notified", [])})
    except Exception:
        await HT.cancel_task(pool, org_id=org_id, task_id=task["id"], actor_id=actor["id"],
                             reason="the instruction could not be submitted")
        raise
    await publish(pool, [ref])
    return new


async def _apply_outcome(pool, org_id, task: dict, outcome: str, actor_id, note) -> dict | None:
    """Apply a decided approval task to its instruction. Idempotent: an
    instruction no longer pending on THIS task is left alone."""
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(f"SELECT * FROM {T_INS} WHERE approval_task_id = $1::uuid AND "
                                      "system_to IS NULL AND org_id = $2::uuid FOR UPDATE", task["id"], str(org_id))
            if row is None or row["status"] != "pending_approval":
                return dict(row) if row else None
            row = dict(row)
            if outcome == "approve":
                if uuid.UUID(str(actor_id)) in row["maker_ids"]:
                    raise MakerCheckerViolation("maker-checker: a maker of this instruction cannot approve it")
                new = await _update(conn, row["id"], {"status": "approved", "approved_by": uuid.UUID(str(actor_id)),
                                                      "approved_at": datetime.now(timezone.utc)})
                ref = await _event(conn, org_id=org_id, instruction_id=row["id"], action="approve",
                                   from_status="pending_approval", to_status="approved", actor_id=actor_id,
                                   detail={"task_id": task["id"], "note": note})
            else:
                new = await _update(conn, row["id"], {"status": "draft", "approval_task_id": None})
                ref = await _event(conn, org_id=org_id, instruction_id=row["id"], action="reject",
                                   from_status="pending_approval", to_status="draft", actor_id=actor_id,
                                   detail={"task_id": task["id"], "note": note})
    await publish(pool, [ref])
    return new


async def _on_task_complete(pool, task, outcome, actor_id, note):
    return await _apply_outcome(pool, task["org_id"], task, outcome, actor_id, note)


HT.register_kind(TASK_KIND, label="Execution approval", outcomes=("approve", "reject"), maker_checker=True,
                 on_complete=_on_task_complete)


def _approval_refusal(acct: dict, row: dict) -> str | None:
    """rep7b — an instruction is submitted / approved only while its account's
    authority allows it (the same policy as creation). A draft written before
    rep7 on a held-away account (read-only) is refused with the reason — kept,
    never deleted or cancelled by this check."""
    from services import authority as AU

    return AU.refusal(acct, "approving an execution instruction", path="instructions",
                      executor=row["executor_type"])


async def _approval_precheck(pool, task, outcome, actor_id):
    if outcome != "approve":
        return
    async with pool.acquire() as conn:
        row = await conn.fetchrow(f"SELECT * FROM {T_INS} WHERE approval_task_id = $1::uuid AND system_to IS NULL",
                                  task["id"])
        if row is None:
            return
        acct = await load_account(conn, row["org_id"], row["account_id"])
    why = _approval_refusal(acct, dict(row))
    if why:
        raise HT.HumanTaskConflict(why)


HT.register_precheck(TASK_KIND, _approval_precheck)


async def sync_task_outcome(pool, *, org_id, instruction_id) -> dict:
    """Recovery: an instruction still pending on a task the engine already
    decided (the subject update failed after the engine committed) takes the
    recorded outcome now."""
    async with pool.acquire() as conn:
        row = await load(conn, org_id, instruction_id)
        if row["status"] != "pending_approval" or not row["approval_task_id"]:
            return row
        task = await HT.get_task(conn, org_id, row["approval_task_id"])
    if task["status"] == "completed" and task["outcome"] in ("approve", "reject"):
        return await _apply_outcome(pool, org_id, task, task["outcome"], task["completed_by"], task["note"]) or row
    return row


async def decide(pool, *, org_id, instruction_id, actor_id, outcome: str, note: str | None = None) -> dict:
    """Approve / reject through the instruction's open human task.

    Maker-checker twice over: the app refuses anyone who made, edited or
    submitted the instruction (``maker_ids``); the engine refuses the task's
    maker (``proposed_by``), and the DB CHECK on both tables backs it."""
    if outcome not in ("approve", "reject"):
        raise ExecutionError("outcome is approve or reject")
    await sync_task_outcome(pool, org_id=org_id, instruction_id=instruction_id)
    async with pool.acquire() as conn:
        actor = await _human(conn, actor_id, org_id)
        row = await load(conn, org_id, instruction_id)
    _need(row, ("pending_approval",), "approve or reject")
    if actor["id"] in row["maker_ids"]:
        raise MakerCheckerViolation(
            "maker-checker: you made, edited or submitted this instruction, so a different holder of "
            f"{APPROVE} must {outcome} it")
    try:
        done = await HT.complete_task(pool, org_id=org_id, task_id=row["approval_task_id"], actor_id=actor["id"],
                                      outcome=outcome, note=note)
    except HT.MakerCheckerError as exc:
        raise MakerCheckerViolation(str(exc)) from exc
    result = done.get("subject_result")
    if result is None:
        async with pool.acquire() as conn:
            result = await load(conn, org_id, instruction_id)
    return result


# ── Logged human actions ────────────────────────────────────────────────────
async def _transition(pool, *, org_id, instruction_id, actor_id, allowed: tuple[str, ...], what: str,
                      action: str, to_status: str, fields: dict, detail: dict | None = None) -> tuple[dict, EventRef]:
    async with pool.acquire() as conn:
        actor = await _human(conn, actor_id, org_id)
        async with conn.transaction():
            row = await load(conn, org_id, instruction_id, for_update=True)
            _need(row, allowed, what)
            extra = fields(actor) if callable(fields) else fields
            new = await _update(conn, row["id"], {"status": to_status, **extra})
            ref = await _event(conn, org_id=org_id, instruction_id=row["id"], action=action,
                               from_status=row["status"], to_status=to_status, actor_id=actor["id"],
                               detail=detail or {})
    await publish(pool, [ref])
    return new, ref


async def mark_sent(pool, *, org_id, instruction_id, actor_id, channel: str, note: str | None = None) -> dict:
    if channel not in SENT_CHANNEL_LABELS:
        raise ExecutionError(f"channel must be one of {list(SENT_CHANNEL_LABELS)}")
    now = datetime.now(timezone.utc)
    new, _ = await _transition(
        pool, org_id=org_id, instruction_id=instruction_id, actor_id=actor_id, allowed=("approved",),
        what="mark sent", action="mark_sent", to_status="sent",
        fields=lambda a: {"sent_by": a["id"], "sent_at": now, "sent_channel": channel, "sent_note": note},
        detail={"channel": channel, "note": note})
    return new


async def enter_fill(pool, *, org_id, instruction_id, actor_id, quantity: Decimal, price: Decimal,
                     filled_at: datetime, lots: list[tuple[str, Decimal]] | None = None, completes: bool = False,
                     note: str | None = None) -> dict:
    """A fill a person reports. Only after the instruction is sent; never more
    than the instructed quantity; lots of THIS account and security, and the
    instructed lots when the instruction named any."""
    if not isinstance(quantity, Decimal) or not isinstance(price, Decimal):
        raise ExecutionError("fill quantity and price must be Decimal")
    if quantity <= 0 or price <= 0:
        raise ExecutionError("fill quantity and price must be greater than zero")
    if filled_at.tzinfo is None:
        raise ExecutionError("the fill time needs a time zone")
    if filled_at > datetime.now(timezone.utc):
        raise ExecutionError("the fill time is in the future")
    lots = lots or []
    async with pool.acquire() as conn:
        actor = await _human(conn, actor_id, org_id)
        async with conn.transaction():
            row = await load(conn, org_id, instruction_id, for_update=True)
            _need(row, ("sent", "partially_filled"), "enter a fill on")
            if lots:
                await _validate_lots(conn, org_id, row["account_id"], row["global_security_id"], row["side"], lots,
                                     quantity)
                instructed = {str(x["tax_lot_id"]) for x in await _lots(conn, row["id"])}
                if instructed and any(_uuid(lid, "tax lot") not in instructed for lid, _ in lots):
                    raise ExecutionError("a fill's lots must be among the lots this instruction names")
                if sum((q for _, q in lots), Decimal(0)) != quantity:
                    raise ExecutionError("the fill's lots must add up to the fill quantity")
            prior = await _fills(conn, row["id"])
            done_q, done_n = _filled(prior)
            q_total, n_total = done_q + quantity, done_n + quantity * price
            if row["quantity"] is not None and q_total > _dec(row["quantity"]):
                raise ExecutionError(f"this fill would take the total to {q_total}, more than the "
                                     f"{row['quantity']} instructed")
            complete = completes or (row["quantity"] is not None and q_total == _dec(row["quantity"])) or (
                row["notional"] is not None and n_total >= _dec(row["notional"]))
            fill_id = await conn.fetchval(
                f"INSERT INTO {T_FILL} (org_id, instruction_id, quantity, price, filled_at, lots, completes, note, "
                "entered_by) VALUES ($1::uuid, $2, $3, $4, $5, $6::jsonb, $7, $8, $9::uuid) RETURNING id",
                str(org_id), row["id"], quantity, price, filled_at,
                _dumps([{"tax_lot_id": _uuid(lid, "tax lot"), "quantity": str(q)} for lid, q in lots]),
                bool(completes), note, str(actor["id"]))
            to_status = "filled" if complete else "partially_filled"
            new = row if to_status == row["status"] else await _update(conn, row["id"], {"status": to_status})
            ref = await _event(conn, org_id=org_id, instruction_id=row["id"], action="fill",
                               from_status=row["status"], to_status=to_status, actor_id=actor["id"],
                               detail={"fill_id": str(fill_id), "quantity": str(quantity), "price": str(price),
                                       "filled_at": filled_at.isoformat(), "lots": len(lots),
                                       "total_quantity": str(q_total), "completes": bool(completes)})
    await publish(pool, [ref])
    return new


async def reconcile(pool, *, org_id, instruction_id, actor_id, method: str, reference: str | None = None,
                    note: str | None = None, transaction_ids: list[str] | None = None) -> dict:
    """filled → reconciled. ``custodian_data`` names what it was matched to
    (and any portfolio.transactions ids given must be this account's, in this
    security); ``manual`` needs a note saying how it was confirmed."""
    if method not in RECONCILE_METHOD_LABELS:
        raise ExecutionError(f"method must be one of {list(RECONCILE_METHOD_LABELS)}")
    if method == "custodian_data" and not (reference or "").strip():
        raise ExecutionError("matching to custodian data needs the custodian reference it was matched to")
    if method == "manual" and not (note or "").strip():
        raise ExecutionError("a manual confirmation needs a note saying how it was confirmed")
    txids = [_uuid(t, "transaction") for t in (transaction_ids or [])]
    async with pool.acquire() as conn:
        row = await load(conn, org_id, instruction_id)
        for t in txids:
            ok = await conn.fetchval(
                "SELECT 1 FROM portfolio.transactions x JOIN portfolio.positions p ON p.id = x.position_id "
                "JOIN portfolio.assets a ON a.id = p.asset_id WHERE x.id = $1::uuid AND x.org_id = $2::uuid "
                "AND p.account_id = $3::uuid AND a.global_security_id = $4::uuid AND x.system_to IS NULL",
                t, str(org_id), str(row["account_id"]), str(row["global_security_id"]))
            if not ok:
                raise ExecutionError(f"transaction {t} is not this account's, in this security")
    now = datetime.now(timezone.utc)
    new, _ = await _transition(
        pool, org_id=org_id, instruction_id=instruction_id, actor_id=actor_id, allowed=("filled",),
        what="reconcile", action="reconcile", to_status="reconciled",
        fields=lambda a: {"reconciled_by": a["id"], "reconciled_at": now, "reconcile_method": method,
                          "reconcile_reference": (reference or "").strip() or None, "reconcile_note": note},
        detail={"method": method, "reference": reference, "note": note, "transaction_ids": txids})
    return new


async def close(pool, *, org_id, instruction_id, actor_id, note: str | None = None) -> dict:
    now = datetime.now(timezone.utc)
    new, _ = await _transition(
        pool, org_id=org_id, instruction_id=instruction_id, actor_id=actor_id, allowed=("reconciled",),
        what="close", action="close", to_status="closed",
        fields=lambda a: {"closed_by": a["id"], "closed_at": now}, detail={"note": note})
    return new


async def cancel(pool, *, org_id, instruction_id, actor_id, reason: str) -> tuple[dict, EventRef]:
    """Any open state → cancelled, with a reason. An open approval task is
    withdrawn on the engine first."""
    if not (reason or "").strip():
        raise ExecutionError("a cancellation needs a reason")
    async with pool.acquire() as conn:
        actor = await _human(conn, actor_id, org_id)
        row = await load(conn, org_id, instruction_id)
    _need(row, OPEN_STATUSES, "cancel")
    if row["status"] == "pending_approval" and row["approval_task_id"]:
        try:
            await HT.cancel_task(pool, org_id=org_id, task_id=row["approval_task_id"], actor_id=actor["id"],
                                 reason=f"instruction cancelled: {reason}")
        except HT.HumanTaskConflict:
            pass  # already decided — the transition below re-checks the status
    now = datetime.now(timezone.utc)
    return await _transition(
        pool, org_id=org_id, instruction_id=instruction_id, actor_id=actor["id"], allowed=OPEN_STATUSES,
        what="cancel", action="cancel", to_status="cancelled",
        fields=lambda a: {"cancelled_by": a["id"], "cancelled_at": now, "cancel_reason": reason.strip()},
        detail={"reason": reason.strip(), "previous_task_id": str(row["approval_task_id"])
                if row["approval_task_id"] else None})


# ── Execute-by handling (the only automatic step, always logged) ────────────
async def expire_due(pool, *, today: date | None = None, org_id=None) -> dict:
    """Apply each overdue open instruction's ``if_not_filled`` policy.

    * not yet working (draft / pending approval), or policy ``cancel`` → expired
    * ``carry`` (working) → execute_by moves to today; still working
    * ``ask`` (working) → one todo per decision-maker; nothing changes status
    Logged with no actor (the CHECK allows it for these three actions only).
    The caller supplies the RLS context (the cron tick runs in platform scope).
    """
    from services.workflow_engine import cancel_user_task

    today = today or date.today()
    out = {"expired": [], "carried": [], "asked": []}
    async with pool.acquire() as conn:
        args: list = [today]
        sql = (f"SELECT id, org_id FROM {T_INS} WHERE system_to IS NULL AND status = ANY($2::text[]) "
               "AND execute_by < $1")
        args.append(list(OPEN_STATUSES))
        if org_id:
            args.append(str(org_id))
            sql += " AND org_id = $3::uuid"
        else:
            # the cross-org sweep (the cron tick) never acts on a test org (services.orgs); a verify
            # passes its own org_id explicitly
            from services import orgs as ORGS

            sql += " AND " + ORGS.not_test_org_sql(f"{T_INS}.org_id")
        due = await conn.fetch(sql + " ORDER BY execute_by, id", *args)
    for d in due:
        oid, iid = str(d["org_id"]), str(d["id"])
        refs = []
        async with pool.acquire() as conn:
            async with conn.transaction():
                row = await load(conn, oid, iid, for_update=True)
                if row["status"] not in OPEN_STATUSES or row["execute_by"] >= today:
                    continue
                policy = row["if_not_filled"]
                working = row["status"] in WORKING_STATUSES
                if working and policy == "carry":
                    await _update(conn, iid, {"execute_by": today})
                    refs.append(await _event(conn, org_id=oid, instruction_id=iid, action="carry",
                                             from_status=row["status"], to_status=row["status"], actor_id=None,
                                             detail={"from_execute_by": row["execute_by"], "to_execute_by": today}))
                    out["carried"].append(iid)
                elif working and policy == "ask":
                    already = await conn.fetchval(
                        f"SELECT 1 FROM {T_EVT} WHERE instruction_id = $1::uuid AND action = 'ask' "
                        "AND detail->>'execute_by' = $2", iid, row["execute_by"].isoformat())
                    if already:
                        continue
                    recipients = {str(m) for m in row["maker_ids"]}
                    recipients |= {str(r["id"]) for r in await conn.fetch(
                        """SELECT DISTINCT u.id FROM users u JOIN user_roles ur ON ur.user_id = u.id
                           JOIN roles r ON r.id = ur.role_id AND r.org_id = u.org_id
                           JOIN role_permissions rp ON rp.role_id = ur.role_id
                           JOIN permissions p ON p.id = rp.permission_id
                           WHERE u.org_id = $1::uuid AND p.name = $2 AND u.is_active
                             AND COALESCE(u.status, 'active') = 'active'""", oid, MANAGE)}
                    for uid in sorted(recipients):
                        await conn.execute(
                            "INSERT INTO member_todos (org_id, user_id, kind, category, source, related_type, "
                            "related_id, title, detail, action_key, priority, status) VALUES ($1::uuid, $2::uuid, "
                            "'actual', 'execution', $3, 'execution_instruction', $4::uuid, $5, $6, $7, 15, 'open')",
                            oid, uid, TODO_SOURCE_ASK, iid,
                            f"Not filled by {row['execute_by'].isoformat()}: carry or cancel?",
                            "The instruction is still working past its execute-by date. Decide whether to carry it "
                            "or cancel it.", f"/portfolio/instructions/{iid}")
                    refs.append(await _event(conn, org_id=oid, instruction_id=iid, action="ask",
                                             from_status=row["status"], to_status=row["status"], actor_id=None,
                                             detail={"execute_by": row["execute_by"].isoformat(),
                                                     "notified": sorted(recipients)}))
                    out["asked"].append(iid)
                else:
                    reason = (f"not filled by the execute-by date {row['execute_by'].isoformat()} (if not filled: "
                              f"{IF_NOT_FILLED_LABELS[policy].lower()})" if working else
                              f"not approved and sent by the execute-by date {row['execute_by'].isoformat()}")
                    task_id = row["approval_task_id"] if row["status"] == "pending_approval" else None
                    await _update(conn, iid, {"status": "expired", "expired_at": datetime.now(timezone.utc),
                                              "expiry_reason": reason})
                    refs.append(await _event(conn, org_id=oid, instruction_id=iid, action="expire",
                                             from_status=row["status"], to_status="expired", actor_id=None,
                                             detail={"reason": reason, "task_id": str(task_id) if task_id else None}))
                    out["expired"].append(iid)
        if refs and refs[0].action == "expire" and refs[0].payload.get("task_id"):
            try:
                await cancel_user_task(pool, uuid.UUID(refs[0].payload["task_id"]), None,
                                       "the instruction expired at its execute-by date")
            except Exception:  # noqa: BLE001 — the task may already be decided; the expiry stands
                pass
        await publish(pool, refs)
    return out


async def expire_tick(pool, *, today: date | None = None) -> dict:
    """The cron entrypoint's step: :func:`expire_due` across every org, in
    platform scope (contextvars, so every pooled transaction SET LOCALs it)."""
    from services.database import reset_rls_context, set_rls_context

    tokens = set_rls_context(None, True)
    try:
        return await expire_due(pool, today=today)
    finally:
        reset_rls_context(tokens)


# ── Pending effect (for later sprints) ──────────────────────────────────────
async def pending_differences(conn, org_id, account_id=None) -> list[dict]:
    """Open instructions as signed pending differences per (account, security):
    what the account will look like once the open instructions are done."""
    args: list = [str(org_id), list(OPEN_STATUSES)]
    sql = (f"SELECT i.*, COALESCE((SELECT sum(f.quantity) FROM {T_FILL} f WHERE f.instruction_id = i.id "
           "AND f.valid_to IS NULL AND f.system_to IS NULL), 0) AS filled_quantity, "
           f"COALESCE((SELECT sum(f.quantity * f.price) FROM {T_FILL} f WHERE f.instruction_id = i.id "
           "AND f.valid_to IS NULL AND f.system_to IS NULL), 0) AS filled_notional "
           f"FROM {T_INS} i WHERE i.org_id = $1::uuid AND i.system_to IS NULL AND i.status = ANY($2::text[])")
    if account_id:
        args.append(_uuid(account_id, "account"))
        sql += " AND i.account_id = $3::uuid"
    rows = await conn.fetch(sql + " ORDER BY i.account_id, i.global_security_id, i.created_at", *args)
    agg: dict[tuple[str, str], dict] = {}
    for r in rows:
        sign = Decimal(1) if r["side"] == "buy" else Decimal(-1)
        k = (str(r["account_id"]), str(r["global_security_id"]))
        a = agg.setdefault(k, {"account_id": k[0], "global_security_id": k[1], "pending_quantity": Decimal(0),
                               "pending_notional": Decimal(0), "instructions": []})
        if r["quantity"] is not None:
            a["pending_quantity"] += sign * (_dec(r["quantity"]) - _dec(r["filled_quantity"]))
        else:
            a["pending_notional"] += sign * max(Decimal(0), _dec(r["notional"]) - _dec(r["filled_notional"]))
        a["instructions"].append({"id": str(r["id"]), "status": r["status"], "side": r["side"]})
    return list(agg.values())


# ── Lists and detail ────────────────────────────────────────────────────────
def _num(v):
    return None if v is None else str(v)


def instruction_out(r: dict, *, sec: dict | None = None, acct: dict | None = None,
                    filled_quantity=None) -> dict:
    return {
        "id": str(r["id"]), "status": r["status"], "side": r["side"], "quantity": _num(r["quantity"]),
        "notional": _num(r["notional"]), "price_kind": r["price_kind"], "limit_price": _num(r["limit_price"]),
        "range_low": _num(r["range_low"]), "range_high": _num(r["range_high"]),
        "reference_price": _num(r["reference_price"]),
        "reference_at": r["reference_at"].isoformat() if r["reference_at"] else None,
        "wash_sale_notes": r["wash_sale_notes"], "executor_type": r["executor_type"],
        "executor_name": r["executor_name"], "executor_contact": r["executor_contact"],
        "execute_by": r["execute_by"].isoformat(), "if_not_filled": r["if_not_filled"], "source": r["source"],
        "source_ref": str(r["source_ref"]) if r["source_ref"] else None, "routed_via": r["routed_via"],
        "routing_reason": r["routing_reason"], "notes": r["notes"], "account_id": str(r["account_id"]),
        "global_security_id": str(r["global_security_id"]), "maker_id": str(r["maker_id"]),
        "maker_ids": [str(m) for m in r["maker_ids"]],
        "approval_task_id": str(r["approval_task_id"]) if r["approval_task_id"] else None,
        "approved_by": str(r["approved_by"]) if r["approved_by"] else None,
        "approved_at": r["approved_at"].isoformat() if r["approved_at"] else None,
        "sent_channel": r["sent_channel"], "sent_at": r["sent_at"].isoformat() if r["sent_at"] else None,
        "reconcile_method": r["reconcile_method"], "reconcile_reference": r["reconcile_reference"],
        "reconcile_note": r["reconcile_note"], "cancel_reason": r["cancel_reason"],
        "expiry_reason": r["expiry_reason"], "created_at": r["created_at"].isoformat(),
        "filled_quantity": _num(filled_quantity),
        "security": {"name": sec["name"], "ticker": (sec.get("identifiers") or {}).get("ticker"),
                     "cusip": (sec.get("identifiers") or {}).get("cusip")} if sec else None,
        "account": {"account_number_masked": acct["account_number_masked"], "custodian_code": acct["custodian_code"],
                    "is_held_away": acct["is_held_away"]} if acct else None,
    }


async def list_instructions(conn, org_id, *, status=None, account_id=None, executor_type=None, due_from=None,
                            due_to=None, limit: int = 200) -> list[dict]:
    args: list = [str(org_id)]
    sql = (f"SELECT i.*, a.account_number_masked, a.custodian_code, a.is_held_away, s.name AS security_name, "
           "(SELECT id_value FROM portfolio.securities_global_identifiers t WHERE t.global_security_id = s.id "
           " AND t.id_type = 'ticker' AND t.system_to IS NULL AND t.valid_to IS NULL LIMIT 1) AS ticker, "
           f"COALESCE((SELECT sum(f.quantity) FROM {T_FILL} f WHERE f.instruction_id = i.id AND f.valid_to IS NULL "
           "AND f.system_to IS NULL), 0) AS filled_quantity "
           f"FROM {T_INS} i JOIN public.accounts a ON a.id = i.account_id "
           "JOIN portfolio.securities_global s ON s.id = i.global_security_id "
           "WHERE i.org_id = $1::uuid AND i.system_to IS NULL")
    if status:
        if status not in STATUS_LABELS and status != "open":
            raise ExecutionError(f"unknown status {status!r}")
        if status == "open":
            args.append(list(OPEN_STATUSES))
            sql += f" AND i.status = ANY(${len(args)}::text[])"
        else:
            args.append(status)
            sql += f" AND i.status = ${len(args)}"
    if account_id:
        args.append(_uuid(account_id, "account"))
        sql += f" AND i.account_id = ${len(args)}::uuid"
    if executor_type:
        if executor_type not in EXECUTOR_LABELS:
            raise ExecutionError(f"unknown executor {executor_type!r}")
        args.append(executor_type)
        sql += f" AND i.executor_type = ${len(args)}"
    if due_from:
        args.append(due_from)
        sql += f" AND i.execute_by >= ${len(args)}"
    if due_to:
        args.append(due_to)
        sql += f" AND i.execute_by <= ${len(args)}"
    args.append(max(1, min(int(limit), 1000)))
    sql += f" ORDER BY i.execute_by, i.created_at DESC LIMIT ${len(args)}"
    out = []
    for r in await conn.fetch(sql, *args):
        d = dict(r)
        out.append(instruction_out(
            d, sec={"name": d["security_name"], "identifiers": {"ticker": d["ticker"]}},
            acct={"account_number_masked": d["account_number_masked"], "custodian_code": d["custodian_code"],
                  "is_held_away": d["is_held_away"]}, filled_quantity=d["filled_quantity"]))
    return out


async def _names(conn, ids) -> dict[str, str]:
    ids = [i for i in {str(x) for x in ids if x}]
    if not ids:
        return {}
    rows = await conn.fetch("SELECT id, COALESCE(full_name, email) AS n FROM users WHERE id = ANY($1::uuid[])", ids)
    return {str(r["id"]): r["n"] for r in rows}


async def detail(conn, org_id, instruction_id) -> dict:
    row = await load(conn, org_id, instruction_id)
    sec = await _security(conn, row["global_security_id"])
    acct = await load_account(conn, org_id, row["account_id"])
    fills = await _fills(conn, row["id"])
    lots = await _lots(conn, row["id"])
    events = [dict(e) for e in await conn.fetch(
        f"SELECT * FROM {T_EVT} WHERE instruction_id = $1 AND org_id = $2::uuid ORDER BY created_at, id",
        row["id"], str(org_id))]
    tasks = await HT.list_tasks(conn, org_id, kind=TASK_KIND, subject_type="execution_instruction",
                                subject_id=row["id"], limit=20)
    names = await _names(conn, [e["actor_id"] for e in events] + [f["entered_by"] for f in fills]
                         + [row["maker_id"], row["approved_by"]] + [t["maker_id"] for t in tasks]
                         + [t["completed_by"] for t in tasks])
    fq, fnot = _filled(fills)
    out = instruction_out(row, sec=sec, acct=acct, filled_quantity=fq)
    out["filled_notional"] = str(fnot)
    out["maker_name"] = names.get(str(row["maker_id"]))
    out["approved_by_name"] = names.get(str(row["approved_by"])) if row["approved_by"] else None
    out["lots"] = [{"id": str(x["id"]), "tax_lot_id": str(x["tax_lot_id"]), "quantity": str(x["quantity"]),
                    "acquired_date": x["acquired_date"].isoformat() if x["acquired_date"] else None,
                    "lot_quantity": _num(x["lot_quantity"]), "adjusted_cost": _num(x["adjusted_cost"]),
                    "term": x["term"]} for x in lots]
    out["fills"] = [{"id": str(f["id"]), "quantity": str(f["quantity"]), "price": str(f["price"]),
                     "filled_at": f["filled_at"].isoformat(), "lots": _loads(f["lots"]) or [],
                     "completes": f["completes"], "note": f["note"], "entered_by": str(f["entered_by"]),
                     "entered_by_name": names.get(str(f["entered_by"]))} for f in fills]
    out["timeline"] = [{"id": str(e["id"]), "action": e["action"], "from_status": e["from_status"],
                        "to_status": e["to_status"], "actor_id": str(e["actor_id"]) if e["actor_id"] else None,
                        "actor_name": names.get(str(e["actor_id"])) if e["actor_id"] else "System (execute-by)",
                        "detail": _loads(e["detail"]) or {}, "at": e["created_at"].isoformat()} for e in events]
    for t in tasks:
        t["maker_name"] = names.get(t["maker_id"] or "")
        t["completed_by_name"] = names.get(t["completed_by"] or "")
    out["tasks"] = tasks
    out["history_versions"] = int(await conn.fetchval(
        f"SELECT count(*) FROM {T_INS} WHERE archived_from_id = $1", row["id"]))
    from services import authority as AU      # rep7b — the manual DVP settlement detail, when there is one

    out["settlement"] = AU.settlement_out(await AU.settlement_of(conn, org_id, row["id"]))
    out["account_authority"] = AU.authority_out(acct)
    out["approval_refused"] = _approval_refusal(acct, row) if row["status"] in ("draft", "pending_approval") else None
    out["held_away"] = bool(acct["is_held_away"])
    return out


def vocabularies() -> dict:
    def pairs(d):
        return [{"key": k, "label": v} for k, v in d.items()]

    return {
        "statuses": pairs(STATUS_LABELS), "open_statuses": list(OPEN_STATUSES),
        "executor_types": pairs(EXECUTOR_LABELS), "letter_executor_types": list(LETTER_EXECUTORS),
        "sides": pairs(SIDE_LABELS), "price_kinds": pairs(PRICE_KIND_LABELS),
        "if_not_filled": pairs(IF_NOT_FILLED_LABELS), "sources": pairs(SOURCE_LABELS),
        "sent_channels": pairs(SENT_CHANNEL_LABELS), "reconcile_methods": pairs(RECONCILE_METHOD_LABELS),
        "actions": pairs(ACTION_LABELS), "documents": pairs(DOCUMENT_LABELS), "editable": [],
    }


def actions_for(row: dict, *, can_write: bool, can_approve: bool, user_id, task_open: bool) -> list[str]:
    """The SERVER's list of actions this caller may take on this instruction
    right now. The screen renders a control only for an action listed here."""
    s = row["status"]
    makers = {str(m) for m in row.get("maker_ids") or []}
    acts: list[str] = []
    if can_write:
        if s == "draft":
            acts += ["edit", "submit", "cancel"]
        elif s == "pending_approval":
            acts += ["cancel"]
        elif s == "approved":
            acts += ["mark_sent", "cancel"]
        elif s in ("sent", "partially_filled"):
            acts += ["fill", "cancel"]
        elif s == "filled":
            acts += ["reconcile"]
        elif s == "reconciled":
            acts += ["close"]
    if can_approve and s == "pending_approval" and task_open and str(user_id) not in makers:
        acts += ["approve", "reject"]
    if row.get("approval_refused"):
        # rep7b — the account's authority refuses it (e.g. a pre-rep7 draft on a read-only held-away account)
        acts = [a for a in acts if a not in ("submit", "approve")]
    if row.get("executor_type") in LETTER_EXECUTORS:
        acts.append("letter")
    if row.get("settlement"):
        acts.append("settlement_instruction")
    if can_write and s == "filled" and row.get("held_away"):
        acts.append("reconcile_from_held_away")
    return acts


# ── Letter of instruction ───────────────────────────────────────────────────
async def _account_salt(conn, org_id) -> str | None:
    """Read (never mint) the org's account-number salt."""
    from services.custody.base import SALT_SETTING_KEY

    v = await conn.fetchval("SELECT setting_value FROM org_settings WHERE org_id = $1::uuid AND setting_key = $2",
                            str(org_id), SALT_SETTING_KEY)
    v = _loads(v) if isinstance(v, str) and v.startswith('"') else v
    return v if isinstance(v, str) and v else None


async def letter(pool, *, org_id, instruction_id, actor_id, include_full_number: bool = False,
                 full_account_number: str | None = None) -> tuple[bytes, str]:
    """A PDF letter of instruction, generated ON DEMAND from the instruction
    (never stored — the instruction row is the source of truth).

    The account number is the stored MASK unless the caller explicitly asks
    for the full number AND supplies it: the database holds only the mask and a
    salted hash, so the full number is typed by the person, checked against the
    account's hash, used for this one PDF, and never stored or logged. The
    choice itself is logged (event + audit), never the digits.
    """
    from services.custody.base import AccountNumber
    from services.execution_letter import build_letter

    async with pool.acquire() as conn:
        actor = await _human(conn, actor_id, org_id)
        d = await detail(conn, org_id, instruction_id)
        if d["executor_type"] not in LETTER_EXECUTORS:
            raise ExecutionError("a letter of instruction is for a member or another adviser executing a "
                                 "held-away account; this instruction's executor is "
                                 f"{EXECUTOR_LABELS[d['executor_type']].lower()}")
        acct = await load_account(conn, org_id, d["account_id"])
        org_name = await conn.fetchval("SELECT name FROM organizations WHERE id = $1::uuid", str(org_id))
        number = None
        if include_full_number:
            if not (full_account_number or "").strip():
                raise ExecutionError("to include the full account number, type it — only the masked number is "
                                     "stored")
            number = AccountNumber(full_account_number)
            salt = await _account_salt(conn, org_id)
            if not salt or number.hashed(salt) != acct["account_number_hash"]:
                raise ExecutionError("that is not this account's number")
        elif full_account_number:
            raise ExecutionError("a full account number was given without choosing to include it")
    pdf = build_letter(d, org_name=org_name or "", account_number=number, prepared_by=_who_name(actor))
    async with pool.acquire() as conn:
        async with conn.transaction():
            ref = await _event(conn, org_id=org_id, instruction_id=d["id"], action="letter",
                               from_status=d["status"], to_status=d["status"], actor_id=actor["id"],
                               detail={"include_full_number": bool(include_full_number)})
            if include_full_number:
                await conn.execute(
                    "INSERT INTO audit_log (org_id, user_id, action, resource_type, resource_id, payload) "
                    "VALUES ($1::uuid, $2::uuid, 'execution.letter_full_account_number', 'execution_instruction', "
                    "$3::uuid, $4::jsonb)", str(org_id), str(actor["id"]), d["id"],
                    _dumps({"event_id": ref.id, "account_number_masked": acct["account_number_masked"],
                            "verified_against_hash": True}))
    await publish(pool, [ref])
    return pdf, f"letter-of-instruction-{d['id'][:8]}.pdf"


async def settlement_letter(pool, *, org_id, instruction_id, actor_id, include_full_number: bool = False,
                            full_account_number: str | None = None) -> tuple[bytes, str]:
    """rep7b — the DVP settlement instruction PDF for a managed account at
    another custodian, ON DEMAND (never stored). Masked unless the full number
    is explicitly chosen, typed and checked against the account's hash —
    exactly :func:`letter`'s rule. Logged as a 'letter' event naming the
    document; the digits never are."""
    from services.custody.base import AccountNumber
    from services.settlement_letter import build_settlement

    async with pool.acquire() as conn:
        actor = await _human(conn, actor_id, org_id)
        d = await detail(conn, org_id, instruction_id)
        st = d.get("settlement")
        if not st:
            raise ExecutionError("a DVP settlement instruction is for a managed account at another custodian, "
                                 "executed by a broker or trader; this instruction carries no settlement detail")
        acct = await load_account(conn, org_id, d["account_id"])
        org_name = await conn.fetchval("SELECT name FROM organizations WHERE id = $1::uuid", str(org_id))
        number = None
        if include_full_number:
            if not (full_account_number or "").strip():
                raise ExecutionError("to include the full account number, type it — only the masked number is "
                                     "stored")
            number = AccountNumber(full_account_number)
            salt = await _account_salt(conn, org_id)
            if not salt or number.hashed(salt) != acct["account_number_hash"]:
                raise ExecutionError("that is not this account's number")
        elif full_account_number:
            raise ExecutionError("a full account number was given without choosing to include it")
    pdf = build_settlement(d, st, org_name=org_name or "", account_number=number, prepared_by=_who_name(actor))
    async with pool.acquire() as conn:
        async with conn.transaction():
            ref = await _event(conn, org_id=org_id, instruction_id=d["id"], action="letter",
                               from_status=d["status"], to_status=d["status"], actor_id=actor["id"],
                               detail={"document": "dvp_settlement_instruction",
                                       "include_full_number": bool(include_full_number),
                                       "settlement_date": st["settlement_date"]})
            if include_full_number:
                await conn.execute(
                    "INSERT INTO audit_log (org_id, user_id, action, resource_type, resource_id, payload) "
                    "VALUES ($1::uuid, $2::uuid, 'execution.settlement_full_account_number', "
                    "'execution_instruction', $3::uuid, $4::jsonb)", str(org_id), str(actor["id"]), d["id"],
                    _dumps({"event_id": ref.id, "account_number_masked": acct["account_number_masked"],
                            "verified_against_hash": True}))
    await publish(pool, [ref])
    return pdf, f"dvp-settlement-{d['id'][:8]}.pdf"


async def reconcile_from_held_away(pool, *, org_id, instruction_id, actor_id, record_id, note: str | None = None
                                   ) -> dict:
    """rep7b — a FILLED instruction on a held-away account (advise, or managed
    at another custodian) reconciled when the held-away feed / import shows the
    trade: reconcile_method 'custodian_data' with the held-away record as the
    reference. The record must be a TRANSACTION of this account, same security
    and side (and quantity, when the record states one)."""
    async with pool.acquire() as conn:
        await _human(conn, actor_id, org_id)
        row = await load(conn, org_id, instruction_id)
        rec = await conn.fetchrow(
            """SELECT r.id, r.record_type, r.record_id, r.account_id, r.valid_to, t.transaction_type_code AS side,
                      t.quantity, a.global_security_id
               FROM public.held_away_records r
               JOIN portfolio.transactions t ON t.id = r.record_id AND t.system_to IS NULL
               JOIN portfolio.positions p ON p.id = t.position_id
               JOIN portfolio.assets a ON a.id = p.asset_id AND a.system_to IS NULL
               WHERE r.id = $1::uuid AND r.org_id = $2::uuid AND r.system_to IS NULL""",
            _uuid(record_id, "held-away record"), str(org_id))
    if rec is None or rec["record_type"] != "transaction":
        raise ExecutionNotFound("held-away transaction record not found")
    if str(rec["account_id"]) != str(row["account_id"]):
        raise ExecutionConflict("that held-away record is another account's")
    if str(rec["global_security_id"]) != str(row["global_security_id"]) or (rec["side"] or "") != row["side"]:
        raise ExecutionConflict("that held-away transaction is a different security or side")
    if rec["quantity"] is not None and row["quantity"] is not None and abs(Decimal(str(rec["quantity"]))) != Decimal(
            str(row["quantity"])):
        raise ExecutionConflict(f"that held-away transaction is {abs(Decimal(str(rec['quantity'])))} shares; the "
                                f"instruction is {row['quantity']}")
    return await reconcile(pool, org_id=org_id, instruction_id=instruction_id, actor_id=actor_id,
                           method="custodian_data", reference=f"held_away_record:{rec['id']}",
                           transaction_ids=[str(rec["record_id"])],
                           note=note or "matched to the held-away feed / import")


def _who_name(user: dict) -> str:
    return user.get("full_name") or user.get("email") or ""
