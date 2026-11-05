"""PERMS1 Task 5 — remove exactly the HollisMuse re-run test rows, by id. One-off.

    doppler-hydrated, no prompts:
    python3 apps/api/scripts/perms1_remove_muse_rows.py            # DRY RUN: print the rows, roll back
    python3 apps/api/scripts/perms1_remove_muse_rows.py --apply    # delete, verify, commit

WHAT (nothing else):
  * workflow "MUSE – workflow" v1  workflow_definitions c76c0067-dc07-415b-93fb-ad55e81357b5 (draft, no runs,
    no triggers — asserted), its workflow_versions and workflow_steps;
  * the orphaned Nov 3 "In progress" MUSE-model.csv upload portfolio.model_uploads 9e593a8f-… (its model and
    draft_created event were removed by bugfix2's clean-up — REP-10) and the five "Duplicate" MUSE-model.csv
    entries of 2026-11-04 07:23 UTC that point at it (duplicate_of). They carry no events and no versions
    (asserted).
The upload log is append-only ("Nothing is ever removed"): its immutability trigger is NOT weakened. These are
test rows, removed through the documented privileged path only — the operator (postgres) connection with
``models.teardown = 'fixture'`` set LOCAL (portfolio.model_teardown_allowed), by id. The audit_log rows that
recorded the uploads are the record and are kept. The stored R2 object is not touched.

Every touched table: count + md5 over every row NOT in the delete set before, every remaining row after — they
must match and zero listed rows may remain, or the transaction rolls back. No secret is printed.
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
MUSE_WORKFLOW = UUID("c76c0067-dc07-415b-93fb-ad55e81357b5")
MUSE_WORKFLOW_NAME = "MUSE – workflow"
ORPHAN_UPLOAD = UUID("9e593a8f-7025-490d-8d5f-385a46ff3a01")
DUPLICATE_UPLOADS = [UUID(x) for x in (
    "da16bd09-e478-436e-b9d3-800326ff7c42", "c3228f25-7dbe-4bc4-adc3-a97ee56dbfad",
    "dc28778f-5461-4435-b74c-1e29d7feb83e", "0d948645-7cbd-4dd2-b22a-119fe534bc3b",
    "c9e9bb16-ff8e-40f8-8538-3d7be4450fe8")]
UPLOAD_FILE = "MUSE-model.csv"
OPERATOR_DSN_VAR = "DATABASE_URL_PRECUTOVER_POSTGRES_BACKUP"


async def resolve(conn) -> dict:
    """{table: [ids]} in delete order (duplicates before the receipt they point at; steps → versions → def)."""
    versions = [r["id"] for r in await conn.fetch(
        "SELECT id FROM workflow_versions WHERE workflow_definition_id = $1 ORDER BY id", MUSE_WORKFLOW)]
    steps = [r["id"] for r in await conn.fetch(
        "SELECT id FROM workflow_steps WHERE workflow_version_id = ANY($1::uuid[]) ORDER BY id", versions)]
    return {
        "dup_uploads": sorted(DUPLICATE_UPLOADS),
        "orphan_upload": [ORPHAN_UPLOAD],
        "public.workflow_steps": steps,
        "public.workflow_versions": versions,
        "public.workflow_definitions": [MUSE_WORKFLOW],
    }


async def guards(conn, plan) -> list[str]:
    bad = []
    d = await conn.fetchrow("SELECT org_id, name FROM workflow_definitions WHERE id = $1", MUSE_WORKFLOW)
    if d is None or d["org_id"] != DEFAULT_ORG or d["name"] != MUSE_WORKFLOW_NAME:
        bad.append(f"workflow {MUSE_WORKFLOW} is not the default org's {MUSE_WORKFLOW_NAME!r}: {dict(d) if d else None}")
    runs = await conn.fetchval("SELECT count(*) FROM workflow_runs WHERE workflow_version_id = ANY($1::uuid[])",
                               plan["public.workflow_versions"])
    trig = await conn.fetchval("SELECT count(*) FROM workflow_triggers WHERE workflow_definition_id = $1",
                               MUSE_WORKFLOW)
    if runs or trig:
        bad.append(f"the workflow has {runs} run(s) / {trig} trigger(s) — refused")
    o = await conn.fetchrow("SELECT org_id, file_name, outcome FROM portfolio.model_uploads WHERE id = $1",
                            ORPHAN_UPLOAD)
    if o is None or o["org_id"] != DEFAULT_ORG or o["file_name"] != UPLOAD_FILE or o["outcome"] != "received":
        bad.append(f"upload {ORPHAN_UPLOAD} is not the default org's received {UPLOAD_FILE}: {dict(o) if o else None}")
    dups = await conn.fetch("SELECT id, org_id, file_name, outcome, duplicate_of, uploaded_at FROM "
                            "portfolio.model_uploads WHERE id = ANY($1::uuid[])", DUPLICATE_UPLOADS)
    for r in dups:
        if (r["org_id"] != DEFAULT_ORG or r["file_name"] != UPLOAD_FILE or r["outcome"] != "duplicate"
                or r["duplicate_of"] != ORPHAN_UPLOAD or r["uploaded_at"].strftime("%Y-%m-%d %H:%M") !=
                f"2026-11-04 07:{r['uploaded_at'].strftime('%M')}" or r["uploaded_at"].hour != 7):
            bad.append(f"upload {r['id']} is not a 2026-11-04 07:xx UTC duplicate of {ORPHAN_UPLOAD}")
    if len(dups) != 5:
        bad.append(f"expected the 5 duplicate rows, found {len(dups)}")
    ids = [ORPHAN_UPLOAD, *DUPLICATE_UPLOADS]
    ev = await conn.fetchval("SELECT count(*) FROM portfolio.model_upload_events WHERE upload_id = ANY($1::uuid[])",
                             ids)
    ver = await conn.fetchval("SELECT count(*) FROM portfolio.model_versions WHERE upload_id = ANY($1::uuid[])", ids)
    other_dups = await conn.fetchval("SELECT count(*) FROM portfolio.model_uploads WHERE duplicate_of = $1 AND "
                                     "NOT (id = ANY($2::uuid[]))", ORPHAN_UPLOAD, DUPLICATE_UPLOADS)
    if ev or ver or other_dups:
        bad.append(f"the uploads carry {ev} event(s) / {ver} version(s) / {other_dups} other duplicate(s) — refused")
    bad += await demo_refusals(conn, plan)   # CRMDETAIL1 — zero demo rows touched
    return bad


async def fingerprint(conn, table: str, exclude: list[UUID]) -> tuple[int, str]:
    row = await conn.fetchrow(
        f"SELECT count(*) AS n, md5(COALESCE(string_agg(to_jsonb(t)::text, '|' ORDER BY id), '')) AS h "
        f"FROM {table} t WHERE NOT (id = ANY($1::uuid[]))", exclude)
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
            print("PERMS1 Task 5 — HollisMuse re-run rows to delete:")
            for k, ids in plan.items():
                print(f"  {k:30s} {len(ids):2d}  {', '.join(str(i) for i in ids)}")
            bad = await guards(conn, plan)
            if bad:
                for b in bad:
                    print(f"[FAIL] {b}")
                await tr.rollback()
                return 1
            uploads = [*plan["dup_uploads"], *plan["orphan_upload"]]
            tables = {"portfolio.model_uploads": uploads,
                      "public.workflow_steps": plan["public.workflow_steps"],
                      "public.workflow_versions": plan["public.workflow_versions"],
                      "public.workflow_definitions": plan["public.workflow_definitions"]}
            before = {t: await fingerprint(conn, t, ids) for t, ids in tables.items()}
            totals = {t: await conn.fetchval(f"SELECT count(*) FROM {t}") for t in tables}
            for key in ("dup_uploads", "orphan_upload"):
                n = await conn.fetchval("WITH d AS (DELETE FROM portfolio.model_uploads WHERE id = ANY($1::uuid[]) "
                                        "RETURNING 1) SELECT count(*) FROM d", plan[key])
                if n != len(plan[key]):
                    raise RuntimeError(f"model_uploads ({key}): deleted {n} of {len(plan[key])}")
            for t in ("public.workflow_steps", "public.workflow_versions", "public.workflow_definitions"):
                n = await conn.fetchval(f"WITH d AS (DELETE FROM {t} WHERE id = ANY($1::uuid[]) RETURNING 1) "
                                        "SELECT count(*) FROM d", tables[t])
                if n != len(tables[t]):
                    raise RuntimeError(f"{t}: deleted {n} of {len(tables[t])}")
            ok = True
            for t, ids in tables.items():
                after = await fingerprint(conn, t, [])
                remain = await conn.fetchval(f"SELECT count(*) FROM {t} WHERE id = ANY($1::uuid[])", ids)
                same = after == before[t] and after[0] == totals[t] - len(ids) and remain == 0
                ok &= same
                print(f"  [{'PASS' if same else 'FAIL'}] {t}: {totals[t]} → {after[0]} rows, {remain} listed rows "
                      f"left, other rows' checksum {'unchanged' if after[1] == before[t][1] else 'CHANGED'}")
            if not ok:
                print("[FAIL] a table changed beyond the listed rows — rolled back, nothing deleted")
                await tr.rollback()
                return 1
            if apply:
                await tr.commit()
                print("COMMITTED — the listed rows are removed; every other row unchanged.")
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
