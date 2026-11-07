"""recalculate_rollups — ROLLUP2 Task 7 (operator): refresh the stored net-worth snapshot (``entity_holdings``) from
THE net-worth core.

Why: before ROLLUP2 the rollup dropped every valued position with no taxonomy key, so the stored snapshot of six
Olivo entities was short by $111,970,400 and the CRUT had none. The cards now compute live (``services.networth``),
but the snapshot still feeds the allocation sunburst and the flexible / primary household rollups; this rewrites it
from the same lines and shares (``portfolio_rollup.compute_buckets``), unclassified holdings under ``unclassified``.

Per entity it prints: the snapshot BEFORE (latest stored snapshot on or before the as-of), AFTER (what the rollup
writes), and the live CARD figure — AFTER must equal CARD to the cent for every entity, else the line says
MISMATCH (and the real run refuses to write).

Usage (the operator runs the real pass; the sprint ran only --dry-run):
    doppler run -- python3 scripts/recalculate_rollups.py --org 00000000-0000-0000-0000-000000000001 --dry-run
    doppler run -- python3 scripts/recalculate_rollups.py --org 00000000-0000-0000-0000-000000000001 \
        --household f1aa5e56-3e76-427a-ab07-e93b4bbedd4f
``--household`` limits the run to that household's family (closed under ownership, as Recalculate does);
``--as-of YYYY-MM-DD`` (default today) is the snapshot date written. The real run writes a snapshot for that date
(a re-run the same day rewrites the same rows — idempotent; ``CHANGED`` counts buckets whose value changed). No
prompts. Final line ``CHANGED: N`` (0 on a dry run).
"""

from __future__ import annotations

import argparse
import asyncio
import pathlib
import sys
from datetime import date
from decimal import Decimal
from uuid import UUID

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

import asyncpg  # noqa: E402

from services import networth as NW  # noqa: E402
from services.portfolio_assets import _OrgWrite  # noqa: E402
from services.portfolio_rollup import ROLLUP_SOURCE, compute_buckets, rollup_entity_holdings  # noqa: E402

ZERO = Decimal(0)

SNAPSHOT_SQL = """
    WITH snap AS (SELECT entity_id, max(as_of_date) AS d FROM entity_holdings
                   WHERE org_id = $1::uuid AND source = $2 AND as_of_date <= $3 GROUP BY entity_id)
    SELECT h.entity_id::text AS entity_id, h.taxonomy_key, h.market_value
      FROM entity_holdings h JOIN snap ON snap.entity_id = h.entity_id AND snap.d = h.as_of_date
     WHERE h.org_id = $1::uuid AND h.source = $2
"""


async def plan(conn, org: str, as_of: date, household: str | None) -> dict:
    """Read-only: before / after / card per entity, and the bucket-level change count."""
    from services import portfolio_refresh as PR

    scope = None
    if household:
        async with _OrgWrite(conn, org) as c:
            members = [str(r["entity_id"]) for r in await c.fetch(
                "SELECT m.entity_id FROM household_memberships m JOIN households h ON h.id = m.household_id "
                "WHERE h.id = $1::uuid AND h.org_id = $2::uuid", household, org)]
        if not members:
            raise SystemExit(f"household {household} has no members in org {org}")
        scope = await PR.family_of(conn, org, members)
    async with _OrgWrite(conn, org) as c:
        rows, valued, skipped, _n = await compute_buckets(c, org, as_of, scope)
        before_rows = await c.fetch(SNAPSHOT_SQL, org, ROLLUP_SOURCE, as_of)
        same_day = {(r["entity_id"], r["taxonomy_key"]): r["market_value"] for r in await c.fetch(
            "SELECT entity_id::text AS entity_id, taxonomy_key, market_value FROM entity_holdings WHERE org_id = "
            "$1::uuid AND source = $2 AND as_of_date = $3", org, ROLLUP_SOURCE, as_of)}
        ents = sorted({r[0] for r in rows} | ({r["entity_id"] for r in before_rows} if scope is None else
                                              {r["entity_id"] for r in before_rows} & set(scope)))
        ctx = await NW.load(c, org, ents, as_of=as_of)
        names = {str(r["id"]): r["display_name"] for r in await c.fetch(
            "SELECT id, display_name FROM entities WHERE org_id = $1::uuid AND id = ANY($2::uuid[])", org, ents)}
    before: dict[str, Decimal] = {}
    for r in before_rows:
        before[r["entity_id"]] = before.get(r["entity_id"], ZERO) + r["market_value"]
    after: dict[str, Decimal] = {}
    for e, _k, amt in rows:
        after[e] = after.get(e, ZERO) + amt
    lines = []
    for e in ents:
        card = NW.q(NW.figures(ctx, {e})["gross"])
        a = after.get(e, ZERO)
        lines.append({"entity_id": e, "name": names.get(e) or e, "before": before.get(e), "after": a, "card": card,
                      "agree": a == card})
    changed = sum(1 for e, k, amt in rows if same_day.get((e, k)) != amt)
    return {"lines": lines, "changed_buckets": changed, "buckets": len(rows), "scope": scope,
            "unclassified": sum(1 for v in valued if not v["taxonomy_key"]), "skipped": len(skipped)}


def show(p: dict) -> None:
    print(f"{'entity':44} {'before':>18} {'after':>18} {'delta':>16}  card agrees")
    for ln in p["lines"]:
        b = ln["before"]
        d = ln["after"] - (b or ZERO)
        print(f"{ln['name'][:44]:44} {('-' if b is None else f'{b:,.2f}'):>18} {ln['after']:>18,.2f} {d:>16,.2f}  "
              f"{'yes' if ln['agree'] else 'MISMATCH card ' + format(ln['card'], ',.2f')}")
    print(f"BUCKETS: {p['buckets']} (would change {p['changed_buckets']}); unclassified lines counted "
          f"{p['unclassified']}; valueless lines skipped {p['skipped']}")


async def main_async(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--org", required=True)
    ap.add_argument("--household")
    ap.add_argument("--as-of", type=date.fromisoformat, default=None)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--dsn-env", default="DATABASE_URL")
    a = ap.parse_args(argv)
    org = str(UUID(a.org))
    hh = str(UUID(a.household)) if a.household else None
    as_of = a.as_of or date.today()
    from _db_connect import resolve_dsn

    dsn, how = await resolve_dsn(a.dsn_env)
    if not dsn:
        print(f"no working {a.dsn_env}: {how} — run through Doppler")
        return 2
    print(f"DATABASE: {how}\nMODE: {'dry-run' if a.dry_run else 'write'}; org {org}; household {hh}; as-of {as_of}")
    conn = await asyncpg.connect(dsn, statement_cache_size=0)
    changed = 0
    try:
        async with conn.transaction(readonly=True):
            p = await plan(conn, org, as_of, hh)
        show(p)
        bad = [ln["name"] for ln in p["lines"] if not ln["agree"]]
        if bad:
            print(f"REFUSED: the rollup and the live card disagree for {bad} — nothing written")
            print("CHANGED: 0")
            return 1
        if not a.dry_run:
            res = await rollup_entity_holdings(conn, org_id=org, as_of_date=as_of,
                                               scope=set(p["scope"]) if p["scope"] is not None else None)
            changed = p["changed_buckets"]
            print(f"WROTE: {res.buckets_written} buckets, removed {res.buckets_removed}, zeroed {res.buckets_zeroed}")
    finally:
        await conn.close()
    print(f"CHANGED: {changed}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main_async()))
