"""backfill_taxonomy_keys — BUGFIX3 FIX 1 (c): classify the existing unkeyed assets by kind.

Why: every manual creation path (Add asset, Add private investment, manual asset, integrated import) stored NO taxonomy
key, and ``portfolio_rollup`` skips a position whose ``COALESCE(position.taxonomy_key, asset.default_taxonomy_key)`` is
NULL — the Olivo family's $0 net worth (23 of 23 positions unkeyed, 2026-11-05). New assets are now keyed at creation
(``services.portfolio_kind_taxonomy.key_for_kind``); this script applies the SAME mapping to the assets created before.

What it touches: ``portfolio.assets.default_taxonomy_key`` only, on CURRENT asset rows whose key is NULL and whose kind
(``asset_type``) maps to a key that exists in the org's own taxonomy. Through ``portfolio_securities.update_asset`` —
the system-axis correction (CLAUDE.md Rule 3: the id is KEPT, the old version archived), never a bare UPDATE.
Positions are NOT rewritten: the rollup reads the asset's key through the COALESCE, and a valid-axis restatement of
every position would change their ids. A position that carries its own key is untouched; an asset whose kind has no
mapping ('other', 'unclassified', 'equity' …) is LISTED, never guessed.

Idempotent: a second run finds nothing (the assets it keyed are no longer NULL).

Usage (the operator runs it; the sprint never ran it on real or demo data):
    doppler run -- python3 scripts/backfill_taxonomy_keys.py --org 00000000-0000-0000-0000-000000000001 --dry-run
    doppler run -- python3 scripts/backfill_taxonomy_keys.py --org 00000000-0000-0000-0000-000000000001
then Recalculate the household(s) (People & Access → household → Recalculate), or wait for the nightly rollup.
``--all-orgs`` instead of ``--org`` walks every organization. No prompts. Final line: ``CHANGED: N`` (0 on a dry run).
"""

from __future__ import annotations

import argparse
import asyncio
import pathlib
import sys
from collections import defaultdict
from uuid import UUID

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

import asyncpg  # noqa: E402

from services.portfolio_assets import _OrgWrite  # noqa: E402
from services.portfolio_kind_taxonomy import kind_map, org_taxonomy_keys  # noqa: E402
from services.portfolio_securities import update_asset  # noqa: E402

CANDIDATES_SQL = """
    SELECT a.id::text AS id, a.asset_type, a.name,
           (SELECT count(*) FROM portfolio.positions p
             WHERE p.asset_id = a.id AND p.org_id = a.org_id AND p.valid_to IS NULL AND p.system_to IS NULL
               AND p.taxonomy_key IS NULL) AS unkeyed_positions
      FROM portfolio.assets a
     WHERE a.org_id = $1::uuid AND a.default_taxonomy_key IS NULL AND a.valid_to IS NULL AND a.system_to IS NULL
     ORDER BY a.asset_type, a.name, a.id
"""


async def plan_org(conn, org_id: str) -> dict:
    """{kind: {"key", "assets": [...], "positions": n}} for one org, under its own RLS context."""
    async with _OrgWrite(conn, org_id) as c:
        rows = await c.fetch(CANDIDATES_SQL, org_id)
        mapping = await kind_map(c, org_id)
        keys = await org_taxonomy_keys(c, org_id)
    plan: dict = defaultdict(lambda: {"key": None, "assets": [], "positions": 0, "why": None})
    for r in rows:
        kind = r["asset_type"]
        p = plan[kind]
        mapped = mapping.get(kind)
        if not mapped:
            p["why"] = "no mapping for this kind (portfolio.kind_taxonomy) — left unkeyed"
        elif mapped not in keys:
            p["why"] = f"mapped key {mapped} is not in this org's asset_taxonomy — left unkeyed"
        else:
            p["key"] = mapped
        p["assets"].append(r["id"])
        p["positions"] += int(r["unkeyed_positions"])
    return dict(plan)


async def run(conn, org_ids: list[str], *, dry_run: bool) -> int:
    changed = 0
    for org in org_ids:
        plan = await plan_org(conn, org)
        print(f"ORG {org}: {sum(len(p['assets']) for p in plan.values())} unkeyed current assets")
        for kind in sorted(plan):
            p = plan[kind]
            action = (f"-> {p['key']}" if p["key"] else f"SKIP ({p['why']})")
            print(f"  KIND {kind}: assets {len(p['assets'])}, live unkeyed positions {p['positions']} {action}")
        if dry_run:
            continue
        for kind in sorted(plan):
            p = plan[kind]
            if not p["key"]:
                continue
            for aid in p["assets"]:
                await update_asset(conn, org_id=org, asset_id=aid, changes={"default_taxonomy_key": p["key"]})
                changed += 1
        print(f"  SET {org}: {sum(len(p['assets']) for p in plan.values() if p['key'])} assets keyed")
    return changed


async def all_orgs(conn) -> list[str]:
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
        return [str(r["id"]) for r in await conn.fetch("SELECT id FROM organizations ORDER BY id")]


async def main_async(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    who = ap.add_mutually_exclusive_group(required=True)
    who.add_argument("--org", action="append", help="org id (repeatable)")
    who.add_argument("--all-orgs", action="store_true")
    ap.add_argument("--dry-run", action="store_true", help="print the counts per kind; write nothing")
    ap.add_argument("--dsn-env", default="DATABASE_URL", help="env var holding the DSN (default DATABASE_URL)")
    a = ap.parse_args(argv)
    sys.path.insert(0, str(HERE))
    from _db_connect import resolve_dsn  # Doppler-hydrated; tries the separate DB_PASSWORD secret too

    dsn, how = await resolve_dsn(a.dsn_env)
    if not dsn:
        print(f"no working {a.dsn_env}: {how} — run through Doppler: doppler run -- python3 "
              f"{pathlib.Path(__file__).name} ...")
        return 2
    print(f"DATABASE: {how}")
    conn = await asyncpg.connect(dsn, statement_cache_size=0)
    try:
        orgs = await all_orgs(conn) if a.all_orgs else [str(UUID(o)) for o in a.org]
        print(f"MODE: {'dry-run' if a.dry_run else 'write'}; orgs {len(orgs)}")
        changed = await run(conn, orgs, dry_run=a.dry_run)
    finally:
        await conn.close()
    print(f"CHANGED: {changed}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main_async()))
