"""cpsim03 — the custodian-API route for execution instructions: who may take it (D2), "Send to custodian" (D5),
"Import fills" (D8), the custodian side of a cancel (D11) and the order-webhook to-do (D9).

The first time anything in Hollisworks leaves the manual route — so a PERSON decides every step that matters:
  * a person approves (unchanged: services.execution's maker-checker human task, services.sod);
  * a person presses "Send to custodian" (:func:`send_to_custodian`, manage_execution — the gate today's "Mark sent"
    has; services.sod gates approval only, so no new floor is invented);
  * a person imports the fills (:func:`import_fills`, manage_execution), each booked through the EXISTING
    ``execution.enter_fill`` with that person as ``entered_by``;
  * reconciling them against the synced custodian feed is the EXISTING ``execution.reconcile`` (custodian_data).
None of these is registered as an assistant / workflow action, a trigger or an unattended (S29b) verb — they are
declared ``never`` in services.workflow_autonomy and NOT registered (the CASH07 pattern). A webhook only raises a to-do.

ONLY A SIMULATED COUNTERPARTY. The route exists for the cpsim02 ``simulator`` environment alone; services talk to it
over HTTP (services/altruist_trading.py) and never read the simulator's own schema. A real Altruist-crosswalked account
NEVER takes it: the real Altruist route stays manual (equity_trading_api 'unknown', no real trading client).

D2 — an account routes to the simulator only when ALL hold (:func:`simulator_route`; the first one missing is the
reason, and the trade list then takes today's manual route exactly):
  1. it is NOT crosswalked ``ALTRUIST`` (a real Altruist account never routes to a simulator);
  2. it IS crosswalked ``ALTRUIST_SIMULATOR`` (cpsim02 D5);
  3. its org's ``counterparty_sim.allowed`` is on and names a counterparty (cpsim02 D2);
  4. its org has an active ``simulator`` connection;
  5. ``counterparty_sim_may_connect(org, household)`` — a test org freely, a real org's demo household only;
  6. authority × custody is integrated + managed (rep7b; route_trade_list decides it first, re-checked at send);
  7. the capability ``equity_trading_api`` of the simulator's custodian code (``altruist_simulator``) is an evidenced
     'yes' (custodian_capabilities.available — 'unknown' is not available). Written by scripts/cpsim03_capabilities.py.
Per trade (:func:`trade_refusal`): buy / sell only (shorts stay manual — rep13), no option leg (a CHECK makes option
terms manual-only — rep8), no acceptable-range price (the orders contract has market and limit only).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from services import execution as X

ROUTE = "custodian_api"
CHANNEL = "custodian_api"
ENVIRONMENT = "simulator"
SIMULATOR_CAPABILITY_CUSTODIAN = "altruist_simulator"
CAPABILITY_KEY = "equity_trading_api"
XWALK_SIMULATOR = "ALTRUIST_SIMULATOR"
XWALK_REAL = "ALTRUIST"
TODO_SOURCE_FILLS = "execution_custodian_fills"
TODO_SOURCE_REJECT = "execution_custodian_reject"
ORDER_EVENT_PREFIX = "order."
SENT_STATUSES = ("accepted", "working", "partially_filled", "filled")
CANCELLABLE = ("accepted", "working", "partially_filled")
#: D2 conditions, in the order they are checked (the verify removes each one in turn)
CONDITIONS = ("real_crosswalk", "crosswalk", "allowed", "connection", "demo_household", "authority", "capability")


def _plain(d) -> str | None:
    if d is None:
        return None
    d = Decimal(d)
    s = format(d, "f")
    return s.rstrip("0").rstrip(".") if "." in s else s


async def _xwalk(conn, org_id, account_id, source: str) -> str | None:
    return await conn.fetchval(
        "SELECT external_id FROM portfolio.external_references WHERE org_id = $1::uuid AND source_system = $2 "
        "AND record_type = 'account' AND record_id = $3::uuid ORDER BY last_seen DESC LIMIT 1",
        str(org_id), source, str(account_id))


def _refused(condition: str, reason: str) -> dict:
    return {"ok": False, "condition": condition, "reason": reason, "slug": None, "external_account_id": None}


async def simulator_route(conn, org_id, account: dict, *, capability_custodian: str | None = None) -> dict:
    """D2 — ``{ok, condition, reason, slug, external_account_id}``. Reads public tables only (the crosswalk, the org
    settings, the connection, the public may-connect wrapper, the capability matrix)."""
    from services import authority as AU
    from services.altruist_oauth import AltruistSimulatorRefused, get_active_connection, simulator_counterparty
    from services.custodian_capabilities import available, capability

    if await _xwalk(conn, org_id, account["id"], XWALK_REAL):
        return _refused("real_crosswalk", "the account is crosswalked to real Altruist — the real Altruist route stays "
                                          "manual (no published orders contract; equity_trading_api not evidenced)")
    ext = await _xwalk(conn, org_id, account["id"], XWALK_SIMULATOR)
    if not ext:
        return _refused("crosswalk", "the account is not crosswalked to a simulated custodian")
    try:
        slug = await simulator_counterparty(conn, org_id=str(org_id))
    except AltruistSimulatorRefused as exc:
        return _refused("allowed", str(exc))
    c = await get_active_connection(conn, org_id=str(org_id), environment=ENVIRONMENT)
    if c is None or c.get("status") != "connected":
        return _refused("connection", "the organization has no active connection to its simulated custodian")
    may = await conn.fetchval("SELECT public.counterparty_sim_may_connect($1::uuid, $2::uuid)", str(org_id),
                              str(account["household_id"]) if account.get("household_id") else None)
    if not may:
        return _refused("demo_household", "not a demo household: in a real organization only a household tagged demo "
                                          "may use a simulated custodian")
    e = AU.effective(account)
    if e["custody"] != "integrated" or e["authority"] != "managed":
        return _refused("authority", "only an integrated, managed account may be traded through a custodian API")
    capability_custodian = capability_custodian or SIMULATOR_CAPABILITY_CUSTODIAN   # read at call time (a verify
    cap = await capability(conn, capability_custodian, CAPABILITY_KEY)                # substitutes a fixture code)
    if not available(cap):
        return _refused("capability", f"{capability_custodian} {CAPABILITY_KEY} is '{cap.value}' (evidence: "
                                      f"{cap.evidence}) — unknown is treated as not available")
    return {"ok": True, "condition": None, "reason": f"simulated custodian {slug}", "slug": slug,
            "external_account_id": ext}


def trade_refusal(trades) -> str | None:
    """Per-trade rules of the custodian-API route (each refusal keeps the whole list manual)."""
    for i, t in enumerate(trades, start=1):
        side = t["side"] if isinstance(t, dict) else t.side
        option = t.get("option_terms") if isinstance(t, dict) else t.option_leg
        kind = t["price_kind"] if isinstance(t, dict) else t.price_guidance.kind
        if side not in ("buy", "sell"):
            return f"trade {i}: {X.SIDE_LABELS.get(side, side)} is carried out manually (the orders contract has buy " \
                   "and sell only)"
        if option:
            return f"trade {i}: an option leg is manual-only"
        if kind == "range":
            return f"trade {i}: an acceptable-range price is carried out manually (the orders contract has market " \
                   "and limit only)"
    return None


def order_body(row: dict, sec: dict, external_account_id: str) -> dict:
    """The POST /v2/orders body (contract §8.2): decimals as strings; client_order_id = the instruction id."""
    ids = sec.get("identifiers") or {}
    body = {"client_order_id": str(row["id"]), "account_id": external_account_id, "side": row["side"],
            "order_type": "limit" if row["price_kind"] == "limit" else "market", "time_in_force": "gtc"}
    if ids.get("ticker"):
        body["symbol"] = ids["ticker"]
    if ids.get("cusip"):
        body["cusip"] = ids["cusip"]
    if row["quantity"] is not None:
        body["quantity"] = _plain(row["quantity"])
    else:
        body["notional"] = _plain(row["notional"])
    if row["price_kind"] == "limit":
        body["limit_price"] = _plain(row["limit_price"])
    return body


async def _todo(conn, *, org_id, user_id, source: str, instruction_id, title: str, detail: str) -> str | None:
    """One open to-do per (source, instruction, person) — a second notification never piles up."""
    if not user_id:
        return None
    if await conn.fetchval("SELECT 1 FROM member_todos WHERE org_id = $1::uuid AND user_id = $2::uuid AND source = $3 "
                           "AND related_id = $4::uuid AND status = 'open'", str(org_id), str(user_id), source,
                           str(instruction_id)):
        return None
    return str(await conn.fetchval(
        "INSERT INTO member_todos (org_id, user_id, kind, category, source, related_type, related_id, title, detail, "
        "action_key, priority, status) VALUES ($1::uuid, $2::uuid, 'actual', 'execution', $3, 'execution_instruction', "
        "$4::uuid, $5, $6, $7, 15, 'open') RETURNING id", str(org_id), str(user_id), source, str(instruction_id),
        title, detail, f"/portfolio/instructions/{instruction_id}"))


def _api_error(exc: Exception) -> X.ExecutionConflict:
    from services.altruist_trading import AltruistOrderRefused

    if isinstance(exc, AltruistOrderRefused):
        return X.ExecutionConflict(f"the simulated custodian refused the request (HTTP {exc.status})"
                                   + (f": {exc.detail}" if exc.detail else ""))
    return X.ExecutionConflict(f"the simulated custodian could not be reached: {exc}")


_API_ERRORS: tuple = ()


def _api_errors() -> tuple:
    global _API_ERRORS
    if not _API_ERRORS:
        from services.altruist_oauth import (AltruistConfigError, AltruistNotConnected, AltruistOAuthError,
                                             AltruistSimulatorRefused)
        _API_ERRORS = (AltruistOAuthError, AltruistNotConnected, AltruistSimulatorRefused, AltruistConfigError)
    return _API_ERRORS


# ── D5: Send to custodian ───────────────────────────────────────────────────────────────────────────────────────────
async def send_to_custodian(pool, *, org_id, instruction_id, actor_id, transport: Any = None) -> dict:
    """An APPROVED, custodian-API-routed instruction → POST /v2/orders. Accepted (accepted / working / partially
    filled / filled) → the EXISTING mark_sent path with channel 'custodian_api' and the order id. Rejected → the
    instruction stays approved; a 'custodian_reject' event carries the reason and the sender gets a to-do."""
    from services import altruist_trading as AT

    async with pool.acquire() as conn:
        actor = await X._human(conn, actor_id, org_id)
        row = await X.load(conn, org_id, instruction_id)
        X._need(row, ("approved",), "send to the custodian")
        await X._refuse_in_block(conn, row["id"], "send")
        if row["routed_via"] == X.FIX_SIM_CHANNEL:
            if row["source"] == "proposal":
                raise X.ExecutionConflict("a proposal's simulated-FIX trade is sent automatically after its approval "
                                          "— see its events (Automatic step stopped → carry it out manually)")
            fix_person = True
        else:
            fix_person = False
    if fix_person:      # cpsim05 — a PERSON sends a non-proposal fix_sim instruction over simulated FIX
        from services import fix_sim_auto as FA

        out = await FA.run_person(pool, org_id=org_id, instruction_id=row["id"], actor_id=actor["id"],
                                  transport=transport)
        async with pool.acquire() as conn:
            cur = await X.load(conn, org_id, row["id"])
        return {"instruction": cur, "outcome": out.get("outcome"), "reason": out.get("reason"), "fix": out.get("fix"),
                "protocol": "fix", "simulated": True}
    async with pool.acquire() as conn:
        if row["routed_via"] != ROUTE:
            raise X.ExecutionConflict("this instruction was routed to manual execution — a person carries it out and "
                                      "marks it sent (Mark sent)")
        why = trade_refusal([row])
        if why:
            raise X.ExecutionConflict(why)
        acct = await X.load_account(conn, org_id, row["account_id"])
        from services import authority as AU

        e = AU.effective(acct)
        if e["custody"] != "integrated" or e["authority"] != "managed":
            raise X.ExecutionConflict("only an integrated, managed account may be traded through a custodian API — "
                                      "mark it sent manually")
        route = await simulator_route(conn, org_id, acct)
        if not route["ok"]:
            raise X.ExecutionConflict(f"the custodian route is not available now: {route['reason']} — carry the "
                                      "instruction out manually and mark it sent")
        sec = await X._security(conn, row["global_security_id"])
        body = order_body(row, sec, route["external_account_id"])
        try:
            session = await AT.order_session(conn, org_id=str(org_id))
        except _api_errors() as exc:
            raise _api_error(exc) from exc
    # fix 1: the connection (and the transaction the RLS pool opened on it) is released BEFORE the HTTP call — the
    # API never holds a transaction open while it waits on the custodian. Every gate above is re-checked by mark_sent.
    try:
        http_status, order = await AT.submit_with_session(session, order=body, transport=transport)
    except _api_errors() as exc:
        raise _api_error(exc) from exc
    if order.get("client_order_id") != str(row["id"]) or not order.get("order_id"):
        raise X.ExecutionConflict("the simulated custodian answered with an order that is not this instruction's")
    status = order.get("status")
    if status in SENT_STATUSES:
        new = await X.mark_sent(pool, org_id=org_id, instruction_id=row["id"], actor_id=actor["id"], channel=CHANNEL,
                                note=f"order {order['order_id']} at simulated custodian {route['slug']} — {status} "
                                     "(SIMULATED)", custodian_order_id=order["order_id"])
        return {"instruction": new, "order": order, "outcome": "sent", "http_status": http_status,
                "counterparty": route["slug"]}
    reason = order.get("reject_reason") or f"the order is {status}"
    async with pool.acquire() as conn:
        async with conn.transaction():
            ref = await X._event(conn, org_id=org_id, instruction_id=row["id"], action="custodian_reject",
                                 from_status="approved", to_status="approved", actor_id=actor["id"],
                                 detail={"reason": reason, "order_id": order["order_id"], "order_status": status,
                                         "counterparty": route["slug"], "simulated": True})
            todo = await _todo(conn, org_id=org_id, user_id=actor["id"], source=TODO_SOURCE_REJECT,
                               instruction_id=row["id"], title="The custodian refused an order — decide what next",
                               detail=f"Refused: {reason}. The instruction is still approved: carry it out manually "
                                      "(Mark sent) or cancel it.")
    await X.publish(pool, [ref])
    async with pool.acquire() as conn:
        cur = await X.load(conn, org_id, row["id"])
    return {"instruction": cur, "order": order, "outcome": "rejected", "reason": reason, "todo_id": todo,
            "http_status": http_status, "counterparty": route["slug"]}


# ── D8: Import fills ────────────────────────────────────────────────────────────────────────────────────────────────
def _ts(v) -> datetime:
    t = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


async def _booked(conn, instruction_id) -> set[str]:
    return {r["custodian_exec_id"] for r in await conn.fetch(
        f"SELECT custodian_exec_id FROM {X.T_FILL} WHERE instruction_id = $1::uuid AND custodian_exec_id IS NOT NULL "
        "AND system_to IS NULL", str(instruction_id))}


async def _order_of(conn, org_id, row, transport):
    from services import altruist_trading as AT

    if not row.get("custodian_order_id"):
        raise X.ExecutionConflict("this instruction has no custodian order")
    try:
        order = await AT.fetch_order(conn, org_id=str(org_id), order_id=row["custodian_order_id"], transport=transport)
    except _api_errors() as exc:
        raise _api_error(exc) from exc
    if order.get("client_order_id") != str(row["id"]) or order.get("order_id") != row["custodian_order_id"]:
        raise X.ExecutionConflict("the custodian's order is not this instruction's")
    return order


async def import_fills(pool, *, org_id, instruction_id, actor_id, transport: Any = None) -> dict:
    """GET the order and book each NEW execution through execution.enter_fill (entered_by = the importer). Idempotent
    by exec id (a column + a unique index); never more than instructed (enter_fill refuses)."""
    import asyncpg

    async with pool.acquire() as conn:
        actor = await X._human(conn, actor_id, org_id)
        row = await X.load(conn, org_id, instruction_id)
        X._need(row, ("sent", "partially_filled"), "import fills for")
    if row["sent_channel"] == X.FIX_SIM_CHANNEL:     # cpsim05 — collect over FIX (35=H), book, sync, reconcile
        from services import fix_sim_auto as FA

        out = await FA.run_person(pool, org_id=org_id, instruction_id=row["id"], actor_id=actor["id"],
                                  transport=transport)
        async with pool.acquire() as conn:
            cur = await X.load(conn, org_id, row["id"])
        return {"instruction": cur, "booked": out.get("booked") or [], "already_booked": [], "fix": out.get("fix"),
                "protocol": "fix", "simulated": True}
    async with pool.acquire() as conn:
        if row["sent_channel"] != CHANNEL:
            raise X.ExecutionConflict("this instruction was not sent to a custodian API — enter its fills by hand")
        order = await _order_of(conn, org_id, row, transport)
        booked = await _booked(conn, row["id"])
    execs = [e for e in (order.get("executions") or []) if e.get("exec_id") and e["exec_id"] not in booked]
    done, skipped = [], [e["exec_id"] for e in (order.get("executions") or []) if e.get("exec_id") in booked]
    new = row
    for i, e in enumerate(execs):
        last = i == len(execs) - 1
        try:
            new = await X.enter_fill(
                pool, org_id=org_id, instruction_id=row["id"], actor_id=actor["id"], quantity=Decimal(e["quantity"]),
                price=Decimal(e["price"]), filled_at=_ts(e["executed_at"]),
                completes=bool(last and order.get("status") == "filled"),
                note=f"custodian_api exec {e['exec_id']}", custodian_exec_id=e["exec_id"])
        except asyncpg.UniqueViolationError:
            skipped.append(e["exec_id"])          # booked by a concurrent import — the index keeps it once
            continue
        except X.ExecutionError as exc:
            raise X.ExecutionConflict(f"booked {len(done)} execution(s); execution {e['exec_id']} was refused: {exc}") \
                from exc
        done.append(e["exec_id"])
    async with pool.acquire() as conn:
        cur = await X.load(conn, org_id, row["id"])
    return {"instruction": cur, "order": order, "booked": done, "already_booked": skipped}


# ── D11: the custodian side of a cancel ────────────────────────────────────────────────────────────────────────────
async def before_cancel(pool, *, org_id, row: dict, actor: dict, transport: Any = None):
    """Called by execution.cancel before its own transition. None → cancel as usual. Otherwise the instruction STAYS
    and this returns ``(row, EventRef)`` of a 'custodian_cancel' event. Raises ExecutionConflict when the custodian
    refuses (the order is already filled)."""
    from services import altruist_trading as AT

    if not row.get("custodian_order_id") or row["status"] not in ("sent", "partially_filled"):
        return None
    async with pool.acquire() as conn:
        order = await _order_of(conn, org_id, row, transport)
        if order.get("status") in CANCELLABLE:
            try:
                order = await AT.cancel_order(conn, org_id=str(org_id), order_id=row["custodian_order_id"],
                                              transport=transport)
            except _api_errors() as exc:
                if getattr(exc, "status", None) == 409:
                    raise X.ExecutionConflict(f"the custodian refused the cancel: {exc.detail or 'conflict'} — import "
                                              "the fills instead") from exc
                raise _api_error(exc) from exc
            cancelled_now = True
        elif order.get("status") == "cancelled":
            cancelled_now = False
        else:
            raise X.ExecutionConflict(f"the custodian order is already {order.get('status')} — it cannot be cancelled; "
                                      "import the fills instead")
        booked = await _booked(conn, row["id"])
    execs = [e["exec_id"] for e in (order.get("executions") or [])]
    unbooked = [x for x in execs if x not in booked]
    if not execs or (not cancelled_now and not unbooked):
        return None          # nothing was executed (or everything is booked and the order was already cancelled)
    if not cancelled_now and unbooked:
        raise X.ExecutionConflict(f"the custodian order was cancelled after {len(execs)} execution(s); import the "
                                  f"{len(unbooked)} not yet booked before cancelling the instruction")
    async with pool.acquire() as conn:
        async with conn.transaction():
            ref = await X._event(conn, org_id=org_id, instruction_id=row["id"], action="custodian_cancel",
                                 from_status=row["status"], to_status=row["status"], actor_id=actor["id"],
                                 detail={"order_id": row["custodian_order_id"], "executions": len(execs),
                                         "not_booked": len(unbooked), "simulated": True,
                                         "reason": f"custodian order cancelled after {len(execs)} execution(s) — the "
                                                   "instruction stays partially filled: import the fills, then cancel "
                                                   "it or let its if-not-filled policy apply"})
        cur = await X.load(conn, org_id, row["id"])
    return cur, ref


# ── D9: an order webhook → a to-do for the sender (never a booking) ─────────────────────────────────────────────────
async def order_event_todo(conn, *, org_id, payload: dict) -> dict | None:
    """For an ``order.*`` event signed by the simulator: the instruction named by client_order_id (this org's, with
    this custodian order id) gets a to-do for its sender. Runs on the receiver's connection (platform RLS context)."""
    et = str(payload.get("event_type") or "")
    if not et.startswith(ORDER_EVENT_PREFIX):
        return None
    try:
        iid = str(uuid.UUID(str(payload.get("client_order_id"))))
    except (TypeError, ValueError):
        return {"todo": None, "reason": "client_order_id is not an instruction id"}
    row = await conn.fetchrow(
        f"SELECT id, sent_by, status, custodian_order_id, sent_channel FROM {X.T_INS} WHERE id = $1::uuid AND "
        "org_id = $2::uuid AND system_to IS NULL", iid, str(org_id))
    if row is None or row["custodian_order_id"] != payload.get("order_id"):
        return {"todo": None, "reason": "no instruction of this organization carries that order"}
    if row["sent_channel"] == X.FIX_SIM_CHANNEL:     # cpsim05 — its fills are collected over FIX, no to-do
        return {"todo": None, "reason": "sent over simulated FIX — its fills are collected by the FIX path"}
    if row["status"] not in ("sent", "partially_filled") or not row["sent_by"]:
        return {"todo": None, "reason": f"the instruction is {row['status']}"}
    tid = await _todo(conn, org_id=org_id, user_id=row["sent_by"], source=TODO_SOURCE_FILLS, instruction_id=row["id"],
                      title="Fills available — import them",
                      detail=f"The simulated custodian reports {et} for order {payload.get('order_id')}. Open the "
                             "instruction and press Import fills (nothing is booked until you do).")
    return {"todo": tid, "instruction_id": iid}


# ── the instruction page ────────────────────────────────────────────────────────────────────────────────────────────
async def panel(conn, org_id, row: dict, acct: dict) -> dict:
    """What the instruction page shows about the custodian route (no network): routed via, whether Send is available
    now (D2 + the per-trade rules, re-read) and why not."""
    out = {"routed_via": row["routed_via"], "custodian_order_id": row.get("custodian_order_id"),
           "simulated": row["routed_via"] in (ROUTE, X.FIX_SIM_CHANNEL), "send_available": False,
           "send_refused": None, "counterparty": None, "import_available": False,
           "protocol": "fix" if row["routed_via"] == X.FIX_SIM_CHANNEL else ("rest" if row["routed_via"] == ROUTE
                                                                             else None)}
    if row["routed_via"] == X.FIX_SIM_CHANNEL:      # cpsim05
        r = await simulator_route(conn, org_id, acct)
        out["counterparty"] = r["slug"]
        out["automatic"] = row.get("source") == "proposal"
        if row["status"] == "approved" and not out["automatic"]:
            why = None if r["ok"] else r["reason"]
            why = why or trade_refusal([row])
            out["send_available"] = why is None
            out["send_refused"] = why
        elif row["status"] == "approved":
            out["send_refused"] = "sent automatically after the proposal's approval (simulated counterparty)"
        out["import_available"] = row["status"] in ("sent", "partially_filled") and \
            row.get("sent_channel") == X.FIX_SIM_CHANNEL
        return out
    if row["routed_via"] != ROUTE:
        if acct.get("custodian_code") and str(acct["custodian_code"]).upper().startswith("ALTRUIST"):
            r = await simulator_route(conn, org_id, acct)
            out["send_refused"] = ("routed to manual execution" + (f" — {r['reason']}" if not r["ok"] else ""))
        return out
    r = await simulator_route(conn, org_id, acct)
    out["counterparty"] = r["slug"]
    if row["status"] == "approved":
        why = None if r["ok"] else r["reason"]
        why = why or trade_refusal([row])
        out["send_available"] = why is None
        out["send_refused"] = why
    out["import_available"] = row["status"] in ("sent", "partially_filled") and row.get("sent_channel") == CHANNEL
    return out


async def order_view(pool, *, org_id, instruction_id, transport: Any = None) -> dict:
    """The live custodian order of an instruction, each execution marked booked or not, and the synced
    portfolio.transactions (this account's, source ref ``sim-x-<exec_id>``) the reconcile picker preselects."""
    async with pool.acquire() as conn:
        row = await X.load(conn, org_id, instruction_id)
        order = await _order_of(conn, org_id, row, transport)
        booked = await _booked(conn, row["id"])
        refs = ["sim-x-" + e["exec_id"] for e in (order.get("executions") or []) if e.get("exec_id")]
        synced = [] if not refs else [
            {"id": str(r["id"]), "external_ref": r["external_ref"], "trade_date": r["trade_date"].isoformat()
             if r["trade_date"] else None, "quantity": _plain(r["quantity"]), "price": _plain(r["price"]),
             "same_security": bool(r["same_security"])}
            for r in await conn.fetch(
                "SELECT x.id, x.external_ref, x.trade_date, x.quantity, x.price, "
                "(a.global_security_id = $4::uuid) AS same_security FROM portfolio.transactions x "
                "JOIN portfolio.positions p ON p.id = x.position_id JOIN portfolio.assets a ON a.id = p.asset_id "
                "WHERE x.org_id = $1::uuid AND p.account_id = $2::uuid AND x.external_ref = ANY($3::text[]) "
                "AND x.system_to IS NULL ORDER BY x.trade_date, x.external_ref", str(org_id), str(row["account_id"]),
                refs, str(row["global_security_id"]))]
    for e in order.get("executions") or []:
        e["booked"] = e.get("exec_id") in booked
    return {"order": order, "synced_transactions": synced, "simulated": True,
            "reconcile_reference": row["custodian_order_id"],
            "preselect_transaction_ids": [s["id"] for s in synced if s["same_security"]]}
