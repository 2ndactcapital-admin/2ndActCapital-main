"""CASH05 — Treasury → ACTIVITY: the ledger of the family's cash / sleeve / ladder holdings, from the existing
``portfolio.transactions`` (append-only; corrections are bi-temporal supersessions — only current rows are read), with a
DETAIL DRAWER per entry whose every link resolves. Read-only.

LEDGER TYPES — every transaction type code maps to EXACTLY ONE of the six (``TYPE_MAP``; docs/cash/
CASH_TREASURY_SCREENS.md), except ``valuation`` (a mark moves no money: listed as excluded, never an entry):

    transfer  buy (+), sell (−), call_* investment / org cost / partnership expense (+ capital into the holding),
              dist_roc, dist_recallable, dist_stock (− capital returned out of the holding)
    maturity  a SELL of a Treasury on or after its maturity_date (−, the proceeds leave the ladder)
    dividend  dividend, dist_income, dist_gain (+)
    interest  interest (+)
    fee       fee_expense, call_mgmt_fee (−)
    credit    adjustment (the stored sign)

CHANGE is the effect on THE HOLDING the transaction is booked on (a buy moves money INTO the sleeve), from the amount
``COALESCE(net_amount, gross_amount, quantity × price)`` — stored positive by every writer — times the type's sign.

PENDING vs SETTLED: an entry is PENDING when its ``settle_date`` is after "today"; with no settle date it is settled on
its trade date (said so). Settlement guidance per instrument (``GUIDANCE``).

SCOPE: the transactions of every position of an entity in the scope (the household's reached entities with a share,
or the one filtered entity) whose asset / key is LIQUID (``cash_holdings.is_liquid``). Amounts are the records' own
(100 %), each with its owner — the ledger lists what happened, the balance chart weighs it.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from services import cash_holdings as CH

ZERO = Decimal(0)
TYPES = (("transfer", "Transfer"), ("dividend", "Dividend"), ("interest", "Interest"), ("maturity", "Maturity"),
         ("fee", "Fee"), ("credit", "Credit"))
TYPE_MAP = {
    "buy": ("transfer", 1), "sell": ("transfer", -1),
    "call_investment": ("transfer", 1), "call_org_cost": ("transfer", 1), "call_partnership_expense": ("transfer", 1),
    "dist_roc": ("transfer", -1), "dist_recallable": ("transfer", -1), "dist_stock": ("transfer", -1),
    "dividend": ("dividend", 1), "dist_income": ("dividend", 1), "dist_gain": ("dividend", 1),
    "interest": ("interest", 1),
    "fee_expense": ("fee", -1), "call_mgmt_fee": ("fee", -1),
    "adjustment": ("credit", 0),
}
EXCLUDED_CODES = {"valuation": "a valuation mark moves no money — not an activity entry"}
GUIDANCE = {
    "mmf": "a money-market fund settles the same business day (T+0) when instructed before the fund's cut-off",
    "bill": "a Treasury settles the next business day (T+1)",
    "note": "a Treasury settles the next business day (T+1)",
    "cash": "cash moves are available when the custodian posts them",
    None: "settles on the custodian's standard cycle (T+1 for most securities)",
}
NO_SETTLE = "no settlement date recorded — treated as settled on the trade date"

LEDGER_SQL = """
SELECT t.id::text AS id, t.position_id::text AS position_id, t.transaction_type_code AS code, t.trade_date,
       t.settle_date, t.quantity, t.price, t.gross_amount, t.net_amount, t.fees, t.currency_code, t.external_ref,
       t.source_system, p.owner_entity_id::text AS owner_entity_id, p.asset_id::text AS asset_id,
       p.account_id::text AS account_id, p.quantity AS position_quantity, p.cost_basis AS position_cost_basis,
       COALESCE(p.taxonomy_key, a.default_taxonomy_key) AS taxonomy_key, tt.label AS type_label,
       ac.account_number_masked
  FROM portfolio.transactions t
  JOIN portfolio.positions p ON p.id = t.position_id AND p.org_id = t.org_id
  JOIN portfolio.assets a ON a.id = p.asset_id AND a.org_id = p.org_id AND a.valid_to IS NULL AND a.system_to IS NULL
  LEFT JOIN public.transaction_types tt ON tt.code = t.transaction_type_code
  LEFT JOIN public.accounts ac ON ac.id = p.account_id AND ac.org_id = p.org_id AND ac.valid_to IS NULL
   AND ac.system_to IS NULL
 WHERE t.org_id = $1::uuid AND t.valid_to IS NULL AND t.system_to IS NULL
   AND p.owner_entity_id = ANY($2::uuid[])
   AND ($3::date IS NULL OR t.trade_date >= $3::date) AND ($4::date IS NULL OR t.trade_date <= $4::date)
   AND ($5::uuid IS NULL OR t.id = $5::uuid)
 ORDER BY t.trade_date DESC, t.id
"""


def amount_of(r: dict) -> Decimal | None:
    for k in ("net_amount", "gross_amount"):
        if r.get(k) is not None:
            return Decimal(str(r[k]))
    if r.get("quantity") is not None and r.get("price") is not None:
        return Decimal(str(r["quantity"])) * Decimal(str(r["price"]))
    return None


def ledger_type(code: str, kind: str | None, maturity: date | None, trade_date: date) -> tuple[str | None, int]:
    """(type, sign) for one transaction; (None, 0) = excluded (a valuation). Unknown codes are 'credit' at the stored
    sign — never dropped. Pure."""
    if code in EXCLUDED_CODES:
        return None, 0
    t, sign = TYPE_MAP.get(code, ("credit", 0))
    if code == "sell" and kind in ("bill", "note") and maturity is not None and trade_date >= maturity:
        return "maturity", -1
    return t, sign


def entry_of(r: dict, asset: dict, today: date, *, names: dict) -> dict | None:
    """One ledger entry (Decimal) or None (excluded). Pure."""
    kind = asset.get("kind")
    typ, sign = ledger_type(r["code"], kind, asset.get("maturity"), r["trade_date"])
    if typ is None:
        return None
    amt = amount_of(r)
    if amt is None:
        change = None
    elif sign == 0:
        change = amt
    else:
        change = abs(amt) * sign
    settle = r.get("settle_date")
    pending = settle is not None and settle > today
    guidance = GUIDANCE.get(kind, GUIDANCE[None])
    if pending:
        status_text = f"Pending — settles {settle.isoformat()}; {guidance}"
    elif settle is None:
        status_text = NO_SETTLE
    else:
        status_text = f"Settled {settle.isoformat()}"
    label = r.get("type_label") or r["code"]
    return {"id": r["id"], "date": r["trade_date"], "settle_date": settle, "type": typ, "code": r["code"],
            "code_label": label, "description": f"{label} — {asset.get('name') or 'a holding'}",
            "change": change, "amount_missing": amt is None, "pending": pending, "status_text": status_text,
            "guidance": guidance if pending else None, "holding_kind": kind or "other",
            "security_id": asset.get("sid"), "asset_id": r["asset_id"], "holding": asset.get("name"),
            "position_id": r["position_id"], "owner_entity_id": r["owner_entity_id"],
            "owner_name": names.get(r["owner_entity_id"]), "account_id": r.get("account_id"),
            "account": r.get("account_number_masked"), "quantity": r.get("quantity"),
            "position_quantity": r.get("position_quantity"), "position_cost_basis": r.get("position_cost_basis"),
            "maturity": asset.get("maturity"), "source_system": r.get("source_system")}


async def ledger(conn, scope: CH.Scope, today: date, *, start: date | None = None, end: date | None = None,
                 assets: dict | None = None, entry_id=None) -> dict:
    """{entries (newest first), excluded, assets}. ``assets`` may be passed in (one read per screen)."""
    owners = scope.owners
    if not owners:
        return {"entries": [], "excluded": [], "assets": {}}
    assets = assets if assets is not None else await CH.liquid_assets(conn, scope.org_id, owners)
    rows = [dict(r) for r in await conn.fetch(LEDGER_SQL, scope.org_id, owners, start, end,
                                              str(entry_id) if entry_id else None)]
    entries, excluded = [], []
    for r in rows:
        if not CH.is_liquid(r, assets):
            continue
        e = entry_of(r, assets.get(r["asset_id"]) or {}, today, names=scope.names)
        if e is None:
            excluded.append({"id": r["id"], "code": r["code"], "why": EXCLUDED_CODES.get(r["code"])})
            continue
        entries.append(e)
    return {"entries": entries, "excluded": excluded, "assets": assets}


def filter_entries(entries: list[dict], *, types=None, status: str | None = None) -> list[dict]:
    out = entries
    if types:
        out = [e for e in out if e["type"] in set(types)]
    if status == "pending":
        out = [e for e in out if e["pending"]]
    elif status == "settled":
        out = [e for e in out if not e["pending"]]
    return out


def entry_out(e: dict, *, member: bool) -> dict:
    return {"id": e["id"], "date": e["date"].isoformat(), "settle_date": e["settle_date"].isoformat()
            if e["settle_date"] else None, "type": e["type"], "code": e["code"], "code_label": e["code_label"],
            "description": e["description"], "change": CH.money(e["change"]) if e["change"] is not None else None,
            "change_text": None if e["change"] is not None else "not estimated — needs the amount",
            "pending": e["pending"], "status_text": e["status_text"], "holding": e["holding"],
            "holding_kind": e["holding_kind"], "owner_name": e["owner_name"], "account": e["account"]}


def totals(entries: list[dict]) -> dict:
    by = {k: ZERO for k, _ in TYPES}
    for e in entries:
        if e["change"] is not None:
            by[e["type"]] += e["change"]
    return by


# ═══ The drawer ═════════════════════════════════════════════════════════════════════════════════════════════════
async def drawer(conn, scope: CH.Scope, entry_id, today: date, *, member: bool, perms: set, super_admin: bool,
                 doc_allowed: set, staff: bool) -> dict | None:
    """One entry's detail: the source record, account (last four only), security, settlement date and the LINKED
    instruction / proposal / documents. Every link names the API path that resolves it (``api``) and the screen
    (``href``); a link is offered only when the caller's own permissions open it. None = not in scope (404)."""
    got = await ledger(conn, scope, today, entry_id=entry_id)
    if not got["entries"]:
        return None
    e = got["entries"][0]
    acct = None
    if e["account_id"]:
        a = await conn.fetchrow("SELECT account_number_masked, custodian_code, registration_type, tax_status FROM "
                                "public.accounts WHERE id = $1::uuid AND org_id = $2::uuid AND valid_to IS NULL AND "
                                "system_to IS NULL", e["account_id"], scope.org_id)
        if a:
            acct = {"account": a["account_number_masked"], "custodian": a["custodian_code"],
                    "registration_type": a["registration_type"], "tax_status": a["tax_status"]}
    asset = got["assets"].get(e["asset_id"]) or {}

    def can(p):
        return super_admin or p in perms

    links = []
    if can("view_portfolio"):
        links.append({"kind": "transaction", "label": "Transaction record", "id": e["id"],
                      "api": f"/api/v1/portfolio/transactions/{e['id']}",
                      "href": f"/portfolio/transactions?transaction_id={e['id']}"})
        links.append({"kind": "position", "label": "Position", "id": e["position_id"],
                      "api": f"/api/v1/portfolio/positions/{e['position_id']}",
                      "href": f"/portfolio/positions?position_id={e['position_id']}"})
    instr = [dict(r) for r in await conn.fetch(
        "SELECT DISTINCT i.id::text AS id, i.status, i.source, i.source_ref::text AS source_ref "
        "FROM public.execution_instruction_events ev JOIN public.execution_instructions i ON i.id = ev.instruction_id "
        "AND i.org_id = ev.org_id AND i.system_to IS NULL WHERE ev.org_id = $1::uuid AND ev.system_to IS NULL AND "
        "ev.action = 'reconcile' AND ev.detail -> 'transaction_ids' ? $2 ORDER BY 1", scope.org_id, e["id"])]
    if not member:
        for i in instr:
            if can("view_execution"):
                links.append({"kind": "instruction", "label": f"Execution instruction ({i['status']})", "id": i["id"],
                              "api": f"/api/v1/execution/instructions/{i['id']}",
                              "href": f"/portfolio/instructions/{i['id']}"})
            if i["source"] == "proposal" and i["source_ref"] and can("view_proposals"):
                links.append({"kind": "proposal", "label": "Proposal", "id": i["source_ref"],
                              "api": f"/api/v1/proposals/{i['source_ref']}",
                              "href": f"/portfolio/proposals/{i['source_ref']}"})
    docs = [dict(r) for r in await conn.fetch(
        "SELECT d.id::text AS id, d.original_filename, d.entity_id::text AS entity_id, d.storage_key IS NOT NULL AS "
        "has_file FROM public.document_record_links l JOIN public.documents d ON d.id = l.document_id AND d.org_id = "
        "l.org_id WHERE l.org_id = $1::uuid AND l.record_type = 'portfolio_transaction' AND l.record_id = $2::uuid "
        "ORDER BY d.created_at, d.id", scope.org_id, e["id"])]
    doc_rows = []
    from services.document_embedding import document_visible

    for d in docs:
        if not document_visible(d["entity_id"], doc_allowed, staff):
            continue          # the download route's own rule — a listed link always opens
        if d["entity_id"] is not None and d["entity_id"] not in scope.shares:
            continue          # another family's document is never listed
        doc_rows.append({"id": d["id"], "name": d["original_filename"], "has_file": d["has_file"]})
        if d["has_file"]:
            links.append({"kind": "document", "label": d["original_filename"], "id": d["id"],
                          "api": f"/api/v1/documents/{d['id']}/download",
                          "href": f"/api/documents/{d['id']}/download"})
    return {"entry": entry_out(e, member=member),
            "source": {"table": "portfolio.transactions", "id": e["id"], "code": e["code"],
                       "code_label": e["code_label"], "source_system": e["source_system"],
                       "position_id": e["position_id"], "quantity": None if e["quantity"] is None
                       else format(Decimal(str(e["quantity"])), "f")},
            "account": acct, "owner_name": e["owner_name"],
            "security": {"name": asset.get("name"), "kind": e["holding_kind"], "ticker": asset.get("ticker"),
                         "cusip": None if member else asset.get("cusip"),
                         "maturity": asset["maturity"].isoformat() if asset.get("maturity") else None},
            "settlement": {"trade_date": e["date"].isoformat(), "settle_date": e["settle_date"].isoformat()
                           if e["settle_date"] else None, "pending": e["pending"], "text": e["status_text"]},
            "documents": doc_rows, "links": links,
            "links_note": None if links else "No linked record this caller can open."}


def vocabularies() -> dict:
    return {"types": [{"key": k, "label": v} for k, v in TYPES],
            "statuses": [{"key": "pending", "label": "Pending"}, {"key": "settled", "label": "Settled"}],
            "type_map": [{"code": c, "type": t} for c, (t, _s) in sorted(TYPE_MAP.items())],
            "excluded_codes": [{"code": c, "why": w} for c, w in EXCLUDED_CODES.items()],
            "editable": [], "inline_editable": []}
