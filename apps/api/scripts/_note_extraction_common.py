"""Shared setup for the noteextractb1 scripts: Doppler hydrate, a working DB
connection (app_service), the registry-generated field spec, the proxy's
deployment catalogue and the participants table. No provider is called here."""
from __future__ import annotations

import asyncio
import os
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve()
sys.path.insert(0, str(HERE.parent))
from _db_bootstrap import bootstrap_async  # noqa: E402  (also puts apps/api on sys.path)

import asyncpg  # noqa: E402

DEFAULT_ESCALATION_ENV = "NOTE_EXTRACTION_ESCALATION_MODEL"


async def connect():
    url = await bootstrap_async(quiet=True)
    if not url:
        raise SystemExit("no working DATABASE_URL (Doppler hydrate failed)")
    return await asyncpg.connect(url, statement_cache_size=0)


async def load_context(conn):
    from services.note_extraction import participants, proxy, schema

    specs = schema.build_field_specs(await schema.load_registry_rows(conn))
    schema.assert_no_shared_protection_field(specs)
    catalog = await asyncio.to_thread(proxy.deployment_catalog)
    parts = await participants.load_participants(conn)
    return specs, catalog, parts


def parse_efforts(items: list[str] | None) -> dict[str, str]:
    out = {}
    for it in items or []:
        if "=" not in it:
            raise SystemExit(f"--effort expects deployment=level, got {it!r}")
        dep, level = it.split("=", 1)
        out[dep.strip()] = level.strip()
    return out


def escalation_default() -> str | None:
    return (os.environ.get(DEFAULT_ESCALATION_ENV) or "").strip() or None


def print_plan(plan, cap: float) -> None:
    s = plan.summary()
    print(f"\nDRY RUN — no provider was called.")
    print(f"  notes planned:        {s['notes']}")
    print(f"  planned calls:        {s['calls']}")
    for k, v in s["by_slot"].items():
        print(f"    {k:45} {v['calls']:6} calls  ~{v['est_input_tokens']:>10} in-tokens  ~${v['est_cost_usd']:.4f}")
    print(f"  estimated total:      ${s['est_total_usd']:.4f}  (spending cap ${cap:.2f})")
    if s["notes_skipped"]:
        print(f"  notes not loadable:   {len(plan.notes_skipped)} (first: {s['notes_skipped'][:3]})")
    print("  (estimates: input = chars/4, output = max_tokens ceiling, Jev/escalation assumed on "
          "a share of notes — a pessimistic upper bound for reader calls)")
