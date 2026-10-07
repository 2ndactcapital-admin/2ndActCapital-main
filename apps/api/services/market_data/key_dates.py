"""Key dates, regimes and personal dates (mkt03b).

    build_key_dates(conn, org_id, user_id)            → GET    /market/key-dates
    create_custom_date(conn, org_id, user_id, raw)    → POST   /market/key-dates/custom
    delete_custom_date(conn, org_id, user_id, id)     → DELETE /market/key-dates/custom/{id}

Reference rows (market_data.key_dates, market_data.regimes) are global reads,
written only by the seed loader. Personal rows (market_data.user_key_dates)
are read and written on the REQUEST connection: RLS isolates the org, and every
query here ALSO filters by user_id from the verified session (personal.py).
This module never imports platform_scope.

"The available data range" is the earliest first observation and the latest
last observation across ACTIVE series, read per request through the mkt03
repository (``active_series`` + ``series_bounds``) — never hardcoded. A
personal date is accepted from the first day of the first month to the last
day of the last month.
"""
from __future__ import annotations

import calendar
import re
from datetime import date
from typing import Any

import asyncpg
from pydantic import BaseModel, ConfigDict

from services.market_data import read_repository as repo
from services.market_data.personal import (
    clean_name,
    month_label,
    not_found,
    parse_row_id,
    refuse,
)
from services.market_data.read_service import _parse_body

CUSTOM_DATES_MAX = 50
NAME_MAX = 60

MSG_NAME_EMPTY = "Enter a name for this date."
MSG_NAME_LONG = "Names can be up to 60 characters."
MSG_DATE = "Pick a date."
MSG_DUPLICATE = "You already saved that name on that date."
MSG_LIMIT = "You can save up to 50 dates. Delete one first."
MSG_NO_DATA = "There is no market data yet, so no date can be saved."

KIND_LABELS = {
    "equity_crash": "Equity crash",
    "market_peak_trough": "Market peak or trough",
    "banking_credit": "Banking or credit crisis",
    "sovereign_currency": "Sovereign or currency crisis",
    "rates_monetary": "Rates and monetary policy",
    "geopolitical_trade": "Geopolitical or trade",
    "policy_regime": "Policy regime",
}
REGIME_TYPE_LABELS = {
    "recession": "Recession",
    "fed_tightening": "Fed tightening",
}

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class CustomDateBody(BaseModel):
    """extra='forbid': a body naming org_id or user_id (or anything else) is a
    422 that names neither the field nor its value. Values are checked by hand
    so each refusal carries the exact message the UI shows."""

    model_config = ConfigDict(extra="forbid")

    name: Any = None
    event_date: Any = None


def _iso(d):
    return d.isoformat() if d else None


async def data_range(conn) -> tuple[date | None, date | None]:
    series = await repo.active_series(conn)
    bounds = await repo.series_bounds(conn, [s["id"] for s in series])
    firsts = [b["first"] for b in bounds.values() if b.get("first")]
    lasts = [b["last"] for b in bounds.values() if b.get("last")]
    return (min(firsts) if firsts else None, max(lasts) if lasts else None)


def allowed_window(first: date, last: date) -> tuple[date, date]:
    return (first.replace(day=1), last.replace(day=calendar.monthrange(last.year, last.month)[1]))


def outside_message(first: date, last: date) -> str:
    return f"That date is outside the available data ({month_label(first)} to {month_label(last)})."


def parse_event_date(value) -> date:
    if not isinstance(value, str) or not _DATE_RE.match(value):
        raise refuse(MSG_DATE, "event_date", "date_format")
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise refuse(MSG_DATE, "event_date", "date_format") from None


def vocabularies() -> dict:
    return {
        "editable": [],
        "inline_editable": [],
        "kinds": [{"key": k, "label": v} for k, v in KIND_LABELS.items()],
        "regime_types": [{"key": k, "label": v} for k, v in REGIME_TYPE_LABELS.items()],
    }


def _custom_out(r) -> dict:
    return {"id": str(r["id"]), "name": r["name"], "event_date": r["event_date"].isoformat(),
            "created_at": r["created_at"].isoformat(), "updated_at": r["updated_at"].isoformat()}


async def build_key_dates(conn, org_id, user_id) -> dict:
    key_rows = await conn.fetch(
        """SELECT slug, name, kind, start_date, start_precision, end_date, end_precision, source
             FROM market_data.key_dates
            WHERE is_active
            ORDER BY start_date, slug""")
    regime_rows = await conn.fetch(
        """SELECT regime_type, name, start_date, end_date, source
             FROM market_data.regimes
            WHERE is_active
            ORDER BY start_date, regime_type""")
    custom_rows = await conn.fetch(
        """SELECT id, name, event_date, created_at, updated_at
             FROM market_data.user_key_dates
            WHERE org_id = $1::uuid AND user_id = $2::uuid
            ORDER BY event_date, lower(name), id""",
        str(org_id), str(user_id))
    first, last = await data_range(conn)
    return {
        "key_dates": [{
            "slug": r["slug"], "name": r["name"], "kind": r["kind"],
            "start_date": r["start_date"].isoformat(), "start_precision": r["start_precision"],
            "end_date": _iso(r["end_date"]), "end_precision": r["end_precision"], "source": r["source"],
        } for r in key_rows],
        "custom_dates": [_custom_out(r) for r in custom_rows],
        "regimes": [{
            "regime_type": r["regime_type"], "name": r["name"], "start_date": r["start_date"].isoformat(),
            "end_date": r["end_date"].isoformat(), "source": r["source"],
        } for r in regime_rows],
        "data_range": {"first": _iso(first), "last": _iso(last)},
        "vocabularies": vocabularies(),
        "limits": {"custom_dates_max": CUSTOM_DATES_MAX, "name_max": NAME_MAX},
    }


async def _lock_owner(conn, table: str, org_id, user_id) -> None:
    """Serialise one user's writes to one table for the rest of the
    transaction, so two concurrent saves cannot both pass the 50-row limit.
    Transaction-scoped: safe under the transaction pooler."""
    await conn.execute("SELECT pg_advisory_xact_lock(hashtextextended($1, 0))",
                       f"mkt03b:{table}:{org_id}:{user_id}")


async def create_custom_date(conn, org_id, user_id, raw: bytes) -> dict:
    body = _parse_body(CustomDateBody, raw)
    name = clean_name(body.name, max_len=NAME_MAX, empty_msg=MSG_NAME_EMPTY, long_msg=MSG_NAME_LONG)
    event_date = parse_event_date(body.event_date)
    first, last = await data_range(conn)
    if first is None or last is None:
        raise refuse(MSG_NO_DATA, "event_date", "no_data")
    lo, hi = allowed_window(first, last)
    if not lo <= event_date <= hi:
        raise refuse(outside_message(first, last), "event_date", "out_of_range")

    await _lock_owner(conn, "user_key_dates", org_id, user_id)
    dup = await conn.fetchval(
        """SELECT 1 FROM market_data.user_key_dates
            WHERE org_id = $1::uuid AND user_id = $2::uuid AND lower(name) = lower($3) AND event_date = $4""",
        str(org_id), str(user_id), name, event_date)
    if dup:
        raise refuse(MSG_DUPLICATE, "name", "duplicate", status=409)
    count = await conn.fetchval(
        "SELECT count(*) FROM market_data.user_key_dates WHERE org_id = $1::uuid AND user_id = $2::uuid",
        str(org_id), str(user_id))
    if count >= CUSTOM_DATES_MAX:
        raise refuse(MSG_LIMIT, None, "limit")
    try:
        row = await conn.fetchrow(
            """INSERT INTO market_data.user_key_dates (org_id, user_id, name, event_date)
               VALUES ($1::uuid, $2::uuid, $3, $4)
               RETURNING id, name, event_date, created_at, updated_at""",
            str(org_id), str(user_id), name, event_date)
    except asyncpg.UniqueViolationError:
        raise refuse(MSG_DUPLICATE, "name", "duplicate", status=409) from None
    return _custom_out(row)


async def delete_custom_date(conn, org_id, user_id, row_id: str) -> dict:
    rid = parse_row_id(row_id)
    deleted = await conn.fetchval(
        """DELETE FROM market_data.user_key_dates
            WHERE id = $1 AND org_id = $2::uuid AND user_id = $3::uuid
        RETURNING id""",
        rid, str(org_id), str(user_id))
    if deleted is None:
        raise not_found()
    return {"deleted": True}
