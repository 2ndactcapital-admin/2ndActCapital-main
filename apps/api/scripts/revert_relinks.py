"""revert_relinks — VISIBILITY1 FIX 1 (H): undo master relinks of manual assets that must NEVER resolve through the
security master.

WHY. ``services.portfolio_manual.relink_manual_assets`` (run by the nightly portfolio refresh, trigger 'nightly')
used to skip only asset_type 'cash'. At 2026-11-07 07:00:56 UTC it relinked the Olivo demo's DIGITAL ASSETS by
ticker — "Bitcoin" (BTC) → "Grayscale Bitcoin Mini Trust (BTC)" and "Ether" (ETH) → "Grayscale Ethereum Staking Mini
ETF" — exactly what BUGFIX4 forbade ("cash / digital assets never resolve through the master"). The relink now skips
every kind in ``services.held_away.NO_MASTER_ASSET_TYPES``; this script reverts the relinks already made.

WHAT IT DOES. Lists every ``portfolio.asset_relinks`` row of ``--org`` whose asset's CURRENT asset_type is in
``NO_MASTER_ASSET_TYPES`` (asset, from / to security, when, trigger, and whether the asset is still linked to that
security). The real run unlinks each still-linked asset as a NEW SYSTEM VERSION (Rule 3, system axis — positions and
valuations point at the asset's id, so the id is KEPT): the prior version is archived with ``system_to = now()``, the
live row's ``global_security_id`` is set back to NULL with ``system_from = now()``; the relink row is closed
(``valid_to = now()``) so it no longer shows as a live relink; one ``manual_asset_relink_reverted`` audit row per asset.

IDEMPOTENT. An asset no longer linked to the relink's security is skipped — a second run prints ``CHANGED: 0``.

Usage (the operator runs it; the sprint ran ONLY --dry-run on the live org):
    doppler run -- python3 apps/api/scripts/revert_relinks.py --org 00000000-0000-0000-0000-000000000001 --dry-run
    doppler run -- python3 apps/api/scripts/revert_relinks.py --org 00000000-0000-0000-0000-000000000001
then Recalculate the affected household(s). No prompts. Final line: ``CHANGED: N`` (assets unlinked; 0 on a dry run).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import pathlib
import sys
from uuid import UUID

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
for _site in sorted(HERE.parent.glob("venv/lib/python3*/site-packages")):
    sys.path.insert(0, str(_site))

import asyncpg  # noqa: E402

from services.held_away import NO_MASTER_ASSET_TYPES  # noqa: E402
from services.portfolio_assets import _OrgWrite  # noqa: E402

AUDIT_ACTION = "manual_asset_relink_reverted"

#: every relink row of the org whose asset's live asset_type never resolves through the master
CANDIDATES_SQL = """
    SELECT r.id::text AS relink_id, r.asset_id::text AS asset_id, r.asset_name, r.global_security_id::text AS to_gsid,
           r.security_name, r.id_type, r.id_value, r.trigger, r.created_at, (r.valid_to IS NULL AND r.system_to IS NULL)
           AS relink_live, a.asset_type, a.global_security_id::text AS current_gsid
      FROM portfolio.asset_relinks r
      JOIN portfolio.assets a ON a.id = r.asset_id AND a.org_id = r.org_id AND a.valid_to IS NULL AND a.system_to IS NULL
     WHERE r.org_id = $1::uuid AND a.asset_type = ANY($2::text[])
     ORDER BY r.created_at, r.id
"""


async def plan(conn, org_id: str) -> list[dict]:
    async with _OrgWrite(conn, org_id) as c:
        rows = [dict(r) for r in await c.fetch(CANDIDATES_SQL, org_id, sorted(NO_MASTER_ASSET_TYPES))]
    for r in rows:
        # the asset is linked by THIS relink when its live row still points at the relink's security
        r["to_revert"] = bool(r["relink_live"] and r["current_gsid"] == r["to_gsid"])
        r["from_gsid"] = None   # a manual asset is relinked only from NULL (relink_manual_assets' WHERE)
    return rows


async def apply(conn, org_id: str, rows: list[dict], *, actor_id: str | None = None) -> int:
    changed = 0
    for r in rows:
        if not r["to_revert"]:
            continue
        async with _OrgWrite(conn, org_id) as c:     # one transaction per asset (archive + unlink + close + audit)
            live = await c.fetchrow(
                "SELECT * FROM portfolio.assets WHERE id = $1::uuid AND org_id = $2::uuid AND global_security_id = "
                "$3::uuid AND valid_to IS NULL AND system_to IS NULL FOR UPDATE", r["asset_id"], org_id, r["to_gsid"])
            if live is None:                           # unlinked meanwhile — idempotent
                continue
            cols = [k for k in live.keys() if k != "id"]
            await c.execute(
                f"INSERT INTO portfolio.assets ({', '.join(cols)}) SELECT "
                + ", ".join("now()" if k == "system_to" else k for k in cols)
                + " FROM portfolio.assets WHERE id = $1::uuid", r["asset_id"])
            tag = await c.execute(
                "UPDATE portfolio.assets SET global_security_id = NULL, system_from = now() WHERE id = $1::uuid "
                "AND org_id = $2::uuid AND global_security_id = $3::uuid", r["asset_id"], org_id, r["to_gsid"])
            if tag != "UPDATE 1":
                # zero rows on a row just read FOR UPDATE: an RLS UPDATE policy / context problem, not "no row"
                raise RuntimeError(f"unlink of asset {r['asset_id']} matched {tag!r} after reading the row — check "
                                   "pg_policies UPDATE on portfolio.assets and the org context")
            tag = await c.execute(
                "UPDATE portfolio.asset_relinks SET valid_to = now() WHERE id = $1::uuid AND org_id = $2::uuid "
                "AND valid_to IS NULL AND system_to IS NULL", r["relink_id"], org_id)
            if tag != "UPDATE 1":
                raise RuntimeError(f"closing relink {r['relink_id']} matched {tag!r} — check pg_policies UPDATE on "
                                   "portfolio.asset_relinks and the org context")
            await c.execute(
                "INSERT INTO audit_log (org_id, user_id, action, resource_type, resource_id, payload) "
                "VALUES ($1::uuid, $2::uuid, $3, 'portfolio_asset', $4::uuid, $5::jsonb)",
                org_id, actor_id, AUDIT_ACTION, r["asset_id"],
                json.dumps({"relink_id": r["relink_id"], "asset_name": r["asset_name"], "asset_type": r["asset_type"],
                            "unlinked_from": r["to_gsid"], "security_name": r["security_name"],
                            "relink_trigger": r["trigger"], "relinked_at": r["created_at"].isoformat(),
                            "reason": "kind never resolves through the master (BUGFIX4 / VISIBILITY1 FIX 1)"}))
        changed += 1
    return changed


def print_plan(org_id: str, rows: list[dict]) -> None:
    print(f"ORG: {org_id}")
    print(f"KINDS NEVER RESOLVED THROUGH THE MASTER: {', '.join(sorted(NO_MASTER_ASSET_TYPES))}")
    print(f"RELINKS FOUND: {len(rows)} (to revert: {sum(1 for r in rows if r['to_revert'])})")
    for r in rows:
        state = "LINKED → revert" if r["to_revert"] else "already unlinked / relink closed — skip"
        print(f"  - asset {r['asset_id']} \"{r['asset_name']}\" [{r['asset_type']}] by {r['id_type']} "
              f"{r['id_value']}: from {r['from_gsid'] or 'none (manual)'} → {r['to_gsid']} \"{r['security_name']}\" "
              f"at {r['created_at'].isoformat()} trigger {r['trigger']} — {state}")


async def main_async(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--org", required=True, help="org id")
    ap.add_argument("--dry-run", action="store_true", help="list the relinks; write nothing")
    ap.add_argument("--dsn-env", default="DATABASE_URL")
    a = ap.parse_args(argv)
    org = str(UUID(a.org))
    from _db_connect import resolve_dsn

    dsn, how = await resolve_dsn(a.dsn_env)
    if not dsn:
        print(f"no working {a.dsn_env}: {how} — run through Doppler")
        return 2
    print(f"DATABASE: {how}")
    print(f"MODE: {'dry-run' if a.dry_run else 'write'}")
    conn = await asyncpg.connect(dsn, statement_cache_size=0)
    try:
        rows = await plan(conn, org)
        print_plan(org, rows)
        changed = 0 if a.dry_run else await apply(conn, org, rows)
    finally:
        await conn.close()
    print(f"CHANGED: {changed}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main_async()))
