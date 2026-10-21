"""BUGFIX1 — the data repairs Task 1 proved are needed. Dry run by default.

    python3 apps/api/scripts/_bugfix1_repairs.py            # report only
    python3 apps/api/scripts/_bugfix1_repairs.py --apply    # repair, then re-read

1. DEAL LIFECYCLE MIRROR (MKT-01 / DASH-01). Every live deal's `deal_stage`
   must hold the deal_stages config key of its `deal_status` (the ONE
   lifecycle, services/deal_lifecycle). Live on 2026-10-21 none did: six held
   'sourced', two 'active', one 'under_review' — free text, never a config
   key. Each mismatched deal is restated on the SYSTEM axis (deals has live
   FKs, so its id stays): the prior version is copied with system_to = now()
   by deal_lifecycle.archive_version, then deal_stage is set. deal_status is
   never touched. One audit_log row per deal (action 'lifecycle_mirror').

2. CRM-04 TAX-ID DUPLICATE. James Hargrove (10000000-…-0003) had two CURRENT
   entity_tax_ids rows with the same type (ssn), country (US) and stored value
   (identical tax_id_encrypted) — a true duplicate, not a display bug. Every
   such group keeps its EARLIEST row; the others are closed on the valid axis
   (valid_to = now()), never deleted.

TEST-Submit Test is NOT restored here: that goes through the new Un-archive
endpoint (POST /deals/{id}/unarchive) so the real code path is what restores
it — verify_bugfix1.py does that and re-reads the row.

Prints every row before and after. DATABASE_URL connects as app_service
(RLS enforced): every read and write runs inside services.database.
platform_scope (SET LOCAL app.is_super_admin) — without it the plan reads ZERO
rows and silently reports "nothing to do". Each repair is one transaction and is idempotent (a second --apply
finds nothing to do).
"""
from __future__ import annotations

import asyncio
import json
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from _db_connect import admin_dsn, connect  # noqa: E402

from services import deal_lifecycle as dlc  # noqa: E402
from services.database import platform_scope  # noqa: E402


async def deal_mirror_plan(conn) -> list[dict]:
    async with platform_scope(conn):
        return await _deal_mirror_plan(conn)


async def _deal_mirror_plan(conn) -> list[dict]:
    rows = await conn.fetch(
        "SELECT id, org_id, name, deal_status::text AS status, deal_stage FROM deals "
        "WHERE valid_to IS NULL AND system_to IS NULL ORDER BY org_id, name, id")
    out = []
    stages_by_org: dict = {}
    for r in rows:
        if r["org_id"] not in stages_by_org:
            stages_by_org[r["org_id"]] = await dlc.lifecycle(conn, r["org_id"])
        want = dlc.key_for(stages_by_org[r["org_id"]], r["status"]) or r["status"]
        if r["deal_stage"] != want:
            out.append({"deal_id": str(r["id"]), "org_id": str(r["org_id"]), "name": r["name"],
                        "deal_status": r["status"], "deal_stage_before": r["deal_stage"],
                        "deal_stage_after": want})
    return out


async def apply_deal_mirror(conn, plan: list[dict]) -> list[dict]:
    done = []
    async with platform_scope(conn):
        for p in plan:
            version_id = await dlc.archive_version(conn, p["org_id"], p["deal_id"])
            n = await conn.execute(
                "UPDATE deals SET deal_stage = $3, system_from = now(), updated_at = now() "
                "WHERE id = $1 AND org_id = $2 AND valid_to IS NULL AND system_to IS NULL",
                p["deal_id"], p["org_id"], p["deal_stage_after"])
            if n != "UPDATE 1":
                raise RuntimeError(f"deal {p['deal_id']}: expected 1 row, got {n}")
            await conn.execute(
                "INSERT INTO audit_log (org_id, action, resource_type, resource_id, payload) "
                "VALUES ($1, 'lifecycle_mirror', 'deals', $2, $3::jsonb)",
                p["org_id"], p["deal_id"],
                json.dumps({"deal_stage": p["deal_stage_after"], "from": p["deal_stage_before"],
                            "version_id": str(version_id), "sprint": "bugfix1"}))
            done.append({**p, "version_id": str(version_id)})
    return done


async def tax_dup_plan(conn) -> list[dict]:
    async with platform_scope(conn):
        return await _tax_dup_plan(conn)


async def _tax_dup_plan(conn) -> list[dict]:
    rows = await conn.fetch(
        """
        SELECT id, org_id, entity_id, tax_id_type::text AS t, tax_id_country AS c, tax_id_last4,
               created_at,
               row_number() OVER (PARTITION BY entity_id, tax_id_type, tax_id_country, tax_id_encrypted
                                  ORDER BY created_at, id) AS rn
        FROM entity_tax_ids
        WHERE valid_to IS NULL AND system_to IS NULL
        """)
    return [{"id": str(r["id"]), "org_id": str(r["org_id"]), "entity_id": str(r["entity_id"]),
             "type": r["t"], "country": r["c"], "last4": r["tax_id_last4"],
             "created_at": r["created_at"].isoformat()} for r in rows if r["rn"] > 1]


async def apply_tax_dups(conn, plan: list[dict]) -> int:
    async with platform_scope(conn):
        for p in plan:
            n = await conn.execute(
                "UPDATE entity_tax_ids SET valid_to = now() WHERE id = $1 AND valid_to IS NULL "
                "AND system_to IS NULL", p["id"])
            if n != "UPDATE 1":
                raise RuntimeError(f"tax id {p['id']}: expected 1 row, got {n}")
            await conn.execute(
                "INSERT INTO audit_log (org_id, action, resource_type, resource_id, payload) "
                "VALUES ($1, 'duplicate_closed', 'entity_tax_ids', $2, $3::jsonb)",
                p["org_id"], p["id"], json.dumps({"entity_id": p["entity_id"], "sprint": "bugfix1"}))
    return len(plan)


async def main(apply: bool) -> int:
    dsn, how = await admin_dsn()
    if not dsn:
        print(f"[BLOCKED] no working DATABASE_URL ({how})")
        return 2
    conn = await connect(dsn)
    try:
        deals = await deal_mirror_plan(conn)
        taxes = await tax_dup_plan(conn)
        async with platform_scope(conn):
            seen = await conn.fetchval("SELECT count(*) FROM deals WHERE system_to IS NULL")
        if not seen:
            print("[BLOCKED] zero live deals visible — RLS context not applied; refusing to report 'nothing to do'")
            return 2
        print(f"deal lifecycle mirror: {len(deals)} deal(s) to restate")
        for d in deals:
            print(f"  {d['name']} ({d['deal_id']}): status {d['deal_status']}; "
                  f"deal_stage {d['deal_stage_before']!r} -> {d['deal_stage_after']!r}")
        print(f"tax-id duplicates: {len(taxes)} row(s) to close")
        for t in taxes:
            print(f"  {t['id']} entity {t['entity_id']} {t['type']}/{t['country']} ****{t['last4']}")
        if not apply:
            print("dry run — nothing changed (pass --apply)")
            return 0
        await apply_deal_mirror(conn, deals)
        await apply_tax_dups(conn, taxes)
        left_d, left_t = await deal_mirror_plan(conn), await tax_dup_plan(conn)
        print(f"after: {len(left_d)} deal(s) still mismatched, {len(left_t)} duplicate(s) still current")
        return 0 if not left_d and not left_t else 1
    finally:
        await conn.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(main("--apply" in sys.argv)))
