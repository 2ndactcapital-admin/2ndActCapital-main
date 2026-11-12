"""ALTREAD1 D3 / D4 — what the CUSTODIAN reports for an account: its balance and its tax lots (Altruist's documented
``account_balance`` and ``/v2/accounts/{id}/cost-basis/``), stored beside — never inside — Hollisworks' own data.

  account_custodian_balances   (public)     one live row per (org, account, feed source); valid-axis (Rule 3): a changed
                                            balance or as-of date closes the live row and inserts a new one. NOT
                                            account_balances_daily — that is the treasury's cash source, and D3 leaves
                                            treasury unchanged.
  custodian_tax_lots           (portfolio)  one live row per (org, account, feed source, the custodian's lot id);
                                            valid-axis; a lot missing from a COMPLETE new snapshot is closed. Never
                                            portfolio.tax_lots: that table has eight unfiltered readers and three FKs,
                                            and its lots are Hollisworks-computed (ALTREAD1_NOTES §1c).

WHOSE LOTS THE TAX ENGINE READS — ``account_lot_method_settings.lot_source`` on the account's APPROVED lot-method row
('hollisworks' — the default, and the answer when there is no row — or 'custodian'), set through the existing
maker-checker lot-method change (services.lot_method). ``lots_for_engine`` is what ``account_models.load_holdings``
(the tax engine's one lot chokepoint: harvest, rebalance / drift) uses: Hollisworks lots unless the account says
'custodian'. A custodian lot has no Hollisworks lot id, so the engine sees it with ``id = None`` and it can never be
named on an execution instruction (the specific-lot picker and execution keep reading Hollisworks lots).

``reconcile`` compares the two per security: quantity, basis and lot-count differences. Nothing here writes a
Hollisworks lot, a position, a transaction or account_balances_daily.

Every write goes through ``_OrgWrite`` (transaction + SET LOCAL app.current_org_id — RLS is the real gate). Decimal
end to end; amounts leave as decimal strings.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from services.altruist_oauth import parse_altruist_date, to_decimal
from services.portfolio_assets import _OrgWrite, _require_org

from services.lot_method import DEFAULT_LOT_SOURCE, LOT_SOURCE_LABELS, LOT_SOURCES  # noqa: E402 — ONE vocabulary
SOURCES = ("altruist", "altruist_simulator")
T_BAL = "public.account_custodian_balances"
T_LOTS = "portfolio.custodian_tax_lots"
LOT_FIELDS = ("quantity", "market_value", "cost_basis_unadjusted", "cost_basis_adjusted", "unrealized_gain_loss")
#: the reconciliation's per-security result — the UI shows these labels (Rule 1)
RECON_STATUS_LABELS = {"match": "Matches", "differs": "Differs", "only_custodian": "Custodian only",
                       "only_hollisworks": "Hollisworks only"}


def _s(v) -> str | None:
    if v is None:
        return None
    if isinstance(v, Decimal):
        return format(v, "f")
    return str(v)


def _eq(a, b) -> bool:
    if a is None or b is None:
        return a is None and b is None
    return Decimal(a) == Decimal(b)


# ── D3: the custodian balance ───────────────────────────────────────────────────────────────────────────────────────
async def store_balance(conn, *, org_id: str, account_id: str, source_system: str, environment: str, balance: Any,
                        as_of: Any, status: str | None = None) -> dict:
    """Record the custodian's balance for one account. ``{"stored": bool, "unchanged": bool}``. A balance that is not a
    number, or an as-of date that is neither YYYY/MM/DD nor ISO, raises ValueError (the caller records it)."""
    org_id = _require_org(org_id)
    if source_system not in SOURCES:
        raise ValueError(f"source_system must be one of {SOURCES}")
    amount = to_decimal(balance, "account_balance")
    if amount is None:
        return {"stored": False, "unchanged": False, "reason": "no account_balance"}
    as_of_date = parse_altruist_date(as_of)
    if as_of_date is None:
        raise ValueError("account_balance has no as_of_date")
    async with _OrgWrite(conn, org_id) as c:
        live = await c.fetchrow(
            f"SELECT id, account_balance, as_of_date, custodian_status FROM {T_BAL} WHERE org_id = $1::uuid AND "
            "account_id = $2::uuid AND source_system = $3 AND valid_to IS NULL FOR UPDATE",
            org_id, str(account_id), source_system)
        if live and _eq(live["account_balance"], amount) and live["as_of_date"] == as_of_date \
                and (live["custodian_status"] or None) == (status or None):
            return {"stored": False, "unchanged": True}
        if live:
            await c.execute(f"UPDATE {T_BAL} SET valid_to = now() WHERE id = $1::uuid AND valid_to IS NULL",
                            str(live["id"]))
        await c.execute(
            f"INSERT INTO {T_BAL} (org_id, account_id, source_system, environment, account_balance, as_of_date, "
            "custodian_status) VALUES ($1::uuid, $2::uuid, $3, $4, $5::numeric, $6, $7)",
            org_id, str(account_id), source_system, environment, format(amount, "f"), as_of_date, status)
    return {"stored": True, "unchanged": False}


async def latest_balances(conn, org_id: str, account_id: str) -> list[dict]:
    rows = await conn.fetch(
        f"SELECT source_system, environment, account_balance, as_of_date, custodian_status, fetched_at FROM {T_BAL} "
        "WHERE org_id = $1::uuid AND account_id = $2::uuid AND valid_to IS NULL ORDER BY as_of_date DESC, source_system",
        str(org_id), str(account_id))
    return [{"source_system": r["source_system"], "environment": r["environment"],
             "account_balance": _s(r["account_balance"]), "as_of_date": r["as_of_date"].isoformat(),
             "custodian_status": r["custodian_status"],
             "fetched_at": r["fetched_at"].isoformat() if r["fetched_at"] else None} for r in rows]


# ── D4: custodian lots ──────────────────────────────────────────────────────────────────────────────────────────────
async def _security_for(conn, cusip: str | None, symbol: str | None) -> str | None:
    """The ONE live security a lot names (CUSIP first, then ticker); None when nothing — or more than one — matches."""
    for id_type, value in (("cusip", cusip), ("ticker", symbol)):
        if not value:
            continue
        ids = [str(r["id"]) for r in await conn.fetch(
            "SELECT DISTINCT s.id FROM portfolio.securities_global_identifiers i JOIN portfolio.securities_global s "
            "ON s.id = i.global_security_id WHERE i.id_type = $1 AND upper(i.id_value) = upper($2) "
            "AND i.valid_to IS NULL AND i.system_to IS NULL AND s.valid_to IS NULL AND s.system_to IS NULL "
            "AND s.merged_into_id IS NULL", id_type, str(value).strip())]
        if len(ids) == 1:
            return ids[0]
        if len(ids) > 1:
            return None
    return None


def parse_lot(row: dict, fallback_as_of: date) -> dict:
    """One normalized cost-basis row → the stored shape. Raises ValueError (a per-row error) on a missing id, a
    non-numeric amount, a missing quantity or a bad date."""
    ext = str(row.get("id") or "").strip()
    if not ext:
        raise ValueError("a cost-basis row carries no id")
    out = {"external_id": ext[:200], "cusip": row.get("cusip"), "symbol": row.get("ticker"),
           "description": row.get("name")}
    for f in LOT_FIELDS:
        out[f] = to_decimal(row.get(f), f)
    if out["quantity"] is None:
        raise ValueError(f"lot {ext}: no quantity")
    out["open_date"] = parse_altruist_date(row.get("open_date"))
    out["as_of_date"] = parse_altruist_date(row.get("as_of_date")) or fallback_as_of
    return out


async def store_lots(conn, *, org_id: str, account_id: str, source_system: str, environment: str, rows: list[dict],
                     fallback_as_of: date, complete: bool = True) -> dict:
    """Store one account's cost-basis snapshot as CUSTODIAN lots. Unchanged lots are left alone; a changed lot closes
    its live row and inserts the new one; with ``complete`` (the read was not cut by the page ceiling) a live lot
    missing from the snapshot is closed. Returns counts and per-row errors. Never touches portfolio.tax_lots."""
    org_id = _require_org(org_id)
    if source_system not in SOURCES:
        raise ValueError(f"source_system must be one of {SOURCES}")
    parsed, errors = [], []
    for row in rows:
        try:
            parsed.append(parse_lot(row, fallback_as_of))
        except ValueError as exc:
            errors.append({"external_id": row.get("id"), "reason": str(exc)})
    stored = unchanged = closed = 0
    async with _OrgWrite(conn, org_id) as c:
        live = {r["external_id"]: dict(r) for r in await c.fetch(
            f"SELECT * FROM {T_LOTS} WHERE org_id = $1::uuid AND account_id = $2::uuid AND source_system = $3 "
            "AND valid_to IS NULL FOR UPDATE", org_id, str(account_id), source_system)}
        seen = set()
        for p in parsed:
            if p["external_id"] in seen:
                errors.append({"external_id": p["external_id"], "reason": "the lot id appears twice in the snapshot"})
                continue
            seen.add(p["external_id"])
            cur = live.get(p["external_id"])
            if cur and all(_eq(cur[f], p[f]) for f in LOT_FIELDS) and cur["open_date"] == p["open_date"] \
                    and cur["as_of_date"] == p["as_of_date"] and (cur["cusip"] or None) == (p["cusip"] or None) \
                    and (cur["symbol"] or None) == (p["symbol"] or None):
                unchanged += 1
                continue
            if cur:
                await c.execute(f"UPDATE {T_LOTS} SET valid_to = now() WHERE id = $1::uuid AND valid_to IS NULL",
                                str(cur["id"]))
            sec = await _security_for(c, p["cusip"], p["symbol"])
            await c.execute(
                f"INSERT INTO {T_LOTS} (org_id, account_id, source_system, environment, external_id, "
                "global_security_id, cusip, symbol, description, quantity, market_value, cost_basis_unadjusted, "
                "cost_basis_adjusted, unrealized_gain_loss, open_date, as_of_date) VALUES ($1::uuid, $2::uuid, $3, $4, "
                "$5, $6::uuid, $7, $8, $9, $10::numeric, $11::numeric, $12::numeric, $13::numeric, $14::numeric, $15, "
                "$16)", org_id, str(account_id), source_system, environment, p["external_id"], sec, p["cusip"],
                p["symbol"], (p["description"] or None) and str(p["description"])[:300],
                *[_s(p[f]) for f in LOT_FIELDS], p["open_date"], p["as_of_date"])
            stored += 1
        if complete:
            gone = [str(v["id"]) for k, v in live.items() if k not in seen]
            if gone:
                await c.execute(f"UPDATE {T_LOTS} SET valid_to = now() WHERE id = ANY($1::uuid[]) AND valid_to IS NULL",
                                gone)
                closed = len(gone)
    return {"stored": stored, "unchanged": unchanged, "closed": closed, "errors": errors, "complete": complete}


async def custodian_lots(conn, org_id: str, account_id: str, global_security_id: str | None = None) -> list[dict]:
    """The account's live custodian lots (every feed source), oldest open date first."""
    args = [str(org_id), str(account_id)]
    sql = (f"SELECT l.*, s.name AS security_name FROM {T_LOTS} l LEFT JOIN portfolio.securities_global s ON "
           "s.id = l.global_security_id AND s.valid_to IS NULL AND s.system_to IS NULL "
           "WHERE l.org_id = $1::uuid AND l.account_id = $2::uuid AND l.valid_to IS NULL")
    if global_security_id:
        args.append(str(global_security_id))
        sql += " AND l.global_security_id = $3::uuid"
    sql += " ORDER BY l.open_date NULLS LAST, l.external_id"
    return [dict(r) for r in await conn.fetch(sql, *args)]


def lot_out(r: dict) -> dict:
    return {"id": str(r["id"]), "external_id": r["external_id"], "source_system": r["source_system"],
            "environment": r["environment"],
            "global_security_id": str(r["global_security_id"]) if r["global_security_id"] else None,
            "label": r.get("security_name") or r["description"] or r["symbol"] or r["cusip"],
            "cusip": r["cusip"], "symbol": r["symbol"], "quantity": _s(r["quantity"]),
            "market_value": _s(r["market_value"]), "cost_basis_unadjusted": _s(r["cost_basis_unadjusted"]),
            "cost_basis_adjusted": _s(r["cost_basis_adjusted"]), "unrealized_gain_loss": _s(r["unrealized_gain_loss"]),
            "open_date": r["open_date"].isoformat() if r["open_date"] else None,
            "as_of_date": r["as_of_date"].isoformat()}


async def lot_source_for(conn, org_id: str, account_id: str) -> str:
    """'hollisworks' (the default — also when the account has no approved lot-method row) or 'custodian'."""
    v = await conn.fetchval(
        "SELECT lot_source FROM public.account_lot_method_settings WHERE org_id = $1::uuid AND account_id = $2::uuid "
        "AND status = 'approved' AND valid_to IS NULL AND system_to IS NULL ORDER BY decided_at DESC LIMIT 1",
        str(org_id), str(account_id))
    return v if v in LOT_SOURCES else DEFAULT_LOT_SOURCE


def engine_shape(r: dict) -> dict:
    """A custodian lot in ``execution.account_lots``' row shape — with NO Hollisworks lot id (``id`` None): a custodian
    lot can be valued and termed by the engine but never named on an instruction."""
    return {"id": None, "custodian_lot_id": str(r["id"]),
            "acquired_date": r["open_date"].isoformat() if r["open_date"] else None,
            "quantity": r["quantity"], "adjusted_cost": r["cost_basis_adjusted"],
            "original_cost": r["cost_basis_unadjusted"], "term": None,
            "global_security_id": str(r["global_security_id"]) if r["global_security_id"] else None,
            "source": "custodian"}


async def lots_for_engine(conn, org_id: str, account_id: str) -> tuple[list[dict], str]:
    """(lots, source) — what the tax engine reads for this account: Hollisworks lots (``execution.account_lots``,
    unchanged) unless the account's approved setting says 'custodian'."""
    from services import execution as X

    source = await lot_source_for(conn, org_id, account_id)
    if source == "custodian":
        return [engine_shape(r) for r in await custodian_lots(conn, org_id, account_id)], source
    return await X.account_lots(conn, org_id, account_id), source


# ── reconciliation ──────────────────────────────────────────────────────────────────────────────────────────────────
async def reconcile(conn, org_id: str, account_id: str) -> dict:
    """Per security: Hollisworks open lots vs the custodian's live lots — quantity, basis (adjusted, else unadjusted /
    original) and lot counts, with every difference flagged. Unmatched custodian lots (no security found) are their
    own rows, keyed by CUSIP / symbol."""
    from services import execution as X

    hw = await X.account_lots(conn, org_id, account_id)
    cu = await custodian_lots(conn, org_id, account_id)
    keys: dict[str, dict] = {}

    def slot(key: str) -> dict:
        return keys.setdefault(key, {"key": key, "label": None, "hw_quantity": Decimal(0), "cu_quantity": Decimal(0),
                                     "hw_basis": Decimal(0), "cu_basis": Decimal(0), "hw_lots": 0, "cu_lots": 0,
                                     "hw_basis_known": True, "cu_basis_known": True})

    for lot in hw:
        s = slot(lot["global_security_id"])
        s["hw_quantity"] += Decimal(lot["quantity"] or 0)
        cost = lot["adjusted_cost"] if lot["adjusted_cost"] is not None else lot["original_cost"]
        if cost is None:
            s["hw_basis_known"] = False
        else:
            s["hw_basis"] += Decimal(cost)
        s["hw_lots"] += 1
    for r in cu:
        key = str(r["global_security_id"]) if r["global_security_id"] else \
            f"unmatched:{r['cusip'] or r['symbol'] or r['external_id']}"
        s = slot(key)
        s["label"] = s["label"] or r.get("security_name") or r["description"] or r["symbol"] or r["cusip"]
        s["cu_quantity"] += Decimal(r["quantity"])
        cost = r["cost_basis_adjusted"] if r["cost_basis_adjusted"] is not None else r["cost_basis_unadjusted"]
        if cost is None:
            s["cu_basis_known"] = False
        else:
            s["cu_basis"] += Decimal(cost)
        s["cu_lots"] += 1
    sec_ids = [k for k in keys if not k.startswith("unmatched:")]
    if sec_ids:
        for r in await conn.fetch("SELECT id::text AS id, name FROM portfolio.securities_global WHERE id = "
                                  "ANY($1::uuid[]) AND valid_to IS NULL AND system_to IS NULL", sec_ids):
            keys[r["id"]]["label"] = keys[r["id"]]["label"] or r["name"]
    rows = []
    for k, s in sorted(keys.items(), key=lambda kv: (kv[1]["label"] or kv[0])):
        qd = s["cu_quantity"] - s["hw_quantity"]
        bd = (s["cu_basis"] - s["hw_basis"]) if (s["cu_basis_known"] and s["hw_basis_known"]) else None
        if not s["hw_lots"]:
            status = "only_custodian"
        elif not s["cu_lots"]:
            status = "only_hollisworks"
        elif qd == 0 and bd == 0 and s["cu_lots"] == s["hw_lots"]:
            status = "match"
        else:
            status = "differs"
        rows.append({"key": k, "label": s["label"] or k, "status": status, "status_label": RECON_STATUS_LABELS[status],
                     "hollisworks": {"quantity": _s(s["hw_quantity"]), "basis": _s(s["hw_basis"]) if
                                     s["hw_basis_known"] else None, "lots": s["hw_lots"]},
                     "custodian": {"quantity": _s(s["cu_quantity"]), "basis": _s(s["cu_basis"]) if
                                   s["cu_basis_known"] else None, "lots": s["cu_lots"]},
                     "quantity_difference": _s(qd), "basis_difference": _s(bd),
                     "quantity_differs": qd != 0, "basis_differs": bd is None or bd != 0,
                     "lot_count_differs": s["cu_lots"] != s["hw_lots"]})
    return {"rows": rows, "differences": sum(1 for r in rows if r["status"] != "match"),
            "hollisworks_lots": len(hw), "custodian_lots": len(cu)}


async def account_panel(conn, org_id: str, account_id: str) -> dict:
    """The account page's custodian section: the balance(s) with their as-of dates, the custodian lots, the
    reconciliation and the effective lot source."""
    source = await lot_source_for(conn, org_id, account_id)
    return {"balances": await latest_balances(conn, org_id, account_id),
            "lots": [lot_out(r) for r in await custodian_lots(conn, org_id, account_id)],
            "reconciliation": await reconcile(conn, org_id, account_id),
            "lot_source": source, "lot_source_label": LOT_SOURCE_LABELS[source]}
