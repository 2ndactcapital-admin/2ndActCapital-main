"""Every write the layer makes. Rule 3 VALID-axis restatement for the series
stores (a changed value closes the live row and inserts the new one; an equal
value is left alone; an absent one is inserted), insert-only for the call log.

Platform rows (org_id NULL) are written in their OWN transaction with
app.is_super_admin LOCAL (rep1's platform_tx); an org's upload rows are
written on the caller's connection under the org's own RLS context."""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from decimal import Decimal

from services.replica_holdings import platform_tx

LIVE = "valid_to IS NULL AND system_to IS NULL"


async def _restate(conn, *, table: str, key_cols: dict, date_col: str, val_col: str, day: date, value: Decimal,
                   extra: dict) -> str:
    """'inserted' | 'revised' | 'unchanged' for one point."""
    where = " AND ".join(f"{c} IS NOT DISTINCT FROM ${i + 1}" + ("::uuid" if c in ("org_id", "global_security_id")
                                                                  else "") for i, c in enumerate(key_cols))
    args = list(key_cols.values())
    n = len(args)
    cur = await conn.fetchrow(f"SELECT id, {val_col} AS v FROM {table} WHERE {where} AND {date_col} = ${n + 1} "
                              f"AND {LIVE} FOR UPDATE", *args, day)
    if cur is not None and Decimal(str(cur["v"])) == value:
        return "unchanged"
    if cur is not None:
        await conn.execute(f"UPDATE {table} SET valid_to = now() WHERE id = $1", cur["id"])
    cols = list(key_cols) + [date_col, val_col] + list(extra)
    vals = args + [day, value] + list(extra.values())
    casts = {"org_id": "::uuid", "global_security_id": "::uuid", "upload_id": "::uuid", "licence": "::jsonb"}
    ph = ", ".join(f"${i + 1}{casts.get(c, '')}" for i, c in enumerate(cols))
    await conn.execute(f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({ph})", *vals)
    return "revised" if cur is not None else "inserted"


async def upsert_index_levels(pool, index_key: str, points: list[tuple[date, Decimal]], *, provider_key: str,
                              org_id=None, upload_id=None, licence: dict | None = None, conn=None) -> int:
    """Platform rows when org_id is None (own platform transaction), else on ``conn`` (org context)."""
    async def run(c) -> int:
        n = 0
        for day, v in points:
            got = await _restate(c, table="portfolio.index_levels",
                                 key_cols={"index_key": index_key, "provider_key": provider_key, "org_id": org_id},
                                 date_col="level_date", val_col="value", day=day, value=Decimal(v),
                                 extra={"upload_id": upload_id, "licence": json.dumps(licence or {})})
            n += got != "unchanged"
        return n
    if org_id is None:
        async with platform_tx(pool) as c:
            return await run(c)
    return await run(conn)


async def upsert_prices(pool, gsid: str, points: list[tuple[date, Decimal]], *, provider_key: str, currency=None,
                        org_id=None, upload_id=None, licence: dict | None = None, conn=None) -> int:
    async def run(c) -> int:
        n = 0
        for day, v in points:
            got = await _restate(c, table="portfolio.securities_global_prices",
                                 key_cols={"global_security_id": gsid, "price_type": "close",
                                           "provider_key": provider_key, "org_id": org_id},
                                 date_col="price_date", val_col="price", day=day, value=Decimal(v),
                                 extra={"currency_code": currency, "source": provider_key, "upload_id": upload_id,
                                        "licence": json.dumps(licence or {}),
                                        "as_of": datetime.now(timezone.utc)})
            n += got != "unchanged"
        return n
    if org_id is None:
        async with platform_tx(pool) as c:
            return await run(c)
    return await run(conn)


async def insert_corporate_action(conn, *, gsid: str, action_type: str, ex_date: date, record_date, pay_date,
                                  terms: dict, provider_key: str, org_id, upload_id, licence: dict) -> str:
    """One recorded fact (Portfolio F's table). Corporate actions are events, not a series: a second
    identical action (same security, type, ex_date, terms, provider, org) is not inserted twice."""
    dup = await conn.fetchval(
        "SELECT id FROM portfolio.securities_global_corporate_actions WHERE global_security_id = $1::uuid "
        "AND action_type = $2 AND ex_date = $3 AND terms = $4::jsonb AND provider_key = $5 "
        "AND org_id IS NOT DISTINCT FROM $6::uuid AND " + LIVE, gsid, action_type, ex_date, json.dumps(terms),
        provider_key, org_id)
    if dup:
        return str(dup)
    return str(await conn.fetchval(
        "INSERT INTO portfolio.securities_global_corporate_actions (global_security_id, action_type, ex_date, "
        "record_date, pay_date, terms, source_system, provider_key, as_of, org_id, upload_id, licence) "
        "VALUES ($1::uuid, $2, $3, $4, $5, $6::jsonb, $7, $7, now(), $8::uuid, $9::uuid, $10::jsonb) RETURNING id",
        gsid, action_type, ex_date, record_date, pay_date, json.dumps(terms), provider_key, org_id, upload_id,
        json.dumps(licence)))


# ── call log + budgets ──────────────────────────────────────────────────────
async def record_calls(pool, provider_key: str, calls: int, *, data_type: str | None, ok: bool,
                       error: str | None = None, run_id=None, day: date | None = None) -> None:
    async with platform_tx(pool) as c:
        await c.execute("INSERT INTO portfolio.provider_call_log (provider_key, call_date, calls, data_type, ok, error, "
                        "run_id) VALUES ($1, COALESCE($2::date, (now() AT TIME ZONE 'utc')::date), $3, $4, $5, $6, "
                        "$7::uuid)", provider_key, day, int(calls), data_type, ok, error, run_id)


async def calls_used(conn, provider_key: str, day: date) -> int:
    return int(await conn.fetchval("SELECT COALESCE(sum(calls), 0) FROM portfolio.provider_call_log WHERE "
                                   "provider_key = $1 AND call_date = $2", provider_key, day))


async def budget_left(conn, provider: dict, day: date) -> int | None:
    """None = no budget (unlimited); else what is left today (never negative)."""
    b = provider.get("daily_call_budget")
    if b is None:
        return None
    return max(0, int(b) - await calls_used(conn, provider["key"], day))


# ── quality events ──────────────────────────────────────────────────────────
async def record_quality_event(conn, *, org_id, data_type: str, subject_key: str, gsid, event_date: date, kind: str,
                               provider_key: str = "", detail: dict | None = None, run_id=None) -> str:
    """Valid-axis per (org, type, subject, day, kind, provider): a re-run with a changed detail closes the live
    row and inserts; an identical one returns the live id."""
    cur = await conn.fetchrow(
        "SELECT id, detail FROM portfolio.data_quality_events WHERE org_id IS NOT DISTINCT FROM $1::uuid AND "
        "data_type = $2 AND subject_key = $3 AND event_date = $4 AND kind = $5 AND provider_key = $6 AND " + LIVE,
        org_id, data_type, subject_key, event_date, kind, provider_key)
    body = json.dumps(detail or {}, sort_keys=True, default=str)
    if cur is not None:
        prev = cur["detail"] if isinstance(cur["detail"], str) else json.dumps(cur["detail"] or {}, sort_keys=True)
        if json.loads(prev) == json.loads(body):
            return str(cur["id"])
        await conn.execute("UPDATE portfolio.data_quality_events SET valid_to = now() WHERE id = $1", cur["id"])
    return str(await conn.fetchval(
        "INSERT INTO portfolio.data_quality_events (org_id, owner_scope, data_type, subject_key, global_security_id, "
        "event_date, kind, provider_key, detail, run_id) VALUES ($1::uuid, CASE WHEN $1::uuid IS NULL THEN "
        "'platform' ELSE 'org' END, $2, $3, $4::uuid, $5, $6, $7, $8::jsonb, $9::uuid) RETURNING id",
        org_id, data_type, subject_key, gsid, event_date, kind, provider_key, body, run_id))
