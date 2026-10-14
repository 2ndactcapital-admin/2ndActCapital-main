"""notefix2.structural Task 8 — load a second-reader findings file into
``portfolio.note_gold_second_reader`` for the review screen.

    python3 apps/api/scripts/load_second_reader_findings.py docs/gold/gold1_second_reader_v1.json

IDEMPOTENT: keyed on (reference_filing_id, field_key, seq, source) — ``seq``
is the 0-based position of a finding among findings SHARING THE SAME field
within the same note's own ``issues`` array in the source file (confirmed
live: 3 of gold-1's 50 notes flag the same field twice with two distinct
issues, so (filing, field) alone is not a unique key). A second run of the
same file inserts 0 new rows (``ON CONFLICT ... DO NOTHING``).

Writes ONLY this reference table — never ``note_gold_values``; a human still
confirms, corrects or marks absent on the review screen. Super-admin RLS
context, like every other write in this module."""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import _note_extraction_common as common


def rows_from_file(path: Path, *, source: str) -> list[tuple]:
    data = json.loads(path.read_text())
    out: list[tuple] = []
    for note in data.get("notes", []):
        fid = note["reference_filing_id"]
        seq_by_field: dict[str, int] = {}
        for issue in note.get("issues", []):
            field_key = issue["field"]
            seq = seq_by_field.get(field_key, 0)
            seq_by_field[field_key] = seq + 1
            prefill = issue.get("prefill")
            suggested = issue.get("should_be")
            out.append((
                fid, field_key, seq,
                json.dumps(prefill) if prefill is not None else None,
                json.dumps(suggested) if suggested is not None else None,
                issue.get("evidence"), issue["severity"], issue["kind"], source,
            ))
    return out


async def load(conn, rows: list[tuple]) -> dict:
    inserted = 0
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
        for row in rows:
            res = await conn.execute(
                """INSERT INTO portfolio.note_gold_second_reader
                     (reference_filing_id, field_key, seq, prefill, suggested, evidence, severity, kind, source)
                   VALUES ($1, $2, $3, $4::jsonb, $5::jsonb, $6, $7, $8, $9)
                   ON CONFLICT (reference_filing_id, field_key, seq, source) DO NOTHING""",
                *row,
            )
            if res != "INSERT 0 0":
                inserted += 1
    return {"attempted": len(rows), "inserted": inserted, "already_loaded": len(rows) - inserted}


async def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    if not argv:
        print("usage: load_second_reader_findings.py <path-to-json> [--source NAME]")
        return 2
    path = Path(argv[0])
    source = "gold1_second_reader_v1"
    if "--source" in argv:
        source = argv[argv.index("--source") + 1]
    if not path.exists():
        print(f"not found: {path}")
        return 2
    rows = rows_from_file(path, source=source)
    conn = await common.connect()
    try:
        result = await load(conn, rows)
        print(f"{path}: {result['attempted']} findings read, {result['inserted']} inserted, "
              f"{result['already_loaded']} already loaded (source={source!r})")
        total = await conn.fetchval(
            "SELECT count(*) FROM portfolio.note_gold_second_reader WHERE source = $1", source)
        print(f"live total for source={source!r}: {total}")
        return 0
    finally:
        await conn.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
