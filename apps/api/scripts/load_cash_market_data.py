"""CASH03 Task 7 — ONE fetch of Treasury Fiscal Data (bills / notes) and the latest SEC N-MFP filings (money-market
funds) for the funds the plan uses. An OPERATOR step: the sprint ran only ``--dry-run --fixtures`` (no network).

    doppler run -- python3 apps/api/scripts/load_cash_market_data.py --dry-run --fixtures   # recorded payloads only
    doppler run -- python3 apps/api/scripts/load_cash_market_data.py --dry-run              # real fetch, writes nothing
    doppler run -- python3 apps/api/scripts/load_cash_market_data.py --real                 # fetch + write
        [--only treasury|nmfp] [--cik 1234567 ...]

  --fixtures  the recorded payloads (apps/api/tests/fixtures/cash03) behind a FixtureTransport: no network and no
              database at all (the parse is printed; nothing is written).
  --dry-run   a real fetch through the providers' real transport (sec.gov through services/edgar_fetch — the declared
              User-Agent and the 10/s limiter; Treasury Fiscal Data keyless), parsed and PRINTED — nothing is written
              and no call budget is used.
  --real      the same fetch, then written through the ONE write path: the security master via
              security_master_load.Matcher, the yields via security_data.writes.upsert_index_levels, one
              provider_call_log row per provider. REFUSED for a provider that is not 'active' (both ship
              'not_connected'): a Super Admin switches it on first (Admin → Security data → Providers).
  --cik       the fund registrants (unpadded CIKs) whose N-MFP filings to read; default = the CIKs of the money-market
              classes the security master already holds (security_data.nightly.mmf_ciks, at most 50).

Never prints a secret. Writes only platform market-data / security-master rows — no org row, no demo row.
"""

from __future__ import annotations

import asyncio
import pathlib
import sys
from datetime import date, datetime, timedelta, timezone

HERE = pathlib.Path(__file__).resolve().parent
API_DIR = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(API_DIR))


#: the recorded fixtures' own "today" (their auction / report dates are relative to it)
FIXTURE_TODAY = date(2026, 11, 9)


def _arg(argv, name):
    return [argv[i + 1] for i, a in enumerate(argv) if a == name and i + 1 < len(argv)]


def print_treasury(res, auctions=None) -> None:
    print(f"\nTreasury Fiscal Data: {res.pages} page(s), {res.auctions} auctions, {res.issues} issues"
          + (f", {len(res.rejected)} row(s) rejected" if res.rejected else ""))
    for why in res.rejected[:20]:
        print(f"  rejected: {why}")
    for a in (auctions or [])[:40]:
        y = f"{a.yield_pct}% ({a.basis})" if a.yield_pct is not None else "no yield"
        print(f"  {a.cusip} {a.security_type:<4} {a.security_term:<16} auction {a.auction_date} matures "
              f"{a.maturity_date}  {y}")
    if res.master:
        print(f"  security master: {res.master}")
    if res.yields_written:
        print(f"  yields written: {res.yields_written}")


def print_nmfp(res) -> None:
    print(f"\nSEC N-MFP: {res.filings} filing(s) read, {len(res.parsed)} series parsed"
          + (f", {len(res.rejected)} rejected" if res.rejected else ""))
    for why in res.rejected[:20]:
        print(f"  rejected: {why}")
    for f in res.parsed:
        print(f"  {f.series_id} {f.category:<16} report {f.report_date} WAM {f.wam_days} WAL {f.wal_days} "
              f"({f.series_name or '—'})")
        for c in f.classes:
            print(f"    {c.class_id} 7-day net yield {c.yield_pct}% as of {c.yield_date} ({c.name or '—'})")
    if res.master:
        print(f"  security master: {res.master}")
    if res.yields_written:
        print(f"  yields written: {res.yields_written}")


async def main(argv) -> int:
    from services import edgar_nmfp as NM
    from services.market_data import treasury_fiscal as TF

    dry, real, fixtures = "--dry-run" in argv, "--real" in argv, "--fixtures" in argv
    only = (_arg(argv, "--only") or [None])[0]
    if dry == real:
        print("choose exactly one of --dry-run or --real")
        return 2
    if real and fixtures:
        print("--fixtures is a dry run only (fixture identifiers never enter the real security master)")
        return 2
    today = datetime.now(timezone.utc).date()
    if fixtures:
        import _cash03_fixtures as F

        today = FIXTURE_TODAY
        print(f"[INFO] FIXTURES — recorded payloads only, no network, no database; today = {today}")
        if only in (None, "treasury"):
            t = F.transport(today)
            res = await TF.refresh(None, t, today=today, dry_run=True)
            auctions, _rej, _p = await TF.fetch_all(F.transport(today), today - timedelta(days=TF.LOOKBACK_DAYS))
            print_treasury(res, TF.issues(auctions))
        if only in (None, "nmfp"):
            res = await NM.refresh(None, F.transport(today), today=today, ciks=set(_arg(argv, "--cik")) or F.FIXTURE_CIKS,
                                   dry_run=True)
            print_nmfp(res)
        print("\nDRY RUN — nothing written (fixtures).")
        return 0

    from _db_connect import admin_dsn, connect, hydrate

    hydrate()
    from services.database import get_pool
    from services.security_data import nightly as SN
    from services.security_data import registry as R
    from services.security_data.providers.reference_common import RealSourceTransport

    dsn, how = await admin_dsn()
    if not dsn:
        print(f"no database: {how}")
        return 1
    conn = await connect(dsn)
    try:
        rows = await R.providers(conn)
        for k in ("treasury_fiscal", "sec_nmfp"):
            p = rows.get(k)
            print(f"[INFO] provider {k}: {p['status'] if p else 'NOT REGISTERED (apply docs/cash03_part1.sql)'}")
        ciks = set(_arg(argv, "--cik")) or await SN.mmf_ciks(conn)
    finally:
        await conn.close()
    if real:
        off = [k for k in ("treasury_fiscal", "sec_nmfp") if (only is None or k.startswith(only[:4]))
               and (rows.get(k) or {}).get("status") != "active"]
        if off:
            print(f"refused: {', '.join(off)} not active — a Super Admin switches it on first (Admin → Security data → "
                  "Providers), then re-run")
            return 1
    pool = await get_pool() if real else None
    rc = 0
    t = RealSourceTransport()
    try:
        if only in (None, "treasury"):
            try:
                res = await TF.refresh(pool, t, today=today, dry_run=dry)
                print_treasury(res)
            except Exception as exc:  # noqa: BLE001 — reported, the other source still runs
                from services.market_data.adapters import scrub_error
                print(f"[FAIL] Treasury Fiscal Data: {scrub_error(exc)}")
                rc = 1
        if only in (None, "nmfp"):
            if not ciks:
                print("\nSEC N-MFP: no fund CIK — the security master holds no money-market class with a CIK yet "
                      "(run the sec_mutual_funds sync, or pass --cik)")
            else:
                try:
                    print_nmfp(await NM.refresh(pool, t, today=today, ciks=ciks, dry_run=dry))
                except Exception as exc:  # noqa: BLE001
                    from services.market_data.adapters import scrub_error
                    print(f"[FAIL] SEC N-MFP: {scrub_error(exc)}")
                    rc = 1
    finally:
        await t.aclose()
    print("\nDRY RUN — nothing written." if dry else "\nWritten (security master + index levels + call log).")
    return rc


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1:])))
