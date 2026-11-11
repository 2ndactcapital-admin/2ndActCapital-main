"""cpsim03 — the simulated custodian's ORDERS: accept an order, answer with the scripted outcome, report executions,
and move the book for fills (contract §8 — every field ASSUMED; docs/cpsim/CPSIM03_NOTES.md D6 / D7).

Every function takes a connection already inside a transaction AS ``cpsim_app`` (cpsim.db.tx / as_simulator). Nothing
here touches a table outside ``cpsim``; the security master and the platform prices are read-only reference data.

OUTCOMES (D6) — decided from the counterparty's behaviour row when the order is RECEIVED, at the last PLATFORM close on
or before the counterparty's clock date (``book.platform_price``):
  * the checks a real custodian would make come first: a security type other than equity / fund → rejected
    "unsupported instrument"; no price → rejected "no price"; a sell above the position → rejected "insufficient
    position" (a sell never takes a position below zero);
  * reject  → rejected with the behaviour's reason;
  * accept  → filled in full at the price — a limit buy only if price ≤ limit, a limit sell only if price ≥ limit,
              otherwise working;
  * partial → one execution for partial_pct of the quantity, rounded DOWN to the unit (one share for a whole-share
              quantity, 0.000001 otherwise); the remainder stays working until cancelled;
  * delay   → working until the clock advances, then filled as accept.
Working orders are re-evaluated on every clock advance (:func:`reevaluate` — delay orders and unmarketable limits; a
partial's remainder is not). A notional order converts at the fill price, fractional (6 dp, rounded down).

BOOK MOVES (D7) — for fills only, a contained step toward v2: each execution writes a buy / sell transaction
``sim-x-<exec_id>`` (trade date = the clock date, settle = + 1 US business day, no fees) and moves that account's
position: the snapshot dated the clock date is changed (closed and re-inserted, valid axis), or — when the latest
snapshot is older — copied forward to the clock date with the traded security moved (a first buy adds a row). Market
value of the traded row = quantity × the fill price. No cash ledger, no corporate actions, no fees. A later book upload
REPLACES the account as before (and so overwrites fills).

Bi-temporal: an order is SYSTEM axis (executions reference its id): a status change archives a copy first
(services.bitemporal.archive_live_row) and keeps the id. Executions are append-only (a trigger refuses any change).
"""

from __future__ import annotations

import hashlib
import re
from datetime import date, datetime, timedelta, timezone
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal, InvalidOperation

from cpsim import book as B
from cpsim import oauth as O
from services.bitemporal import archive_live_row
from services.us_business_days import next_business_day

STATUSES = ("accepted", "working", "partially_filled", "filled", "rejected", "cancelled")
WORKING = ("accepted", "working", "partially_filled")
SIDES = ("buy", "sell")
ORDER_TYPES = ("market", "limit")
TIFS = ("day", "gtc")
SUPPORTED_TYPES = ("equity", "fund")       # equities, ETFs and mutual funds (the security master files ETFs under fund)
FRACTION = Decimal("0.000001")
WHOLE = Decimal(1)
REJECT_NO_PRICE = "no price"
REJECT_UNSUPPORTED = "unsupported instrument"
REJECT_INSUFFICIENT = "insufficient position"
REJECT_DEFAULT = "rejected by the simulated custodian"
CLIENT_ID_RX = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,79}$")
DEC_RX = re.compile(r"^\d+(\.\d+)?$")
FIELDS = ("client_order_id", "account_id", "symbol", "cusip", "side", "quantity", "notional", "order_type",
          "limit_price", "time_in_force")
STATUS_LABELS = {"accepted": "Accepted", "working": "Working", "partially_filled": "Partially filled",
                 "filled": "Filled", "rejected": "Rejected", "cancelled": "Cancelled"}


class OrderError(Exception):
    status = 422

    def __init__(self, detail: str, errors: list | None = None):
        super().__init__(detail)
        self.errors = errors or []


class OrderNotFound(OrderError):
    status = 404


class OrderConflict(OrderError):
    status = 409


def _h(*parts) -> str:
    return hashlib.sha256("|".join(str(p) for p in parts).encode("utf-8")).hexdigest()[:16]


def order_id_for(counterparty_id, client_order_id: str) -> str:
    return "sim-o-" + _h(counterparty_id, "order", client_order_id)


def exec_id_for(counterparty_id, order_id: str, seq: int) -> str:
    return "sim-e-" + _h(counterparty_id, "exec", order_id, seq)


def _iso(ts) -> str | None:
    if ts is None:
        return None
    if isinstance(ts, datetime):
        return ts.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    return ts.isoformat()


# ── request parsing (strict: decimals as STRINGS, nothing guessed) ──────────────────────────────────────────────────
def _dec(v, name, errors) -> Decimal | None:
    if v is None or (isinstance(v, str) and not v.strip()):
        return None
    if not isinstance(v, str) or not DEC_RX.match(v.strip()):
        errors.append({"field": name, "message": "must be a positive decimal STRING (e.g. \"10.5\")"})
        return None
    try:
        d = Decimal(v.strip())
    except InvalidOperation:
        errors.append({"field": name, "message": "is not a decimal"})
        return None
    if d <= 0:
        errors.append({"field": name, "message": "must be greater than zero"})
        return None
    return d


def parse(body) -> dict:
    """The POST body → a validated request. Raises OrderError(422) listing every problem."""
    if not isinstance(body, dict):
        raise OrderError("the body must be a JSON object", [{"field": "$", "message": "not an object"}])
    errors: list = []
    unknown = sorted(set(body) - set(FIELDS))
    if unknown:
        errors.append({"field": ",".join(unknown), "message": "unknown field(s)"})
    txt = {k: (body.get(k).strip() if isinstance(body.get(k), str) and body.get(k).strip() else None)
           for k in ("client_order_id", "account_id", "symbol", "cusip", "side", "order_type", "time_in_force")}
    if not txt["client_order_id"] or not CLIENT_ID_RX.match(txt["client_order_id"]):
        errors.append({"field": "client_order_id", "message": "required: 1–80 letters, digits, _ . : -"})
    if not txt["account_id"]:
        errors.append({"field": "account_id", "message": "required"})
    if not (txt["symbol"] or txt["cusip"]):
        errors.append({"field": "symbol", "message": "symbol and/or cusip is required"})
    if txt["side"] not in SIDES:
        errors.append({"field": "side", "message": f"one of {', '.join(SIDES)}"})
    q = _dec(body.get("quantity"), "quantity", errors)
    n = _dec(body.get("notional"), "notional", errors)
    if (q is None) == (n is None) and not any(e["field"] in ("quantity", "notional") for e in errors):
        errors.append({"field": "quantity", "message": "give exactly one of quantity or notional"})
    otype = txt["order_type"] or "market"
    if otype not in ORDER_TYPES:
        errors.append({"field": "order_type", "message": f"one of {', '.join(ORDER_TYPES)}"})
    lim = _dec(body.get("limit_price"), "limit_price", errors)
    if otype == "limit" and lim is None and not any(e["field"] == "limit_price" for e in errors):
        errors.append({"field": "limit_price", "message": "required for a limit order"})
    if otype == "market" and body.get("limit_price") not in (None, ""):
        errors.append({"field": "limit_price", "message": "a market order carries no limit price"})
    tif = txt["time_in_force"] or "gtc"
    if tif not in TIFS:
        errors.append({"field": "time_in_force", "message": f"one of {', '.join(TIFS)}"})
    if errors:
        raise OrderError("malformed order", errors)
    return {"client_order_id": txt["client_order_id"], "account_id": txt["account_id"], "symbol": txt["symbol"],
            "cusip": txt["cusip"], "side": txt["side"], "quantity": q, "notional": n, "order_type": otype,
            "limit_price": lim if otype == "limit" else None, "time_in_force": tif}


# ── reads ───────────────────────────────────────────────────────────────────────────────────────────────────────────
async def _live(conn, counterparty_id, *, order_id=None, client_order_id=None, for_update=False):
    col, val = ("order_id", order_id) if order_id is not None else ("client_order_id", client_order_id)
    return await conn.fetchrow(
        f"SELECT * FROM cpsim.orders WHERE counterparty_id = $1::uuid AND {col} = $2 AND system_to IS NULL"
        + (" FOR UPDATE" if for_update else ""), str(counterparty_id), val)


async def _executions(conn, order_row_id) -> list:
    return list(await conn.fetch("SELECT * FROM cpsim.order_executions WHERE order_row_id = $1::uuid "
                                 "ORDER BY executed_at, exec_id", str(order_row_id)))


def _filled(execs) -> tuple[Decimal, Decimal]:
    q = sum((Decimal(e["quantity"]) for e in execs), Decimal(0))
    n = sum((Decimal(e["quantity"]) * Decimal(e["price"]) for e in execs), Decimal(0))
    return q, n


def execution_out(e) -> dict:
    return {"exec_id": e["exec_id"], "quantity": B.fmt(e["quantity"]), "price": B.fmt(e["price"]),
            "executed_at": _iso(e["executed_at"]), "trade_date": _iso(e["trade_date"]),
            "settle_date": _iso(e["settle_date"])}


def order_out(r, execs) -> dict:
    q, n = _filled(execs)
    return {"order_id": r["order_id"], "client_order_id": r["client_order_id"], "account_id": r["account_external_id"],
            "symbol": r["symbol"], "cusip": r["cusip"], "side": r["side"], "quantity": B.fmt(r["quantity"]),
            "notional": B.fmt(r["notional"]), "order_type": r["order_type"], "limit_price": B.fmt(r["limit_price"]),
            "time_in_force": r["time_in_force"], "status": r["status"], "reject_reason": r["reject_reason"],
            "filled_quantity": B.fmt(q), "average_price": B.fmt((n / q).quantize(FRACTION)) if q else None,
            "executions": [execution_out(e) for e in execs], "trade_date": _iso(r["trade_date"]),
            "created_at": _iso(r["created_at"]), "updated_at": _iso(r["updated_at"])}


async def get(conn, counterparty_id, order_id: str) -> dict:
    r = await _live(conn, counterparty_id, order_id=order_id)
    if r is None:
        raise OrderNotFound("no such order at this simulated custodian")
    return order_out(r, await _executions(conn, r["id"]))


async def list_for_account(conn, counterparty_id, account_id: str) -> list[dict]:
    rows = await conn.fetch("SELECT * FROM cpsim.orders WHERE counterparty_id = $1::uuid AND account_external_id = $2 "
                            "AND system_to IS NULL ORDER BY created_at DESC, order_id", str(counterparty_id),
                            account_id)
    return [order_out(r, await _executions(conn, r["id"])) for r in rows]


async def list_recent(conn, counterparty_id, limit: int = 100) -> list[dict]:
    """The admin page's Orders tab (read-only): the newest orders with their outcome and executions."""
    rows = await conn.fetch("SELECT * FROM cpsim.orders WHERE counterparty_id = $1::uuid AND system_to IS NULL "
                            "ORDER BY created_at DESC, order_id LIMIT $2", str(counterparty_id), int(limit))
    out = []
    for r in rows:
        o = order_out(r, await _executions(conn, r["id"]))
        o["outcome"] = r["outcome"]
        out.append(o)
    return out


# ── the book (D7) ───────────────────────────────────────────────────────────────────────────────────────────────────
async def _position_qty(conn, counterparty_id, account_id: str, security_id, clock: date) -> Decimal:
    v = await conn.fetchval(
        "SELECT COALESCE(sum(quantity), 0) FROM cpsim.book_positions WHERE counterparty_id = $1::uuid "
        "AND account_external_id = $2 AND valid_to IS NULL AND security_id = $3::uuid AND as_of = ("
        "SELECT max(as_of) FROM cpsim.book_positions WHERE counterparty_id = $1::uuid AND account_external_id = $2 "
        "AND valid_to IS NULL AND as_of <= $4)", str(counterparty_id), account_id, str(security_id), clock)
    return Decimal(v or 0)


async def _identifiers(conn, security_id) -> dict:
    rows = await conn.fetch("SELECT id_type, id_value FROM portfolio.securities_global_identifiers WHERE "
                            "global_security_id = $1::uuid AND valid_to IS NULL AND system_to IS NULL "
                            "ORDER BY is_primary DESC, id_type", str(security_id))
    out: dict = {}
    for r in rows:
        out.setdefault(r["id_type"], r["id_value"])
    return out


async def _book_move(conn, counterparty_id, order, sec: dict, idents: dict, qty: Decimal, price: Decimal,
                     clock: date, exec_id: str, settle: date) -> None:
    cid, acct, side = str(counterparty_id), order["account_external_id"], order["side"]
    ccy = (sec.get("currency_code") or B.DEFAULT_CCY).upper()
    gross = (qty * price).quantize(B.MONEY, rounding=ROUND_HALF_UP)
    await conn.execute(
        "INSERT INTO cpsim.book_transactions (counterparty_id, account_external_id, external_id, type, trade_date, "
        "settle_date, row_type, security_id, cusip, isin, ticker, name, quantity, price, gross_amount, fees, taxes, "
        "net_amount, currency_code) VALUES ($1::uuid, $2, $3, $4, $5, $6, 'security', $7::uuid, $8, $9, $10, $11, $12, "
        "$13, $14, 0, 0, $14, $15)", cid, acct, "sim-x-" + exec_id, side, clock, settle, str(order["security_id"]),
        idents.get("cusip"), idents.get("isin"), idents.get("ticker"), sec.get("name") or "", qty, price, gross, ccy)
    snap = await conn.fetchval("SELECT max(as_of) FROM cpsim.book_positions WHERE counterparty_id = $1::uuid AND "
                               "account_external_id = $2 AND valid_to IS NULL AND as_of <= $3", cid, acct, clock)
    rows = [] if snap is None else list(await conn.fetch(
        "SELECT * FROM cpsim.book_positions WHERE counterparty_id = $1::uuid AND account_external_id = $2 "
        "AND valid_to IS NULL AND as_of = $3 ORDER BY external_id", cid, acct, snap))
    sign = Decimal(1) if side == "buy" else Decimal(-1)

    def moved(r) -> dict:
        old_q = Decimal(r["quantity"] or 0)
        new_q = old_q + sign * qty
        if new_q < 0:
            raise OrderConflict(REJECT_INSUFFICIENT)
        cost = r["cost_basis"]
        if cost is not None:
            cost = Decimal(cost) + gross if side == "buy" else (
                (Decimal(cost) * new_q / old_q).quantize(B.MONEY, rounding=ROUND_HALF_UP) if old_q else Decimal(0))
        return {**dict(r), "quantity": new_q, "market_value": (new_q * price).quantize(B.MONEY, rounding=ROUND_HALF_UP),
                "cost_basis": cost, "price_source": "platform_price"}

    async def put(r: dict, external_id: str) -> None:
        await conn.execute(
            "INSERT INTO cpsim.book_positions (counterparty_id, account_external_id, external_id, as_of, row_type, "
            "security_id, cusip, isin, ticker, name, asset_type, quantity, market_value, cost_basis, currency_code, "
            "price_source) VALUES ($1::uuid, $2, $3, $4, $5, $6::uuid, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16)",
            cid, acct, external_id, clock, r["row_type"], str(r["security_id"]) if r.get("security_id") else None,
            r.get("cusip"), r.get("isin"), r.get("ticker"), r.get("name") or "", r.get("asset_type"), r.get("quantity"),
            r["market_value"], r.get("cost_basis"), r["currency_code"], r["price_source"])

    hit = next((r for r in rows if r["security_id"] is not None and str(r["security_id"]) == str(order["security_id"])),
               None)
    if hit is None and side == "sell":
        raise OrderConflict(REJECT_INSUFFICIENT)
    fresh = {"row_type": "security", "security_id": order["security_id"], "cusip": idents.get("cusip"),
             "isin": idents.get("isin"), "ticker": idents.get("ticker"), "name": sec.get("name") or "",
             "asset_type": sec.get("security_type"), "quantity": Decimal(0), "market_value": Decimal(0),
             "cost_basis": Decimal(0), "currency_code": ccy, "price_source": "platform_price"}
    if snap == clock:
        if hit is not None:
            await conn.execute("UPDATE cpsim.book_positions SET valid_to = now() WHERE id = $1::uuid", hit["id"])
            await put(moved(hit), hit["external_id"])
        else:
            await put(moved(fresh), B.generated_id(cid, "positions", acct, clock.isoformat(), order["security_id"]))
        return
    for r in rows:               # copy the older snapshot forward to the clock date, the traded row moved
        new = moved(r) if r is hit else dict(r)
        await put(new, B.generated_id(cid, "positions", acct, clock.isoformat(), r["external_id"]))
    if hit is None:
        await put(moved(fresh), B.generated_id(cid, "positions", acct, clock.isoformat(), order["security_id"]))


# ── state changes ───────────────────────────────────────────────────────────────────────────────────────────────────
async def _set_status(conn, row, status: str, reason: str | None = None):
    await archive_live_row(conn, "cpsim.orders", row["id"])
    return await conn.fetchrow(
        "UPDATE cpsim.orders SET status = $2, reject_reason = $3, updated_at = now(), system_from = now() "
        "WHERE id = $1::uuid AND system_to IS NULL RETURNING *", row["id"], status, reason)


async def _execute(conn, counterparty_id, row, qty: Decimal, price: Decimal, clock: date) -> dict:
    sec = await B.security_row(conn, str(row["security_id"]))
    idents = await _identifiers(conn, row["security_id"])
    n = await conn.fetchval("SELECT count(*) FROM cpsim.order_executions WHERE order_row_id = $1::uuid", row["id"])
    exec_id = exec_id_for(counterparty_id, row["order_id"], int(n) + 1)
    settle = next_business_day(clock + timedelta(days=1))
    await _book_move(conn, counterparty_id, row, sec, idents, qty, price, clock, exec_id, settle)
    e = await conn.fetchrow(
        "INSERT INTO cpsim.order_executions (counterparty_id, order_row_id, exec_id, quantity, price, executed_at, "
        "trade_date, settle_date, book_transaction_id) VALUES ($1::uuid, $2::uuid, $3, $4, $5, $6, $7, $8, $9) "
        "RETURNING *", str(counterparty_id), row["id"], exec_id, qty, price, O.now(), clock, settle, "sim-x-" + exec_id)
    return dict(e)


def _unit(qty: Decimal) -> Decimal:
    return WHOLE if qty == qty.to_integral_value() else FRACTION


def _marketable(row, price: Decimal) -> bool:
    if row["order_type"] != "limit":
        return True
    lim = Decimal(row["limit_price"])
    return price <= lim if row["side"] == "buy" else price >= lim


async def _remaining_qty(conn, row, price: Decimal) -> Decimal:
    execs = await _executions(conn, row["id"])
    q, n = _filled(execs)
    if row["quantity"] is not None:
        return Decimal(row["quantity"]) - q
    left = Decimal(row["notional"]) - n
    return (left / price).quantize(FRACTION, rounding=ROUND_DOWN) if left > 0 else Decimal(0)


async def _fill_rest(conn, counterparty_id, row, price: Decimal, clock: date):
    """Fill everything still open at ``price`` (accept, or a delayed / limit order once it can). Returns (row, execs)."""
    qty = await _remaining_qty(conn, row, price)
    if row["side"] == "sell" and qty > await _position_qty(conn, counterparty_id, row["account_external_id"],
                                                          row["security_id"], clock):
        return await _set_status(conn, row, "rejected", REJECT_INSUFFICIENT), []
    made = []
    if qty > 0:
        made.append(await _execute(conn, counterparty_id, row, qty, price, clock))
    return await _set_status(conn, row, "filled"), made


async def submit(conn, cp: dict, body, *, clock: date, behaviour: dict | None) -> tuple[dict, bool, list[str]]:
    """POST /v2/orders. Returns (order, created, events): created False for a repeat client_order_id (the SAME order is
    returned, whatever the repeated body says — never a second one); events = webhook event types to send."""
    req = parse(body)
    cid = str(cp["id"])
    existing = await _live(conn, cid, client_order_id=req["client_order_id"])
    if existing is not None:
        return order_out(existing, await _executions(conn, existing["id"])), False, []
    if not await B.account_exists(conn, cid, req["account_id"]):
        raise OrderNotFound("no such account in this simulated custodian's book")
    sec_id, why, detail = await B.resolve_security(conn, {"cusip": req["cusip"], "ticker": req["symbol"]})
    if sec_id is None:
        raise OrderError("the security is not recognised",
                         [{"field": "symbol", "message": B.REASONS.get(why, why), **({"detail": detail} if detail else {})}])
    beh = behaviour or {}
    outcome = beh.get("default_outcome") if beh.get("default_outcome") in ("accept", "reject", "partial", "delay") \
        else "accept"
    row = await conn.fetchrow(
        "INSERT INTO cpsim.orders (counterparty_id, order_id, client_order_id, account_external_id, security_id, symbol, "
        "cusip, side, quantity, notional, order_type, limit_price, time_in_force, outcome, partial_pct, status, "
        "trade_date) VALUES ($1::uuid, $2, $3, $4, $5::uuid, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, 'accepted', "
        "$16) RETURNING *", cid, order_id_for(cid, req["client_order_id"]), req["client_order_id"], req["account_id"],
        sec_id, req["symbol"], req["cusip"], req["side"], req["quantity"], req["notional"], req["order_type"],
        req["limit_price"], req["time_in_force"], outcome,
        None if beh.get("partial_pct") is None else Decimal(str(beh["partial_pct"])), clock)
    sec = await B.security_row(conn, sec_id)
    price = await B.platform_price(conn, sec_id, clock)
    events = ["order.updated"]
    if (sec.get("security_type") or "") not in SUPPORTED_TYPES:
        row = await _set_status(conn, row, "rejected", REJECT_UNSUPPORTED)
    elif price is None:
        row = await _set_status(conn, row, "rejected", REJECT_NO_PRICE)
    else:
        price = Decimal(price)
        wanted = Decimal(row["quantity"]) if row["quantity"] is not None else \
            (Decimal(row["notional"]) / price).quantize(FRACTION, rounding=ROUND_DOWN)
        if row["side"] == "sell" and wanted > await _position_qty(conn, cid, row["account_external_id"], sec_id,
                                                                  clock):
            row = await _set_status(conn, row, "rejected", REJECT_INSUFFICIENT)
        elif outcome == "reject":
            row = await _set_status(conn, row, "rejected", (beh.get("reject_reason") or "").strip() or REJECT_DEFAULT)
        elif outcome == "delay" or not _marketable(row, price):
            row = await _set_status(conn, row, "working")
        elif outcome == "partial":
            pct = Decimal(str(beh.get("partial_pct") or 0))
            part = (wanted * pct / Decimal(100)).quantize(_unit(wanted), rounding=ROUND_DOWN)
            if part > 0:
                await _execute(conn, cid, row, part, price, clock)
                events.append("order.filled")
            row = await _set_status(conn, row, "partially_filled" if part > 0 else "working")
        else:
            row, made = await _fill_rest(conn, cid, row, price, clock)
            if made:
                events.append("order.filled")
    return order_out(row, await _executions(conn, row["id"])), True, events


async def cancel(conn, cp: dict, order_id: str) -> dict:
    """DELETE /v2/orders/{id}: only while working; 409 once filled / rejected / cancelled (executions stay)."""
    row = await _live(conn, cp["id"], order_id=order_id, for_update=True)
    if row is None:
        raise OrderNotFound("no such order at this simulated custodian")
    if row["status"] not in WORKING:
        raise OrderConflict(f"the order is already {STATUS_LABELS[row['status']].lower()} — it cannot be cancelled")
    row = await _set_status(conn, row, "cancelled")
    return order_out(row, await _executions(conn, row["id"]))


async def reevaluate(conn, counterparty_id, clock: date) -> list[dict]:
    """Every clock advance: each working order (delay, or a limit not marketable before) is evaluated as accept at the
    new date's price. A partial's remainder stays working. Returns [{order_id, account_id, client_order_id, status,
    executions: [exec_id …]}] — what the advance filled (the admin page shows it; webhooks are sent from it)."""
    rows = await conn.fetch(
        "SELECT * FROM cpsim.orders WHERE counterparty_id = $1::uuid AND system_to IS NULL AND status = ANY($2::text[]) "
        "AND outcome <> 'partial' ORDER BY created_at, order_id FOR UPDATE", str(counterparty_id), list(WORKING))
    out = []
    for row in rows:
        price = await B.platform_price(conn, str(row["security_id"]), clock)
        if price is None or not _marketable(row, Decimal(price)):
            continue
        new, made = await _fill_rest(conn, str(counterparty_id), row, Decimal(price), clock)
        out.append({"order_id": new["order_id"], "client_order_id": new["client_order_id"],
                    "account_id": new["account_external_id"], "status": new["status"],
                    "reject_reason": new["reject_reason"], "executions": [e["exec_id"] for e in made],
                    "price": B.fmt(price)})
    return out
