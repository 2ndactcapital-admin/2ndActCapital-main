"""TRACK1 — load the platform's named security lists (OPERATOR) and track their members for prices.

    doppler run -- apps/api/venv/bin/python apps/api/scripts/security_lists_load.py --all --dry-run
    doppler run -- apps/api/venv/bin/python apps/api/scripts/security_lists_load.py --all
    doppler run -- apps/api/venv/bin/python apps/api/scripts/security_lists_load.py --list core_etfs --list sp500

Flags: --list KEY (repeat) | --all · --dry-run · --file PATH (default docs/security_lists/security_lists_v1.json — the
ONE reviewed data file) · --no-track (store the list and its members, track nothing) · --show-misses N (default 25).

--dry-run makes NO network call and writes nothing: per list, the members it resolves against the security master
(by CUSIP, then ticker), the misses (reported, never created), how many are not yet tracked, and the estimate for
tracking them all (calls, days at the free source's daily budget, nights through the nightly alone, the daily upkeep
against the budget). An index list (S&P 500 via SPY, Russell 2000 via IWM's N-PORT) resolves from Replication Studio's
live snapshot when one exists; otherwise the dry run says where the real run will fetch from.

A real run fetches the index lists' holdings through Replication Studio's own pipeline (issuer file / SEC N-PORT),
stores each list (portfolio.security_lists + members; a re-run restates only what changed), tracks every member
(portfolio.tracked_securities, naming the list; audited), and records one security_data_runs row (summary.job =
'security_lists_load'). Nothing is priced here: the prices backfill / the nightly prices job load the history (coverage
reason 'tracked'). Exit 0 / 1 (a list failed) / 2 (bad arguments or data file).

REVIEWQ1 — --resolve-misses (with --list KEY | --all; run AFTER a real load stored the misses):
    doppler run -- apps/api/venv/bin/python apps/api/scripts/security_lists_load.py --all --resolve-misses --dry-run
    doppler run -- apps/api/venv/bin/python apps/api/scripts/security_lists_load.py --all --resolve-misses
every stored miss with an ISIN / CUSIP is looked up through the platform's OpenFIGI provider (the US composite listing).
A FIGI (else ticker) already in the master → a 'list_match' review item (Security master → Review queue: Keep both
adds the miss's identifier as an ALTERNATE identifier; Use existing rejects it); nothing in the master → an
'add_to_master' item (OpenFIGI's ticker / name / type; Use incoming creates it). NEVER auto-merged. Once confirmed, the
next load resolves the member. --dry-run here DOES call OpenFIGI (read-only look-ups, ~10 per request without
OPENFIGI_API_KEY) and writes nothing — no review item, no mapping row."""

from __future__ import annotations

import argparse
import asyncio
import json
import pathlib
import sys
from datetime import datetime, timezone

HERE = pathlib.Path(__file__).resolve().parent
API_DIR = HERE.parent
sys.path.insert(0, str(HERE))

from _db_connect import hydrate  # noqa: E402

hydrate()   # Doppler over HTTPS — never a stale local copy (CLAUDE.md "Secrets")
sys.path.insert(0, str(API_DIR))


def parse(argv):
    ap = argparse.ArgumentParser(description="Load the named security lists and track their members (TRACK1).")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--list", action="append", dest="lists")
    g.add_argument("--all", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--file", default=None)
    ap.add_argument("--no-track", action="store_true")
    ap.add_argument("--show-misses", type=int, default=25)
    ap.add_argument("--resolve-misses", action="store_true")
    return ap.parse_args(argv)


async def resolve_misses(args, *, pool=None, transport=None, printer=print, throttle: bool = True) -> dict:
    """REVIEWQ1 — the stored misses → OpenFIGI → review items (security_lists.resolve_misses). ``transport`` is
    injectable (the verify passes a recorded one; no network)."""
    from services.database import get_pool, platform_scope
    from services.security_data import security_lists as SL
    from services.security_data.providers import openfigi as OF
    from services.security_data.providers.reference_common import RealSourceTransport

    pool = pool or await get_pool()
    own = None
    if transport is None:
        transport = own = RealSourceTransport()
    try:
        async with pool.acquire() as conn:
            async with platform_scope(conn):
                out = await SL.resolve_misses(conn, None if args.all else args.lists, transport=transport,
                                              key=OF.api_key(), dry_run=args.dry_run, throttle=throttle)
    finally:
        if own is not None:
            await own.aclose()
    printer(f"MISSES: lists {', '.join(out['lists']) or '(none stored)'} — {out['misses_with_identifier']} with an "
            f"ISIN / CUSIP ({out['misses_without_identifier']} without), {out['openfigi_requests']} OpenFIGI request(s)")
    printer(f"  already in the master (next load resolves them) {out['already_in_master']} · list_match {out['list_match']} · add_to_master {out['add_to_master']} · OpenFIGI no answer "
            f"{out['no_answer']} · not reached {out['not_reached']}")
    for r in out["rows"][:max(0, args.show_misses)]:
        printer(f"  {r['id_type']} {r['value']} {(r.get('name') or '')[:40]} → {r['outcome']}"
                + (f" {r.get('security_name')} (by {r.get('matched_by')})" if r.get("security_id") else "")
                + (f" {r.get('ticker')} {r.get('openfigi_name') or ''}" if r["outcome"] == "add_to_master" else ""))
    if args.dry_run:
        printer("dry run: OpenFIGI looked up, nothing written")
    else:
        printer(f"  review items created {out['items_created']} (already open / known: {out['already_open_or_known']}) "
                "— decide them on Security master → Review queue")
    out["exit"] = 0
    return out


async def run(args, *, pool=None, http=None, printer=print) -> dict:
    """The load. ``pool`` / ``http`` are injectable (the verify passes its own; ``http`` is only ever used by a real
    run's index lists). Returns {lists: {key: report}, estimate, failed}."""
    from services.database import get_pool, platform_scope
    from services.security_data import prices as P
    from services.security_data import security_lists as SL
    from services.security_data import tracking as TR

    try:
        defs = SL.select(SL.load_definitions(args.file or SL.DATA_FILE), None if args.all else args.lists)
    except SL.ListFileError as exc:
        printer(f"DATA FILE: {exc}")
        return {"error": str(exc), "exit": 2}
    pool = pool or await get_pool()
    own_http = None
    if not args.dry_run and http is None and any(d["source"]["kind"] == "index_holdings" for d in defs):
        from services import replica_holdings as RH
        http = own_http = RH.make_http()
    report: dict = {"lists": {}, "failed": [], "dry_run": bool(args.dry_run)}
    started = datetime.now(timezone.utc)
    all_ids: set[str] = set()
    try:
        for d in defs:
            try:
                async with pool.acquire() as conn:
                    async with platform_scope(conn):
                        r = await SL.resolve(conn, d, http=http, dry_run=args.dry_run)
                        ids = [m["gsid"] for m in r.members]
                        live = await TR.live_rows(conn, ids) if ids else {}
                        untracked = [g for g in ids if (live.get(g) or {}).get("status") != "tracked"]
                        est = await TR.estimate(conn, untracked) if untracked else None
                        rep = {"name": r.name, "source": r.source_detail, "as_of": r.as_of, "members": len(ids),
                               "ticker_fallback": r.ticker_fallback,
                               "misses": len(r.misses), "not_yet_tracked": len(untracked), "estimate": est,
                               "note": r.note, "miss_list": r.misses}
                        if not args.dry_run:
                            rep["written"] = await SL.write(conn, r, track=not args.no_track)
                all_ids |= set(ids)
                report["lists"][d["key"]] = rep
                printer(f"[{d['key']}] {r.name}: {len(ids)} members resolved, {len(r.misses)} misses, "
                        f"{len(untracked)} not yet tracked" + (f"; as of {r.as_of}" if r.as_of else "")
                        + f" — source {r.source_detail}"
                        + (f"; {r.ticker_fallback} by ticker after the CUSIP missed" if r.ticker_fallback else ""))
                if r.note:
                    printer(f"[{d['key']}]   {r.note}")
                for m in r.misses[:max(0, args.show_misses)]:
                    printer(f"[{d['key']}]   MISS {m.get('ticker') or m.get('cusip') or m.get('isin') or '?'} "
                            f"{(m.get('description') or m.get('name') or '')[:60]} — {m['reason']}")
                if len(r.misses) > args.show_misses:
                    printer(f"[{d['key']}]   … {len(r.misses) - args.show_misses} more misses")
                if est:
                    printer(f"[{d['key']}]   estimate: {est['to_load']} to load, {est['estimated_calls']} calls, "
                            f"{est['days_at_budget']} day(s) at {est['daily_budget']}/day ({est['provider']})")
                if "written" in rep:
                    printer(f"[{d['key']}]   written: {json.dumps(rep['written'], default=str)}")
            except Exception as exc:  # noqa: BLE001 — one list failing never stops the others
                report["failed"].append(d["key"])
                report["lists"][d["key"]] = {"error": f"{type(exc).__name__}: {exc}"[:500]}
                printer(f"[{d['key']}] FAILED {type(exc).__name__}: {str(exc)[:300]}")
        async with pool.acquire() as conn:
            async with platform_scope(conn):
                live = await TR.live_rows(conn, sorted(all_ids)) if all_ids else {}
                todo = [g for g in sorted(all_ids) if (live.get(g) or {}).get("status") != "tracked"]
                report["estimate"] = await TR.estimate(conn, todo) if todo else None
                report["progress"] = await TR.progress(conn)
        e = report["estimate"]
        printer(f"ALL LISTS: {len(all_ids)} distinct securities, {len(todo)} not yet tracked" + (
            f"; {e['to_load']} to load = {e['estimated_calls']} calls, {e['days_at_budget']} day(s) at the daily budget "
            f"of {e['daily_budget']}, {e['nights_nightly_only']} night(s) through the nightly alone; upkeep "
            f"{e['upkeep_calls_per_day']} calls/day for {e['coverage_after']} covered" if e else ""))
        for w in (e or {}).get("warnings") or []:
            printer(f"WARNING {w}")
        if args.dry_run:
            printer("dry run: nothing fetched, nothing written")
        else:
            await P.record_run(pool, job="security_lists_load", trigger="manual",
                               status="failed" if report["failed"] and len(report["failed"]) == len(defs) else (
                                   "partial" if report["failed"] else "success"), started=started,
                               summary={"lists": {k: {kk: vv for kk, vv in v.items() if kk != "miss_list"}
                                                  for k, v in report["lists"].items()},
                                        "failed": report["failed"], "progress": report["progress"]})
    finally:
        if own_http is not None:
            await own_http.aclose()
    report["exit"] = 1 if report["failed"] else 0
    return report


def main() -> int:
    args = parse(sys.argv[1:])
    if args.resolve_misses:
        return int(asyncio.run(resolve_misses(args)).get("exit", 1))
    return int(asyncio.run(run(args)).get("exit", 1))


if __name__ == "__main__":
    sys.exit(main())
