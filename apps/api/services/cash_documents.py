"""CASH05 — Treasury → DOCUMENTS: statements, trade confirmations and 1099s of the family's CASH accounts, from
Chancery's ``documents`` through its polymorphic ``document_record_links`` (no new storage, no new table). Dates in
America/New_York (Mercury shows UTC — rejected, v1 §10). Read-only.

THE TYPE comes from the link (``document_record_links.record_type`` is polymorphic text with no CHECK — Portfolio
Phase D): ``RECORD_TYPES`` below. **[FIND]** Chancery's ``doc_category`` vocabulary (12 rows) has no statement /
confirmation / 1099 category and ``documents`` carries no category column, so the link is the only place a
document's role for an ACCOUNT can live. Links are written through the existing ``POST /documents/{id}/record-links``
(any record type) — cash05 writes none.

CASH ACCOUNTS: the scope's open accounts (``cash_instruments.scope_accounts``) that hold a liquid position
(``cash_holdings.is_liquid``) or report a cash balance above zero. A document is listed when it is linked to one of
them (a statement / confirmation / 1099 of the account) or to a ledger transaction of one of them (a confirmation —
Phase D's ``portfolio_transaction``). Visibility is the download route's own rule
(``document_embedding.visible_entity_ids`` + ``document_visible``), so every listed link opens.
"""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

from services import cash_activity as CA
from services import cash_holdings as CH

EASTERN = ZoneInfo("America/New_York")
TZ_LABEL = "ET"
RECORD_TYPES = {
    "account_statement": "statement",
    "held_away_account": "statement",        # held_away's own statement filing (services.held_away)
    "account_confirmation": "confirmation",
    "portfolio_transaction": "confirmation",  # Portfolio Phase D — a document linked to a ledger transaction
    "account_tax_form": "tax_1099",
}
TYPES = (("statement", "Statement"), ("confirmation", "Trade confirmation"), ("tax_1099", "1099"))
ACCOUNT_RECORD_TYPES = tuple(k for k in RECORD_TYPES if k != "portfolio_transaction")

DOCS_SQL = """
SELECT d.id::text AS id, d.original_filename, d.status, d.created_at, d.entity_id::text AS entity_id,
       d.storage_key IS NOT NULL AS has_file, l.record_type, l.record_id::text AS record_id
  FROM public.document_record_links l
  JOIN public.documents d ON d.id = l.document_id AND d.org_id = l.org_id
 WHERE l.org_id = $1::uuid
   AND ((l.record_type = ANY($2::text[]) AND l.record_id = ANY($3::uuid[]))
        OR (l.record_type = 'portfolio_transaction' AND l.record_id = ANY($4::uuid[])))
 ORDER BY d.created_at DESC, d.id, l.record_type
"""


def eastern(ts: datetime) -> tuple[date, str]:
    """(the Eastern calendar date, 'YYYY-MM-DD HH:MM ET') of a timestamptz. Pure."""
    local = ts.astimezone(EASTERN)
    return local.date(), local.strftime("%Y-%m-%d %H:%M ") + TZ_LABEL


async def cash_accounts(conn, scope: CH.Scope, today: date, accounts: list[dict], assets: dict) -> list[dict]:
    """The scope's accounts that hold cash (a liquid position, or a positive latest cash balance)."""
    from services.portfolio_rollup import _current_positions

    rows = await _current_positions(conn, scope.org_id, today, scope.owners) if scope.owners else []
    pids = [r["position_id"] for r in rows if CH.is_liquid(r, assets)]
    with_liquid = {r["account_id"] for r in await conn.fetch(
        "SELECT DISTINCT account_id::text AS account_id FROM portfolio.positions WHERE id = ANY($1::uuid[]) AND "
        "account_id IS NOT NULL", pids)} if pids else set()
    return [a for a in accounts if a["id"] in with_liquid or (a.get("cash_value") or 0) > 0]


async def build(conn, pool, scope: CH.Scope, today: date, *, accounts: list[dict], user_id, staff: bool,
                member: bool, types=None) -> dict:
    from services.document_embedding import document_visible, visible_entity_ids

    assets = await CH.liquid_assets(conn, scope.org_id, scope.owners)
    cash_accts = await cash_accounts(conn, scope, today, accounts, assets)
    ids = [a["id"] for a in cash_accts]
    led = await CA.ledger(conn, scope, today, assets=assets)
    tx_ids = [e["id"] for e in led["entries"] if e["account_id"] in set(ids)]
    tx_acct = {e["id"]: e["account_id"] for e in led["entries"]}
    rows = [dict(r) for r in await conn.fetch(DOCS_SQL, scope.org_id, list(ACCOUNT_RECORD_TYPES), ids, tx_ids)] \
        if (ids or tx_ids) else []
    allowed = await visible_entity_ids(pool, scope.org_id, user_id, staff)
    masked = {a["id"]: a["account_number_masked"] for a in cash_accts}
    seen, docs = set(), []
    for r in rows:
        if r["id"] in seen:
            continue
        if not document_visible(r["entity_id"], allowed, staff):
            continue
        if r["entity_id"] is not None and r["entity_id"] not in scope.shares:
            continue                                   # never another family's document
        typ = RECORD_TYPES[r["record_type"]]
        if types and typ not in set(types):
            continue
        seen.add(r["id"])
        acct = r["record_id"] if r["record_type"] != "portfolio_transaction" else tx_acct.get(r["record_id"])
        d, stamp = eastern(r["created_at"])
        docs.append({"id": r["id"], "name": r["original_filename"], "type": typ,
                     "type_label": dict(TYPES)[typ], "account": masked.get(acct), "date": d.isoformat(),
                     "datetime": stamp, "status": r["status"], "has_file": r["has_file"],
                     "transaction_id": r["record_id"] if r["record_type"] == "portfolio_transaction" else None,
                     "links": ([{"kind": "download", "label": "Open / download", "api": f"/api/v1/documents/{r['id']}"
                                 "/download", "href": f"/api/documents/{r['id']}/download"}] if r["has_file"] else []),
                     "file_text": None if r["has_file"] else "no stored file yet"})
    return {"documents": docs, "accounts": [{"id": a["id"], "account": a["account_number_masked"]} for a in cash_accts],
            "timezone": "America/New_York", "date_basis": "the document's filing time, shown in Eastern time",
            "empty_text": None if docs else "No statements, confirmations or 1099s are filed for these cash accounts "
                                            "yet."}


def vocabularies() -> dict:
    return {"types": [{"key": k, "label": v} for k, v in TYPES],
            "record_types": [{"record_type": k, "type": v} for k, v in RECORD_TYPES.items()],
            "editable": [], "inline_editable": []}
