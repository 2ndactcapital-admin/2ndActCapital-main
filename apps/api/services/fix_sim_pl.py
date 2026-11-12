"""cpsim05 (D7) — what a proposal's simulated FIX execution realized, and the account's unrealized P/L. NO new P/L
engine: every figure comes from an existing computation, and every figure is labelled SIMULATED.

  * per trade: quantity filled, average fill price (the instruction's fills), proceeds (sell) / cost (buy);
  * realized ST / LT: the TAX ENGINE's own entries for those fills — ``loss_bank_entries`` source ``fill`` (per named
    lot: q × (fill price − the lot's unit cost), term acquired → fill date; services.loss_bank.ingest, which the
    automatic step runs after the batch), or the custodian's ``lot_close`` entry that superseded one. A sell whose fill
    named no lots (a custodian-lot account) shows "awaiting the custodian's closed lot" — never a guess;
  * unrealized: ``harvest.evaluate_lot`` (``(price − unit cost) × quantity``) over the lots the tax engine reads for
    the account (``custodian_lots.lots_for_engine`` — Hollisworks lots, or the custodian's when the lot source says so)
    at the last PLATFORM close — the same predicate the simulated counterparty fills at (cpsim/book.platform_price).
Reads only; a constant number of queries per proposal (no per-row reads).
"""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal

LABEL = "SIMULATED"
ROUTE = "fix_sim"
CENT = Decimal("0.01")
_EVAL_SETTINGS = {"trading_cost_bps": Decimal(0), "loss_pct": Decimal(0), "loss_min": Decimal(0)}


def _s(d) -> str | None:
    if d is None:
        return None
    d = Decimal(d)
    s = format(d, "f")
    return s.rstrip("0").rstrip(".") if "." in s else s


def _lots(f) -> list:
    v = f["lots"]
    return v if isinstance(v, list) else (json.loads(v) if v else [])


def _money(d: Decimal) -> str:
    return _s(d.quantize(CENT))


async def last_platform_closes(conn, security_ids: list[str], as_of: date) -> dict[str, tuple[Decimal, date]]:
    """{security id: (close, date)} — the newest live platform close on or before ``as_of``, ONE query."""
    if not security_ids:
        return {}
    rows = await conn.fetch(
        "SELECT DISTINCT ON (global_security_id) global_security_id, price, price_date "
        "FROM portfolio.securities_global_prices WHERE global_security_id = ANY($1::uuid[]) AND org_id IS NULL "
        "AND price_date <= $2 AND valid_to IS NULL AND system_to IS NULL AND price_type = 'close' "
        "ORDER BY global_security_id, price_date DESC", security_ids, as_of)
    return {str(r["global_security_id"]): (Decimal(r["price"]), r["price_date"]) for r in rows}


async def unrealized(conn, org_id, account_id, as_of: date) -> dict:
    from services import harvest as H
    from services.custodian_lots import lots_for_engine

    lots, source = await lots_for_engine(conn, str(org_id), str(account_id))
    px = await last_platform_closes(conn, sorted({str(l["global_security_id"]) for l in lots
                                                  if l.get("global_security_id")}), as_of)
    total, rows, missing = Decimal(0), [], 0
    for l in lots:
        q = Decimal(str(l["quantity"] or 0))
        cost = l["adjusted_cost"] if l.get("adjusted_cost") is not None else l.get("original_cost")
        p = px.get(str(l.get("global_security_id")))
        if q <= 0 or cost is None or p is None:
            missing += 1
            continue
        ev = H.evaluate_lot(quantity=q, unit_cost=Decimal(str(cost)) / q, price=p[0], term=l.get("term") or "",
                            settings=_EVAL_SETTINGS, rates=None)
        total += ev["unrealized"]
        rows.append({"global_security_id": str(l["global_security_id"]), "quantity": _s(q), "price": _s(p[0]),
                     "price_date": p[1].isoformat(), "unrealized": _money(ev["unrealized"])})
    return {"total": _money(total), "lots": rows, "lots_without_price_or_cost": missing, "lot_source": source,
            "as_of": as_of.isoformat(), "label": LABEL}


async def proposal_pl(conn, org_id, proposal: dict, instruction_ids: list[str], *, today: date | None = None
                      ) -> dict | None:
    """The proposal page's simulated-execution panel, or None when no instruction is routed fix_sim."""
    if not instruction_ids:
        return None
    ins = [dict(r) for r in await conn.fetch(
        "SELECT i.id, i.side, i.quantity, i.notional, i.status, i.global_security_id, i.custodian_order_id, "
        "i.account_id, s.name AS security_name FROM public.execution_instructions i "
        "LEFT JOIN portfolio.securities_global s ON s.id = i.global_security_id AND s.system_to IS NULL "
        "AND s.valid_to IS NULL WHERE i.id = ANY($1::uuid[]) AND i.org_id = $2::uuid AND i.system_to IS NULL "
        "AND i.routed_via = $3 ORDER BY i.created_at, i.id", instruction_ids, str(org_id), ROUTE)]
    if not ins:
        return None
    ids = [r["id"] for r in ins]
    fills = await conn.fetch("SELECT id, instruction_id, quantity, price, lots FROM public.execution_fills WHERE "
                             "instruction_id = ANY($1::uuid[]) AND valid_to IS NULL AND system_to IS NULL", ids)
    fill_ids = [f["id"] for f in fills]
    gains = await conn.fetch(
        "SELECT e.source, e.source_id, e.character, e.amount, e.tax_lot_id, f.instruction_id FROM loss_bank_entries e "
        "LEFT JOIN public.execution_fills f ON f.id = e.source_id AND e.source = 'fill' WHERE e.org_id = $1::uuid "
        "AND e.system_to IS NULL AND e.valid_to IS NULL AND ((e.source = 'fill' AND e.source_id = ANY($2::uuid[])) "
        "OR (e.source = 'lot_close' AND e.tax_lot_id IN (SELECT x.tax_lot_id FROM loss_bank_entries x WHERE "
        "x.org_id = $1::uuid AND x.source = 'fill' AND x.source_id = ANY($2::uuid[]) AND x.system_to IS NULL)))",
        str(org_id), fill_ids) if fill_ids else []
    lot_to_ins = {}
    for f in fills:
        for x in _lots(f):
            lot_to_ins[str(x["tax_lot_id"])] = f["instruction_id"]
    by_ins: dict = {r["id"]: {"short": Decimal(0), "long": Decimal(0), "entries": 0} for r in ins}
    for g in gains:
        iid = g["instruction_id"] if g["source"] == "fill" else lot_to_ins.get(str(g["tax_lot_id"]))
        if iid in by_ins and g["character"] in ("short", "long"):
            by_ins[iid][g["character"]] += Decimal(g["amount"])
            by_ins[iid]["entries"] += 1
    trades, tot = [], {"proceeds": Decimal(0), "cost": Decimal(0), "st": Decimal(0), "lt": Decimal(0)}
    for r in ins:
        fs = [f for f in fills if f["instruction_id"] == r["id"]]
        q = sum((Decimal(f["quantity"]) for f in fs), Decimal(0))
        gross = sum((Decimal(f["quantity"]) * Decimal(f["price"]) for f in fs), Decimal(0))
        g = by_ins[r["id"]]
        named = any(_lots(f) for f in fs)
        realized_note = None
        if r["side"] == "sell" and fs and not g["entries"]:
            realized_note = ("awaiting the custodian's closed lot (the fill names no lots)" if not named else
                             "not yet computed by the tax engine")
        trades.append({"instruction_id": str(r["id"]), "security": r["security_name"], "side": r["side"],
                       "status": r["status"], "order_id": r["custodian_order_id"], "filled_quantity": _s(q),
                       "average_price": _s((gross / q).quantize(Decimal("0.000001"))) if q else None,
                       "proceeds": _money(gross) if r["side"] == "sell" else None,
                       "cost": _money(gross) if r["side"] == "buy" else None,
                       "realized_st": _money(g["short"]) if g["entries"] else None,
                       "realized_lt": _money(g["long"]) if g["entries"] else None,
                       "realized_note": realized_note, "label": LABEL})
        tot["proceeds" if r["side"] == "sell" else "cost"] += gross
        tot["st"] += g["short"]
        tot["lt"] += g["long"]
    return {"label": LABEL, "simulated": True, "trades": trades,
            "totals": {"proceeds": _money(tot["proceeds"]), "cost": _money(tot["cost"]),
                       "realized_st": _money(tot["st"]), "realized_lt": _money(tot["lt"]),
                       "realized": _money(tot["st"] + tot["lt"]), "label": LABEL},
            "unrealized": await unrealized(conn, org_id, ins[0]["account_id"], today or date.today()),
            "method": "realized = the tax engine's loss-bank entries for these fills (lot method in force); "
                      "unrealized = harvest.evaluate_lot over the tax engine's lots at the last platform close"}
