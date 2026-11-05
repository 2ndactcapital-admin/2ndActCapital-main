"""BUGFIX2 Task 7 — remove exactly the HollisMuse test rows, by id. One-off.

    doppler-hydrated, no prompts:
    python3 apps/api/scripts/bugfix2_remove_muse_rows.py            # DRY RUN: print the rows, roll back
    python3 apps/api/scripts/bugfix2_remove_muse_rows.py --apply    # delete, verify, commit

WHAT (the rows HollisMuse created on production, 2026-11-03 — nothing else):
  * model "MUSE – Model" v1  portfolio.models a9458f13-c360-4e30-a4db-11eb0366c6cd, with its version rows
    (portfolio.model_versions + model_version_positions), its upload log entry (portfolio.model_upload_events),
    its model_events, and its OPEN approval human task — the system:human_task:approve_models RUN the
    version's approval_task_id belongs to (workflow_runs + workflow_run_steps) and the to-dos that point at it
    (member_todos.related_id = that step or run).
  * workflow "MUSE – workflow" v1  workflow_definitions 8935770a-2cb6-4892-aef9-26a643d75810, with its
    workflow_versions and workflow_steps (it has no runs and no triggers — asserted, else refused).

HOW
  * The delete set is resolved by id from the two roots above and PRINTED (table, ids) before anything is
    deleted. Every row must belong to the default org; anything else aborts.
  * ONE transaction on the operator (postgres) connection: the portfolio model tables refuse DELETE unless
    ``models.teardown = 'fixture'`` is set LOCAL and the role is not app_service
    (portfolio.model_teardown_allowed) — the only path past their immutability triggers.
  * For every touched table, BEFORE: row count + md5 over the primary key of every row NOT in the delete set;
    AFTER: row count + md5 over every remaining row. They must match (count_after = count_before - deleted,
    same checksum) and zero MUSE rows may remain, or the transaction rolls back. Primary keys are read from
    pg_constraint, not assumed.
No secret is printed.
"""
from __future__ import annotations

import asyncio
import sys
from uuid import UUID

from _db_bootstrap import bootstrap_async  # noqa: E402  (puts apps/api + venv on sys.path)
from _db_connect import connect, resolve_dsn  # noqa: E402
import _verify_cleanup as VC  # noqa: E402  (CRMDETAIL1 — the permanent-demo-data guard)


async def demo_refusals(conn, plan) -> list[str]:
    """CRMDETAIL1 — refuse if ANY planned row belongs to permanent demo data (a record tagged demo-*, or a household
    whose members all are). A removal script never deletes demo rows; asserted before anything is deleted."""
    return await VC.demo_rows_by_ids(conn, {t: list(ids) for t, ids in plan.items()})

DEFAULT_ORG = UUID("00000000-0000-0000-0000-000000000001")
MUSE_MODEL = UUID("a9458f13-c360-4e30-a4db-11eb0366c6cd")
MUSE_WORKFLOW = UUID("8935770a-2cb6-4892-aef9-26a643d75810")
APPROVE_MODELS_DEF_NAME = "system:human_task:approve_models"
# The operator (postgres) DSN the verify teardowns use (verify_lotmethod): DATABASE_URL is app_service, which
# portfolio.model_teardown_allowed refuses by design.
OPERATOR_DSN_VAR = "DATABASE_URL_PRECUTOVER_POSTGRES_BACKUP"

# child-before-parent (FKs read live: model_versions.approval_task_id → workflow_run_steps, so the model
# rows go before the run; workflow_steps → workflow_versions → workflow_definitions).
ORDER = [
    "public.member_todos",
    "portfolio.model_events",
    "portfolio.model_upload_events",
    "portfolio.model_version_positions",
    "portfolio.model_versions",
    "portfolio.models",
    "public.workflow_run_steps",
    "public.workflow_runs",
    "public.workflow_steps",
    "public.workflow_versions",
    "public.workflow_definitions",
]


async def resolve(conn) -> dict[str, list[UUID]]:
    one = conn.fetch
    model = [r["id"] for r in await one("SELECT id FROM portfolio.models WHERE id = $1 AND org_id = $2",
                                        MUSE_MODEL, DEFAULT_ORG)]
    versions = [r["id"] for r in await one("SELECT id FROM portfolio.model_versions WHERE model_id = ANY($1::uuid[])",
                                           model)]
    steps_appr = [r["approval_task_id"] for r in await one(
        "SELECT DISTINCT approval_task_id FROM portfolio.model_versions WHERE model_id = ANY($1::uuid[]) "
        "AND approval_task_id IS NOT NULL", model)]
    runs = [r["workflow_run_id"] for r in await one(
        "SELECT DISTINCT s.workflow_run_id FROM workflow_run_steps s JOIN workflow_runs r ON r.id = s.workflow_run_id "
        "JOIN workflow_versions v ON v.id = r.workflow_version_id JOIN workflow_definitions d ON d.id = v.workflow_definition_id "
        "WHERE s.id = ANY($1::uuid[]) AND d.name = $2", steps_appr, APPROVE_MODELS_DEF_NAME)]
    run_steps = [r["id"] for r in await one("SELECT id FROM workflow_run_steps WHERE workflow_run_id = ANY($1::uuid[])",
                                            runs)]
    wf = [r["id"] for r in await one("SELECT id FROM workflow_definitions WHERE id = $1 AND org_id = $2",
                                     MUSE_WORKFLOW, DEFAULT_ORG)]
    wf_versions = [r["id"] for r in await one(
        "SELECT id FROM workflow_versions WHERE workflow_definition_id = ANY($1::uuid[])", wf)]
    return {
        "public.member_todos": [r["id"] for r in await one(
            "SELECT id FROM member_todos WHERE related_id = ANY($1::uuid[])", run_steps + runs)],
        "portfolio.model_events": [r["id"] for r in await one(
            "SELECT id FROM portfolio.model_events WHERE model_id = ANY($1::uuid[]) OR version_id = ANY($2::uuid[])",
            model, versions)],
        "portfolio.model_upload_events": [r["id"] for r in await one(
            "SELECT id FROM portfolio.model_upload_events WHERE model_id = ANY($1::uuid[]) OR version_id = ANY($2::uuid[])",
            model, versions)],
        "portfolio.model_version_positions": [r["id"] for r in await one(
            "SELECT id FROM portfolio.model_version_positions WHERE version_id = ANY($1::uuid[])", versions)],
        "portfolio.model_versions": versions,
        "portfolio.models": model,
        "public.workflow_run_steps": run_steps,
        "public.workflow_runs": runs,
        "public.workflow_steps": [r["id"] for r in await one(
            "SELECT id FROM workflow_steps WHERE workflow_version_id = ANY($1::uuid[])", wf_versions)],
        "public.workflow_versions": wf_versions,
        "public.workflow_definitions": wf,
    }


async def guards(conn, plan) -> list[str]:
    """Refusals: anything that would make this more than the MUSE rows."""
    bad = []
    if len(plan["portfolio.models"]) != 1:
        bad.append(f"MUSE model {MUSE_MODEL} not found exactly once in the default org")
    if len(plan["public.workflow_definitions"]) != 1:
        bad.append(f"MUSE workflow {MUSE_WORKFLOW} not found exactly once in the default org")
    wf_runs = await conn.fetchval("SELECT count(*) FROM workflow_runs WHERE workflow_version_id = ANY($1::uuid[])",
                                  plan["public.workflow_versions"])
    trig = await conn.fetchval("SELECT count(*) FROM workflow_triggers WHERE workflow_definition_id = ANY($1::uuid[])",
                               plan["public.workflow_definitions"])
    if wf_runs or trig:
        bad.append(f"the MUSE workflow has {wf_runs} runs / {trig} triggers — refusing (expected none)")
    # Nothing ELSE may hang off the model (a real account, proposal, backtest ...).
    for t, c in (("account_model_links", "model_id"), ("rebalance_proposals", "model_id"), ("tilt_sets", "model_id"),
                 ("backtests", "model_id"), ("portfolio.replica_model_specs", "model_id"),
                 ("portfolio.model_library_entries", "model_id"), ("portfolio.model_subscriptions", "model_id"),
                 ("portfolio.model_uploads", "model_id")):
        n = await conn.fetchval(f"SELECT count(*) FROM {t} WHERE {c} = ANY($1::uuid[])", plan["portfolio.models"])
        if n:
            bad.append(f"{t} has {n} rows referencing the MUSE model — refusing")
    for t in ORDER:
        if not plan[t] or t == "public.member_todos":
            continue
        schema, name = t.split(".")
        has_org = await conn.fetchval("SELECT 1 FROM information_schema.columns WHERE table_schema = $1 "
                                      "AND table_name = $2 AND column_name = 'org_id'", schema, name)
        if has_org:
            n = await conn.fetchval(f"SELECT count(*) FROM {t} WHERE id = ANY($1::uuid[]) AND org_id IS DISTINCT FROM $2",
                                    plan[t], DEFAULT_ORG)
            if n:
                bad.append(f"{t}: {n} planned rows outside the default org — refusing")
    bad += await demo_refusals(conn, plan)   # CRMDETAIL1 — zero demo rows touched
    return bad


async def pk_cols(conn, table: str) -> list[str]:
    rows = await conn.fetch(
        "SELECT a.attname FROM pg_constraint c JOIN pg_attribute a ON a.attrelid = c.conrelid "
        "AND a.attnum = ANY(c.conkey) WHERE c.conrelid = $1::regclass AND c.contype = 'p' ORDER BY a.attnum", table)
    return [r["attname"] for r in rows]


async def fingerprint(conn, table: str, exclude: list[UUID]) -> tuple[int, str]:
    pk = await pk_cols(conn, table)
    if pk != ["id"]:
        raise RuntimeError(f"{table}: primary key is {pk}, expected (id)")
    row = await conn.fetchrow(
        f"SELECT count(*) AS n, md5(COALESCE(string_agg(id::text, ',' ORDER BY id), '')) AS h "
        f"FROM {table} WHERE NOT (id = ANY($1::uuid[]))", exclude)
    return int(row["n"]), row["h"]


async def main() -> int:
    apply = "--apply" in sys.argv
    await bootstrap_async(quiet=True)
    dsn, how = await resolve_dsn(OPERATOR_DSN_VAR)
    if not dsn:
        print(f"[FAIL] no working operator DSN ({how})")
        return 1
    conn = await connect(dsn)
    role = await conn.fetchval("SELECT current_user")
    print(f"operator connection role: {role}")
    if role == "app_service":
        print("[FAIL] the operator DSN resolved to app_service — the model teardown path refuses it")
        await conn.close()
        return 1
    try:
        tr = conn.transaction()
        await tr.start()
        try:
            await conn.execute("SELECT set_config('models.teardown', 'fixture', true)")
            await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
            plan = await resolve(conn)
            print("BUGFIX2 Task 7 — HollisMuse rows to delete:")
            for t in ORDER:
                print(f"  {t:38s} {len(plan[t]):3d}  {', '.join(str(i) for i in plan[t])}")
            bad = await guards(conn, plan)
            if bad:
                for b in bad:
                    print(f"[FAIL] {b}")
                await tr.rollback()
                return 1
            before = {t: await fingerprint(conn, t, plan[t]) for t in ORDER}
            totals = {t: await conn.fetchval(f"SELECT count(*) FROM {t}") for t in ORDER}
            for t in ORDER:
                if plan[t]:
                    n = await conn.fetchval(f"WITH d AS (DELETE FROM {t} WHERE id = ANY($1::uuid[]) RETURNING 1) "
                                            "SELECT count(*) FROM d", plan[t])
                    if n != len(plan[t]):
                        raise RuntimeError(f"{t}: deleted {n} of {len(plan[t])}")
            ok = True
            for t in ORDER:
                after = await fingerprint(conn, t, [])
                remain = await conn.fetchval(f"SELECT count(*) FROM {t} WHERE id = ANY($1::uuid[])", plan[t])
                same = after == before[t] and after[0] == totals[t] - len(plan[t]) and remain == 0
                ok &= same
                print(f"  [{'PASS' if same else 'FAIL'}] {t}: {totals[t]} → {after[0]} rows, "
                      f"{remain} MUSE rows left, other rows' checksum {'unchanged' if after[1] == before[t][1] else 'CHANGED'}")
            if not ok:
                print("[FAIL] a table changed beyond the MUSE rows — rolled back, nothing deleted")
                await tr.rollback()
                return 1
            if apply:
                await tr.commit()
                print("COMMITTED — the MUSE rows are removed; every other row unchanged.")
            else:
                await tr.rollback()
                print("DRY RUN — rolled back. Re-run with --apply to delete.")
            return 0
        except BaseException:
            await tr.rollback()
            raise
    finally:
        await conn.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
