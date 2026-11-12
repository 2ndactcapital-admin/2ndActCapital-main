"""cpsim05 (D6) — a BLOCK on the simulated FIX route. Every member is at the SIMULATED counterparty (routed
``fix_sim``); the block's own maker-checker approval (rep9, unchanged) is its approval of record (Joe: the
maker-checker is the approval). Then, after the approval request (services.fix_sim_auto's after-response gate):

  1. NewOrderSingle for the BLOCK (591 present; Account = the block's executing account reference, else
     ``CUSTODIAN-DESK`` — a simulated account executes at the custodian desk, which has none — CPSIM05_NOTES §1c);
  2. accepted → the EXISTING ``blocks.mark_sent`` (channel ``fix_sim``; every member instruction sent with it);
  3. each trade report → the EXISTING ``blocks.enter_fill`` — the block's average price is recomputed by Hollisworks
     from the fills (AvgPx is never read);
  4. fully filled → the EXISTING ``blocks.allocate`` (the policy's allocation, each member's rep2 fill at the average
     price, its own lots);
  5. AllocationInstruction (35=J) with each allocation's crosswalked account and quantity at the Hollisworks average →
     AllocationInstructionAck (35=P) accepted → each allocation ``blocks.set_settlement(settled, custodian_data,
     reference = AllocID)``;
  6. the post-fill sync, each member reconciled against its synced ``sim-a-…`` transaction (the Ack names it, 467),
     ``loss_bank.ingest``.
A partial fill waits for a clock advance (routers.admin_counterparties → fix_sim_auto.collect_after_advance →
:func:`collect_blocks`). Any failure → the block stays in its true state and its approver gets a to-do. A person may
re-run the step (POST /execution/blocks/{id}/fix-sim, manage_blocks) — idempotent (ClOrdID, ExecIDs, AllocID).
Manual stays the default route and is always offered; the real ``fix`` router stays NotConnected.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from services import blocks as BK
from services import execution as X
from services import fix_sim_auto as FA

log = logging.getLogger(__name__)

ROUTE = FA.ROUTE
TODO_SOURCE = "block_fix_sim"
VENUE = "SIMULATED FIX"


async def member_refusal(conn, org_id, instruction: dict, account: dict) -> str | None:
    """Why this member cannot be on a fix_sim block (None = it can): routed fix_sim + cpsim03's D2 route now."""
    from services import custodian_execution as CX
    from services.custody.trading import AltruistTradeAdapter

    if instruction.get("routed_via") != ROUTE:
        return f"{account.get('account_number_masked')}: not routed to the simulated FIX counterparty"
    sim = await CX.simulator_route(conn, org_id, account,
                                   capability_custodian=AltruistTradeAdapter.simulator_capability_custodian)
    if not sim["ok"]:
        return f"{account.get('account_number_masked')}: {sim['reason']}"
    return None


def after_block_approved(pool, *, org_id, block: dict, actor_id):
    """blocks._apply_block_outcome (approve) of a fix_sim block → the steps, after the approval request."""
    if block.get("route") != ROUTE or block.get("status") != "approved":
        return None
    return FA._schedule(_run_scoped(pool, str(org_id), str(block["id"]), str(actor_id)))


async def _run_scoped(pool, org_id: str, block_id: str, actor_id: str) -> None:
    from services.database import reset_rls_context, set_rls_context

    tokens = set_rls_context(org_id, False)
    try:
        await run(pool, org_id=org_id, block_id=block_id, actor_id=actor_id)
    finally:
        reset_rls_context(tokens)


async def _fail(pool, org_id, block: dict, actor_id, reason: str, res: dict | None = None) -> dict:
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                "INSERT INTO audit_log (org_id, user_id, action, resource_type, resource_id, payload) VALUES ($1::uuid, "
                "$2::uuid, 'block.fix_sim_failed', 'trade_block', $3::uuid, $4::jsonb)", str(org_id), str(actor_id),
                str(block["id"]), X._dumps({"reason": reason[:1000], "simulated": True, "fix": FA._exchange(res)}))
            await BK._todo(conn, org_id, {str(actor_id)}, source=TODO_SOURCE, related_type="trade_block",
                           related_id=block["id"], title="The simulated FIX block step stopped — decide what next",
                           detail=f"{reason[:600]} The block is {block['status']}: re-run it (Run simulated FIX), "
                                  "or carry it out manually.", action_key=f"/portfolio/blocks/{block['id']}")
    out = {"block_id": str(block["id"]), "outcome": "failed", "reason": reason}
    FA._results.append(out)
    return out


async def _booked_execs(conn, block_id) -> set[str]:
    return {f["note"].split("fix_sim exec ", 1)[1].strip() for f in await BK.fills_of(conn, block_id)
            if (f.get("note") or "").startswith("fix_sim exec ")}


async def run(pool, *, org_id, block_id, actor_id, transport: Any = None) -> dict:
    """The block's simulated FIX steps from wherever it stands (idempotent)."""
    from services import custodian_execution as CX
    from services import execution_routers as RT
    from services import fix_sim_client as FC

    transport = transport if transport is not None else FA._transport
    res = None
    async with pool.acquire() as conn:
        block = await BK.load(conn, org_id, block_id)
    try:
        if block["route"] != ROUTE:
            return await _fail(pool, org_id, block, actor_id, "the block is not on the simulated FIX route")
        router = RT.simulated_router()
        if router is None:
            return await _fail(pool, org_id, block, actor_id, "the simulated FIX router is not connected")
        async with pool.acquire() as conn:
            allocs = await BK.allocations_of(conn, block["id"])
            for a in allocs:
                if a["status"] == "cancelled":
                    continue
                why = await member_refusal(conn, org_id, await X.load(conn, org_id, a["instruction_id"]),
                                           await X.load_account(conn, org_id, a["account_id"]))
                if why:
                    return await _fail(pool, org_id, block, actor_id, f"not sent: {why}")
            sec = await X._security(conn, block["global_security_id"])
            ea = await BK._execution_account(conn, org_id, block["execution_account_id"]) \
                if block.get("execution_account_id") else None
            sess = await FC.session(conn, org_id=str(org_id))
        # 1–2: the block order (connection released before the HTTP call)
        if block["status"] == "approved":
            res = await router.submit_block(block, allocs, session=sess, sec=sec, transport=transport,
                                            account_ref=(ea or {}).get("account_reference_masked")
                                            or FA_DESK)
            s = res["summary"]
            if s["session_reject"] or s["status"] == "rejected" or not s["order_id"]:
                return await _fail(pool, org_id, block, actor_id, "the simulated counterparty refused the block "
                                   f"order: {s['session_reject'] or s['reject_reason']}", res)
            block = await BK.mark_sent(pool, org_id=org_id, block_id=block["id"], actor_id=actor_id,
                                       channel=ROUTE, via_fix=True,
                                       note=f"automatic — simulated counterparty: FIX OrderID {s['order_id']} "
                                            f"({s['status']}) at {sess['slug']} (SIMULATED)")
        elif block["status"] in ("sent", "partially_filled", "filled", "allocated"):
            res = await router.execution_reports(block, session=sess, transport=transport)
            s = res["summary"]
            if s["session_reject"]:
                return await _fail(pool, org_id, block, actor_id, f"status request rejected: {s['session_reject']}",
                                   res)
        else:
            return {"block_id": str(block["id"]), "outcome": "nothing_to_do", "status": block["status"]}
        # 3: fills — Hollisworks recomputes the average (blocks.enter_fill); idempotent by ExecID in the note
        async with pool.acquire() as conn:
            done = await _booked_execs(conn, block["id"])
        for t in s["trades"]:
            if t["exec_id"] in done or block["status"] not in ("sent", "partially_filled"):
                continue
            block = await BK.enter_fill(pool, org_id=org_id, block_id=block["id"], actor_id=actor_id,
                                        quantity=Decimal(t["quantity"]), price=Decimal(t["price"]),
                                        filled_at=FC.transact_time(t["transact_time"]), venue=VENUE,
                                        note=f"fix_sim exec {t['exec_id']}")
        # 4: allocate once fully filled
        if block["status"] == "filled":
            block = await BK.allocate(pool, org_id=org_id, block_id=block["id"], actor_id=actor_id,
                                      note="automatic — simulated counterparty (Hollisworks average price)")
        if block["status"] != "allocated":
            out = {"block_id": str(block["id"]), "outcome": "working", "status": block["status"],
                   "fix": FA._exchange(res)}
            FA._results.append(out)
            return out
        # 5: the AllocationInstruction at the Hollisworks average; the Ack settles each allocation
        async with pool.acquire() as conn:
            allocs = [a for a in await BK.allocations_of(conn, block["id"]) if a["status"] == "allocated"
                      and Decimal(str(a["final_quantity"] or 0)) > 0]
            legs, by_acct = [], {}
            for a in allocs:
                ext = await CX._xwalk(conn, org_id, a["account_id"], CX.XWALK_SIMULATOR)
                legs.append((ext, Decimal(str(a["final_quantity"]))))
                by_acct[ext] = a
            fills = await BK.fills_of(conn, block["id"])
        if not legs:
            return await _fail(pool, org_id, block, actor_id, "nothing was allocated to any account")
        trade_date = max(f["filled_at"] for f in fills).astimezone(timezone.utc).strftime("%Y%m%d")
        if s.get("order_id") is None:
            s = (await router.execution_reports(block, session=sess, transport=transport))["summary"]
        ack = await router.send_allocations(block, allocs, session=sess, sec=sec, order_id=s["order_id"],
                                            avg_price=Decimal(str(block["average_price"])), trade_date=trade_date,
                                            legs=legs, transport=transport)
        alloc = ack["summary"]["alloc"] or {}
        if not alloc.get("accepted"):
            return await _fail(pool, org_id, block, actor_id, "the simulated counterparty rejected the "
                               f"AllocationInstruction: {alloc.get('text') or ack['summary']['session_reject']}", ack)
        for ext, a in by_acct.items():
            if a["settlement_status"] != "settled":
                await BK.set_settlement(pool, org_id=org_id, block_id=block["id"], allocation_id=a["id"],
                                        actor_id=actor_id, status="settled", method="custodian_data",
                                        reference=alloc["alloc_id"],
                                        note="automatic — simulated counterparty (AllocationInstructionAck accepted)")
        # 6: sync, reconcile each member against its sim-a transaction, the tax engine's gains
        out = {"block_id": str(block["id"]), "outcome": "allocated", "alloc_id": alloc["alloc_id"],
               "average_price": str(block["average_price"]), "fix": FA._exchange(ack),
               "after": await _after(pool, org_id, block, by_acct, alloc.get("legs") or {}, actor_id, transport)}
        FA._results.append(out)
        return out
    except Exception as exc:  # noqa: BLE001 — the block keeps its true state
        log.exception("cpsim05 block step for %s failed", block_id)
        async with pool.acquire() as conn:
            block = await BK.load(conn, org_id, block_id)
        return await _fail(pool, org_id, block, actor_id, f"{type(exc).__name__}: {exc}", res)


FA_DESK = "CUSTODIAN-DESK"


async def _after(pool, org_id, block: dict, by_acct: dict, refs: dict, actor_id, transport) -> dict:
    from services import loss_bank as LB
    from services.altruist_identity import resolve_identity
    from services.altruist_oauth import SIMULATOR
    from services.altruist_positions_sync import sync_resolved_accounts

    out = {"synced": False, "reconciled": [], "unmatched": []}
    try:
        async with pool.acquire() as conn:
            await resolve_identity(conn, org_id=str(org_id), environment=SIMULATOR, transport=transport)
            await sync_resolved_accounts(conn, org_id=str(org_id), environment=SIMULATOR, transport=transport)
        out["synced"] = True
    except Exception as exc:  # noqa: BLE001
        await _fail(pool, org_id, block, actor_id, f"the post-fill sync failed: {type(exc).__name__}: {exc}")
        return out
    for ext, a in by_acct.items():
        ref = refs.get(ext)
        async with pool.acquire() as conn:
            ins = await X.load(conn, org_id, a["instruction_id"])
            tx = await conn.fetch(
                "SELECT x.id FROM portfolio.transactions x JOIN portfolio.positions p ON p.id = x.position_id "
                "JOIN portfolio.assets a ON a.id = p.asset_id WHERE x.org_id = $1::uuid AND p.account_id = $2::uuid "
                "AND x.external_ref = $3 AND a.global_security_id = $4::uuid AND x.system_to IS NULL", str(org_id),
                str(a["account_id"]), ref, str(ins["global_security_id"])) if ref else []
        if ins["status"] != "filled" or not tx:
            out["unmatched"].append({"instruction_id": str(ins["id"]), "reference": ref, "status": ins["status"]})
            continue
        await X.reconcile(pool, org_id=org_id, instruction_id=ins["id"], actor_id=actor_id, method="custodian_data",
                          reference=ref, transaction_ids=[str(t["id"]) for t in tx],
                          note=f"automatic — simulated counterparty: block {block['id']} allocation",
                          detail_extra={"on_behalf": FA.ON_BEHALF, "simulated": True, "channel": ROUTE})
        out["reconciled"].append(str(ins["id"]))
    try:
        await LB.ingest(pool, org_id=org_id)
    except Exception as exc:  # noqa: BLE001
        log.warning("cpsim05 loss_bank.ingest after block %s: %r", block["id"], exc)
    return out


async def collect_blocks(client_order_ids: list[str]) -> None:
    """After a clock advance: fix_sim blocks still working whose id the advance filled → run (as the approver)."""
    from services.database import get_pool, reset_rls_context, set_rls_context

    pool = await get_pool()
    tokens = set_rls_context(None, True)
    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch(f"SELECT id, org_id, approved_by FROM {BK.T_BLK} WHERE id = ANY($1::uuid[]) AND "
                                    "system_to IS NULL AND route = $2 AND status = ANY($3::text[])",
                                    client_order_ids, ROUTE, ["sent", "partially_filled"])
    finally:
        reset_rls_context(tokens)
    for r in rows:
        await _run_scoped(pool, str(r["org_id"]), str(r["id"]), str(r["approved_by"]))


def now_utc() -> datetime:
    return datetime.now(timezone.utc)
