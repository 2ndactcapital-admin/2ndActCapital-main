"""relationship_status_cleanup — UDFUI1 operator script: fold relationship status 'active' into 'client'.

Joe's decision (2026-11-13): an entity whose status is 'active' becomes 'client'; 'contact' stays its own status and
is never touched. Run by the OPERATOR, never by a sprint against real rows:

    doppler run -- apps/api/venv/bin/python apps/api/scripts/relationship_status_cleanup.py --dry-run
    doppler run -- apps/api/venv/bin/python apps/api/scripts/relationship_status_cleanup.py

  --dry-run      list every live entity carrying 'active' (org, id, name, type); change nothing
  (no flag)      the real run: each one moves to 'client'
  --org <uuid>   restrict to one org (repeatable). The verify uses this to touch its fixture org only.

HOW EACH ROW CHANGES — the app's own entity write path, never a bare UPDATE:
  services.entity_writes.update_entity_row (Rule 3, SYSTEM axis: the prior version is archived as a new row with
  system_to = now(), the live row keeps its id, so ~90 FKs stay valid) + services.audit.write_audit_log
  (action 'status_cleanup', old/new status), in ONE transaction per entity, under THAT entity's org RLS context
  (app_service is not a bypass role). The row is re-read FOR UPDATE inside the transaction and skipped unless it
  still says 'active' — a re-run is a no-op.

The scan across orgs runs under services.database.platform_scope (SET LOCAL app.is_super_admin, one transaction).

AFTERWARDS the script reports how many live rows still carry 'active'. 'active' is already NOT OFFERED on any picker
(services.crm_config.RETIRED_STATUSES); validation keeps ACCEPTING it until a later sprint removes it — do that only
once this report says 0.
"""

from __future__ import annotations

import argparse
import asyncio
import pathlib
import sys
from uuid import UUID

HERE = pathlib.Path(__file__).resolve().parent
API_DIR = HERE.parent
for _site in sorted(API_DIR.glob("venv/lib/python3*/site-packages")):
    sys.path.insert(0, str(_site))
sys.path.insert(0, str(API_DIR))

FROM_STATUS = "active"
TO_STATUS = "client"
AUDIT_ACTION = "status_cleanup"

_SCAN = """
SELECT id, org_id, display_name, entity_type::text AS entity_type
FROM entities
WHERE status = $1 AND valid_to IS NULL AND system_to IS NULL
  AND ($2::uuid[] IS NULL OR org_id = ANY($2::uuid[]))
ORDER BY org_id, display_name, id
"""


async def scan(pool, org_ids: list[UUID] | None) -> list[dict]:
    from services.database import platform_scope

    async with pool.acquire() as conn:
        async with platform_scope(conn):
            rows = await conn.fetch(_SCAN, FROM_STATUS, org_ids)
    return [dict(r) for r in rows]


async def move_one(pool, row: dict) -> dict | None:
    """One entity, one transaction, its own org's RLS context. Returns the updated live row or None (skipped)."""
    from services.audit import write_audit_log
    from services.database import reset_rls_context, set_rls_context
    from services.entity_writes import update_entity_row

    tokens = set_rls_context(row["org_id"], False)
    try:
        async with pool.acquire() as conn:
            async with conn.transaction():
                current = await conn.fetchrow(
                    "SELECT id, status FROM entities WHERE id = $1 AND org_id = $2 "
                    "AND valid_to IS NULL AND system_to IS NULL FOR UPDATE",
                    row["id"], row["org_id"])
                if current is None or current["status"] != FROM_STATUS:
                    return None
                updated = await update_entity_row(conn, row["org_id"], row["id"], {"status": TO_STATUS},
                                                  returning="id, org_id, status, display_name")
                if updated is None:
                    raise RuntimeError(f"entity {row['id']}: the live row could not be updated")
                await write_audit_log(conn, org_id=row["org_id"], action=AUDIT_ACTION, table_name="entities",
                                      record_id=row["id"], old={"status": FROM_STATUS},
                                      new={"status": TO_STATUS})
                return dict(updated)
    finally:
        reset_rls_context(tokens)


async def run(*, dry_run: bool, org_ids: list[UUID] | None = None, out=print) -> dict:
    """The whole job; returns {found, moved, skipped, remaining} (the verify reads it)."""
    from services.database import get_pool

    pool = await get_pool()
    found = await scan(pool, org_ids)
    out(f"{'DRY RUN — ' if dry_run else ''}{len(found)} live entit{'y' if len(found) == 1 else 'ies'} "
        f"with status '{FROM_STATUS}'{' (orgs: ' + ', '.join(map(str, org_ids)) + ')' if org_ids else ''}:")
    for r in found:
        out(f"  org {r['org_id']}  entity {r['id']}  {r['entity_type']:<14}  {r['display_name']}")
    moved, skipped = [], []
    if not dry_run:
        for r in found:
            res = await move_one(pool, r)
            (moved if res else skipped).append(str(r["id"]))
            out(f"  {'moved → ' + TO_STATUS if res else 'skipped (no longer ' + FROM_STATUS + ')'}: {r['display_name']} ({r['id']})")
    remaining = await scan(pool, org_ids)
    out(f"{'Would move' if dry_run else 'Moved'} {len(found) if dry_run else len(moved)}; skipped {len(skipped)}; "
        f"'contact' untouched. Live rows still '{FROM_STATUS}': {len(remaining)}.")
    if not dry_run and not remaining:
        out(f"Nothing carries '{FROM_STATUS}' any more — a later sprint may drop it from validation "
            f"(services.crm_config.RETIRED_STATUSES).")
    return {"found": [str(r["id"]) for r in found], "moved": moved, "skipped": skipped,
            "remaining": [str(r["id"]) for r in remaining]}


def parse_args(argv):
    p = argparse.ArgumentParser(description="Fold relationship status 'active' into 'client' (operator script).")
    p.add_argument("--dry-run", action="store_true", help="list only; change nothing")
    p.add_argument("--org", action="append", default=None, type=UUID, help="restrict to this org (repeatable)")
    return p.parse_args(argv)


async def _main(argv) -> int:
    from services.database import close_pool

    args = parse_args(argv)
    try:
        await run(dry_run=args.dry_run, org_ids=args.org)
    finally:
        await close_pool()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(_main(sys.argv[1:])))
