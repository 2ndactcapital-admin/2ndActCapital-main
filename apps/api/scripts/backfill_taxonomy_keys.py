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

BUGFIX4:
* the default map now also covers the security MASTER's unambiguous types (equity / fixed_income /
  structured_note), so the same run keys master-resolved held-away assets of those types; ``fund`` stays unmapped
  (equity AND bond ETFs share it) and is LISTED;
* ``--reclassify ASSET_ID:KIND`` (repeatable, with ONE ``--org``) — the operator names an asset whose kind was
  entered wrongly and gives it the right kind (system axis, id kept), then keys it from that kind. The case it was
  built for: K.19 "Family office working capital" entered as ``other`` (no bucket, counted $0) →
  ``--reclassify eddf9851-7140-4d38-a558-486d019616e3:cash_holding`` (Cash & Equivalents). Only the kinds of
  ``portfolio.kind_taxonomy`` are accepted; a dry run prints what would change.

ROLLUP2:
* ``--funds`` — every fund asset (fund / etf / mutual_fund / closed_end_fund) gets a PROPOSED key from what it
  holds: the security master's category through the fund category map (services.fund_classification), cross-checked
  against the fund's name. Each line prints the asset, the status (apply / unchanged / review / unclassified), the
  proposed key and the reason (category → key). The real run applies ONLY ``apply`` proposals (system axis, id
  kept); ``review`` and ``unclassified`` are listed for the operator, never guessed. ``--funds`` replaces the kind
  pass (a fund's kind maps to nothing).
* ``--reclassify ASSET_ID:KEY`` — a value starting ``taxonomy_`` is a KEY: the asset keeps its kind and gets that key
  (must be an active key of the org's taxonomy). Repeatable; KIND and KEY items can be mixed.

    doppler run -- python3 scripts/backfill_taxonomy_keys.py --org 00000000-0000-0000-0000-000000000001 --funds --dry-run
    doppler run -- python3 scripts/backfill_taxonomy_keys.py --org 00000000-0000-0000-0000-000000000001 --funds
    doppler run -- python3 scripts/backfill_taxonomy_keys.py --org 00000000-0000-0000-0000-000000000001 \
        --reclassify <BTC asset id>:taxonomy_mc_7_3 --reclassify <SGOV asset id>:taxonomy_sub_8_1_2

SECMASTER2:
* ``--funds`` also proposes the SHARPER key for funds that are already keyed (US vs international developed vs
  emerging, large vs small cap — services.fund_classification): when the asset's key is an ANCESTOR of the sharper
  one (taxonomy_sc_1 → taxonomy_mc_1_1) the line is ``REFINE old -> new | reason``. A real run applies refine lines
  ONLY with ``--refine``; without it they are listed and counted, never written. ``review`` is never applied.

    doppler run -- python3 scripts/backfill_taxonomy_keys.py --org 00000000-0000-0000-0000-000000000001 --funds --dry-run
    doppler run -- python3 scripts/backfill_taxonomy_keys.py --org 00000000-0000-0000-0000-000000000001 --funds --refine
"""

from __future__ import annotations

import argparse
import asyncio
import pathlib
import sys
from collections import defaultdict
from decimal import Decimal
from uuid import UUID

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

import asyncpg  # noqa: E402

from services.portfolio_assets import _OrgWrite  # noqa: E402
from services.portfolio_kind_taxonomy import (  # noqa: E402
    ReclassifyError,
    kind_map,
    org_taxonomy_keys,
    reclassify_target,
)
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


async def reclassify(conn, org: str, items: list[tuple[str, str]], *, dry_run: bool) -> int:
    """BUGFIX4 — set each named asset's kind (and the key that kind maps to). Idempotent: an asset already of that
    kind with that key is reported UNCHANGED."""
    changed = 0
    async with _OrgWrite(conn, org) as c:
        rows = {r["id"]: r for r in await c.fetch(
            "SELECT id::text AS id, name, asset_type, default_taxonomy_key FROM portfolio.assets WHERE org_id = "
            "$1::uuid AND id = ANY($2::uuid[]) AND valid_to IS NULL AND system_to IS NULL", org,
            [a for a, _ in items])}
        targets = {}
        for aid, target in items:   # CATCHALL1 — the ONE rule (portfolio_kind_taxonomy.reclassify_target)
            r = rows.get(aid)
            if r is None:
                continue
            try:
                targets[aid] = await reclassify_target(c, org, current_kind=r["asset_type"], target=target)
            except ReclassifyError as exc:
                targets[aid] = exc
    for aid, target in items:
        as_key = target.startswith("taxonomy_")          # ROLLUP2 — ASSET_ID:KEY keeps the kind, sets the key
        r = rows.get(aid)
        if r is None:
            print(f"  RECLASSIFY {aid}: NOT FOUND in org {org} (current rows only) — skipped")
            continue
        got = targets[aid]
        if isinstance(got, ReclassifyError):
            print(f"  RECLASSIFY {aid}: {got} — skipped")
            continue
        kind, key = got
        if r["asset_type"] == kind and r["default_taxonomy_key"] == key:
            print(f"  RECLASSIFY {aid} ({r['name']}): already {kind} -> {key} — UNCHANGED")
            continue
        print(f"  RECLASSIFY {aid} ({r['name']}): {r['asset_type']}/{r['default_taxonomy_key']} -> {kind}/{key}")
        if not dry_run:
            await update_asset(conn, org_id=org, asset_id=aid,
                               changes={"default_taxonomy_key": key} if as_key else {"asset_type": kind,
                                                                                     "default_taxonomy_key": key})
            changed += 1
    return changed


async def funds(conn, org: str, *, dry_run: bool, refine: bool = False) -> int:
    """ROLLUP2 — propose (and, on a real run, apply) a key per fund asset from its category. Returns assets keyed.
    SECMASTER2 — ``refine`` lines (an ancestor key → the sharper one) are applied only when ``refine`` is True."""
    from services.fund_classification import propose

    async with _OrgWrite(conn, org) as c:
        props = await propose(c, org)
    counts: dict[str, int] = defaultdict(int)
    print(f"ORG {org}: {len(props)} fund assets")
    for p in props:
        counts[p["status"]] += 1
        print(f"  FUND {p['asset_id']} {p['asset_type']:<12} {p['name'][:60]!r}: {p['status'].upper()}"
              f" {p['current_key'] or '-'} -> {p['key'] or '-'} | {p['reason']}"
              f" | live unkeyed positions {p['live_unkeyed_positions']}, value {Decimal(p['live_value'] or 0):,.2f}")
    print(f"  FUNDS {org}: " + ", ".join(f"{k} {v}" for k, v in sorted(counts.items())))
    changed = 0
    refines = [p for p in props if p["status"] == "refine" and p["key"]]
    for p in refines:
        print(f"  REFINE {p['asset_id']} {p['name'][:60]!r}: {p['current_key']} -> {p['key']} | {p['reason']}"
              f"{'' if refine else ' (not applied: --refine)'}")
    if dry_run:
        return 0
    for p in props:
        if (p["status"] == "apply" or (refine and p["status"] == "refine")) and p["key"]:
            await update_asset(conn, org_id=org, asset_id=p["asset_id"], changes={"default_taxonomy_key": p["key"]})
            changed += 1
    print(f"  SET {org}: {changed} fund assets keyed"
          f"{f' ({len(refines)} refined)' if refine and refines else ''}")
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
    ap.add_argument("--reclassify", action="append", default=[], metavar="ASSET_ID:KIND|ASSET_ID:KEY",
                    help="BUGFIX4: give a named asset the right kind (and its key); ROLLUP2: or a taxonomy KEY "
                         "(taxonomy_...); repeatable; needs exactly one --org")
    ap.add_argument("--funds", action="store_true",
                    help="ROLLUP2: propose a key per fund asset from its master category (apply only 'apply')")
    ap.add_argument("--refine", action="store_true",
                    help="SECMASTER2: with --funds, also apply REFINE proposals (an ancestor key -> the sharper one)")
    ap.add_argument("--dsn-env", default="DATABASE_URL", help="env var holding the DSN (default DATABASE_URL)")
    a = ap.parse_args(argv)
    items = []
    for x in a.reclassify:
        aid, _, kind = x.partition(":")
        try:
            items.append((str(UUID(aid)), kind.strip()))
        except ValueError:
            ap.error(f"--reclassify {x!r} is ASSET_ID:KIND")
    if items and (a.all_orgs or len(a.org or []) != 1):
        ap.error("--reclassify needs exactly one --org")
    if a.refine and not a.funds:
        ap.error("--refine needs --funds")
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
        changed = await reclassify(conn, orgs[0], items, dry_run=a.dry_run) if items else 0
        if a.funds:
            for o in orgs:
                changed += await funds(conn, o, dry_run=a.dry_run, refine=a.refine)
        else:
            changed += await run(conn, orgs, dry_run=a.dry_run)
    finally:
        await conn.close()
    print(f"CHANGED: {changed}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main_async()))
