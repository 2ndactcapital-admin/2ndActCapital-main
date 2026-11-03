"""One-off (aibudget1 fix 1): reproduce the verify_ensemblesystemone [Y]
rollback check's TypeError, then prove the corrected injection fails the
INSERT AFTER the retire and the retire rolls back. Fixture task_key + fixture
System One row; every row created is deleted and zero-remaining is asserted.
llm_options is stubbed (no proxy call) — the stub's shape mirrors the real
option dicts validate_selection / validate_budget read.
"""
from __future__ import annotations

import os
os.environ["AUTHZ_TEST_MODE"] = "1"

import asyncio
import pathlib
import sys
import traceback

HERE = pathlib.Path(__file__).resolve()
sys.path.insert(0, str(HERE.parent))
from _db_bootstrap import bootstrap_async  # noqa: E402

import asyncpg  # noqa: E402

TASK = "aib1fix1_task_99000000000000000000000000af1001"
S1 = "aib1fix1-s1-99000000-0000-0000-0000-0000000af101"
results = []


def check(ok, label, detail=""):
    results.append(ok)
    print(f"[{'PASS' if ok else 'FAIL'}] {label}" + (f"  — {detail}" if detail else ""))


class Tracing:
    """Delegates to the real connection; records every SQL statement."""
    def __init__(self, conn):
        self._c, self.log = conn, []

    def __getattr__(self, n):
        return getattr(self._c, n)

    async def fetchrow(self, q, *a):
        self.log.append(" ".join(q.split())[:40])
        return await self._c.fetchrow(q, *a)


async def _super(conn):
    await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")


async def teardown(conn):
    async with conn.transaction():
        await _super(conn)
        await conn.execute("DELETE FROM ai_ensemble_configs WHERE task_key = $1", TASK)
        await conn.execute("DELETE FROM ai_system_one_models WHERE key = $1", S1)


async def main():
    dsn = await bootstrap_async()
    conn = await asyncpg.connect(dsn, statement_cache_size=0, ssl="require")
    import services.ai_ensembles as ens
    ens.KNOWN_TASK_KEYS = frozenset({*ens.KNOWN_TASK_KEYS, TASK})

    async def stub_llm_options(c, task_key=None):  # the REAL signature (aibudget1)
        return [{"model_id": m, "model_version": f"{m}-v", "selectable": True, "unavailable_reason": None,
                 "public_data_only": False, "default_budget_kind": "unlimited"}
                for m in ("claude-haiku", "claude-sonnet")]

    ens.llm_options = stub_llm_options
    try:
        await teardown(conn)
        async with conn.transaction():
            await _super(conn)
            await conn.execute(
                """INSERT INTO ai_system_one_models (key, display_name, provider, model_route, model_version,
                     availability, is_default, last_verified_at, last_check_detail, notes)
                   VALUES ($1, 'aib1fix1 fixture', 'typesafe', 'fixture-route', 'fx-v', 'available', false,
                           now(), 'fixture', 'aibudget1 fix1 one-off')""", S1)
        for m1, m2 in (("claude-haiku", "claude-sonnet"), ("claude-sonnet", "claude-haiku")):
            async with conn.transaction():
                await _super(conn)
                r = await ens.activate_ensemble(conn, TASK, m1, m2, S1, created_by=None)
        c2_id = r["ensemble"]["id"]

        real = ens.llm_options

        # ── A: the verify's ORIGINAL injection (one-arg stub) ──
        async def broken_old(c):
            opts = await real(c)
            for o in opts:
                if o["model_id"] == "claude-haiku":
                    o["model_version"] = None
            return opts

        # ── B: the corrected injection (forwards task_key) ──
        async def broken_new(c, *args, **kwargs):
            opts = await real(c, *args, **kwargs)
            for o in opts:
                if o["model_id"] == "claude-haiku":
                    o["model_version"] = None
            return opts

        for tag, fn in (("ORIGINAL", broken_old), ("CORRECTED", broken_new)):
            ens.llm_options = fn
            t = Tracing(conn)
            raised, tb = None, ""
            try:
                async with conn.transaction():
                    await _super(conn)
                    try:
                        await ens.activate_ensemble(t, TASK, "claude-haiku", "claude-sonnet", S1, created_by=None)
                    except Exception as exc:  # noqa: BLE001
                        raised, tb = exc, traceback.format_exc()
                    mid = await conn.fetchrow("SELECT is_active, retired_at FROM ai_ensemble_configs WHERE id=$1::uuid", c2_id)
            finally:
                ens.llm_options = real
            retired = any(s.startswith("UPDATE ai_ensemble_configs") for s in t.log)
            print(f"\n--- {tag} injection: {type(raised).__name__}: {raised}")
            print("statements issued:", t.log)
            if tag == "ORIGINAL":
                print(tb.strip().splitlines()[-4:])
                check(isinstance(raised, TypeError) and not retired and t.log == [],
                      "reproduced: the one-arg stub raises TypeError on activate_ensemble's first line, "
                      "BEFORE any SQL (no retire)")
            else:
                async with conn.transaction():
                    await _super(conn)
                    after = await conn.fetchrow("SELECT is_active, retired_at FROM ai_ensemble_configs WHERE id=$1::uuid", c2_id)
                    n = await conn.fetchval("SELECT count(*) FROM ai_ensemble_configs WHERE task_key=$1", TASK)
                check(isinstance(raised, asyncpg.NotNullViolationError) and retired
                      and t.log[-1].startswith("INSERT INTO ai_ensemble_configs")
                      and mid["is_active"] is True and mid["retired_at"] is None
                      and after["is_active"] is True and n == 2,
                      "fixed: NotNullViolation on the INSERT, AFTER the retire UPDATE ran; retire rolled back "
                      "(#2 active, 2 rows)", f"retire_ran={retired} rows={n}")
    finally:
        await teardown(conn)
        async with conn.transaction():
            await _super(conn)
            left = (await conn.fetchval("SELECT count(*) FROM ai_ensemble_configs WHERE task_key=$1", TASK),
                    await conn.fetchval("SELECT count(*) FROM ai_system_one_models WHERE key=$1", S1))
        check(left == (0, 0), "teardown: zero fixture rows remain", f"{left}")
        await conn.close()
    print(f"\nTOTAL {sum(results)} PASS, {len(results) - sum(results)} FAIL")


asyncio.run(main())
