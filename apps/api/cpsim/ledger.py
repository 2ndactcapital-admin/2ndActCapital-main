"""cpsim04 D3 — the simulated custodian's (and the simulated banks') CASH LEDGER.

Every function takes a connection already inside a transaction AS ``cpsim_app`` (cpsim.db.tx / as_simulator). Nothing
here touches a table outside ``cpsim``.

THE BALANCE is the account's USD CASH row (``book_positions`` row_type 'cash', the cpsim02 documented exception) in its
latest snapshot on or before the counterparty's clock date — the same row the read API serves, so the feed and the
ledger can never disagree. A MOVE (:func:`move`):
  1. is idempotent: one ``cpsim.cash_movements`` row per (counterparty, kind, source ref, account) — UNIQUE; a second
     call for the same leg moves nothing and returns None;
  2. moves the cash row on the valid axis exactly as cpsim03 moved a security row: the snapshot dated the clock date is
     changed (the row closed and re-inserted with the same external id), or an older snapshot is copied forward to the
     clock date with the cash row moved (a first credit to an account with no cash row adds one when ``create``);
  3. writes the book transaction the feed serves (an explicit id, ISO dates, the cash row's NAME so the client's sync
     finds the cash position — services/altruist_positions_sync ``_security_key``), when one is given;
  4. records ``balance_after``. Money is Decimal end to end, quantized to cents (ROUND_HALF_UP) only at the boundary.

Kinds (the CHECK in docs/cpsim04_part1.sql): transfer_debit / transfer_credit (a transfer's legs), return_credit /
return_debit (an ACH return reversing them), fill_buy / fill_sell (a cpsim03 execution's cash, on its settle date).
A debit never takes a balance below zero unless the caller allows it (an ACH return's reversal at the receiving end; a
fill — v1 has no buying-power check, CPSIM04_NOTES §4).
"""

from __future__ import annotations

from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from cpsim import book as B

CCY = "USD"
CASH_NAME = "Cash"
KINDS = ("transfer_debit", "transfer_credit", "return_credit", "return_debit", "fill_buy", "fill_sell")


class InsufficientFunds(Exception):
    """The account's cash row does not hold the amount (or the account has no cash row)."""


def cents(v) -> Decimal:
    return Decimal(v).quantize(B.MONEY, rounding=ROUND_HALF_UP)


async def _snapshot(conn, counterparty_id, account_id: str, clock: date):
    snap = await conn.fetchval("SELECT max(as_of) FROM cpsim.book_positions WHERE counterparty_id = $1::uuid AND "
                               "account_external_id = $2 AND valid_to IS NULL AND as_of <= $3", str(counterparty_id),
                               account_id, clock)
    rows = [] if snap is None else [dict(r) for r in await conn.fetch(
        "SELECT * FROM cpsim.book_positions WHERE counterparty_id = $1::uuid AND account_external_id = $2 "
        "AND valid_to IS NULL AND as_of = $3 ORDER BY external_id", str(counterparty_id), account_id, snap)]
    return snap, rows


def _cash_of(rows: list[dict]) -> dict | None:
    return next((r for r in rows if r["row_type"] == "cash" and (r["currency_code"] or CCY) == CCY), None)


async def balance(conn, counterparty_id, account_id: str, clock: date) -> Decimal | None:
    """The account's cash balance on ``clock`` (Decimal), or None when its book holds no cash row."""
    _snap, rows = await _snapshot(conn, counterparty_id, account_id, clock)
    c = _cash_of(rows)
    return None if c is None else Decimal(c["market_value"] or 0)


async def cash_name(conn, counterparty_id, account_id: str, clock: date) -> str:
    _snap, rows = await _snapshot(conn, counterparty_id, account_id, clock)
    c = _cash_of(rows)
    return (c or {}).get("name") or CASH_NAME


async def moved(conn, counterparty_id, kind: str, source_ref: str, account_id: str) -> bool:
    return bool(await conn.fetchval(
        "SELECT EXISTS (SELECT 1 FROM cpsim.cash_movements WHERE counterparty_id = $1::uuid AND movement_kind = $2 "
        "AND source_ref = $3 AND account_external_id = $4)", str(counterparty_id), kind, source_ref, account_id))


async def _put(conn, cid: str, acct: str, clock: date, r: dict, external_id: str) -> None:
    await conn.execute(
        "INSERT INTO cpsim.book_positions (counterparty_id, account_external_id, external_id, as_of, row_type, "
        "security_id, cusip, isin, ticker, name, asset_type, quantity, market_value, cost_basis, currency_code, "
        "price_source) VALUES ($1::uuid, $2, $3, $4, $5, $6::uuid, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16)",
        cid, acct, external_id, clock, r["row_type"], str(r["security_id"]) if r.get("security_id") else None,
        r.get("cusip"), r.get("isin"), r.get("ticker"), r.get("name") or "", r.get("asset_type"), r.get("quantity"),
        r["market_value"], r.get("cost_basis"), r["currency_code"], r["price_source"])


async def move(conn, counterparty_id, account_id: str, *, kind: str, source_ref: str, amount: Decimal, clock: date,
               txn: dict | None = None, create: bool = False, allow_negative: bool = False,
               skip_without_cash: bool = False) -> dict | None:
    """Move ``amount`` (signed Decimal) on the account's cash row on ``clock``. Returns {balance_before, balance_after,
    book_transaction_id} or None when this leg already moved (or, with ``skip_without_cash``, the account has no cash
    row). Raises InsufficientFunds when a debit would overdraw (unless ``allow_negative``)."""
    if kind not in KINDS:
        raise ValueError(f"unknown cash movement kind {kind!r}")
    cid, amount = str(counterparty_id), cents(amount)
    if await moved(conn, cid, kind, source_ref, account_id):
        return None
    snap, rows = await _snapshot(conn, cid, account_id, clock)
    hit = _cash_of(rows)
    if hit is None:
        if skip_without_cash:
            return None
        if amount < 0 and not allow_negative:
            raise InsufficientFunds("insufficient funds")
        if amount > 0 and not create:
            raise InsufficientFunds("the account holds no cash row to credit")
    before = Decimal(hit["market_value"] or 0) if hit else Decimal(0)
    after = cents(before + amount)
    if after < 0 and not allow_negative:
        raise InsufficientFunds("insufficient funds")
    fresh = {"row_type": "cash", "security_id": None, "cusip": None, "isin": None, "ticker": None, "name": CASH_NAME,
             "asset_type": "cash", "quantity": None, "market_value": Decimal(0), "cost_basis": None,
             "currency_code": CCY, "price_source": "cash"}
    name = (hit or fresh)["name"] or CASH_NAME
    if snap == clock:
        if hit is not None:
            await conn.execute("UPDATE cpsim.book_positions SET valid_to = now() WHERE id = $1::uuid", hit["id"])
            await _put(conn, cid, account_id, clock, {**hit, "market_value": after, "price_source": "cash"},
                       hit["external_id"])
        else:
            await _put(conn, cid, account_id, clock, {**fresh, "market_value": after},
                       B.generated_id(cid, "positions", account_id, clock.isoformat(), "cash:USD"))
    else:
        for r in rows:           # copy the older snapshot forward to the clock date, the cash row moved
            new = {**r, "market_value": after, "price_source": "cash"} if r is hit else r
            await _put(conn, cid, account_id, clock, new,
                       B.generated_id(cid, "positions", account_id, clock.isoformat(), r["external_id"]))
        if hit is None:
            await _put(conn, cid, account_id, clock, {**fresh, "market_value": after},
                       B.generated_id(cid, "positions", account_id, clock.isoformat(), "cash:USD"))
    txn_id = None
    if txn is not None:
        txn_id = txn["id"]
        net = cents(txn.get("net_amount", abs(amount)))
        await conn.execute(
            "INSERT INTO cpsim.book_transactions (counterparty_id, account_external_id, external_id, type, trade_date, "
            "settle_date, row_type, security_id, cusip, isin, ticker, name, quantity, price, gross_amount, fees, taxes, "
            "net_amount, currency_code) VALUES ($1::uuid, $2, $3, $4, $5, $6, 'cash', NULL, NULL, NULL, NULL, $7, NULL, "
            "NULL, $8, 0, 0, $9, $10)", cid, account_id, txn_id, txn["type"], clock, txn.get("settle_date") or clock,
            name, abs(net), net, CCY)
    await conn.execute(
        "INSERT INTO cpsim.cash_movements (counterparty_id, account_external_id, movement_kind, source_ref, amount, "
        "balance_after, effective_date, book_transaction_id) VALUES ($1::uuid, $2, $3, $4, $5, $6, $7, $8)",
        cid, account_id, kind, source_ref, amount, after, clock, txn_id)
    return {"balance_before": before, "balance_after": after, "book_transaction_id": txn_id}


async def settle_fills(conn, counterparty_id, clock: date) -> list[dict]:
    """D3 — a cpsim03 execution's CASH on its settle date: a buy debits, a sell credits the account's cash row (only
    when its book has one). Runs at every clock advance; idempotent per execution (cash_movements UNIQUE)."""
    cid = str(counterparty_id)
    rows = await conn.fetch(
        "SELECT e.exec_id, e.quantity, e.price, e.settle_date, o.account_external_id, o.side FROM cpsim.order_executions e "
        "JOIN cpsim.orders o ON o.id = e.order_row_id WHERE e.counterparty_id = $1::uuid AND e.settle_date <= $2 "
        "AND NOT EXISTS (SELECT 1 FROM cpsim.cash_movements m WHERE m.counterparty_id = e.counterparty_id "
        "AND m.source_ref = e.exec_id) ORDER BY e.settle_date, e.exec_id", cid, clock)
    out = []
    for r in rows:
        gross = cents(Decimal(r["quantity"]) * Decimal(r["price"]))
        kind, amt = ("fill_buy", -gross) if r["side"] == "buy" else ("fill_sell", gross)
        res = await move(conn, cid, r["account_external_id"], kind=kind, source_ref=r["exec_id"], amount=amt,
                         clock=clock, allow_negative=True, skip_without_cash=True)
        if res is not None:
            out.append({"exec_id": r["exec_id"], "account_id": r["account_external_id"], "kind": kind,
                        "amount": B.fmt(amt), "balance_after": B.fmt(res["balance_after"])})
    return out


async def movements(conn, counterparty_id, account_id: str | None = None, limit: int = 200) -> list[dict]:
    rows = await conn.fetch(
        "SELECT account_external_id, movement_kind, source_ref, amount, balance_after, effective_date, "
        "book_transaction_id, created_at FROM cpsim.cash_movements WHERE counterparty_id = $1::uuid AND "
        "($2::text IS NULL OR account_external_id = $2::text) ORDER BY created_at DESC, source_ref LIMIT $3",
        str(counterparty_id), account_id, int(limit))
    return [{"account_id": r["account_external_id"], "kind": r["movement_kind"], "source_ref": r["source_ref"],
             "amount": B.fmt(r["amount"]), "balance_after": B.fmt(r["balance_after"]),
             "effective_date": r["effective_date"].isoformat(), "book_transaction_id": r["book_transaction_id"]}
            for r in rows]
