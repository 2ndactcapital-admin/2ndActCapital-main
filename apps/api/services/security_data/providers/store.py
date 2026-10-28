"""Reads from the canonical stores, shared by every store-backed provider.
Every read filters provider_key and live rows; an org's upload rows are
visible to that org only (RLS) and the SQL also says so (org_id IS NULL OR =)."""

from __future__ import annotations

from datetime import date

from services.security_data.types import Point, d

LIVE = "valid_to IS NULL AND system_to IS NULL"


async def prices(conn, org_id, provider_key: str, gsids: list[str], *, end: date, start: date | None) -> dict:
    if not gsids:
        return {}
    rows = await conn.fetch(
        "SELECT global_security_id, price_date, price, currency_code, licence, quality_flags, org_id, upload_id "
        "FROM portfolio.securities_global_prices WHERE global_security_id = ANY($1::uuid[]) "
        "AND (provider_key = $2 OR ($2 = 'platform_entry' AND provider_key IS NULL)) "
        "AND price_type = 'close' AND price_date <= $3 AND ($4::date IS NULL OR price_date >= $4::date) "
        "AND (org_id IS NULL OR org_id = $5::uuid) AND " + LIVE +
        " ORDER BY global_security_id, price_date DESC", gsids, provider_key, end, start, str(org_id))
    return _group(rows, "global_security_id", "price_date", "price", provider_key, latest_only=start is None)


async def index_levels(conn, org_id, provider_key: str, keys: list[str], *, end: date, start: date | None) -> dict:
    if not keys:
        return {}
    rows = await conn.fetch(
        "SELECT index_key, level_date, value, licence, quality_flags, org_id, upload_id FROM portfolio.index_levels "
        "WHERE index_key = ANY($1::text[]) AND provider_key = $2 AND level_date <= $3 "
        "AND ($4::date IS NULL OR level_date >= $4::date) AND (org_id IS NULL OR org_id = $5::uuid) AND " + LIVE +
        " ORDER BY index_key, level_date DESC", keys, provider_key, end, start, str(org_id))
    return _group(rows, "index_key", "level_date", "value", provider_key, latest_only=start is None)


async def corporate_actions(conn, org_id, provider_key: str, gsids: list[str], *, end: date,
                            start: date | None) -> dict:
    """Every action with ex_date in [start, end] (start None = all to date), as ONE point per security whose
    value is the list, dated at the latest ex_date."""
    if not gsids:
        return {}
    rows = await conn.fetch(
        "SELECT id, global_security_id, action_type, ex_date, record_date, pay_date, terms, licence, org_id "
        "FROM portfolio.securities_global_corporate_actions WHERE global_security_id = ANY($1::uuid[]) "
        "AND provider_key = $2 AND ex_date <= $3 AND ($4::date IS NULL OR ex_date >= $4::date) "
        "AND (org_id IS NULL OR org_id = $5::uuid) AND " + LIVE + " ORDER BY global_security_id, ex_date DESC",
        gsids, provider_key, end, start, str(org_id))
    out: dict = {}
    for r in rows:
        k = str(r["global_security_id"])
        acts = out.setdefault(k, [Point([], r["ex_date"], provider_key, _lic(r["licence"]))])[0]
        acts.value.append({"id": str(r["id"]), "action_type": r["action_type"], "ex_date": r["ex_date"].isoformat(),
                           "record_date": r["record_date"].isoformat() if r["record_date"] else None,
                           "pay_date": r["pay_date"].isoformat() if r["pay_date"] else None,
                           "terms": _json(r["terms"])})
    return out


async def holdings_snapshots(conn, provider_key: str, fund_gsids: list[str], *, end: date) -> dict:
    if not fund_gsids:
        return {}
    rows = await conn.fetch(
        "SELECT DISTINCT ON (fund_global_security_id) id, fund_global_security_id, as_of, row_count, status, "
        "source_key, licence FROM portfolio.replica_holdings_snapshots WHERE fund_global_security_id = ANY($1::uuid[]) "
        "AND source_key = $2 AND status = 'live' AND as_of <= $3 AND system_to IS NULL "
        "ORDER BY fund_global_security_id, as_of DESC, fetched_at DESC", fund_gsids, provider_key, end)
    return {str(r["fund_global_security_id"]): [Point({"snapshot_id": str(r["id"]), "row_count": int(r["row_count"])},
                                                      r["as_of"], provider_key, _lic(r["licence"]))] for r in rows}


def _json(v):
    import json
    return json.loads(v) if isinstance(v, str) else (v or {})


def _lic(v) -> dict:
    return _json(v) or {}


def _group(rows, key_col: str, date_col: str, val_col: str, provider_key: str, *, latest_only: bool) -> dict:
    out: dict = {}
    for r in rows:
        k = str(r[key_col])
        if latest_only and k in out:
            continue
        out.setdefault(k, []).append(Point(d(r[val_col]), r[date_col], provider_key, _lic(r["licence"]),
                                           list(r["quality_flags"] or []),
                                           {"upload_id": str(r["upload_id"])} if r["upload_id"] else {}))
    return out
