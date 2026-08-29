"""Seed the ALTRUIST provider + rate card into an org. fee37 Task 2.

    python3 scripts/seed_fee37_altruist.py [--org <uuid>] [--verified-on YYYY-MM-DD]

Separate from ``verify_fee37.py`` on purpose. The verify script must leave the
database exactly as it found it (requirement 7), so it seeds, asserts, and
removes only what it inserted. This script is the one that makes the profile
PERSIST. Running verify afterwards is still safe: the seeder is idempotent, so
verify adopts these rows, reports ``created=False``, and deletes nothing.

``--verified-on`` defaults to today, which for the sprint's own run means the
date the rate card was ENTERED — not a claim that anyone re-read
altruist.com. See ``services.cost_model``'s module docstring. Whoever does
perform a real re-check should re-run this with an explicit ``--verified-on``
after updating the constants.
"""

from __future__ import annotations

import argparse
import asyncio
import glob
import pathlib
import sys
from datetime import date

HERE = pathlib.Path(__file__).resolve().parent
API_DIR = HERE.parent
for _s in sorted(glob.glob(str(API_DIR / "venv/lib/python3*/site-packages"))):
    if _s not in sys.path:
        sys.path.insert(0, _s)
for _p in (str(HERE), str(API_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from _db_connect import admin_dsn, connect  # noqa: E402

from services.cost_model import (  # noqa: E402
    ALTRUIST_SCHEDULES,
    UNSEEDED_RATE_CARD_ITEMS,
    seed_altruist_profile,
)

DEFAULT_ORG = "00000000-0000-0000-0000-000000000001"


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--org", default=DEFAULT_ORG)
    ap.add_argument("--verified-on", default=None)
    args = ap.parse_args()

    verified_on = (
        date.fromisoformat(args.verified_on) if args.verified_on else date.today()
    )

    dsn, prov = await admin_dsn()
    print(f"dsn: {prov}")
    if dsn is None:
        print("BLOCKED: no working DSN")
        return 2

    conn = await connect(dsn)
    try:
        before = await conn.fetchval("SELECT count(*) FROM public.cost_schedules")
        seeded = await seed_altruist_profile(
            conn, args.org, source_verified_on=verified_on
        )
        after = await conn.fetchval("SELECT count(*) FROM public.cost_schedules")

        print(
            f"\nprovider {seeded.provider_code} = {seeded.provider_id} "
            f"({'created' if seeded.created else 'already existed'})"
        )
        print(f"cost_schedules: {before} -> {after}\n")

        # Re-read from the database rather than trusting the returned dict —
        # a seeder that reported ids it never committed would look identical.
        rows = await conn.fetch(
            """SELECT cost_code, basis, rate, flat_amount, minimum_amount,
                      frequency, applies_scope, source_url, source_verified_on
               FROM public.cost_schedules WHERE id = ANY($1::uuid[])
               ORDER BY cost_code""",
            list(seeded.schedule_ids.values()),
        )
        for r in rows:
            amt = (
                f"rate={r['rate']}"
                if r["rate"] is not None
                else f"flat={r['flat_amount']}"
            )
            mn = f" min={r['minimum_amount']}" if r["minimum_amount"] else ""
            print(
                f"  {r['cost_code']:<44} {r['basis']:<20} {amt}{mn} "
                f"{r['frequency']}/{r['applies_scope']} verified={r['source_verified_on']}"
            )

        missing = [
            r["cost_code"]
            for r in rows
            if not r["source_url"] or r["source_verified_on"] is None
        ]
        print(
            f"\n{len(rows)}/{len(ALTRUIST_SCHEDULES)} schedules present; "
            f"{len(missing)} missing provenance {missing}"
        )

        print("\nDELIBERATELY NOT SEEDED:")
        for item in UNSEEDED_RATE_CARD_ITEMS:
            print(f"  - {item['item']}: {item['reason']}")
            print(f"      -> {item['belongs_in']}")

        print(
            "\nThese rates are UNVERIFIED. source_verified_on records when they "
            "were entered, not when the source was last read. Re-verify against "
            "altruist.com before billing from them."
        )
        return 0 if not missing and len(rows) == len(ALTRUIST_SCHEDULES) else 1
    finally:
        await conn.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
