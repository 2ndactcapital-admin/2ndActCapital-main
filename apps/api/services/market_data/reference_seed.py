"""Reference data loader for key dates, regimes and platform preset views (mkt03b).

    load_reference(conn, key_dates=, regimes=, views=, dry_run=False) -> LoadResult

Takes ROWS, not files, so a test can call it with fixture rows; the CLI
(scripts/market_data_seed_reference.py) reads the three seed files under
docs/market_data/ and passes them in.

Rules:
  - key_dates upsert on slug; regimes on (regime_type, start_date); platform
    views on lower(name) with owner_scope 'platform' and NULL org_id/user_id.
  - On conflict only the DATA fields and updated_at change, and only when a
    value actually differs (``IS DISTINCT FROM`` guard), so a re-run writes
    nothing and leaves updated_at alone.
  - is_active and notes are never written on an existing row. Nothing is ever
    deleted: a row missing from the input stays.
  - A preset whose config fails the saved-view schema, or names a key that is
    not an ACTIVE series / selectable security, is SKIPPED with a [FIND]; the
    others still load.
  - ``dry_run`` classifies every row (inserted / updated / unchanged) without
    writing, in a read-only transaction.

Writes run inside platform_scope() — the ONLY place in the mkt03b code that
uses it. The request-path modules (key_dates.py, saved_views.py) never do.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date

from services.database import platform_scope
from services.market_data import saved_views as sv
from services.market_data.read_service import MarketDataRequestError

KEY_DATE_FIELDS = ("name", "kind", "start_date", "start_precision", "end_date", "end_precision", "source")
REGIME_FIELDS = ("name", "end_date", "source")
TABLES = ("key_dates", "regimes", "saved_views")


@dataclass
class LoadResult:
    counts: dict = field(default_factory=lambda: {
        t: {"inserted": 0, "updated": 0, "unchanged": 0, "skipped": 0} for t in TABLES})
    finds: list = field(default_factory=list)
    dry_run: bool = False


def _d(value) -> date | None:
    if value is None or isinstance(value, date):
        return value
    return date.fromisoformat(value)


def normalise_key_date(row: dict) -> dict:
    return {"slug": row["slug"], "name": row["name"], "kind": row["kind"],
            "start_date": _d(row["start_date"]), "start_precision": row.get("start_precision", "day"),
            "end_date": _d(row.get("end_date")), "end_precision": row.get("end_precision"),
            "source": row["source"]}


def normalise_regime(row: dict) -> dict:
    return {"regime_type": row["regime_type"], "name": row["name"], "start_date": _d(row["start_date"]),
            "end_date": _d(row["end_date"]), "source": row["source"]}


async def _check_presets(conn, views: list[dict], result: LoadResult) -> list[dict]:
    good = []
    for v in views:
        name = v.get("name")
        try:
            canonical = sv.validate_config(v.get("config"))
        except MarketDataRequestError as exc:
            result.finds.append(f"preset {name!r} SKIPPED: its config fails the saved-view schema "
                                f"({json.dumps(exc.detail().get('errors', []))})")
            result.counts["saved_views"]["skipped"] += 1
            continue
        problems = await sv.selection_problems(conn, canonical["selection"])
        if any(problems.values()):
            named = "; ".join(f"{k}={v}" for k, v in problems.items() if v)
            result.finds.append(f"preset {name!r} SKIPPED: unresolvable key(s) {named}")
            result.counts["saved_views"]["skipped"] += 1
            continue
        good.append({"name": name, "config": canonical})
    return good


def _cfg(value):
    return json.loads(value) if isinstance(value, (str, bytes)) else value


async def _classify(conn, key_dates, regimes, views, result: LoadResult) -> None:
    for r in key_dates:
        cur = await conn.fetchrow(
            f"SELECT {', '.join(KEY_DATE_FIELDS)} FROM market_data.key_dates WHERE slug = $1", r["slug"])
        _tally(result, "key_dates", cur, {f: r[f] for f in KEY_DATE_FIELDS})
    for r in regimes:
        cur = await conn.fetchrow(
            f"SELECT {', '.join(REGIME_FIELDS)} FROM market_data.regimes WHERE regime_type = $1 AND start_date = $2",
            r["regime_type"], r["start_date"])
        _tally(result, "regimes", cur, {f: r[f] for f in REGIME_FIELDS})
    for v in views:
        cur = await conn.fetchrow(
            """SELECT name, config FROM market_data.saved_views
                WHERE owner_scope = 'platform' AND lower(name) = lower($1)""", v["name"])
        if cur is not None:
            cur = {"name": cur["name"], "config": _cfg(cur["config"])}
        _tally(result, "saved_views", cur, {"name": v["name"], "config": v["config"]})


def _tally(result: LoadResult, table: str, current, wanted: dict) -> None:
    if current is None:
        result.counts[table]["inserted"] += 1
    elif any(current[k] != v for k, v in wanted.items()):
        result.counts[table]["updated"] += 1
    else:
        result.counts[table]["unchanged"] += 1


def _count(result: LoadResult, table: str, flags: list, total: int) -> None:
    ins = sum(1 for f in flags if f)
    upd = sum(1 for f in flags if not f)
    result.counts[table]["inserted"] += ins
    result.counts[table]["updated"] += upd
    result.counts[table]["unchanged"] += total - ins - upd


async def load_reference(conn, *, key_dates=(), regimes=(), views=(), dry_run: bool = False) -> LoadResult:
    """``conn`` is a RAW asyncpg connection (not the request pool)."""
    result = LoadResult(dry_run=dry_run)
    kd_rows = [normalise_key_date(r) for r in key_dates]
    rg_rows = [normalise_regime(r) for r in regimes]

    if dry_run:
        async with conn.transaction():
            await conn.execute("SET LOCAL transaction_read_only = on")
            vw_rows = await _check_presets(conn, list(views), result)
            await _classify(conn, kd_rows, rg_rows, vw_rows, result)
        return result

    async with platform_scope(conn):
        vw_rows = await _check_presets(conn, list(views), result)

        flags = []
        for r in kd_rows:
            rec = await conn.fetchrow(
                """INSERT INTO market_data.key_dates
                       (slug, name, kind, start_date, start_precision, end_date, end_precision, source)
                   VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                   ON CONFLICT (slug) DO UPDATE
                      SET name = EXCLUDED.name, kind = EXCLUDED.kind, start_date = EXCLUDED.start_date,
                          start_precision = EXCLUDED.start_precision, end_date = EXCLUDED.end_date,
                          end_precision = EXCLUDED.end_precision, source = EXCLUDED.source,
                          updated_at = now()
                    WHERE (key_dates.name, key_dates.kind, key_dates.start_date, key_dates.start_precision,
                           key_dates.end_date, key_dates.end_precision, key_dates.source)
                          IS DISTINCT FROM
                          (EXCLUDED.name, EXCLUDED.kind, EXCLUDED.start_date, EXCLUDED.start_precision,
                           EXCLUDED.end_date, EXCLUDED.end_precision, EXCLUDED.source)
                RETURNING (xmax = 0) AS inserted""",
                r["slug"], r["name"], r["kind"], r["start_date"], r["start_precision"],
                r["end_date"], r["end_precision"], r["source"])
            if rec is not None:
                flags.append(rec["inserted"])
        _count(result, "key_dates", flags, len(kd_rows))

        flags = []
        for r in rg_rows:
            rec = await conn.fetchrow(
                """INSERT INTO market_data.regimes (regime_type, name, start_date, end_date, source)
                   VALUES ($1, $2, $3, $4, $5)
                   ON CONFLICT (regime_type, start_date) DO UPDATE
                      SET name = EXCLUDED.name, end_date = EXCLUDED.end_date, source = EXCLUDED.source,
                          updated_at = now()
                    WHERE (regimes.name, regimes.end_date, regimes.source)
                          IS DISTINCT FROM (EXCLUDED.name, EXCLUDED.end_date, EXCLUDED.source)
                RETURNING (xmax = 0) AS inserted""",
                r["regime_type"], r["name"], r["start_date"], r["end_date"], r["source"])
            if rec is not None:
                flags.append(rec["inserted"])
        _count(result, "regimes", flags, len(rg_rows))

        flags = []
        for v in vw_rows:
            rec = await conn.fetchrow(
                """INSERT INTO market_data.saved_views (owner_scope, org_id, user_id, name, config)
                   VALUES ('platform', NULL, NULL, $1, $2::jsonb)
                   ON CONFLICT (lower(name)) WHERE owner_scope = 'platform' DO UPDATE
                      SET name = EXCLUDED.name, config = EXCLUDED.config, updated_at = now()
                    WHERE (saved_views.name, saved_views.config)
                          IS DISTINCT FROM (EXCLUDED.name, EXCLUDED.config)
                RETURNING (xmax = 0) AS inserted""",
                v["name"], json.dumps(v["config"]))
            if rec is not None:
                flags.append(rec["inserted"])
        _count(result, "saved_views", flags, len(vw_rows))
    return result


def format_result(result: LoadResult) -> list[str]:
    tag = " (DRY RUN — nothing written)" if result.dry_run else ""
    lines = []
    for t in TABLES:
        c = result.counts[t]
        lines.append(f"market_data.{t}{tag}: inserted {c['inserted']}, updated {c['updated']}, "
                     f"unchanged {c['unchanged']}" + (f", skipped {c['skipped']}" if c["skipped"] else ""))
    lines.extend(f"[FIND] {f}" for f in result.finds)
    return lines
