"""repair_ownership_duplicates — ROLLUP2 Task 4 (operator): archive the older of two (or more) live ownership entries
for the same owner / owned entity, so net worth stops adding them together.

For each pair it prints both (all) entries — table, percentage, valid-from, which one is NEWER — and the proposed
SURVIVOR (the newest; services.ownership_duplicates.group). The real run archives every other entry the way the
Ownership tab's edit archives a version (valid_to = system_to = now()), writes ownership_change_log + audit_log, in
one transaction. Idempotent: a second run finds no pair (``CHANGED: 0``).

Live Olivo on 2026-11-06: no duplicate pair exists (docs/ROLLUP2_DISCOVERY.md 1d) — the dry run says so.

Usage (the operator runs the real pass; the sprint ran only --dry-run):
    doppler run -- python3 scripts/repair_ownership_duplicates.py --org 00000000-0000-0000-0000-000000000001 --dry-run
    doppler run -- python3 scripts/repair_ownership_duplicates.py --org 00000000-0000-0000-0000-000000000001
No prompts. Final line ``CHANGED: N`` (0 on a dry run).
"""

from __future__ import annotations

import argparse
import asyncio
import pathlib
import sys
from uuid import UUID

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

import asyncpg  # noqa: E402

from services import ownership_duplicates as OD  # noqa: E402


async def scan(conn, org: str) -> list[dict]:
    async with conn.transaction(readonly=True):
        await conn.execute("SELECT set_config('app.current_org_id', $1, true), set_config('app.is_super_admin', "
                           "'false', true)", org)
        return OD.repair_plan(await OD.find(conn, org))


def show(plan: list[dict]) -> None:
    if not plan:
        print("  no owner / owned pair has more than one live ownership entry")
    for p in plan:
        print(f"  PAIR {p.get('owner_name') or p['parent_id']} -> {p.get('owned_name') or p['child_id']}: "
              f"{len(p['entries'])} live entries, survivor {p['survivor']}")
        for e in p["entries"]:
            print(f"    {'NEWER ' if e['newer'] else 'older '}{e['id']} {e['source']} {e['ownership_pct']} % "
                  f"from {e['valid_from']}{'  <- keep' if e['id'] == p['survivor'] else '  <- archive'}")


async def run(conn, org: str, *, dry_run: bool, actor_id=None) -> int:
    plan = await scan(conn, org)
    print(f"ORG {org}: {len(plan)} duplicate pair(s)")
    show(plan)
    if dry_run or not plan:
        return 0
    n = await OD.apply_repair(conn, org, plan, actor_id=actor_id)
    print(f"  ARCHIVED {n} entr{'y' if n == 1 else 'ies'}")
    return n


async def main_async(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--org", required=True)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--dsn-env", default="DATABASE_URL")
    a = ap.parse_args(argv)
    org = str(UUID(a.org))
    from _db_connect import resolve_dsn

    dsn, how = await resolve_dsn(a.dsn_env)
    if not dsn:
        print(f"no working {a.dsn_env}: {how} — run through Doppler")
        return 2
    print(f"DATABASE: {how}\nMODE: {'dry-run' if a.dry_run else 'write'}")
    conn = await asyncpg.connect(dsn, statement_cache_size=0)
    try:
        changed = await run(conn, org, dry_run=a.dry_run)
    finally:
        await conn.close()
    print(f"CHANGED: {changed}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main_async()))
