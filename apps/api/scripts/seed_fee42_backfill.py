"""fee42 Task 2 — the real, one-time backfill of ACTIVE SPVs into spv_fee_terms.

This is a MIGRATION, not a test. It is separate from ``verify_fee42.py`` on
purpose: verify tears its own fixtures down and asserts the table's row count is
unchanged when it exits, so a migration that legitimately leaves rows behind
cannot live inside it.

Idempotent. An SPV that already carries active terms is SKIPPED_EXISTS and is
never overwritten — running this twice must not replace hand-entered terms with
inferred defaults.

Run:  python3 scripts/seed_fee42_backfill.py [--dry-run]
"""

from __future__ import annotations

import asyncio
import glob
import pathlib
import sys
from datetime import date

HERE = pathlib.Path(__file__).resolve().parent
API_DIR = HERE.parent
for _site in sorted(glob.glob(str(API_DIR / "venv/lib/python3*/site-packages"))):
    if _site not in sys.path:
        sys.path.insert(0, _site)
for _path in (str(HERE), str(API_DIR)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from _db_connect import app_service_dsn, connect  # noqa: E402

from services.spv_fee_terms import backfill_active_spv_terms  # noqa: E402

ORGS = [
    ("00000000-0000-0000-0000-000000000001", "2nd Act Capital"),
    ("bb347258-8f28-4f49-8cc9-e29ccad82884", "Hollisworks"),
]


async def main() -> int:
    dry = "--dry-run" in sys.argv
    dsn, prov = await app_service_dsn()
    if dsn is None:
        print(f"FATAL: {prov}")
        return 1
    print(f"connected as app_service via {prov}"
          f"{'  [DRY RUN — no writes]' if dry else ''}\n")

    conn = await connect(dsn)
    created = skipped = 0
    try:
        for org_id, org_name in ORGS:
            print(f"{'=' * 78}\n{org_name}  ({org_id})\n{'=' * 78}")
            decisions = await backfill_active_spv_terms(
                conn, org_id, effective_from=date.today(), dry_run=dry
            )
            if not decisions:
                print("  (no SPVs)\n")
                continue
            for d in decisions:
                print(f"\n  {d.name}  [{d.spv_status}]  class={d.class_label!r}")
                print(f"    ACTION : {d.action}"
                      + (f"  -> spv_fee_terms {d.terms_id}" if d.terms_id else ""))
                print(f"    REASON : {d.reason}")
                if d.known:
                    print("    KNOWN (carried verbatim from the flat columns):")
                    for k, v in d.known.items():
                        print(f"      {k:<20} = {v!r}")
                if d.inferred:
                    print("    INFERRED (a default, not a fact):")
                    for k, v in d.inferred.items():
                        print(f"      {k:<20} : {v}")
                if d.action == "CREATED":
                    created += 1
                else:
                    skipped += 1
            print()
    finally:
        await conn.close()

    print("=" * 78)
    print(f"fee42 backfill: {created} created, {skipped} skipped"
          + ("  [DRY RUN]" if dry else ""))
    print("=" * 78)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
