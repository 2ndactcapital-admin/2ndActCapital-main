#!/usr/bin/env python3
"""verify_mkt02c2 — the splice overlap gate, re-proven under the mkt02c2 rule.

    doppler run -- apps/api/venv/bin/python apps/api/scripts/verify_mkt02c2.py

THE RULE (services/market_data/splice.py). On the dates on or after F0 that
both sources carry, the gate passes only if ALL hold:
  1. at least 20 overlap days;
  2. at least 99.0% of them differ by no more than 0.02;
  3. no day differs by more than 0.5% of the series' own value.
Rule 3 replaced an absolute 1.00 limit after the live fred.sp500 vs ^GSPC
overlap showed two isolated single-day feed disagreements (about 0.12% and
0.04%) on 2,514 otherwise-agreeing days.

PHASE A only. Every row it writes belongs to a fixture series whose
series_key starts with 'verify.mkt02c2.'. Every Yahoo adapter runs over a FAKE
transport (verify_mkt02c's guarded helper). It never touches a real series and
never calls Yahoo or FRED. Fixture data and helpers are imported from
verify_mkt02c (never run as a subprocess); verify_mkt02c's own fixtures
('verify.mkt02c.') are a different tag and are left alone.

Hydrates its own secrets from Doppler over HTTPS (scripts/_doppler_env.py).

Output: [PASS] / [FAIL] / [FIND] / [SKIP] lines, then
``TOTAL: <passed> passed, <failed> failed``. Exit 1 on any [FAIL]; exit 2 if
teardown leaves a fixture row behind.
"""
from __future__ import annotations

import asyncio
import contextlib
import io
import os
import sys
import uuid
from decimal import Decimal
from pathlib import Path

import asyncpg

HERE = Path(__file__).resolve()
sys.path.insert(0, str(HERE.parents[1]))  # apps/api, for services.*
sys.path.insert(0, str(HERE.parent))      # scripts/, for _doppler_env + verify_mkt02c + the operator script

from _doppler_env import hydrate_from_doppler  # noqa: E402
from services.database import platform_scope  # noqa: E402
from services.market_data import adapters, registry, splice  # noqa: E402
from services.market_data.yahoo import YahooResponse  # noqa: E402
import market_data_splice_history as msh  # noqa: E402
import verify_mkt02c as vm  # noqa: E402

PREFIX = "verify.mkt02c2."
SERIES_T, OBS_T, RUNS_T = vm.SERIES_T, vm.OBS_T, vm.RUNS_T
D = Decimal

GATE_F0, GATE_PRE, GATE_OWN, GATE_OK = vm.GATE_F0, vm.GATE_PRE, vm.GATE_OWN, vm.GATE_OK
GATE_OWN_DATES = vm.GATE_OWN_DATES

# The live shape: one day off by about 0.12%, one by about 0.04%, every other day within 0.02.
LIVE_A, LIVE_B = 30, 60
LIVE_A_DAY, LIVE_B_DAY = GATE_OWN_DATES[LIVE_A], GATE_OWN_DATES[LIVE_B]
LIVE_A_DELTA = -(GATE_OWN[LIVE_A][1] * D("0.0012"))   # 4030.00 → -4.8360
LIVE_B_DELTA = GATE_OWN[LIVE_B][1] * D("0.0004")      # 4060.00 → +1.6240
LIVE_SHAPE = [(d, v + LIVE_A_DELTA if i == LIVE_A else v + LIVE_B_DELTA if i == LIVE_B else v)
              for i, (d, v) in enumerate(GATE_OK)]
ZERO_IDX = 3

PASSED = 0
FAILED = 0
CREATED_BATCHES: list[uuid.UUID] = []


def check(name: str, ok: bool, why: str, detail: str = "") -> bool:
    global PASSED, FAILED
    if ok:
        PASSED += 1
        print(f"[PASS] {name} — {why}")
    else:
        FAILED += 1
        print(f"[FAIL] {name} — {why}" + (f" | {detail}" if detail else ""))
    return ok


def find(msg: str) -> None:
    print(f"[FIND] {msg}")


def fixture_row(suffix: str, code: str) -> dict:
    r = vm.fixture_row(suffix, vm.FAKE, code, "active")
    r["series_key"] = PREFIX + suffix
    r["name"] = f"verify mkt02c2 {suffix}"
    r["notes"] = "mkt02c2 verify fixture"
    return r


FIXTURES = [fixture_row("gate", "VMKT02C2GATE"), fixture_row("live", "VMKT02C2LIVE"),
            fixture_row("zero", "VMKT02C2ZERO")]
K = {r["series_key"][len(PREFIX):]: r["series_key"] for r in FIXTURES}

FIXTURE_IDS = f"(SELECT id FROM {SERIES_T} WHERE series_key LIKE '{PREFIX}%')"
FIXTURE_RUNS = (f"(series_id IN {FIXTURE_IDS} OR batch_id IN (SELECT batch_id FROM {RUNS_T} "
                f"WHERE series_id IN {FIXTURE_IDS} AND batch_id IS NOT NULL) OR batch_id = ANY($1::uuid[]))")


def fixture_only(key: str) -> str:
    if not key.startswith(PREFIX):
        raise SystemExit(f"[FAIL] verify_mkt02c2 tried to touch a non-fixture series: {key}")
    return key


async def run_script(conn, key, payload_points, *, dry_run=False) -> tuple[int, str]:
    """The operator script's own run() over a FAKE Yahoo transport: exit code + output."""
    adapter, transport = vm.fake_yahoo()
    transport.routes[vm.SYMBOL_PATH] = YahooResponse(200, vm.chart_json(payload_points))
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = await msh.run(conn, adapter, fixture_only(key), vm.SYMBOL, dry_run)
    return code, buf.getvalue()


async def state(reader, key) -> tuple:
    return (len(await vm.all_rows(reader, key)), len(await vm.run_rows(reader, key)), await vm.notes_of(reader, key))


def own_sig(rows: list[dict]) -> list[tuple]:
    return sorted((str(r["id"]), r["obs_date"], r["value"], r["source_provider"], r["valid_to"], r["system_to"])
                  for r in rows if r["source_provider"] is None)


def gate_line(out: str) -> str:
    return next((ln for ln in out.splitlines() if ln.startswith("OVERLAP GATE")), "")


def find_lines(out: str) -> list[str]:
    return [ln for ln in out.splitlines() if ln.startswith("[FIND]")]


# ── Counting + teardown (tag 'verify.mkt02c2.') ─────────────────────────────
async def fixture_counts(conn) -> dict[str, int]:
    async with platform_scope(conn):
        return {
            "series": await conn.fetchval(f"SELECT count(*) FROM {SERIES_T} WHERE series_key LIKE '{PREFIX}%'"),
            "obs": await conn.fetchval(f"SELECT count(*) FROM {OBS_T} WHERE series_id IN {FIXTURE_IDS}"),
            "runs": await conn.fetchval(f"SELECT count(*) FROM {RUNS_T} WHERE {FIXTURE_RUNS}", CREATED_BATCHES),
        }


async def real_counts(conn) -> dict[str, int]:
    async with platform_scope(conn):
        return {
            "series": await conn.fetchval(f"SELECT count(*) FROM {SERIES_T} WHERE series_key NOT LIKE '{PREFIX}%'"),
            "obs": await conn.fetchval(f"SELECT count(*) FROM {OBS_T} WHERE series_id NOT IN {FIXTURE_IDS}"),
            "runs": await conn.fetchval(
                f"SELECT count(*) FROM {RUNS_T} WHERE NOT COALESCE({FIXTURE_RUNS}, false)", CREATED_BATCHES),
        }


async def delete_fixtures(conn, order) -> dict[str, int]:
    """By fixture tag only — never a TRUNCATE. Runs go by fixture series_id AND
    by any batch_id this script created."""
    where = {
        OBS_T: (f"series_id IN {FIXTURE_IDS}", []),
        RUNS_T: (FIXTURE_RUNS, [CREATED_BATCHES]),
        SERIES_T: (f"series_key LIKE '{PREFIX}%'", []),
    }
    deleted = {}
    async with platform_scope(conn):
        for table in order:
            clause, args = where[table]
            status = await conn.execute(f"DELETE FROM {table} WHERE {clause}", *args)
            deleted[table] = int(status.split()[-1])
    return deleted


# ═══════════════════════════════════════════════════════════════════════════
# The gate
# ═══════════════════════════════════════════════════════════════════════════
def g_constants() -> None:
    vals = (splice.MIN_OVERLAP_DAYS, splice.OVERLAP_MIN_PERCENT, splice.OVERLAP_TOLERANCE, splice.OVERLAP_MAX_RELATIVE)
    check("G0 rule constants in services/market_data/splice.py are exactly 20, 99.0, 0.02 and 0.005 (Decimal, not "
          "float), and the old absolute OVERLAP_MAX_DIFF is gone",
          vals == (20, D("99.0"), D("0.02"), D("0.005"))
          and all(isinstance(v, Decimal) for v in vals[1:]) and not hasattr(splice, "OVERLAP_MAX_DIFF"),
          "every proof below is only as good as the thresholds it runs against; a leftover 1.00 limit would "
          "still refuse the live data",
          f"values={vals} old_limit_present={hasattr(splice, 'OVERLAP_MAX_DIFF')}")


async def g_refusals_then_pass(conn, reader) -> None:
    key = K["gate"]
    await vm.seed_own_rows(conn, key, GATE_OWN)
    base = await state(reader, key)
    n = len(GATE_OWN)

    # S3a — rule 2: 5 of ~262 days off by 0.05 (~1.9% beyond 0.02), each far inside 0.5%
    bad_idx = {4, 54, 104, 154, 204}
    code_a, out_a = await run_script(conn, key, GATE_PRE + [(d, v + D("0.05") if i in bad_idx else v)
                                                          for i, (d, v) in enumerate(GATE_OWN)])
    after_a = await state(reader, key)
    line_a = gate_line(out_a)
    check("S3a REFUSES when more than 1% of overlap days differ by more than 0.02 (rule 2 alone): exit 1, zero writes, "
          "each such day printed as [FIND]",
          code_a == 1 and after_a == base and line_a.startswith("OVERLAP GATE FAILED") and "rule 2" in line_a
          and "rule 3" not in line_a and len(find_lines(out_a)) == len(bad_idx),
          "rule 2 is the main protection against a different index, a futures contract or a mis-scaled series — "
          "those fail on most days, not one",
          f"exit={code_a} state={base}->{after_a} gate={line_a!r}")

    # S3b — rule 3: one day off by 25.00 on ~4007 (~0.62%); rule 2 holds (1 of ~262 beyond 0.02)
    bad_day, bad_val = GATE_OWN_DATES[7], GATE_OWN[7][1]
    code_b, out_b = await run_script(conn, key, GATE_PRE + [(d, v + D("25.00") if i == 7 else v)
                                                          for i, (d, v) in enumerate(GATE_OWN)])
    after_b = await state(reader, key)
    line_b = gate_line(out_b)
    check("S3b REFUSES when every day is within tolerance except ONE day off by more than 0.5% of the series value "
          "(rule 3 alone; rule 2 holds): exit 1, zero writes, the offending day named",
          code_b == 1 and after_b == base and line_b.startswith("OVERLAP GATE FAILED") and "rule 3" in line_b
          and "rule 2" not in line_b and bad_day.isoformat() in line_b and str(bad_val) in line_b
          and Decimal(1) * 100 / n < 100 - splice.OVERLAP_MIN_PERCENT,
          "replacing the 1.00 limit must not open the door to a catastrophic single-day break",
          f"exit={code_b} state={base}->{after_b} gate={line_b!r}")

    # S3d — rule 1: no overlap
    code_d, out_d = await run_script(conn, key, GATE_PRE)
    after_d = await state(reader, key)
    line_d = gate_line(out_d)
    check(f"S3d REFUSES with fewer than {splice.MIN_OVERLAP_DAYS} overlap days (here none): exit 1, zero writes, "
          "rule 1 named",
          code_d == 1 and after_d == base and "rule 1" in line_d and "overlapping day" in line_d,
          "zero comparisons would otherwise pass the percentage test vacuously",
          f"exit={code_d} state={base}->{after_d} gate={line_d!r}")

    # S3c — within tolerance everywhere
    own_before = own_sig(await vm.all_rows(reader, key))
    code_c, out_c = await run_script(conn, key, GATE_PRE + GATE_OK)
    rows = await vm.all_rows(reader, key)
    act = vm.active_of(rows)
    pre = {d: r for d, r in act.items() if d < GATE_F0}
    check("S3c a payload within tolerance on every day PASSES: exit 0, every pre-F0 date inserted as 'yahoo', no "
          "[FIND], own rows unchanged",
          code_c == 0 and gate_line(out_c).startswith("OVERLAP GATE PASSED") and not find_lines(out_c)
          and set(pre) == {d for d, _ in GATE_PRE}
          and all(r["source_provider"] == "yahoo" and r["value"] == dict(GATE_PRE)[d] for d, r in pre.items())
          and own_sig(rows) == own_before,
          "the gate must let the genuine series through, or it is just a wall",
          f"exit={code_c} pre={len(pre)}/{len(GATE_PRE)} gate={gate_line(out_c)!r}")


async def g_live_shape(conn, reader) -> None:
    key = K["live"]
    await vm.seed_own_rows(conn, key, GATE_OWN)
    rows0 = await vm.all_rows(reader, key)
    own_before = own_sig(rows0)
    two_before = {d: r for d, r in vm.active_of(rows0).items() if d in (LIVE_A_DAY, LIVE_B_DAY)}
    base = await state(reader, key)

    # Dry run first: same gate, same counts, nothing written.
    code_dry, out_dry = await run_script(conn, key, GATE_PRE + LIVE_SHAPE, dry_run=True)
    after_dry = await state(reader, key)
    code, out = await run_script(conn, key, GATE_PRE + LIVE_SHAPE)
    rows = await vm.all_rows(reader, key)
    act = vm.active_of(rows)
    pre = {d: r for d, r in act.items() if d < GATE_F0}
    on_after = [r for r in rows if r["obs_date"] >= GATE_F0]
    two_after = {d: r for d, r in act.items() if d in (LIVE_A_DAY, LIVE_B_DAY)}
    finds = find_lines(out)
    line = gate_line(out)
    rel_a = splice.relative_percent(abs(LIVE_SHAPE[LIVE_A][1] - GATE_OWN[LIVE_A][1]), GATE_OWN[LIVE_A][1])
    rel_b = splice.relative_percent(abs(LIVE_SHAPE[LIVE_B][1] - GATE_OWN[LIVE_B][1]), GATE_OWN[LIVE_B][1])

    check("S3e the LIVE shape (one day off ~0.12%, one ~0.04%, the rest within 0.02) PASSES: exit 0, every pre-F0 "
          "date inserted as 'yahoo', nothing written on or after F0",
          code == 0 and line.startswith("OVERLAP GATE PASSED")
          and set(pre) == {d for d, _ in GATE_PRE}
          and all(r["source_provider"] == "yahoo" and r["value"] == dict(GATE_PRE)[d] for d, r in pre.items())
          and len(on_after) == len(GATE_OWN) and all(r["source_provider"] is None for r in on_after),
          "this is the data the old 1.00 rule refused; two feeds of the same index disagreeing on two days must "
          "not block the splice",
          f"exit={code} pre={len(pre)}/{len(GATE_PRE)} after={len(on_after)}/{len(GATE_OWN)} gate={line!r}")
    check("S3e both days are reported: exactly two [FIND] lines naming them (with their relative difference) and the "
          "gate line says 2 days were tolerated and the series' own value stands",
          len(finds) == 2 and any(LIVE_A_DAY.isoformat() in f and f"{rel_a}%" in f for f in finds)
          and any(LIVE_B_DAY.isoformat() in f and f"{rel_b}%" in f for f in finds)
          and "2 overlap day(s) beyond 0.02 tolerated" in line and "series' own value stands" in line,
          "a tolerated disagreement must stay visible to the operator, never silently absorbed",
          f"finds={finds} gate={line!r}")
    check("S3e the series' own rows for the two disagreeing days are unchanged (same id, value, NULL provider, still "
          "active), and every own-source row is identical before and after",
          set(two_before) == set(two_after) == {LIVE_A_DAY, LIVE_B_DAY}
          and all(two_after[d]["id"] == two_before[d]["id"] and two_after[d]["value"] == two_before[d]["value"]
                  and two_after[d]["source_provider"] is None for d in two_before)
          and two_after[LIVE_A_DAY]["value"] == GATE_OWN[LIVE_A][1]
          and two_after[LIVE_B_DAY]["value"] == GATE_OWN[LIVE_B][1]
          and own_sig(rows) == own_before,
          "a day-level discrepancy inside FRED's own range is reported but never overwritten — FRED is the source "
          "of record",
          f"before={[(str(d), r['value']) for d, r in two_before.items()]} "
          f"after={[(str(d), r['value']) for d, r in two_after.items()]}")

    def comparable(o: str) -> list[str]:
        keep = ("[FIND]", "OVERLAP GATE", "rows inserted", "overlap ", "within ", "candidate", "worst", "points fetched",
                "earliest date")
        return [ln for ln in o.splitlines() if ln.startswith(keep)]
    check("DRY the dry run with the new rule behaves like the real run — same exit code, gate line, [FIND] lines and "
          "planned counts — except that nothing is written (rows, run rows and notes identical)",
          code_dry == 0 and after_dry == base and "DRY RUN — nothing written" in out_dry
          and comparable(out_dry) == comparable(out) and f"rows inserted {len(GATE_PRE)}" in out_dry,
          "the operator decides from the dry run; it must preview the real run exactly and have zero side effects",
          f"exit={code_dry} state={base}->{after_dry} dry={comparable(out_dry)} real={comparable(out)}")


async def g_zero(conn, reader) -> None:
    # Pure function: no exception, gate fails with a clear message.
    own = dict(GATE_OWN)
    own[GATE_OWN_DATES[ZERO_IDX]] = D("0")
    try:
        g = splice.run_gate(own, GATE_OK, GATE_F0)
        res = splice.SpliceResult(PREFIX + "pure", vm.SYMBOL, True, status="gate_failed", f0=GATE_F0, gate=g)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = msh.print_result(res)
        out, err = buf.getvalue(), None
    except Exception as exc:  # noqa: BLE001
        g, code, out, err = None, None, "", f"{type(exc).__name__}: {exc}"
    check("Z1 a zero series value on an overlap day (gate function + the script's printer): no exception, gate "
          "FAILED on rule 3 with a message naming the zero day, exit 1",
          err is None and g is not None and not g.passed and 3 in g.failed_rules
          and g.zero_value_day == GATE_OWN_DATES[ZERO_IDX] and code == 1
          and "zero" in gate_line(out) and GATE_OWN_DATES[ZERO_IDX].isoformat() in gate_line(out),
          "an index never prints zero, but a bad row must refuse cleanly rather than crash on a division",
          f"err={err} reason={g.reason if g else None} exit={code}")

    # Through the database and the operator's run().
    key = K["zero"]
    seeded = None
    try:
        await vm.seed_own_rows(conn, key, [(d, D("0") if i == ZERO_IDX else v) for i, (d, v) in enumerate(GATE_OWN)])
        seeded = True
    except asyncpg.CheckViolationError as exc:
        find(f"Z2 the database refuses a zero observation value ({exc.constraint_name or 'CHECK'}) — the zero case "
             "cannot occur in stored data; Z1 alone proves the gate handles it")
    if seeded:
        base = await state(reader, key)
        try:
            code, out = await run_script(conn, key, GATE_PRE + GATE_OK)
            err = None
        except Exception as exc:  # noqa: BLE001
            code, out, err = None, "", f"{type(exc).__name__}: {exc}"
        after = await state(reader, key)
        line = gate_line(out)
        check("Z2 the same zero value stored in a fixture series, run through the operator script: no exception, exit "
              "1, zero writes, the gate line names the zero day",
              err is None and code == 1 and after == base and line.startswith("OVERLAP GATE FAILED")
              and "zero" in line and GATE_OWN_DATES[ZERO_IDX].isoformat() in line,
              "the refusal must hold on the real write path, not only in the pure function",
              f"err={err} exit={code} state={base}->{after} gate={line!r}")


async def phase_a(conn, reader) -> None:
    order = await vm.fk_delete_order(reader)
    leftover = await delete_fixtures(conn, order)
    if any(leftover.values()):
        find(f"removed fixture rows left by an earlier interrupted run: {leftover}")
    base_untouched = await vm.untouched_counts(reader)
    base_real = await real_counts(reader)
    guarded_before = vm.GUARDED
    try:
        g_constants()
        async with platform_scope(conn):
            await registry.load_registry(conn, FIXTURES)
        await g_refusals_then_pass(conn, reader)
        await g_live_shape(conn, reader)
        await g_zero(conn, reader)
        check("A.guard every Yahoo adapter this script built ran over a fake transport",
              vm.GUARDED - guarded_before >= 7, "the gate proofs must never call Yahoo",
              f"guarded={vm.GUARDED - guarded_before}")
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001 — reported, then teardown still runs
        check("A.x the gate proofs ran to completion", False, "an unexpected exception aborted the remaining assertions",
              adapters.scrub_error(f"{type(exc).__name__}: {exc}"))
    finally:
        expected_order = order.index(OBS_T) < order.index(RUNS_T) < order.index(SERIES_T)
        deleted = await delete_fixtures(conn, order)
        remaining = await fixture_counts(reader)
        end_real = await real_counts(reader)
        end_untouched = await vm.untouched_counts(reader)
        check("T1 portfolio.securities_global, securities_global_prices and public.fx_rates row counts unchanged",
              end_untouched == base_untouched, "the gate fix writes market_data fixtures only",
              f"before={base_untouched} after={end_untouched}")
        check("T2 teardown order observations → ingest_runs → series, derived from information_schema",
              expected_order, "a parent deleted first fails on the FK (or, with a later cascade, deletes more)",
              f"order={order} deleted={deleted}")
        check("T3 after teardown, NON-fixture row counts in all three market_data tables are exactly as before",
              end_real == base_real, "proves this script wrote and removed only its own rows — never a TRUNCATE",
              f"before={base_real} after={end_real}")
        if any(remaining.values()):
            print(f"[FAIL] T4 fixture rows remain after teardown: {remaining}")
            raise SystemExit(2)
        check("T4 zero 'verify.mkt02c2.' fixture rows (series, observations, runs) remain — re-read independently",
              True, "a leftover fixture would surface in real charts")


async def main() -> int:
    loaded, doppler_err = hydrate_from_doppler()
    if loaded:
        print(f"[INFO] hydrated {len(loaded)} secrets from Doppler over HTTPS")
    elif doppler_err:
        print(f"[INFO] Doppler hydration skipped: {doppler_err} — falling back to the ambient environment")

    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        check("DB DATABASE_URL present", False, "nothing can be proven without the database")
        print(f"TOTAL: {PASSED} passed, {FAILED} failed")
        return 1
    dsn = dsn.replace("postgresql+asyncpg://", "postgresql://")
    try:
        conn = await asyncpg.connect(dsn, statement_cache_size=0, ssl="require")
        reader = await asyncpg.connect(dsn, statement_cache_size=0, ssl="require")
    except Exception as exc:  # noqa: BLE001
        check("DB connect", False, "nothing can be proven without the database", type(exc).__name__)
        print(f"TOTAL: {PASSED} passed, {FAILED} failed")
        return 1
    try:
        await phase_a(conn, reader)
    finally:
        await conn.close()
        await reader.close()
    print(f"TOTAL: {PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
