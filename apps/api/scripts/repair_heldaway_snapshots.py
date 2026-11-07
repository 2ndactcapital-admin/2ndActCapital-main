"""repair_heldaway_snapshots — BUGFIX4 (MUSE-OLIVO-O-04-02): put back the held-away lines that per-line drafts
superseded.

WHY. A held-away import is an account SNAPSHOT: approving one closes every live line of the account
(services/held_away._apply_import). The entry form used to save ONE line per draft, so an account entered line by
line kept only the LAST approved line — Olivo: 56 approved lines, 24 live, 32 superseded across 13 accounts, every
replacement at the SAME account and the SAME as-of.

WHAT IT DOES. For each account of the org whose live as-of is D and which has APPROVED lines at D that were
superseded by ANOTHER approved import of the same account at the same D (``held_away_records.superseded_reason =
'replaced by held-away import <id> …'``), it re-applies the UNION of the account's approved lines at D as one
snapshot: every superseded line is restated as a NEW live position (valid axis — the closed row is kept, never
edited), with its open lots, and a NEW provenance row pointing at the import the line came from (so the line reads
live again on the import). A security that appears in more than one import at D keeps the most recently approved
line (it is live already) and is LISTED as a collision — never summed, never guessed.

Only the per-line era's shape is repaired: the superseded import is a MANUAL draft of exactly ONE position line,
replaced by another MANUAL import. A deliberate full re-entry (a multi-line snapshot replaced by a newer one at the
same date) is a real replacement and is left alone.

Never touches: an account whose lines were superseded by a LATER as-of (that is a real newer statement), a rejected
or draft import, an account that is not held-away, anything outside ``--org``.

IDEMPOTENT. A line already restored (a live provenance row of the same import on a live position of the same asset)
is skipped, so a second run changes nothing (``CHANGED: 0``).

AUDITED. One ``held_away.snapshot_repaired`` audit_log row per account repaired (lines, totals, import ids).

Usage (the operator runs it; the sprint never ran it on real or demo data):
    doppler run -- python3 apps/api/scripts/repair_heldaway_snapshots.py --org 00000000-0000-0000-0000-000000000001 --dry-run
    doppler run -- python3 apps/api/scripts/repair_heldaway_snapshots.py --org 00000000-0000-0000-0000-000000000001
then Recalculate the household(s). ``--account`` (repeatable) limits it to named accounts. No prompts.
Final line: ``CHANGED: N`` (lines restored; 0 on a dry run).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import pathlib
import sys
from decimal import Decimal
from uuid import UUID

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
for _site in sorted(HERE.parent.glob("venv/lib/python3*/site-packages")):
    sys.path.insert(0, str(_site))

import asyncpg  # noqa: E402

from services.portfolio_assets import _OrgWrite  # noqa: E402

REASON_PREFIX = "replaced by held-away import "

#: superseded APPROVED position lines whose replacing import is an APPROVED import of the SAME account at the SAME
#: as-of, on accounts whose live as-of IS that date. One row per superseded line.
CANDIDATES_SQL = """
    WITH live_asof AS (
        SELECT p.account_id, max(p.as_of_date) AS d FROM portfolio.positions p
         WHERE p.org_id = $1::uuid AND p.account_id IS NOT NULL AND p.valid_to IS NULL AND p.system_to IS NULL
         GROUP BY p.account_id)
    SELECT h.id AS record_row_id, h.account_id, h.source_id, h.import_id, h.as_of, h.record_id AS old_position_id,
           h.superseded_reason, i.decided_at, p.asset_id, p.owner_entity_id, p.quantity, p.cost_basis, p.market_value,
           p.authority, p.source_system, p.taxonomy_key, p.ownership_basis, p.valid_to AS closed_at,
           r.id AS by_import_id
      FROM public.held_away_records h
      JOIN public.held_away_imports i ON i.id = h.import_id AND i.system_to IS NULL AND i.status = 'approved'
                                     AND i.kind = 'manual'
                                     AND jsonb_array_length(COALESCE(i.payload->'positions', '[]'::jsonb)) = 1
      JOIN public.accounts a ON a.id = h.account_id AND a.org_id = h.org_id AND a.is_held_away
                            AND a.valid_to IS NULL AND a.system_to IS NULL
      JOIN portfolio.positions p ON p.id = h.record_id AND p.system_to IS NULL AND p.valid_to IS NOT NULL
      JOIN public.held_away_imports r ON r.id::text = substring(h.superseded_reason from
                                          'replaced by held-away import ([0-9a-f-]{36})')
                                     AND r.system_to IS NULL AND r.status = 'approved' AND r.kind = 'manual'
                                     AND r.account_id = h.account_id AND r.as_of = h.as_of AND r.id <> i.id
      JOIN live_asof l ON l.account_id = h.account_id AND l.d = h.as_of
     WHERE h.org_id = $1::uuid AND h.record_type = 'position' AND h.system_to IS NULL AND h.valid_to IS NOT NULL
       AND h.superseded_reason LIKE 'replaced by held-away import %'
       AND ($2::uuid[] IS NULL OR h.account_id = ANY($2::uuid[]))
     ORDER BY h.account_id, i.decided_at, h.record_id
"""

LIVE_SQL = """
    SELECT p.id, p.asset_id, h.import_id, i.decided_at FROM portfolio.positions p
      JOIN public.held_away_records h ON h.record_id = p.id AND h.record_type = 'position' AND h.valid_to IS NULL
                                     AND h.system_to IS NULL
      JOIN public.held_away_imports i ON i.id = h.import_id AND i.system_to IS NULL
     WHERE p.org_id = $1::uuid AND p.account_id = $2::uuid AND p.valid_to IS NULL AND p.system_to IS NULL
"""


def _money(v) -> str:
    return format(Decimal(v or 0), "f")


async def plan(conn, org_id: str, account_ids: list[str] | None = None) -> dict:
    """{account_id: {"as_of", "restore": [line], "collisions": [line], "already": n, "live_lines": n}} — reads only,
    under the org's RLS context."""
    async with _OrgWrite(conn, org_id) as c:
        rows = [dict(r) for r in await c.fetch(CANDIDATES_SQL, org_id, account_ids)]
        out: dict = {}
        for acct in sorted({str(r["account_id"]) for r in rows}):
            live = [dict(x) for x in await c.fetch(LIVE_SQL, org_id, acct)]
            live_assets = {str(x["asset_id"]): x for x in live}
            restored_from = {(str(x["asset_id"]), str(x["import_id"])) for x in live}
            plan_a = {"as_of": None, "restore": [], "collisions": [], "already": 0, "live_lines": len(live)}
            seen: dict[str, dict] = {}
            # the latest-approved superseded line per asset is the candidate; an asset live now is a collision
            for r in sorted((x for x in rows if str(x["account_id"]) == acct), key=lambda x: x["decided_at"]):
                seen[str(r["asset_id"])] = r
            for asset, r in seen.items():
                plan_a["as_of"] = r["as_of"].isoformat()
                line = {"import_id": str(r["import_id"]), "old_position_id": str(r["old_position_id"]),
                        "asset_id": asset, "quantity": _money(r["quantity"]), "market_value": _money(r["market_value"]),
                        "row": r}
                if (asset, str(r["import_id"])) in restored_from:
                    plan_a["already"] += 1
                elif asset in live_assets:
                    plan_a["collisions"].append({**line, "live_position_id": str(live_assets[asset]["id"])})
                else:
                    plan_a["restore"].append(line)
            out[acct] = plan_a
    return out


async def apply(conn, org_id: str, the_plan: dict, *, actor_id: str | None = None) -> int:
    """Restore every planned line, one transaction per account. Returns lines restored."""
    changed = 0
    for acct, p in the_plan.items():
        if not p["restore"]:
            continue
        async with _OrgWrite(conn, org_id) as c:   # one transaction per account (archive + restore together)
            total = Decimal(0)
            for line in p["restore"]:
                r = line["row"]
                # idempotency re-check inside the transaction
                dup = await c.fetchval(
                    "SELECT 1 FROM portfolio.positions p JOIN public.held_away_records h ON h.record_id = p.id AND "
                    "h.record_type = 'position' AND h.valid_to IS NULL AND h.system_to IS NULL WHERE p.org_id = "
                    "$1::uuid AND p.account_id = $2::uuid AND p.asset_id = $3::uuid AND h.import_id = $4::uuid AND "
                    "p.valid_to IS NULL AND p.system_to IS NULL", org_id, acct, line["asset_id"], line["import_id"])
                if dup:
                    continue
                pid = await c.fetchval(
                    """INSERT INTO portfolio.positions (org_id, owner_entity_id, asset_id, as_of_date, ownership_basis,
                           quantity, cost_basis, market_value, authority, source_system, account_id, taxonomy_key)
                       VALUES ($1::uuid, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11::uuid, $12) RETURNING id""",
                    org_id, r["owner_entity_id"], r["asset_id"], r["as_of"], r["ownership_basis"], r["quantity"],
                    r["cost_basis"], r["market_value"], r["authority"], r["source_system"], acct, r["taxonomy_key"])
                await c.execute(
                    "INSERT INTO public.held_away_records (org_id, account_id, source_id, import_id, record_type, "
                    "record_id, as_of) VALUES ($1::uuid, $2::uuid, $3, $4, 'position', $5, $6)",
                    org_id, acct, r["source_id"], r["import_id"], pid, r["as_of"])
                # the lots that were open on the old line when it was closed
                lots = await c.fetch(
                    "SELECT acquired_date, quantity, original_cost, adjusted_cost, term, source_system FROM "
                    "portfolio.tax_lots WHERE position_id = $1 AND status = 'open' AND system_to IS NULL AND "
                    "valid_to = $2", r["old_position_id"], r["closed_at"])
                for lt in lots:
                    lid = await c.fetchval(
                        "INSERT INTO portfolio.tax_lots (org_id, position_id, acquired_date, quantity, original_cost, "
                        "adjusted_cost, term, status, source_system) VALUES ($1::uuid, $2, $3, $4, $5, $6, $7, 'open', "
                        "$8) RETURNING id", org_id, pid, lt["acquired_date"], lt["quantity"], lt["original_cost"],
                        lt["adjusted_cost"], lt["term"], lt["source_system"])
                    await c.execute(
                        "INSERT INTO public.held_away_records (org_id, account_id, source_id, import_id, record_type, "
                        "record_id, as_of) VALUES ($1::uuid, $2::uuid, $3, $4, 'tax_lot', $5, $6)",
                        org_id, acct, r["source_id"], r["import_id"], lid, r["as_of"])
                total += Decimal(r["market_value"] or 0)
                changed += 1
            await c.execute(
                "INSERT INTO audit_log (org_id, user_id, action, resource_type, resource_id, payload) VALUES "
                "($1::uuid, $2::uuid, 'held_away.snapshot_repaired', 'held_away', $3::uuid, $4::jsonb)",
                org_id, actor_id, acct, json.dumps({
                    "as_of": p["as_of"], "restored": len(p["restore"]), "restored_value": _money(total),
                    "imports": sorted({x["import_id"] for x in p["restore"]}),
                    "collisions": [x["asset_id"] for x in p["collisions"]], "script": "repair_heldaway_snapshots"}))
    return changed


def print_plan(org_id: str, the_plan: dict) -> None:
    print(f"ORG {org_id}: {len(the_plan)} account(s) with lines superseded at their own as-of")
    for acct, p in the_plan.items():
        tot = sum((Decimal(x["market_value"]) for x in p["restore"]), Decimal(0))
        print(f"  ACCOUNT {acct} as of {p['as_of']}: live now {p['live_lines']}, restore {len(p['restore'])} "
              f"(value {format(tot, 'f')}), already restored {p['already']}, collisions {len(p['collisions'])}")
        for x in p["restore"]:
            print(f"    RESTORE import {x['import_id']} asset {x['asset_id']} qty {x['quantity']} "
                  f"value {x['market_value']}")
        for x in p["collisions"]:
            print(f"    COLLISION asset {x['asset_id']} from import {x['import_id']} — the later-approved line is "
                  f"live ({x['live_position_id']}); left as is")


async def main_async(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--org", required=True, help="org id")
    ap.add_argument("--account", action="append", default=None, help="account id (repeatable)")
    ap.add_argument("--dry-run", action="store_true", help="list accounts, lines and totals; write nothing")
    ap.add_argument("--dsn-env", default="DATABASE_URL")
    a = ap.parse_args(argv)
    org = str(UUID(a.org))
    accounts = [str(UUID(x)) for x in a.account] if a.account else None
    sys.path.insert(0, str(HERE))
    from _db_connect import resolve_dsn

    dsn, how = await resolve_dsn(a.dsn_env)
    if not dsn:
        print(f"no working {a.dsn_env}: {how} — run through Doppler")
        return 2
    print(f"DATABASE: {how}")
    print(f"MODE: {'dry-run' if a.dry_run else 'write'}")
    conn = await asyncpg.connect(dsn, statement_cache_size=0)
    try:
        the_plan = await plan(conn, org, accounts)
        print_plan(org, the_plan)
        changed = 0 if a.dry_run else await apply(conn, org, the_plan)
    finally:
        await conn.close()
    print(f"CHANGED: {changed}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main_async()))
