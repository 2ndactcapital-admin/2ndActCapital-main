"""cpsim05 (D4 / D5) — the AUTOMATIC execution of an approved proposal's trades at the SIMULATED counterparty, over
FIX 4.4 carried by HTTP (services/fix_sim_client.py). DEMO ONLY: it engages for the simulated route and nothing else.

Joe (2026-11-11): "The rebalancer has a maker/checker for the trades — we don't need a separate approval to execute the
FIX transaction — fully automatic for demo purposes." So, for an instruction routed ``fix_sim``:

  1. the APPROVAL OF RECORD — a human-maker proposal's instructions start approved (rep5 _verify_upstream); before
     anything is sent an explicit event ``approve_upstream`` records "approved via proposal maker-checker (simulated
     counterparty)" with the proposal, its maker, its checker and its approval task (never a silent skip). A SYSTEM
     proposal's instructions keep rep2's second-person approval; that approval (execution._apply_outcome) is the
     record and starts steps 2–7;
  2. NewOrderSingle (35=D);
  3. accepted → the EXISTING ``execution.mark_sent`` (channel ``fix_sim``, the FIX OrderID, actor = the proposal's
     checker — or the rep2 approver — note "automatic — simulated counterparty"); rejected → a ``custodian_reject``
     event + a to-do for that person (the instruction stays approved, the manual route still open);
  4. each ExecutionReport trade (150=F) → the EXISTING ``execution.enter_fill`` (same actor, note ``fix_sim exec
     <ExecID>``, ``custodian_exec_id`` = ExecID — the unique index books each once; the instruction's named lots are
     relieved in their order, so the tax engine's fill source can value them);
  after the batch:
  5. the post-fill sync (the existing /altruist/sync sequence: resolve_identity → sync_resolved_accounts, simulator
     environment);
  6. ``execution.reconcile(custodian_data)`` — reference = the OrderID, the synced ``sim-x-<ExecID>`` transactions of
     this account in this security (same rules; an unlinked asset → a to-do, never a forced match);
  7. ``loss_bank.ingest`` — the tax engine's own realized figures for the fills (D7 reads them).
Every step is an instruction event + audit row carrying ``on_behalf: "automatic"`` and the FIX exchange. A failure
leaves the instruction in its true state with an ``automatic_failed`` event and a to-do — never a partial booking hidden.

AFTER THE REQUEST (the cpsim03 webhooks-after-response lesson): the entry points only SCHEDULE a detached task; the
proposal-execution / approval request returns at once. ``drain()`` awaits in-flight steps (verify; shutdown).

THE GUARD (D5) — :func:`guard`, re-read at the moment of every send: routed ``fix_sim``; from a PROPOSAL (automatic
mode); the simulated FIX router connected; cpsim03's D2 route (all seven conditions); the per-trade rules; an approval
of record; not in an open block. Any one missing → refused with the reason and left for a person. The database refuses
an automatic / fix_sim event on any instruction not routed ``fix_sim`` (docs/cpsim05_part1.sql). The verb
``execution.fix_sim_auto`` is declared NEVER for unattended runs and is NOT a registered action: the entry points below
have exactly these call sites — services.rebalance_proposals.execute (the choke point), services.execution.
_apply_outcome (rep2 approval of a proposal-sourced fix_sim instruction) and routers.admin_counterparties
.advance_clock (collect the fills a clock advance made — never a new order).

A person may still send / collect a NON-proposal fix_sim instruction (``run_person`` — services.custodian_execution's
Send / Import, manage_execution, a human actor): the same steps with ``on_behalf: "person"``.
"""

from __future__ import annotations

import asyncio
import contextvars
import logging
from decimal import Decimal
from typing import Any

from services import execution as X

log = logging.getLogger(__name__)

ROUTE = CHANNEL = "fix_sim"
ON_BEHALF = "automatic"
VERB = "execution.fix_sim_auto"
TODO_SOURCE = "execution_fix_sim"
APPROVAL_NOTE = "approved via proposal maker-checker (simulated counterparty)"
SENT_NOTE = "automatic — simulated counterparty"
WORKING = ("sent", "partially_filled")

_tasks: set[asyncio.Task] = set()
_results: list[dict] = []
_transport: Any = None
#: the after-response gate of the request that scheduled a step (main.py's outermost middleware sets it): the step
#: waits for it, so it starts only once that request's response has been SENT — not merely after its commit
_gate: contextvars.ContextVar = contextvars.ContextVar("cpsim05_after_response_gate", default=None)
#: monotonic times: when each gated request finished and when each step started (the verify's timing proof)
timings: list[dict] = []


class AfterResponseGate:
    """Pure ASGI middleware (outermost): one Event per HTTP request, set after the response is sent. A step scheduled
    inside the request copies the context, so it sees this Event and waits for it."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        ev = asyncio.Event()
        token = _gate.set(ev)
        try:
            await self.app(scope, receive, send)
        finally:
            ev.set()
            ev.finished_at = asyncio.get_running_loop().time()
            _gate.reset(token)


def set_transport(transport: Any) -> None:
    """Test injection: the transport every FIX / sync call of the automatic step uses (None → the simulator transport
    altruist_oauth injects, else the network — never reached in a verify)."""
    global _transport
    _transport = transport


def _schedule(coro) -> asyncio.Task:
    gate = _gate.get()

    async def gated():
        if gate is not None:
            await gate.wait()
        loop = asyncio.get_running_loop()
        timings.append({"response_finished_at": getattr(gate, "finished_at", None), "started_at": loop.time(),
                        "gated": gate is not None})
        return await coro
    task = asyncio.get_running_loop().create_task(gated())
    _tasks.add(task)

    def done(t: asyncio.Task):
        _tasks.discard(t)
        if not t.cancelled() and t.exception() is not None:
            log.error("cpsim05 automatic step failed: %r", t.exception())
    task.add_done_callback(done)
    return task


async def drain(timeout: float = 300.0) -> list[dict]:
    """Await every in-flight automatic step (a step may schedule another — loop until none). Returns and clears what
    the steps reported."""
    loop_until = asyncio.get_running_loop().time() + timeout
    while _tasks:
        left = loop_until - asyncio.get_running_loop().time()
        if left <= 0:
            break
        await asyncio.wait(list(_tasks), timeout=left)
    out = list(_results)
    _results.clear()
    return out


def pending() -> int:
    return len(_tasks)


# ── entry points (the ONLY call sites are named in the module note) ─────────────────────────────────────────────────
def after_proposal_routed(pool, *, org_id, proposal: dict, routing: dict, instruction_ids: list[str]):
    """rebalance_proposals.execute, after its commits: a HUMAN-maker proposal routed fix_sim → steps 1–7 for each
    instruction, as the proposal's checker. A system proposal is not started here (rep2's approval starts it)."""
    if (routing or {}).get("routed_to") != ROUTE or not instruction_ids:
        return None
    if proposal.get("maker_kind") != "user" or not proposal.get("checker_id"):
        return None
    return _schedule(_run_batch(pool, str(org_id), [str(i) for i in instruction_ids], str(proposal["checker_id"]),
                                upstream={"proposal_id": str(proposal["id"]), "maker_id": str(proposal["maker_id"]),
                                          "checker_id": str(proposal["checker_id"]),
                                          "task_id": str(proposal["approval_task_id"])
                                          if proposal.get("approval_task_id") else None}))


def after_instruction_approved(pool, *, org_id, instruction: dict, actor_id):
    """execution._apply_outcome (approve): a proposal-sourced fix_sim instruction approved by rep2's second person
    (a SYSTEM proposal) → steps 2–7 as that approver."""
    if instruction.get("routed_via") != ROUTE or instruction.get("source") != "proposal" \
            or instruction.get("status") != "approved":
        return None
    return _schedule(_run_batch(pool, str(org_id), [str(instruction["id"])], str(actor_id), upstream=None))


def collect_after_advance(filled: list[dict]):
    """routers.admin_counterparties.advance_clock, after its commit: the FIX orders the advance filled → their
    instructions (sent automatically, channel fix_sim) collect the new fills (35=H) and run steps 4–7. Never sends an
    order. Blocks routed fix_sim collect through services.fix_sim_blocks."""
    ids = [f.get("client_order_id") for f in filled or [] if f.get("protocol") == "fix" and f.get("client_order_id")]
    if not ids:
        return None
    return _schedule(_collect(ids))


# ── the guard (D5) ──────────────────────────────────────────────────────────────────────────────────────────────────
async def guard(conn, org_id, row: dict, acct: dict, *, mode: str = ON_BEHALF) -> str | None:
    """The plain reason the simulated FIX path may NOT run for this instruction now, or None."""
    from services import custodian_execution as CX
    from services import execution_routers as RT
    from services.custody.trading import AltruistTradeAdapter

    if row["routed_via"] != ROUTE:
        return (f"not routed to the simulated FIX counterparty (routed {row['routed_via']}) — the automatic path is "
                "the simulated route's only")
    if mode == ON_BEHALF and row.get("source") != "proposal":
        return "not from a proposal — the automatic path runs only for an approved proposal's trades"
    if RT.simulated_router() is None:
        return "the simulated FIX router is not connected"
    sim = await CX.simulator_route(conn, org_id, acct,
                                   capability_custodian=AltruistTradeAdapter.simulator_capability_custodian)
    if not sim["ok"]:
        return f"the simulated custodian route is not available now ({sim['condition']}): {sim['reason']}"
    why = CX.trade_refusal([row])
    if why:
        return why
    if row["status"] == "approved" and not row.get("approved_by"):
        return "the instruction has no approval of record"
    try:
        await X._refuse_in_block(conn, row["id"], "send")
    except X.ExecutionConflict as exc:
        return str(exc)
    return None


# ── helpers ─────────────────────────────────────────────────────────────────────────────────────────────────────────
def _exchange(res: dict | None) -> dict | None:
    if not res:
        return None
    return {"sent": res["sent"], "received": res["received"]}


async def _todo(pool, org_id, user_id, iid, title: str, detail: str) -> None:
    from services import custodian_execution as CX

    async with pool.acquire() as conn:
        await CX._todo(conn, org_id=org_id, user_id=user_id, source=TODO_SOURCE, instruction_id=iid, title=title,
                       detail=detail)


async def _fail(pool, org_id, iid, actor_id, reason: str, *, res: dict | None = None, mode: str = ON_BEHALF) -> dict:
    """The instruction stays in its TRUE state: an automatic_failed event (only possible on a fix_sim instruction —
    the database refuses it elsewhere; then an audit row alone) + a to-do for the person the step acted for."""
    refs = []
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await X.load(conn, org_id, iid)
            if row["routed_via"] == ROUTE:
                refs.append(await X._event(conn, org_id=org_id, instruction_id=iid, action="automatic_failed",
                                           from_status=row["status"], to_status=row["status"], actor_id=actor_id,
                                           detail={"reason": reason[:1000], "on_behalf": mode, "simulated": True,
                                                   "fix": _exchange(res)}))
            else:
                await conn.execute(
                    "INSERT INTO audit_log (org_id, user_id, action, resource_type, resource_id, payload) VALUES "
                    "($1::uuid, $2::uuid, 'execution.fix_sim_refused', 'execution_instruction', $3::uuid, $4::jsonb)",
                    str(org_id), str(actor_id), str(iid), X._dumps({"reason": reason[:1000], "on_behalf": mode}))
    await X.publish(pool, refs)
    await _todo(pool, org_id, actor_id, iid, "The simulated FIX step stopped — decide what next",
                f"{reason[:600]} The instruction is {row['status']}: carry it out manually, or cancel it.")
    out = {"instruction_id": str(iid), "outcome": "failed", "reason": reason}
    _results.append(out)
    return out


def _relieve(lots: list[dict], used: dict[str, Decimal], qty: Decimal) -> list[tuple[str, Decimal]]:
    """The instruction's named lots, in their order, relieved for ``qty`` (what earlier fills used is skipped). Empty
    when the instruction names no lots (a buy, or a custodian-lot account — the fill then waits for the custodian)."""
    out, left = [], qty
    for lot in lots:
        if left <= 0:
            break
        lid = str(lot["tax_lot_id"])
        avail = Decimal(str(lot["quantity"])) - used.get(lid, Decimal(0))
        if avail <= 0:
            continue
        take = min(avail, left)
        out.append((lid, take))
        used[lid] = used.get(lid, Decimal(0)) + take
        left -= take
    return out if left == 0 else []


async def _book(pool, org_id, iid, actor_id, summary: dict, res: dict, mode: str) -> list[str]:
    """Each NEW trade report → enter_fill. Idempotent by ExecID (the column + unique index)."""
    import asyncpg

    from services import custodian_execution as CX
    from services import fix_sim_client as FC

    async with pool.acquire() as conn:
        row = await X.load(conn, org_id, iid)
        booked = await CX._booked(conn, iid)
        lots = await X._lots(conn, iid) if row["side"] == "sell" else []
        used: dict[str, Decimal] = {}
        for f in await X._fills(conn, iid):
            for x in X._loads(f["lots"]) or []:
                used[str(x["tax_lot_id"])] = used.get(str(x["tax_lot_id"]), Decimal(0)) + Decimal(str(x["quantity"]))
    trades = [t for t in summary["trades"] if t.get("exec_id") and t["exec_id"] not in booked]
    done = []
    for i, t in enumerate(trades):
        last = i == len(trades) - 1
        qty, px = Decimal(t["quantity"]), Decimal(t["price"])
        try:
            await X.enter_fill(
                pool, org_id=org_id, instruction_id=iid, actor_id=actor_id, quantity=qty, price=px,
                filled_at=FC.transact_time(t["transact_time"]), lots=_relieve(lots, used, qty),
                completes=bool(last and summary.get("status") == "filled"), note=f"fix_sim exec {t['exec_id']}",
                custodian_exec_id=t["exec_id"],
                detail_extra={"on_behalf": mode, "fix_exec_id": t["exec_id"], "simulated": True,
                              "fix": _exchange(res) if i == 0 else None})
        except asyncpg.UniqueViolationError:
            continue          # booked by a concurrent step — the index keeps it once
        done.append(t["exec_id"])
    return done


async def _approval_of_record(pool, org_id, iid, actor_id, upstream: dict) -> None:
    """Step 1 — once per instruction (a re-run writes nothing)."""
    async with pool.acquire() as conn:
        if await conn.fetchval(f"SELECT 1 FROM {X.T_EVT} WHERE instruction_id = $1::uuid AND action = 'approve_upstream'",
                               str(iid)):
            return
        async with conn.transaction():
            row = await X.load(conn, org_id, iid)
            ref = await X._event(conn, org_id=org_id, instruction_id=iid, action="approve_upstream",
                                 from_status=row["status"], to_status=row["status"], actor_id=actor_id,
                                 detail={"note": APPROVAL_NOTE, "on_behalf": ON_BEHALF, "simulated": True,
                                         "proposal_id": upstream["proposal_id"], "maker_id": upstream["maker_id"],
                                         "checker_id": upstream["checker_id"], "task_id": upstream.get("task_id"),
                                         "approved_by": str(row["approved_by"]) if row["approved_by"] else None})
    await X.publish(pool, [ref])


# ── one instruction ─────────────────────────────────────────────────────────────────────────────────────────────────
async def run_one(pool, org_id, iid, actor_id, *, upstream: dict | None = None, mode: str = ON_BEHALF,
                  transport: Any = None) -> dict:
    """Steps 1–4 for one instruction (send if approved; collect if already sent over FIX). Returns what happened."""
    from services import custodian_execution as CX
    from services import fix_sim_client as FC

    transport = transport if transport is not None else _transport
    res = None
    try:
        async with pool.acquire() as conn:
            row = await X.load(conn, org_id, iid)
            acct = await X.load_account(conn, org_id, row["account_id"])
            if row["status"] not in ("approved", *WORKING):
                return {"instruction_id": str(iid), "outcome": "nothing_to_do", "status": row["status"]}
            if row["status"] in WORKING and row.get("sent_channel") != CHANNEL:
                return {"instruction_id": str(iid), "outcome": "nothing_to_do", "status": row["status"],
                        "reason": "sent by another channel"}
            why = await guard(conn, org_id, row, acct, mode=mode)
        if why:
            verb = "not sent" if row["status"] == "approved" else "fills not collected"
            return await _fail(pool, org_id, iid, actor_id, f"{verb} {'automatically' if mode == ON_BEHALF else ''}: "
                                                            f"{why}".replace("  ", " "), mode=mode)
        if row["status"] == "approved" and upstream and mode == ON_BEHALF:
            await _approval_of_record(pool, org_id, iid, actor_id, upstream)
        async with pool.acquire() as conn:
            sim = await CX.simulator_route(conn, org_id, acct)
            sec = await X._security(conn, row["global_security_id"])
            sess = await FC.session(conn, org_id=str(org_id))
        # the connection is RELEASED before the HTTP call — no transaction is held while the counterparty answers
        if row["status"] == "approved":
            fields = FC.new_order_single(cl_ord_id=str(row["id"]), account=sim["external_account_id"],
                                         side=row["side"], sec=sec, quantity=row["quantity"],
                                         notional=row["notional"] if row["quantity"] is None else None,
                                         price_kind="limit" if row["price_kind"] == "limit" else "market",
                                         limit_price=row["limit_price"])
            res = await FC.send(sess, "D", fields, transport=transport)
        else:
            res = await FC.send(sess, "H", FC.status_request(cl_ord_id=str(row["id"]),
                                                             order_id=row.get("custodian_order_id"), side=row["side"]),
                                transport=transport)
        s = res["summary"]
        if s["session_reject"]:
            return await _fail(pool, org_id, iid, actor_id, f"the counterparty rejected the message: "
                                                            f"{s['session_reject']}", res=res, mode=mode)
        if row["status"] == "approved":
            if s["status"] == "rejected" or not s["order_id"]:
                reason = s["reject_reason"] or "the simulated counterparty refused the order"
                async with pool.acquire() as conn:
                    async with conn.transaction():
                        ref = await X._event(conn, org_id=org_id, instruction_id=iid, action="custodian_reject",
                                             from_status="approved", to_status="approved", actor_id=actor_id,
                                             detail={"reason": reason, "order_id": s["order_id"], "on_behalf": mode,
                                                     "channel": CHANNEL, "simulated": True, "fix": _exchange(res)})
                await X.publish(pool, [ref])
                await _todo(pool, org_id, actor_id, iid, "The simulated counterparty refused an order — decide what "
                            "next", f"Refused: {reason}. The instruction is still approved: carry it out manually "
                            "(Mark sent) or cancel it.")
                out = {"instruction_id": str(iid), "outcome": "rejected", "reason": reason, "fix": _exchange(res)}
                _results.append(out)
                return out
            await X.mark_sent(pool, org_id=org_id, instruction_id=iid, actor_id=actor_id, channel=CHANNEL,
                              note=f"{SENT_NOTE if mode == ON_BEHALF else 'simulated counterparty'} — FIX OrderID "
                                   f"{s['order_id']} ({s['status']}) at {sess['slug']} (SIMULATED)",
                              custodian_order_id=s["order_id"],
                              detail_extra={"on_behalf": mode, "simulated": True, "fix": _exchange(res)})
        booked = await _book(pool, org_id, iid, actor_id, s, res, mode)
        async with pool.acquire() as conn:
            cur = await X.load(conn, org_id, iid)
        out = {"instruction_id": str(iid), "outcome": "sent" if row["status"] == "approved" else "collected",
               "order_id": s["order_id"], "order_status": s["status"], "booked": booked, "status": cur["status"],
               "fix": _exchange(res)}
        _results.append(out)
        return out
    except Exception as exc:  # noqa: BLE001 — the instruction keeps its true state; the person gets a to-do
        log.exception("cpsim05 automatic step for %s failed", iid)
        return await _fail(pool, org_id, iid, actor_id, f"{type(exc).__name__}: {exc}", res=res, mode=mode)


# ── after the batch: sync, reconcile, the tax engine's gains ────────────────────────────────────────────────────────
async def after_batch(pool, org_id, ids: list[str], actor_id, *, mode: str = ON_BEHALF, transport: Any = None
                      ) -> dict:
    from services import loss_bank as LB
    from services.altruist_identity import resolve_identity
    from services.altruist_oauth import SIMULATOR, require_active_connection
    from services.altruist_positions_sync import sync_resolved_accounts

    transport = transport if transport is not None else _transport
    async with pool.acquire() as conn:
        rows = [await X.load(conn, org_id, i) for i in ids]
    filled = [r for r in rows if r["sent_channel"] == CHANNEL and r["status"] in ("filled", "partially_filled")]
    out = {"synced": False, "reconciled": [], "unmatched": [], "ingested": None}
    if not filled:
        return out
    try:
        async with pool.acquire() as conn:
            await require_active_connection(conn, org_id=str(org_id), environment=SIMULATOR)
            await resolve_identity(conn, org_id=str(org_id), environment=SIMULATOR, transport=transport)
            await sync_resolved_accounts(conn, org_id=str(org_id), environment=SIMULATOR, transport=transport)
        out["synced"] = True
    except Exception as exc:  # noqa: BLE001 — fills stay booked (true state); reconcile waits; a to-do says why
        for r in filled:
            await _fail(pool, org_id, r["id"], actor_id, f"the post-fill sync failed: {type(exc).__name__}: {exc} — "
                                                          "the fills are booked; reconcile after the next sync",
                        mode=mode)
        return out
    for r in filled:
        if r["status"] != "filled":
            continue
        try:
            async with pool.acquire() as conn:
                execs = [f["custodian_exec_id"] for f in await X._fills(conn, r["id"]) if f["custodian_exec_id"]]
                found = await conn.fetch(
                    "SELECT x.id, x.external_ref, (a.global_security_id = $4::uuid) AS same_security "
                    "FROM portfolio.transactions x JOIN portfolio.positions p ON p.id = x.position_id "
                    "JOIN portfolio.assets a ON a.id = p.asset_id WHERE x.org_id = $1::uuid AND p.account_id = $2::uuid "
                    "AND x.external_ref = ANY($3::text[]) AND x.system_to IS NULL", str(org_id), str(r["account_id"]),
                    ["sim-x-" + e for e in execs], str(r["global_security_id"]))
            refs = {f["external_ref"] for f in found if f["same_security"]}
            missing = [e for e in execs if "sim-x-" + e not in refs]
            if missing or not execs:
                unlinked = [f["external_ref"] for f in found if not f["same_security"]]
                why = (f"the synced transaction(s) for {len(missing)} execution(s) are not this account's in this "
                       "security" + (" — the synced asset is not linked to the security master" if unlinked else
                                     " — not in the feed yet"))
                out["unmatched"].append({"instruction_id": str(r["id"]), "reason": why})
                await _todo(pool, org_id, actor_id, r["id"], "Reconcile a simulated FIX fill by hand", why)
                continue
            await X.reconcile(pool, org_id=org_id, instruction_id=r["id"], actor_id=actor_id, method="custodian_data",
                              reference=r["custodian_order_id"], note=f"{SENT_NOTE}: matched the synced feed",
                              transaction_ids=[str(f["id"]) for f in found if f["same_security"]],
                              detail_extra={"on_behalf": mode, "simulated": True, "channel": CHANNEL})
            out["reconciled"].append(str(r["id"]))
        except Exception as exc:  # noqa: BLE001
            await _fail(pool, org_id, r["id"], actor_id, f"automatic reconcile failed: {type(exc).__name__}: {exc}",
                        mode=mode)
    try:
        out["ingested"] = (await LB.ingest(pool, org_id=org_id))["added"]
    except Exception as exc:  # noqa: BLE001 — the P/L panel then says "not yet computed"
        log.warning("cpsim05 loss_bank.ingest failed for %s: %r", org_id, exc)
    _results.append({"batch": [str(i) for i in ids], **out})
    return out


async def _run_batch(pool, org_id: str, ids: list[str], actor_id: str, *, upstream: dict | None) -> None:
    from services.database import reset_rls_context, set_rls_context

    tokens = set_rls_context(org_id, False)
    try:
        for iid in ids:
            await run_one(pool, org_id, iid, actor_id, upstream=upstream)
        await after_batch(pool, org_id, ids, actor_id)
    finally:
        reset_rls_context(tokens)


async def _collect(client_order_ids: list[str]) -> None:
    """Platform scope only to FIND the instructions (one query); each org's work then runs in its own org context."""
    import uuid as _uuid

    from services.database import get_pool, reset_rls_context, set_rls_context

    ids = []
    for c in client_order_ids:
        try:
            ids.append(str(_uuid.UUID(str(c))))
        except ValueError:
            continue
    if not ids:
        return
    pool = await get_pool()
    tokens = set_rls_context(None, True)
    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch(f"SELECT id, org_id, sent_by FROM {X.T_INS} WHERE id = ANY($1::uuid[]) AND "
                                    "system_to IS NULL AND sent_channel = $2 AND status = ANY($3::text[])",
                                    ids, CHANNEL, list(WORKING))
    finally:
        reset_rls_context(tokens)
    by_org: dict[str, list] = {}
    for r in rows:
        by_org.setdefault(str(r["org_id"]), []).append(r)
    for org, rs in by_org.items():
        tokens = set_rls_context(org, False)
        try:
            for r in rs:
                await run_one(pool, org, str(r["id"]), str(r["sent_by"]))
            for actor in {str(r["sent_by"]) for r in rs}:
                await after_batch(pool, org, [str(r["id"]) for r in rs if str(r["sent_by"]) == actor], actor)
        finally:
            reset_rls_context(tokens)
    from services import fix_sim_blocks as FB

    await FB.collect_blocks(ids)


# ── a person's path for a NON-proposal fix_sim instruction (custodian_execution Send / Import) ─────────────────────
async def run_person(pool, *, org_id, instruction_id, actor_id, transport: Any = None) -> dict:
    """Send (approved) or collect (sent) ONE fix_sim instruction as a PERSON, then sync / reconcile / ingest. The
    route gate (manage_execution) and the human actor are the caller's; the D5 guard runs here (mode 'person')."""
    out = await run_one(pool, org_id, instruction_id, actor_id, mode="person", transport=transport)
    out["after"] = await after_batch(pool, org_id, [str(instruction_id)], actor_id, mode="person", transport=transport)
    return out
