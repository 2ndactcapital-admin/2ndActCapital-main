"""PRICES3 — the OPERATOR cutover of portfolio.securities_global_prices to the lean price of record.

    doppler run -- apps/api/venv/bin/python apps/api/scripts/prices3_migrate.py --dry-run
    doppler run -- apps/api/venv/bin/python apps/api/scripts/prices3_migrate.py                 # copy → verify → swap → export
    doppler run -- apps/api/venv/bin/python apps/api/scripts/prices3_migrate.py --resume        # after a kill / timeout
    doppler run -- apps/api/venv/bin/python apps/api/scripts/prices3_migrate.py --status
    doppler run -- apps/api/venv/bin/python apps/api/scripts/prices3_migrate.py --export-archive  # if the run had no R2
    doppler run -- apps/api/venv/bin/python apps/api/scripts/prices3_migrate.py --rollback      # undo the swap (before the drop)
    doppler run -- apps/api/venv/bin/python apps/api/scripts/prices3_migrate.py --drop-old --i-confirm   # days later

Flags: --batch N (rows per copy batch, default 50,000 — whole securities per batch), --sleep S (seconds between
batches, default 2.0: the 2026-11-12 bulk insert made Supabase grow the disk and the database went read-only for
~55 s — the copy is PACED), --no-swap (copy + verify only), --reset-copy (empty the NEW tables and start over; refused
once swapped).

PHASES (portfolio.prices3_migration, one row — also the resume checkpoint and the verification record):
  not_started → copying → copied → verified → swapped → (rolled_back | dropped)

1. COPY   a change-capture trigger goes on the OLD table first (every write marks its security in prices3_dirty);
          then whole securities, in security order, in batches of ~--batch rows: series / provenance / flag
          dictionaries, live rows → security_prices (ordered by series, date — one security's history on ~12 pages),
          closed rows → security_price_history (their ids kept). Each batch is ONE transaction that also advances the
          checkpoint, so a kill loses at most the batch in flight and --resume continues after the last committed one.
2. CATCH UP  securities written while the copy ran are re-copied (dirty, touched after their last resync).
3. VERIFY per security: row count + an md5 over EVERY old column except id (not kept for live rows) and bar (moved to
          the archive) — a superset of "(security, date, price)" — old table vs the compatibility view, compared
          security by security, paced; the result is the migration row's verification record.
4. SWAP   ONE short transaction: lock the old table against writes (SHARE ROW EXCLUSIVE, lock_timeout 10 s), re-verify
          every security marked dirty since the copy began (re-copy on mismatch), a total-count backstop, drop the
          trigger, rename the table → securities_global_prices_pre_prices3, rename the view →
          securities_global_prices, flip store_mode → 'lean' (the writers' switch). Readers keep the name.
5. EXPORT the frozen old table's live platform rows WITH their OHLCV bars → one compact Parquet per year in R2
          (paced, streamed), read back (rows + sum of prices per year), then the archive watermark = swapped_at.
6. DROP   (--drop-old, separate, days later) refuses unless phase = swapped, the verification is green, the archive
          export is green, AND --i-confirm is passed.
ROLLBACK  (--rollback, before the drop) writes every post-swap change back into the old table (new rows, closures,
          deletions), renames both back, store_mode → 'legacy'.

Never prints a secret. Uses the operator DSN (DATABASE_URL_PRECUTOVER_POSTGRES_BACKUP: DDL needs the owner)."""

from __future__ import annotations

import argparse
import asyncio
import json
import pathlib
import sys
import time
from contextlib import asynccontextmanager
from datetime import date

HERE = pathlib.Path(__file__).resolve().parent
API_DIR = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(API_DIR))

from services.security_data import price_store as PS  # noqa: E402

OPERATOR_DSN_VAR = "DATABASE_URL_PRECUTOVER_POSTGRES_BACKUP"
DEFAULT_BATCH = 50_000
DEFAULT_SLEEP = 2.0
LOCK_TIMEOUT = "10s"
STATEMENT_TIMEOUT = "600s"
LEAN_BYTES_PER_ROW = 106      # docs/PRICES3_NOTES.md 1h (measured on the verify fixture: section 7)
HISTORY_BYTES_PER_ROW = 170
WAL_FACTOR = 2.5              # heap + index WAL incl. full-page images after each checkpoint
Z = "'00000000-0000-0000-0000-000000000000'::uuid"


class Refused(Exception):
    """A precondition the operator must see (never a partial action)."""


def say(msg: str) -> None:
    print(msg, flush=True)


@asynccontextmanager
async def ptx(conn, *, timeout: str = STATEMENT_TIMEOUT):
    """One platform unit of work (app.is_super_admin LOCAL, UTC, a statement timeout)."""
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
        await conn.execute("SET LOCAL TimeZone = 'UTC'")
        await conn.execute(f"SET LOCAL statement_timeout = '{timeout}'")
        yield conn


def names(s: str) -> dict:
    return {"old": f"{s}.{PS.OLD}", "pre": f"{s}.{PS.PRE}", "v3": f"{s}.{PS.VIEW_V3}", "mig": f"{s}.prices3_migration",
            "lean": f"{s}.security_prices", "hist": f"{s}.security_price_history", "ser": f"{s}.price_series",
            "prov": f"{s}.price_provenance", "dirty": f"{s}.prices3_dirty", "bars": f"{s}.price_bars_pending",
            "arch": f"{s}.price_archive_state"}


async def relkind(conn, qualified: str) -> str | None:
    sch, _, rel = qualified.partition(".")
    return await conn.fetchval("SELECT c.relkind::text FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                               "WHERE n.nspname = $1 AND c.relname = $2", sch, rel)


async def migration_row(conn, s: str) -> dict:
    async with ptx(conn):
        r = await conn.fetchrow(f"SELECT * FROM {names(s)['mig']} WHERE id = 1")
    return dict(r) if r else {}


async def _set(conn, s: str, **cols) -> None:
    sets = ", ".join(f"{k} = ${i + 1}" + ("::jsonb" if k.endswith("_detail") else "") for i, k in enumerate(cols))
    vals = [json.dumps(v, default=str) if k.endswith("_detail") else v for k, v in cols.items()]
    await conn.execute(f"UPDATE {names(s)['mig']} SET {sets}, updated_at = now() WHERE id = 1", *vals)


# ── plan / dry run ──────────────────────────────────────────────────────────
async def plan(conn, s: str = "portfolio", *, batch: int = DEFAULT_BATCH, sleep: float = DEFAULT_SLEEP) -> dict:
    n = names(s)
    src = n["old"] if await relkind(conn, n["old"]) == "r" else n["pre"]
    async with ptx(conn):
        per = [(r["g"], int(r["n"])) for r in await conn.fetch(
            f"SELECT global_security_id AS g, count(*) AS n FROM {src} GROUP BY 1 ORDER BY 1")]
        st = await conn.fetchrow(
            f"SELECT count(*) AS rows, count(*) FILTER (WHERE valid_to IS NULL AND system_to IS NULL) AS live, "
            f"count(*) FILTER (WHERE org_id IS NOT NULL) AS org, count(*) FILTER (WHERE upload_id IS NOT NULL) AS upl, "
            f"count(*) FILTER (WHERE valid_to IS NULL AND system_to IS NULL AND valid_from <> system_from) AS bad_live, "
            f"count(*) FILTER (WHERE cardinality(quality_flags) > 1) AS multi_flag, "
            f"count(*) FILTER (WHERE bar <> '{{}}'::jsonb) AS bars FROM {src}")
        size = await conn.fetchval("SELECT pg_total_relation_size($1::regclass)", src)
        db = await conn.fetchval("SELECT pg_database_size(current_database())")
        flags = [r[0] for r in await conn.fetch(f"SELECT DISTINCT unnest(quality_flags) FROM {src}")]
    batches = list(batches_of(per, batch))
    live, closed = int(st["live"]), int(st["rows"]) - int(st["live"])
    est_new = live * LEAN_BYTES_PER_ROW + closed * HISTORY_BYTES_PER_ROW
    return {"source": src, "rows": int(st["rows"]), "live": live, "closed": closed, "org_rows": int(st["org"]),
            "upload_rows": int(st["upl"]), "securities": len(per), "bars": int(st["bars"]),
            "live_valid_from_ne_system_from": int(st["bad_live"]), "multi_flag_rows": int(st["multi_flag"]),
            "flags": sorted(flags), "old_total_bytes": int(size), "database_bytes": int(db),
            "estimated_new_bytes": int(est_new), "estimated_wal_bytes": int(est_new * WAL_FACTOR),
            "batches": len(batches), "batch_rows": batch, "sleep_s": sleep,
            "estimated_minutes": round(len(batches) * (sleep + 4.0) / 60 * 2, 1),  # copy + verify passes
            "disk_needed_bytes": int(2 * size + est_new * (1 + WAL_FACTOR))}


def batches_of(per: list[tuple], batch: int):
    """Whole securities per batch: [(lo_exclusive | None, hi_inclusive, rows, n_securities)]."""
    lo, acc, k, hi = None, 0, 0, None
    for g, cnt in per:
        hi, acc, k = g, acc + cnt, k + 1
        if acc >= batch:
            yield lo, hi, acc, k
            lo, acc, k = hi, 0, 0
    if k:
        yield lo, hi, acc, k


def _mb(b: int) -> str:
    return f"{b / 1024 / 1024:,.0f} MB"


def print_plan(p: dict) -> None:
    say(f"[PLAN] source {p['source']}: {p['rows']:,} rows ({p['live']:,} live, {p['closed']:,} restated/closed), "
        f"{p['securities']:,} securities, {p['org_rows']:,} org-uploaded, {p['bars']:,} with an OHLCV bar")
    say(f"[PLAN] old table {_mb(p['old_total_bytes'])} of a {_mb(p['database_bytes'])} database; estimated lean "
        f"price of record {_mb(p['estimated_new_bytes'])} (~{LEAN_BYTES_PER_ROW} B/row); estimated WAL "
        f"~{_mb(p['estimated_wal_bytes'])}")
    say(f"[PLAN] {p['batches']:,} batches of ~{p['batch_rows']:,} rows, {p['sleep_s']} s apart → ~"
        f"{p['estimated_minutes']} min for copy + verify")
    say(f"[PLAN] quality flags in use: {p['flags']}; rows with >1 flag (order canonicalised): {p['multi_flag_rows']:,}")
    if p["live_valid_from_ne_system_from"]:
        say(f"[REFUSE] {p['live_valid_from_ne_system_from']:,} live rows have valid_from <> system_from — the lean "
            "row cannot represent them; the copy will refuse")
    say(f"[DISK] BEFORE the real run: Supabase → Settings → Compute and Disk — free disk must be ≥ "
        f"{_mb(p['disk_needed_bytes'])} (2× the old table + the new table + WAL); grow it FIRST if not "
        "(a disk grow during a bulk write made the database read-only for ~55 s on 2026-11-12).")


# ── copy ────────────────────────────────────────────────────────────────────
def _w(alias: str, mode: str) -> str:
    return {"range": f"{alias}.global_security_id > COALESCE($1::uuid, '{Z[1:-7]}'::uuid) AND "
                     f"{alias}.global_security_id <= $2::uuid",
            "ids": f"{alias}.global_security_id = ANY($1::uuid[])"}[mode]


async def copy_securities(conn, s: str, src: str, mode: str, args: list) -> tuple[int, int]:
    """Re-derive the new rows of the securities selected by ``mode``/``args`` from ``src`` (idempotent: their
    existing lean / history rows are removed first). Caller holds the transaction. Returns (live, closed)."""
    n = names(s)
    wo, ws = _w("o", mode), _w("se", mode)
    await conn.execute(f"DELETE FROM {n['lean']} l USING {n['ser']} se WHERE se.id = l.series_id AND {ws}", *args)
    await conn.execute(f"DELETE FROM {n['hist']} h USING {n['ser']} se WHERE se.id = h.series_id AND {ws}", *args)
    await conn.execute(f"INSERT INTO {n['ser']} (global_security_id, price_type, provider_key, org_id) "
                       f"SELECT DISTINCT o.global_security_id, o.price_type, o.provider_key, o.org_id FROM {src} o "
                       f"WHERE {wo} ON CONFLICT ON CONSTRAINT price_series_key_uq DO NOTHING", *args)
    await conn.execute(f"INSERT INTO {n['prov']} (provider_key, source, currency_code, licence, upload_id, org_id) "
                       f"SELECT DISTINCT o.provider_key, o.source, o.currency_code, o.licence, o.upload_id, o.org_id "
                       f"FROM {src} o WHERE {wo} ON CONFLICT ON CONSTRAINT price_provenance_uq DO NOTHING", *args)
    flags = [r[0] for r in await conn.fetch(f"SELECT DISTINCT unnest(o.quality_flags) FROM {src} o WHERE {wo} AND "
                                            "o.quality_flags <> '{}'", *args)]
    if flags:
        await conn.fetchval(f"SELECT {s}.price_flag_bits($1::text[])", sorted(flags))
    src_cte = (f"WITH src AS (SELECT o.*, se.id AS sid, pv.id AS pid, CASE WHEN o.quality_flags = '{{}}' THEN 0 ELSE "
               f"{s}.price_flag_bits(o.quality_flags) END AS bits FROM {src} o "
               f"JOIN {n['ser']} se ON se.global_security_id = o.global_security_id AND se.price_type = o.price_type "
               f"AND se.provider_key IS NOT DISTINCT FROM o.provider_key AND se.org_id IS NOT DISTINCT FROM o.org_id "
               f"JOIN {n['prov']} pv ON pv.licence = o.licence AND pv.provider_key IS NOT DISTINCT FROM o.provider_key "
               f"AND pv.source IS NOT DISTINCT FROM o.source AND pv.currency_code IS NOT DISTINCT FROM o.currency_code "
               f"AND pv.upload_id IS NOT DISTINCT FROM o.upload_id AND pv.org_id IS NOT DISTINCT FROM o.org_id "
               f"WHERE {wo}) ")
    live = await conn.execute(
        src_cte + f"INSERT INTO {n['lean']} (as_of, recorded_at, series_id, price_date, provenance_id, quality_bits, "
        "price, adjustment_factor) SELECT as_of, system_from, sid, price_date, pid, bits, price, adjustment_factor "
        "FROM src WHERE valid_to IS NULL AND system_to IS NULL ORDER BY sid, price_date", *args)
    closed = await conn.execute(
        src_cte + f"INSERT INTO {n['hist']} (id, series_id, price_date, price, adjustment_factor, provenance_id, "
        "quality_bits, as_of, valid_from, valid_to, system_from, system_to, superseded_at) SELECT id, sid, price_date, "
        "price, adjustment_factor, pid, bits, as_of, valid_from, valid_to, system_from, system_to, "
        "COALESCE(LEAST(valid_to, system_to), now()) FROM src WHERE NOT (valid_to IS NULL AND system_to IS NULL)",
        *args)
    return int(live.split()[-1]), int(closed.split()[-1])


async def install_capture(conn, s: str) -> None:
    n = names(s)
    async with ptx(conn):
        await conn.execute(f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT}'")
        await conn.execute(f"DROP TRIGGER IF EXISTS prices3_dirty ON {n['old']}")
        await conn.execute(f"CREATE TRIGGER prices3_dirty AFTER INSERT OR UPDATE OR DELETE ON {n['old']} FOR EACH ROW "
                           f"EXECUTE FUNCTION {s}.prices3_mark_dirty()")


async def copy(conn, s: str = "portfolio", *, batch: int = DEFAULT_BATCH, sleep: float = DEFAULT_SLEEP,
               resume: bool = False, fail_in_batch: int | None = None, stop_after: int | None = None) -> dict:
    """Paced, resumable copy. ``fail_in_batch`` (verify only) raises INSIDE that batch's transaction after its rows
    are written — a simulated kill; ``stop_after`` stops cleanly after that many batches."""
    n = names(s)
    if await relkind(conn, n["old"]) != "r":
        raise Refused(f"{n['old']} is not a table (already swapped?)")
    m = await migration_row(conn, s)
    if m["phase"] in ("swapped", "dropped"):
        raise Refused(f"phase is '{m['phase']}' — nothing to copy")
    if m["phase"] == "rolled_back":
        raise Refused("phase is 'rolled_back' — run --reset-copy first, then copy again")
    if m["phase"] in ("copied", "verified"):
        return {"action": "already_copied", "rows": m["copied_rows"]}
    if m["phase"] == "copying" and not resume:
        raise Refused(f"a copy is in progress (checkpoint {m['checkpoint_gsid']}, {m['copied_rows']:,} rows) — pass "
                      "--resume to continue it, or --reset-copy to start over")
    p = await plan(conn, s, batch=batch, sleep=sleep)
    if p["live_valid_from_ne_system_from"]:
        raise Refused(f"{p['live_valid_from_ne_system_from']} live rows have valid_from <> system_from")
    if m["phase"] == "not_started":
        await install_capture(conn, s)   # capture FIRST, so no write after the first read can be missed
        async with ptx(conn):
            await _set(conn, s, phase="copying", copy_started_at=await conn.fetchval("SELECT now()"))
    async with ptx(conn):
        cp = await conn.fetchval(f"SELECT checkpoint_gsid FROM {n['mig']} WHERE id = 1")
        per = [(r["g"], int(r["n"])) for r in await conn.fetch(
            f"SELECT global_security_id AS g, count(*) AS n FROM {n['old']} WHERE ($1::uuid IS NULL OR "
            f"global_security_id > $1::uuid) GROUP BY 1 ORDER BY 1", cp)]
    done = {"batches": 0, "rows": 0, "securities": 0, "resumed_from": str(cp) if cp else None}
    t0 = time.monotonic()
    plan_b = list(batches_of(per, batch))
    for i, (lo, hi, rows, k) in enumerate(plan_b):
        lo = lo if lo is not None else cp
        async with ptx(conn):
            cur = await conn.fetchval(f"SELECT checkpoint_gsid FROM {n['mig']} WHERE id = 1 FOR UPDATE")
            if cur is not None and str(cur) >= str(hi):
                continue   # another runner already took it
            live, closed = await copy_securities(conn, s, n["old"], "range", [lo, hi])
            if fail_in_batch is not None and i == fail_in_batch:
                raise RuntimeError(f"simulated kill inside batch {i} (after {live + closed} rows were written)")
            await conn.execute(f"UPDATE {n['mig']} SET checkpoint_gsid = $1::uuid, copied_rows = copied_rows + $2, "
                               f"copied_securities = copied_securities + $3, batches = batches + 1, updated_at = now() "
                               f"WHERE id = 1", hi, live + closed, k)
        done["batches"] += 1
        done["rows"] += live + closed
        done["securities"] += k
        say(f"[COPY] batch {i + 1}/{len(plan_b)}: {k} securities, {live + closed:,} rows "
            f"(live {live:,}, history {closed:,}) — {time.monotonic() - t0:,.0f} s")
        if stop_after is not None and done["batches"] >= stop_after:
            done["action"] = "stopped"
            return done
        if i + 1 < len(plan_b) and sleep:
            await asyncio.sleep(sleep)
    async with ptx(conn):
        await _set(conn, s, phase="copied", copy_finished_at=await conn.fetchval("SELECT now()"))
    done["action"] = "copied"
    return done


# ── checksums / verify ──────────────────────────────────────────────────────
ROW_TEXT = ("concat_ws('|', price_type, COALESCE(provider_key, '∅'), COALESCE(org_id::text, '∅'), price_date::text, "
            "price::text, COALESCE(adjustment_factor::text, '∅'), COALESCE(currency_code, '∅'), COALESCE(source, '∅'), "
            "licence::text, array_to_string(ARRAY(SELECT DISTINCT x FROM unnest(quality_flags) x ORDER BY x), ','), "
            "COALESCE(as_of::text, '∅'), valid_from::text, COALESCE(valid_to::text, '∅'), system_from::text, "
            "COALESCE(system_to::text, '∅'), COALESCE(upload_id::text, '∅'), CASE WHEN valid_to IS NULL AND system_to "
            "IS NULL THEN '' ELSE id::text END)")
ROW_ORDER = ("price_type, provider_key NULLS FIRST, org_id NULLS FIRST, price_date, system_from, valid_to NULLS FIRST, "
             "system_to NULLS FIRST")


async def checksums(conn, rel: str, mode: str, args: list) -> dict:
    """{gsid: (rows, md5)} for the securities selected (caller holds a UTC transaction)."""
    return {str(r["g"]): (int(r["n"]), r["h"]) for r in await conn.fetch(
        f"SELECT global_security_id AS g, count(*) AS n, md5(string_agg({ROW_TEXT}, E'\\n' ORDER BY {ROW_ORDER})) AS h "
        f"FROM {rel} o WHERE {_w('o', mode)} GROUP BY 1", *args)}


async def compare(conn, s: str, src: str, mode: str, args: list) -> list[dict]:
    a = await checksums(conn, src, mode, args)
    b = await checksums(conn, f"{s}.{PS.VIEW_V3}" if await relkind(conn, f"{s}.{PS.VIEW_V3}") else
                        f"{s}.{PS.OLD}", mode, args)
    return [{"gsid": g, "old": a.get(g), "new": b.get(g)} for g in sorted(set(a) | set(b)) if a.get(g) != b.get(g)]


async def verify_all(conn, s: str = "portfolio", *, batch: int = DEFAULT_BATCH, sleep: float = DEFAULT_SLEEP) -> dict:
    """Every security: count + md5 old vs view. Records the result; phase → verified when green."""
    n = names(s)
    m = await migration_row(conn, s)
    if m["phase"] not in ("copied", "verified"):
        raise Refused(f"phase is '{m['phase']}' — verify needs a finished copy")
    async with ptx(conn):
        per = [(r["g"], int(r["n"])) for r in await conn.fetch(
            f"SELECT global_security_id AS g, count(*) AS n FROM {n['old']} GROUP BY 1 ORDER BY 1")]
    bad, rows, secs, digest = [], 0, 0, []
    for lo, hi, cnt, k in batches_of(per, batch):
        async with ptx(conn):
            a = await checksums(conn, n["old"], "range", [lo, hi])
            b = await checksums(conn, n["v3"], "range", [lo, hi])
        for g in sorted(set(a) | set(b)):
            if a.get(g) != b.get(g):
                bad.append({"gsid": g, "old": a.get(g), "new": b.get(g)})
            digest.append(f"{g}:{(a.get(g) or ('', ''))[1]}")
        rows += sum(x[0] for x in a.values())
        secs += len(a)
        if sleep:
            await asyncio.sleep(min(sleep, 1.0))
    async with ptx(conn):
        total_old = await conn.fetchval(f"SELECT count(*) FROM {n['old']}")
        total_new = await conn.fetchval(f"SELECT count(*) FROM {n['v3']}")
        overall_old = await conn.fetchval("SELECT md5($1)", "\n".join(digest))
        ok = not bad and total_old == total_new == rows
        detail = {"securities": secs, "rows_old": total_old, "rows_new": total_new, "mismatches": len(bad),
                  "first_mismatches": bad[:20], "checksum": overall_old,
                  "compared": "per security: row count + md5 over every old column except id (live rows) and bar"}
        await _set(conn, s, verified_ok=ok, verified_at=await conn.fetchval("SELECT now()"), verify_detail=detail,
                   phase="verified" if ok else "copied")
    return {"ok": ok, **detail}


async def catch_up(conn, s: str = "portfolio", *, rounds: int = 5) -> dict:
    """Re-copy securities written while the copy ran (touched after their last resync). Dirty rows are KEPT — the
    swap re-verifies every one of them under the write lock."""
    n = names(s)
    total = 0
    for _ in range(rounds):
        async with ptx(conn):
            todo = [r["g"] for r in await conn.fetch(
                f"SELECT global_security_id AS g FROM {n['dirty']} WHERE resynced_at IS NULL OR touched_at >= "
                f"resynced_at ORDER BY 1")]
        if not todo:
            break
        for i in range(0, len(todo), 40):
            chunk = todo[i:i + 40]
            async with ptx(conn):
                stamp = await conn.fetchval("SELECT clock_timestamp()")
                await copy_securities(conn, s, n["old"], "ids", [chunk])
                await conn.execute(f"UPDATE {n['dirty']} SET resynced_at = $2 WHERE global_security_id = "
                                   f"ANY($1::uuid[])", chunk, stamp)
        total += len(todo)
    return {"resynced_securities": total}


# ── swap / rollback / drop ──────────────────────────────────────────────────
async def swap(conn, s: str = "portfolio") -> dict:
    n = names(s)
    m = await migration_row(conn, s)
    if m["phase"] != "verified" or m["verified_ok"] is not True:
        raise Refused(f"the swap needs a green verification (phase '{m['phase']}', verified_ok {m['verified_ok']})")
    if await relkind(conn, n["old"]) != "r" or await relkind(conn, n["v3"]) != "v":
        raise Refused("the old table / the compatibility view are not where the swap expects them")
    t0 = time.monotonic()
    async with ptx(conn):
        await conn.execute(f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT}'")
        await conn.execute(f"LOCK TABLE {n['old']} IN SHARE ROW EXCLUSIVE MODE")   # writers wait; readers do not
        dirty = [r["g"] for r in await conn.fetch(f"SELECT global_security_id AS g FROM {n['dirty']} ORDER BY 1")]
        recopied = []
        for i in range(0, len(dirty), 200):
            chunk = dirty[i:i + 200]
            bad = await compare(conn, s, n["old"], "ids", [chunk])
            if bad:
                ids = [b["gsid"] for b in bad]
                await copy_securities(conn, s, n["old"], "ids", [ids])
                recopied += ids
                still = await compare(conn, s, n["old"], "ids", [ids])
                if still:
                    raise Refused(f"{len(still)} securities still differ after a re-copy under the lock: "
                                  f"{still[:3]}")
        c_old = await conn.fetchval(f"SELECT count(*) FROM {n['old']}")
        c_new = await conn.fetchval(f"SELECT count(*) FROM {n['v3']}")
        if c_old != c_new:
            raise Refused(f"row counts differ under the lock: old {c_old} vs new {c_new}")
        await conn.execute(f"DROP TRIGGER IF EXISTS prices3_dirty ON {n['old']}")
        await conn.execute(f"ALTER TABLE {n['old']} RENAME TO {PS.PRE}")
        await conn.execute(f"ALTER VIEW {n['v3']} RENAME TO {PS.OLD}")
        at = await conn.fetchval("SELECT now()")
        await _set(conn, s, store_mode="lean", phase="swapped", swapped_at=at)
    return {"action": "swapped", "swapped_at": at, "dirty_checked": len(dirty), "recopied_under_lock": len(recopied),
            "rows": c_old, "lock_held_s": round(time.monotonic() - t0, 2)}


async def rollback(conn, s: str = "portfolio") -> dict:
    """Undo the swap before the drop: every post-swap change goes back into the old table, then the names."""
    n = names(s)
    m = await migration_row(conn, s)
    if m["phase"] != "swapped":
        raise Refused(f"rollback needs phase 'swapped' (got '{m['phase']}')")
    if await relkind(conn, n["pre"]) != "r" or await relkind(conn, n["old"]) != "v":
        raise Refused("the old table / the view are not where the rollback expects them")
    sw = m["swapped_at"]
    cols = ", ".join(c for c in PS.OLD_COLUMNS if c != "bar")
    key = ("p.global_security_id = v.global_security_id AND p.price_type = v.price_type AND p.provider_key IS NOT "
           "DISTINCT FROM v.provider_key AND p.org_id IS NOT DISTINCT FROM v.org_id AND p.price_date = v.price_date AND "
           "p.system_from = v.system_from")
    async with ptx(conn):
        await conn.execute(f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT}'")
        await conn.execute(f"LOCK TABLE {n['lean']}, {n['hist']} IN SHARE ROW EXCLUSIVE MODE")
        # 1. post-swap deletions: an old row with no counterpart in the view at all
        deleted = await conn.execute(f"DELETE FROM {n['pre']} p WHERE NOT EXISTS (SELECT 1 FROM {n['old']} v WHERE "
                                     f"{key})")
        # 2. post-swap closures of rows the old table holds live
        closed = await conn.execute(
            f"UPDATE {n['pre']} p SET valid_to = v.valid_to, system_to = v.system_to FROM {n['old']} v WHERE {key} AND "
            f"p.valid_to IS NULL AND p.system_to IS NULL AND NOT (v.valid_to IS NULL AND v.system_to IS NULL)")
        # 3. rows created after the swap (live or already superseded), with their bar when still pending
        added = await conn.execute(
            f"INSERT INTO {n['pre']} ({cols}, bar) SELECT {', '.join('v.' + c for c in PS.OLD_COLUMNS if c != 'bar')}, "
            f"COALESCE(b.bar, '{{}}'::jsonb) FROM {n['old']} v LEFT JOIN {n['ser']} se ON se.global_security_id = "
            f"v.global_security_id AND se.price_type = v.price_type AND se.provider_key IS NOT DISTINCT FROM "
            f"v.provider_key AND se.org_id IS NOT DISTINCT FROM v.org_id LEFT JOIN {n['bars']} b ON b.series_id = se.id "
            f"AND b.price_date = v.price_date AND b.recorded_at = v.system_from WHERE NOT EXISTS (SELECT 1 FROM "
            f"{n['pre']} p WHERE {key})")
        c_pre = await conn.fetchval(f"SELECT count(*) FROM {n['pre']}")
        c_view = await conn.fetchval(f"SELECT count(*) FROM {n['old']}")
        if c_pre != c_view:
            raise Refused(f"after the write-back the old table holds {c_pre} rows and the view {c_view}")
        await conn.execute(f"ALTER VIEW {n['old']} RENAME TO {PS.VIEW_V3}")
        await conn.execute(f"ALTER TABLE {n['pre']} RENAME TO {PS.OLD}")
        await _set(conn, s, store_mode="legacy", phase="rolled_back", rolled_back_at=await conn.fetchval("SELECT now()"))
    return {"action": "rolled_back", "written_back": {"deleted": int(deleted.split()[-1]),
                                                      "closed": int(closed.split()[-1]),
                                                      "added": int(added.split()[-1])}, "rows": c_pre}


async def drop_old(conn, s: str = "portfolio", *, confirm: bool = False) -> dict:
    n = names(s)
    m = await migration_row(conn, s)
    why = []
    if not confirm:
        why.append("--i-confirm was not passed")
    if m.get("phase") != "swapped":
        why.append(f"phase is '{m.get('phase')}', not 'swapped'")
    if m.get("verified_ok") is not True:
        why.append("the migration's verification is not green")
    if m.get("archive_ok") is not True:
        why.append("the archive export (the only other home of the OHLCV bars) is not green")
    if await relkind(conn, n["pre"]) != "r":
        why.append(f"{n['pre']} does not exist")
    if why:
        raise Refused("--drop-old refused: " + "; ".join(why))
    async with ptx(conn):
        await conn.execute(f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT}'")
        size = await conn.fetchval("SELECT pg_total_relation_size($1::regclass)", n["pre"])
        await conn.execute(f"DROP TABLE {n['pre']}")
        await _set(conn, s, phase="dropped", dropped_at=await conn.fetchval("SELECT now()"))
    return {"action": "dropped", "freed_bytes": int(size)}


async def reset_copy(conn, s: str = "portfolio") -> dict:
    """Empty the NEW (derived) tables and start the copy over. Refused once swapped: then they are the record."""
    n = names(s)
    m = await migration_row(conn, s)
    if m["phase"] in ("swapped", "dropped") or await relkind(conn, n["old"]) != "r":
        raise Refused(f"phase is '{m['phase']}' — the lean tables ARE the price of record now; never reset them")
    async with ptx(conn):
        await conn.execute(f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT}'")
        await conn.execute(f"DROP TRIGGER IF EXISTS prices3_dirty ON {n['old']}")
        await conn.execute(f"TRUNCATE {n['lean']}, {n['hist']}, {n['bars']}, {n['dirty']}")
        await conn.execute(f"UPDATE {n['mig']} SET store_mode = 'legacy', phase = 'not_started', checkpoint_gsid = "
                           f"NULL, copied_rows = 0, copied_securities = 0, batches = 0, copy_started_at = NULL, "
                           f"copy_finished_at = NULL, verified_ok = NULL, verified_at = NULL, verify_detail = '{{}}', "
                           f"swapped_at = NULL, rolled_back_at = NULL, archive_ok = NULL, archive_at = NULL, "
                           f"archive_detail = '{{}}', updated_at = now() WHERE id = 1")
    return {"action": "reset"}


# ── archive export ──────────────────────────────────────────────────────────
def read_back_year(store, key: str):
    """(rows, exact sum of price) of one archive object, read back from the store (one column, no frame)."""
    import io

    import pyarrow.compute as pc
    import pyarrow.parquet as pq
    from decimal import Decimal

    tbl = pq.read_table(io.BytesIO(store.get(key)), columns=["price"])
    total = pc.sum(tbl["price"]).as_py() if tbl.num_rows else Decimal(0)
    return int(tbl.num_rows), Decimal(total)


async def export_archive(conn, s: str = "portfolio", *, store=None, prefix: str | None = None, chunk: int = 50_000,
                         sleep: float = DEFAULT_SLEEP) -> dict:
    """The frozen old table's live PLATFORM rows with their bars → one compact Parquet per year; read back."""
    from services.security_data import price_archive as PA

    n = names(s)
    prefix = prefix or PA.PREFIX
    m = await migration_row(conn, s)
    if m["phase"] != "swapped":
        raise Refused(f"the archive export runs after the swap (phase '{m['phase']}')")
    store = store or PA.default_store()
    if store is None:
        raise Refused("no R2 credentials (R2_ACCOUNT_ID / R2_ACCESS_KEY_ID / R2_SECRET_ACCESS_KEY) — run under doppler")
    live = "org_id IS NULL AND valid_to IS NULL AND system_to IS NULL"
    async with ptx(conn):
        years = [int(r[0]) for r in await conn.fetch(
            f"SELECT DISTINCT extract(year FROM price_date)::int FROM {n['pre']} WHERE {live} ORDER BY 1")]
    detail, ok = {}, True
    for y in years:
        w = PA.YearWriter(store, PA.compact_key(y, prefix))
        want_n, want_sum = 0, 0
        async with ptx(conn, timeout="0"):
            cur = await conn.cursor(
                f"SELECT global_security_id, price_type, provider_key, price_date, price, adjustment_factor, "
                f"currency_code, source, as_of, system_from AS recorded_at, quality_flags, licence, bar FROM {n['pre']} "
                f"WHERE {live} AND price_date >= $1 AND price_date < $2 ORDER BY global_security_id, price_date",
                date(y, 1, 1), date(y + 1, 1, 1))
            while True:
                rows = await cur.fetch(chunk)
                if not rows:
                    break
                w.add([PA.normalise(r) for r in rows])
            row = await conn.fetchrow(f"SELECT count(*) AS n, COALESCE(sum(price), 0) AS s FROM {n['pre']} WHERE "
                                      f"{live} AND price_date >= $1 AND price_date < $2", date(y, 1, 1),
                                      date(y + 1, 1, 1))
            want_n, want_sum = int(row["n"]), row["s"]
        wrote = w.close()
        back_n, got_sum = read_back_year(store, PA.compact_key(y, prefix))
        good = wrote == want_n == back_n and got_sum == want_sum
        ok = ok and good
        detail[str(y)] = {"rows": want_n, "written": wrote, "read_back": back_n, "sum_price": str(want_sum),
                          "sum_read_back": str(got_sum), "ok": good}
        say(f"[EXPORT] year {y}: {wrote:,} rows → {PA.compact_key(y, prefix)} ({'OK' if good else 'MISMATCH'})")
        if sleep:
            await asyncio.sleep(sleep)
    async with ptx(conn):
        await _set(conn, s, archive_ok=ok, archive_at=await conn.fetchval("SELECT now()"),
                   archive_detail={"prefix": prefix, "years": detail})
        if ok:
            await conn.execute(f"INSERT INTO {n['arch']} (prefix, watermark, last_run_at, last_result) VALUES ($1, $2, "
                               f"now(), $3::jsonb) ON CONFLICT (prefix) DO UPDATE SET watermark = COALESCE("
                               f"{n['arch']}.watermark, EXCLUDED.watermark)", prefix, m["swapped_at"],
                               json.dumps({"export": "prices3_migrate"}))
    return {"ok": ok, "years": detail}


# ── CLI ─────────────────────────────────────────────────────────────────────
async def run(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="PRICES3 operator migration", add_help=True)
    for f in ("--dry-run", "--resume", "--status", "--export-archive", "--rollback", "--drop-old", "--i-confirm",
              "--no-swap", "--reset-copy", "--verify-only"):
        ap.add_argument(f, action="store_true")
    ap.add_argument("--batch", type=int, default=DEFAULT_BATCH)
    ap.add_argument("--sleep", type=float, default=DEFAULT_SLEEP)
    ap.add_argument("--schema", default="portfolio", help=argparse.SUPPRESS)
    a = ap.parse_args(argv)
    if a.batch < 1000 or a.sleep < 0:
        say("[REFUSED] --batch must be ≥ 1,000 and --sleep ≥ 0")
        return 2
    from _db_connect import connect, hydrate, resolve_dsn

    hydrate()
    dsn, how = await resolve_dsn(OPERATOR_DSN_VAR)
    if not dsn:
        say(f"[FAIL] no operator DSN: {how}")
        return 1
    conn = await connect(dsn)
    s = a.schema
    try:
        say(f"[INFO] connected as {await conn.fetchval('SELECT current_user')} via {how}; schema {s}")
        if not await conn.fetchval("SELECT to_regclass($1) IS NOT NULL", f"{s}.prices3_migration"):
            say("[FAIL] the PRICES3 schema is not applied (python3 apps/api/scripts/_apply_prices3.py --all)")
            return 1
        if a.status:
            say(json.dumps(await migration_row(conn, s), default=str, indent=1))
            return 0
        if a.dry_run:
            print_plan(await plan(conn, s, batch=a.batch, sleep=a.sleep))
            say(f"[STATE] {json.dumps({k: v for k, v in (await migration_row(conn, s)).items() if k in ('phase', 'store_mode', 'checkpoint_gsid', 'copied_rows')}, default=str)}")
            return 0
        if a.reset_copy:
            say(f"[RESET] {await reset_copy(conn, s)}")
            return 0
        if a.rollback:
            say(f"[ROLLBACK] {await rollback(conn, s)}")
            return 0
        if a.drop_old:
            say(f"[DROP] {await drop_old(conn, s, confirm=a.i_confirm)}")
            return 0
        if a.export_archive:
            r = await export_archive(conn, s, sleep=a.sleep)
            say(f"[EXPORT] {'OK' if r['ok'] else 'NOT OK'}")
            return 0 if r["ok"] else 1
        if a.verify_only:
            r = await verify_all(conn, s, batch=a.batch, sleep=a.sleep)
            say(f"[VERIFY] {'GREEN' if r['ok'] else 'RED'} {json.dumps({k: v for k, v in r.items() if k != 'first_mismatches'}, default=str)}")
            return 0 if r["ok"] else 1
        # the real run
        print_plan(await plan(conn, s, batch=a.batch, sleep=a.sleep))
        say(f"[COPY] {await copy(conn, s, batch=a.batch, sleep=a.sleep, resume=a.resume)}")
        say(f"[CATCH-UP] {await catch_up(conn, s)}")
        r = await verify_all(conn, s, batch=a.batch, sleep=a.sleep)
        say(f"[VERIFY] {'GREEN' if r['ok'] else 'RED'}: {r['securities']:,} securities, {r['rows_old']:,} rows old vs "
            f"{r['rows_new']:,} new, {r['mismatches']} mismatching")
        if not r["ok"]:
            say(f"[STOP] not swapping. First mismatches: {r['first_mismatches'][:5]}")
            return 1
        if a.no_swap:
            say("[STOP] --no-swap: copied and verified; run again without it to swap")
            return 0
        say(f"[CATCH-UP] {await catch_up(conn, s)}")
        say(f"[SWAP] {await swap(conn, s)}")
        from services.security_data import price_archive as PA

        if PA.r2_configured():
            r = await export_archive(conn, s, sleep=a.sleep)
            say(f"[EXPORT] {'OK' if r['ok'] else 'NOT OK — re-run --export-archive'}")
        else:
            say("[NEXT] no R2 credentials here: run --export-archive under doppler before --drop-old")
        say("[NEXT] check the app (Security master → Prices, a household's values, a backtest). Days later: "
            "--drop-old --i-confirm. To undo before then: --rollback.")
        return 0
    except Refused as exc:
        say(f"[REFUSED] {exc}")
        return 3
    finally:
        await conn.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(run(sys.argv[1:])))
