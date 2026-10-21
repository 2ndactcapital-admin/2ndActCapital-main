"""BUGFIX1 — remove the R2 objects of preliminaries the bulk download stored.

    doppler run -p hollisworks -c prd -- apps/api/venv/bin/python apps/api/scripts/cleanup_preliminary_r2.py
    doppler run -p hollisworks -c prd -- apps/api/venv/bin/python apps/api/scripts/cleanup_preliminary_r2.py --apply

Dry run by default: prints how many stored preliminaries, how many R2
objects and how many bytes would go, and changes nothing. --apply deletes the
objects and clears reference_filings.r2_key / text_r2_key (the filing stays
recorded with document_kind = preliminary_pricing_supplement). The operator
runs this; nothing runs it automatically. See services/edgar_bulk.py
(plan_preliminary_cleanup / apply_preliminary_cleanup).
"""
from __future__ import annotations

import asyncio
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from _db_connect import admin_dsn, connect  # noqa: E402

from services import edgar_bulk, edgar_pipeline  # noqa: E402


async def main(apply: bool) -> int:
    dsn, how = await admin_dsn()
    if not dsn:
        print(f"[BLOCKED] no working DATABASE_URL ({how})")
        return 2
    conn = await connect(dsn)
    try:
        plan = await edgar_bulk.plan_preliminary_cleanup(conn)
        mb = plan["bytes"] / (1024 * 1024)
        print(f"stored preliminaries: {plan['count']} filing(s), {plan['objects']} R2 object(s), "
              f"{plan['bytes']} bytes ({mb:.1f} MB)")
        if plan["skipped_referenced"]:
            print(f"left alone (referenced elsewhere): {len(plan['skipped_referenced'])}")
        if not apply:
            print("dry run — nothing changed (pass --apply)")
            return 0
        bucket = edgar_pipeline.r2_bucket()
        out = await edgar_bulk.apply_preliminary_cleanup(conn, plan, bucket=bucket)
        print(f"cleared {out['cleared']} filing(s); {len(out['failed'])} failed")
        for f in out["failed"]:
            print(f"  FAILED {f['accession_number']}: {f['error']}")
        return 0 if not out["failed"] else 1
    finally:
        await conn.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(main("--apply" in sys.argv)))
