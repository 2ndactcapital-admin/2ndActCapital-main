"""GOLD SET sampler (goldset.structural) — three steps, each its own command,
each safe to re-run. No model is called and nothing is fetched from the SEC.

  1. PLAN — a frozen edgar cohort, bank x era (7 x 4 = 28 strata), equal
     allocation, seeded:

        python3 apps/api/scripts/build_gold_set.py --plan --size 300 --name "gold over-fetch 2026-10" [--seed N]
                                                    [--dry-run]

     Then FETCH it with the existing pipeline (Cohorts tab -> Run, or
     POST /admin/edgar/cohorts/{id}/runs). Re-running --plan with the same
     name creates nothing new: the existing cohort of that name is reported.

  2. PROPOSE — from that cohort's FETCHED final pricing supplements only:

        python3 apps/api/scripts/build_gold_set.py --propose <cohort_id> --target 50 --batch "gold-2026-10"
                                                    [--seed N] [--dry-run]

     At least 6 per bank, 1 per era per bank where available, 3 per trap tag
     where available; shortfalls are REPORTED, never padded. A filing already
     in any gold batch is never proposed again, so a re-run adds only what is
     still missing.

  3. REPORT — coverage of a batch:

        python3 apps/api/scripts/build_gold_set.py --report "gold-2026-10"
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from uuid import UUID

import _note_extraction_common as common


def _print_plan(plan: dict) -> None:
    from services.note_extraction import gold_set

    print(f"plan: {len(plan['members'])} members, {plan['per_stratum']} per stratum, "
          f"{len(gold_set.strata())} strata")
    print(f"  {'stratum':34} {'available':>10} {'taken':>6}")
    taken: dict[str, int] = {}
    for _a, st in plan["members"]:
        taken[st] = taken.get(st, 0) + 1
    for b, e in gold_set.strata():
        st = gold_set.stratum_label(b, e)
        print(f"  {st:34} {plan['available'].get(st, 0):>10} {taken.get(st, 0):>6}"
              + ("   SHORT" if st in plan["shortfall"] else ""))
    if plan["shortfall"]:
        print(f"  strata short of {plan['per_stratum']}: {len(plan['shortfall'])} "
              f"(missing {sum(plan['shortfall'].values())} members) — reported, not padded")


async def do_plan(conn, args) -> int:
    from services.database import platform_scope
    from services.note_extraction import gold_set

    async with platform_scope(conn):
        existing = await conn.fetchrow(
            "SELECT id, member_count, definition FROM portfolio.edgar_cohorts WHERE name = $1 AND sealed_at IS NOT NULL "
            "ORDER BY created_at LIMIT 1", args.name.strip())
    if existing is not None:
        d = existing["definition"]
        d = json.loads(d) if isinstance(d, str) else d
        print(f"a cohort named {args.name!r} already exists: {existing['id']} ({existing['member_count']} members, "
              f"seed {d.get('seed')}) — nothing written. Fetch it, or pick another --name.")
        return 0
    if args.dry_run:
        plan = await gold_set.plan_members(conn, size=args.size, seed=args.seed)
        _print_plan(plan)
        print("dry run — re-run without --dry-run to freeze the cohort")
        return 0
    plan = await gold_set.create_plan(conn, name=args.name, size=args.size, seed=args.seed)
    _print_plan(plan)
    print(f"cohort {plan['cohort_id']} frozen (seed {args.seed}). Next: fetch it with the pipeline, then "
          f"--propose {plan['cohort_id']}")
    return 0


def _print_proposal(res: dict) -> None:
    from services.note_extraction import gold_set, traps

    c = res["pool_counts"]
    print(f"cohort {res['cohort_id']}: {c['members']} members, {c['eligible']} fetched final pricing supplements; "
          f"excluded {c['excluded']}")
    print(f"  already in a gold batch: {c['already_in_a_gold_batch']}   unreadable: {c['unreadable']}   "
          f"pool: {c['pool']}")
    chosen = res["chosen"]
    print(f"proposed: {len(chosen)} (inserted {res['inserted']}) into batch {res['batch']!r}")
    for b in gold_set.BANKS:
        row = [sum(1 for n in chosen if n.bank == b and n.era == e) for e, _a, _z in gold_set.ERAS]
        print(f"  {b:18} " + "  ".join(f"{e}:{n}" for (e, _a, _z), n in zip(gold_set.ERAS, row))
              + f"   total {sum(row)}")
    for t in traps.DETECTORS:
        print(f"  trap {t:30} {sum(1 for n in chosen if t in n.trap_tags)}")
    s = res["shortfalls"]
    print("SHORTFALLS (reported, not padded):")
    if s["total"]["missing"]:
        print(f"  total: {s['total']['proposed']} of {s['total']['target']}")
    for k in ("per_bank", "per_stratum", "per_trap"):
        for name, v in s[k].items():
            print(f"  {k[4:]:8} {name:34} need {v['need']} got {v['got']} (pool {v['pool']})")
    if not any(s[k] for k in ("per_bank", "per_stratum", "per_trap")) and not s["total"]["missing"]:
        print("  none")
    for t, why in traps.NEEDS_READINGS.items():
        print(f"  note: '{t}' is a rule proxy — {why}")


async def do_propose(conn, args) -> int:
    from services.note_extraction import gold_set

    res = await gold_set.propose(conn, args.propose, target=args.target, batch=args.batch, seed=args.seed,
                                 write=not args.dry_run,
                                 progress=(lambda fid: print(f"  read {fid}", flush=True)) if args.verbose else None)
    _print_proposal(res)
    if args.dry_run:
        print("dry run — nothing written")
    return 0


async def do_report(conn, args) -> int:
    from services.note_extraction import gold_set

    r = await gold_set.batch_report(conn, args.report)
    print(f"batch {r['batch']!r}: {r['notes']} notes, {r['gold_fields']} current gold values")
    eras = [e for e, _a, _z in gold_set.ERAS]
    print(f"  {'bank':18} " + " ".join(f"{e:>8}" for e in eras) + f" {'total':>6}")
    for b, row in r["bank_x_era"].items():
        print(f"  {b:18} " + " ".join(f"{row[e]:>8}" for e in eras) + f" {sum(row.values()):>6}")
    if r["outside_grid"]:
        print(f"  outside the grid: {r['outside_grid']}")
    print(f"  trap tags:     {r['trap_tags']}")
    print(f"  product types: {r['product_types']}")
    print(f"  review status: {r['status']}")
    return 0


async def main(argv: list[str] | None = None) -> int:
    from services.note_extraction import gold_set

    ap = argparse.ArgumentParser()
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--plan", action="store_true")
    mode.add_argument("--propose", type=UUID, metavar="COHORT_ID")
    mode.add_argument("--report", metavar="SAMPLE_BATCH")
    ap.add_argument("--size", type=int, default=300)
    ap.add_argument("--name")
    ap.add_argument("--seed", type=int, default=gold_set.DEFAULT_SEED)
    ap.add_argument("--target", type=int, default=50)
    ap.add_argument("--batch")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args(argv)
    if args.plan and not (args.name or "").strip():
        ap.error("--plan needs --name")
    if args.propose and not (args.batch or "").strip():
        ap.error("--propose needs --batch")

    conn = await common.connect()
    try:
        if args.plan:
            return await do_plan(conn, args)
        if args.propose:
            return await do_propose(conn, args)
        return await do_report(conn, args)
    except gold_set.GoldSetError as exc:
        print(f"refused: {exc}")
        return 2
    finally:
        await conn.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
