"""Generate services/note_extraction/label_dictionary_v1.json from an inventory run.

Read-only: reads portfolio.edgar_inventory_items / _concepts (one inventory run)
and portfolio.note_terms_field_registry, writes one JSON file. No model call,
no database write.

    python3 apps/api/scripts/generate_note_label_dictionary.py
    python3 apps/api/scripts/generate_note_label_dictionary.py --run <inventory-run-uuid> [--out PATH]

See services/note_extraction/label_dictionary.py for which labels survive and
how a concept is assigned to a registry field.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve()
sys.path.insert(0, str(HERE.parent))
from _db_bootstrap import bootstrap_async  # noqa: E402  (also puts apps/api on sys.path)

import asyncpg  # noqa: E402


async def main(run_id: str, out: pathlib.Path) -> int:
    from services.database import platform_scope
    from services.note_extraction import label_dictionary as ld
    from services.note_extraction import schema

    url = await bootstrap_async(quiet=True)
    if not url:
        print("no working DATABASE_URL (Doppler hydrate failed)")
        return 2
    conn = await asyncpg.connect(url, statement_cache_size=0)
    try:
        async with platform_scope(conn):
            registry = await schema.load_registry_rows(conn, include_retired=True)
            rows = await conn.fetch(
                """SELECT COALESCE(c.mapped_field_key, c.proposed_field_key, c.concept_key,
                                   i.mapped_field_key, i.proposed_field_key) AS concept_key,
                          i.issuer_group, i.label, i.quote
                     FROM portfolio.edgar_inventory_items i
                     LEFT JOIN portfolio.edgar_inventory_concepts c ON c.id = i.concept_id
                    WHERE i.run_id = $1::uuid
                    ORDER BY i.issuer_group, i.label""", run_id)
    finally:
        await conn.close()
    if not rows:
        print(f"inventory run {run_id} has no items — nothing generated")
        return 1
    d = ld.build_dictionary([dict(r) for r in rows], registry, source_run_id=run_id)
    out.write_text(json.dumps(d, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    issuers = {i for f in d["fields"].values() for i in f["by_issuer"]}
    print(f"wrote {out}")
    print(f"  items seen {d['items_seen']}, labels dropped {d['labels_dropped']}, "
          f"fields {len(d['fields'])}, retired buckets {len(d['retired'])}, issuer groups {len(issuers)}, "
          f"unmapped concepts {len(d['unmapped_concepts'])}")
    for k, v in d["fields"].items():
        print(f"  {k:28} {len(v['labels']):3} labels  {len(v['by_issuer']):3} issuers")
    return 0


if __name__ == "__main__":
    from services.note_extraction.label_dictionary import DICTIONARY_PATH, SOURCE_RUN_ID

    ap = argparse.ArgumentParser()
    ap.add_argument("--run", default=SOURCE_RUN_ID)
    ap.add_argument("--out", type=pathlib.Path, default=DICTIONARY_PATH)
    a = ap.parse_args()
    sys.exit(asyncio.run(main(a.run, a.out)))
