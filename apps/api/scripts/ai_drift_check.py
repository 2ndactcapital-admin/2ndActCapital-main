"""S28 — the AI drift monitor, by hand (operator script).

    python3 apps/api/scripts/ai_drift_check.py --dry-run                     # every finding; opens NOTHING
    python3 apps/api/scripts/ai_drift_check.py --dry-run --task member_brief --org 00000000-0000-0000-0000-000000000001
    python3 apps/api/scripts/ai_drift_check.py --dry-run --since 2026-11-06T00:00:00Z
    python3 apps/api/scripts/ai_drift_check.py                               # evaluate AND open / refresh / close alerts

--dry-run evaluates every task and org against the live decision log and prints each finding (ok / drift / not
enough data / off) with its numbers. It runs inside a READ ONLY transaction, so it cannot open, refresh or close an
alert, write a todo or an audit row — the database refuses any write. Without --dry-run the script does what the
platform job does (services.ai_drift.run_job's evaluation + apply_findings), attributed to the platform org's
Super Admin; prefer the job (Security master → Platform jobs) for routine use.

  --task T     only this task (repeatable)
  --org ID     only this org (repeatable)
  --since TS   the recent window starts here (ISO 8601; rounded to whole hours) instead of 24 h ago; the baseline is
               the 28 days before it
  --now TS     evaluate as of this instant (default: now)
  --include-test-orgs   verify only: also read verify fixture (is_test) orgs — with --dry-run only

Test (verify fixture) orgs are skipped. Calls no model; the only network use is the database (and Doppler for its
credentials). Never prints a secret.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import pathlib
import sys
from datetime import datetime, timezone

HERE = pathlib.Path(__file__).resolve().parent
API_DIR = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(API_DIR))


def _ts(s: str) -> datetime:
    d = datetime.fromisoformat(s.replace("Z", "+00:00"))
    if d.tzinfo is None:
        raise SystemExit(f"{s}: give a timezone (e.g. Z)")
    return d


def parse(argv=None):
    ap = argparse.ArgumentParser(description="AI drift monitor — evaluate the decision log")
    ap.add_argument("--dry-run", action="store_true", help="print findings; open nothing (read-only transaction)")
    ap.add_argument("--task", action="append", default=None)
    ap.add_argument("--org", action="append", default=None)
    ap.add_argument("--since", type=_ts, default=None)
    ap.add_argument("--now", type=_ts, default=None)
    ap.add_argument("--include-test-orgs", action="store_true", help="verify only; with --dry-run only")
    args = ap.parse_args(argv)
    if args.include_test_orgs and not args.dry_run:
        ap.error("--include-test-orgs is for a --dry-run only (a real run never acts on a test org)")
    return args


def print_findings(w, results, names) -> dict:
    from services import ai_drift as AD

    print(f"[INFO] windows: recent {w.recent_start.isoformat()} → {w.now.isoformat()} ({w.recent_hours} h); baseline "
          f"{w.baseline_start.isoformat()} → {w.recent_start.isoformat()} ({w.baseline_days} d)")
    counts: dict[str, int] = {}
    for p in results:
        print(f"\n== {p.task_type} · {names.get(p.org_id, p.org_id)} ({p.org_id}) — baseline {p.baseline['calls']} "
              f"calls, recent {p.recent['calls']} — {AD.STATUSES[p.status]}")
        for f in p.findings:
            counts[f.status] = counts.get(f.status, 0) + 1
            print(f"   [{f.status.upper():12}] {AD.RULE_LABELS[f.rule_key]}: {f.sentence}")
    print(f"\nFINDINGS: {len(results)} task/org pairs; " + ", ".join(f"{k} {v}" for k, v in sorted(counts.items())))
    return counts


async def main_async(args) -> int:
    from _db_connect import admin_dsn, connect, hydrate

    hydrate()
    from services import ai_drift as AD
    from services import security_master_jobs as J

    dsn, how = await admin_dsn()
    if not dsn:
        print(f"[ERROR] no working database connection: {how}")
        return 2
    now = args.now or datetime.now(timezone.utc)
    hours = AD.RECENT_HOURS
    if args.since is not None:
        hours = max(1, round((now - args.since).total_seconds() / 3600))
    conn = await connect(dsn)
    try:
        print(f"[INFO] database via {how} as {await conn.fetchval('SELECT current_user')}; now {now.isoformat()}"
              f"{' — DRY RUN (read-only transaction)' if args.dry_run else ''}")
        if args.dry_run:
            async with conn.transaction(readonly=True):
                await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
                w, results = await AD.evaluate(conn, now=now, org_ids=args.org, task_types=args.task,
                                               recent_hours=hours, include_test_orgs=args.include_test_orgs)
                names = await AD.org_names(conn, {p.org_id for p in results})
            print_findings(w, results, names)
            print("[INFO] dry run: no alert opened, refreshed or closed; no todo or audit row written")
            return 0
    finally:
        await conn.close()

    # a real run: the same path the job takes, on the app pool under a Super Admin context
    os.environ["DATABASE_URL"] = dsn
    from services.database import get_pool, platform_scope, reset_rls_context, set_rls_context

    pool = await get_pool()
    tokens = set_rls_context(str(J.PLATFORM_ORG_ID), True)
    try:
        async with pool.acquire() as c:
            async with platform_scope(c):
                owner = await J.platform_job_owner(c)
                w, results = await AD.evaluate(c, now=now, org_ids=args.org, task_types=args.task, recent_hours=hours)
                names = await AD.org_names(c, {p.org_id for p in results})
                applied = await AD.apply_findings(c, results, w, platform_org_id=J.PLATFORM_ORG_ID,
                                                  owner_ids=[owner], org_names=names)
        print_findings(w, results, names)
        print(f"[INFO] alerts: opened {applied['opened']}, updated {applied['updated']}, recovering "
              f"{applied['recovering']}, closed {applied['closed']}; todos written {applied['todos']}")
        return 0
    finally:
        reset_rls_context(tokens)


if __name__ == "__main__":
    sys.exit(asyncio.run(main_async(parse())))
