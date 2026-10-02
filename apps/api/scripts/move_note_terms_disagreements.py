"""Move the 29 hazard-ensemble MODEL DISAGREEMENTS out of
document_field_corrections into portfolio.note_term_readings (noteextractb1,
Decision 1). Corrections must only ever hold human decisions.

Runs migrations/noteextractb1_move_disagreements.sql — ONE atomic DO block that
inserts two readings per row (Model 1 = the original value + its model, Model 2
= the corrected value + its model), proves every row has both, and only then
deletes the moved rows. Re-running is a no-op.

WHY A SCRIPT: the sprint applied every other Part 1 statement through the
Supabase MCP tool, but MCP cancels any statement containing DELETE in a
non-interactive session (it cannot ask for confirmation). The pre-move state is
captured in scripts/fixtures/noteextractb1_disagreements_premove.json, which
verify_noteextractb1.py compares against.

    python3 apps/api/scripts/move_note_terms_disagreements.py            # dry run: counts only
    python3 apps/api/scripts/move_note_terms_disagreements.py --apply    # move them
"""
from __future__ import annotations

import argparse
import asyncio
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve()
sys.path.insert(0, str(HERE.parent))
from _db_bootstrap import bootstrap_async  # noqa: E402

import asyncpg  # noqa: E402

SQL = (HERE.parents[1] / "migrations" / "noteextractb1_move_disagreements.sql").read_text()

COUNT_SQL = """
SELECT
  (SELECT count(*) FROM public.document_field_corrections
    WHERE target_type = 'note_terms' AND corrected_by IS NULL
      AND notes LIKE '%hazard_ensemble_disagreement%') AS disagreements,
  (SELECT count(*) FROM portfolio.note_term_readings
    WHERE origin = 'migrated_correction') AS migrated_readings,
  (SELECT count(*) FROM public.document_field_corrections) AS corrections_total
"""


async def counts(conn) -> dict:
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
        return dict(await conn.fetchrow(COUNT_SQL))


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="perform the move (default: dry run)")
    args = ap.parse_args()

    url = await bootstrap_async(quiet=True)
    if not url:
        print("no working DATABASE_URL (Doppler hydrate failed)")
        return 2
    conn = await asyncpg.connect(url, statement_cache_size=0)
    try:
        before = await counts(conn)
        print(f"before: {before}")
        if not args.apply:
            print("dry run — nothing changed. Re-run with --apply to move the rows.")
            return 0
        await conn.execute(SQL)  # the DO block sets its own SET LOCAL super-admin context
        after = await counts(conn)
        print(f"after:  {after}")
        moved = before["disagreements"] - after["disagreements"]
        ok = after["disagreements"] == 0 and (
            after["migrated_readings"] - before["migrated_readings"] == 2 * moved)
        print("OK" if ok else "UNEXPECTED COUNTS — inspect before proceeding")
        return 0 if ok else 1
    finally:
        await conn.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
