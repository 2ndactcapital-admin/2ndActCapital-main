"""Score an extraction run against the GOLD SET (goldset.structural).

    python3 apps/api/scripts/score_against_gold.py --run <run_id> [--batch gold-2026-10] \
        [--fields critical|core|all] [--no-write]

Compares the run's resolved values with the CURRENT gold values (valid_to IS
NULL) on the notes both cover. Per field: correct, wrong, missed (gold has a
value, the run none), false (gold absent, the run has a value), accuracy.
Ranges compare min and max; lists use the notefields list comparison; the
normalisation is the cascade's. A field with fewer than 10 reviewed notes is
"too few" and not scored. GATE: PASS when every critical field is >= 95% on at
least 40 reviewed notes. The report is merged into the run's report jsonb
under 'gold_score' (unless --no-write).

ANCHORING: the gold set was pre-filled by gpt-5-mini; that model's own scores
against it are biased upward and are printed with that caveat.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from uuid import UUID

import _note_extraction_common as common


def print_report(r: dict) -> None:
    print(f"run {r['run_id']} ({r['run_kind']}), values from {r['run_values_from']}, "
          f"{r['notes_in_run']} notes in the run, {r['reviewed_notes']} reviewed notes in common"
          + (f", batch {r['batch']!r}" if r.get("batch") else ""))
    print(f"\n  {'field':28} {'crit':4} {'n':>4} {'correct':>7} {'wrong':>5} {'missed':>6} {'false':>5} {'accuracy':>9}")
    for k, f in r["field_results"].items():
        acc = "too few" if f["status"] == "too few" else f"{f['accuracy']:.3f}"
        print(f"  {k:28} {'★' if f['critical'] else '':4} {f['notes']:>4} {f['correct']:>7} {f['wrong']:>5} "
              f"{f['missed']:>6} {f['false']:>5} {acc:>9}")
    ca = r["critical_accuracy"]
    print(f"\ncritical-field accuracy: {'n/a' if ca is None else f'{ca:.3f}'} over {r['critical_fields_scored']} "
          f"scored critical fields" + (f"; too few: {r['critical_fields_too_few']}" if r["critical_fields_too_few"] else ""))
    g = r["gate"]
    print(f"GATE ({g['accuracy']:.0%} on >= {g['min_notes']} reviewed notes, every critical field): {g['result']}")
    for b in g["below"]:
        print(f"  below the gate: {b['field']} — {b['reason']}")
    a = r.get("anchoring") or {}
    print(f"\nANCHORING: {a.get('caveat')}" + ("  THIS RUN IS ANCHORED." if a.get("anchored") else ""))


async def main(argv: list[str] | None = None) -> int:
    from services.note_extraction import schema, scoring

    ap = argparse.ArgumentParser()
    ap.add_argument("--run", type=UUID, required=True)
    ap.add_argument("--batch", default=None)
    ap.add_argument("--fields", choices=scoring.FIELD_SETS, default="critical")
    ap.add_argument("--no-write", action="store_true")
    args = ap.parse_args(argv)
    conn = await common.connect()
    try:
        specs = await schema.load_specs(conn)
        try:
            r = await scoring.score_run(conn, str(args.run), specs, batch=args.batch, fields=args.fields,
                                        write=not args.no_write)
        except LookupError as exc:
            print(f"refused: {exc}")
            return 2
        print_report(r)
        if not args.no_write:
            print(f"\nwritten to note_extraction_runs.report->'gold_score' for run {args.run}")
        return 0
    finally:
        await conn.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
