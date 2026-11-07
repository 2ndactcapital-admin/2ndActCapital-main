"""REP13 — Task 1 discovery (READ ONLY). Prints the facts docs/REP13_DISCOVERY.md records.

    python3 apps/api/scripts/_discover_rep13.py

Runs on the operator DSN and writes NOTHING. Never prints a secret.
"""

from __future__ import annotations

import asyncio
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from _db_connect import connect, hydrate, resolve_dsn  # noqa: E402

hydrate()

OPERATOR_DSN_VAR = "DATABASE_URL_PRECUTOVER_POSTGRES_BACKUP"


async def main() -> None:
    dsn, how = await resolve_dsn(OPERATOR_DSN_VAR)
    if not dsn:
        raise SystemExit(f"no operator DSN: {how}")
    conn = await connect(dsn)
    try:
        print("user:", await conn.fetchval("SELECT current_user"), "via", how)

        async def show(label, sql, *args):
            rows = await conn.fetch(sql, *args)
            print(f"\n== {label} ({len(rows)} rows)")
            for r in rows[:80]:
                print("  ", dict(r))

        await show("1a capability rows (live)",
                   "SELECT custodian_code, capability_key, value, evidence_kind, left(evidence,60) ev, version_no "
                   "FROM public.custodian_capabilities WHERE valid_to IS NULL AND system_to IS NULL "
                   "ORDER BY 1,2")
        await show("1a capability constraints",
                   "SELECT conname, contype::text, pg_get_constraintdef(oid) d FROM pg_constraint "
                   "WHERE conrelid='public.custodian_capabilities'::regclass")
        await show("1a capability indexes",
                   "SELECT indexname, indexdef FROM pg_indexes WHERE tablename='custodian_capabilities'")
        await show("1a account custodian codes",
                   "SELECT custodian_code, count(*) FROM public.accounts GROUP BY 1")
        for t, s in (("models", "portfolio"), ("model_versions", "portfolio"),
                     ("model_version_positions", "portfolio"), ("rebalance_proposals", "public"),
                     ("rebalance_proposal_trades", "public"), ("execution_instructions", "public"),
                     ("execution_fills", "public"), ("trade_blocks", "public"), ("block_allocations", "public"),
                     ("account_model_links", "public"), ("tax_lots", "portfolio"), ("positions", "portfolio"),
                     ("account_authority_settings", "public"), ("suitability_profiles", "public"),
                     ("member_suitability", "public"), ("harvest_candidates", "public")):
            cols = [r["column_name"] for r in await conn.fetch(
                "SELECT column_name FROM information_schema.columns WHERE table_schema=$1 AND table_name=$2 "
                "ORDER BY ordinal_position", s, t)]
            print(f"\n{s}.{t}: {cols}")
        for rel in ("portfolio.models", "portfolio.model_versions", "portfolio.model_version_positions",
                    "public.rebalance_proposal_trades", "public.execution_instructions", "public.execution_fills",
                    "public.trade_blocks", "public.rebalance_proposals"):
            await show(f"CHECKs {rel}",
                       "SELECT conname, pg_get_constraintdef(oid) d FROM pg_constraint WHERE conrelid=$1::regclass "
                       "AND contype='c'", rel)
        await show("1e suitability-ish tables",
                   "SELECT table_schema, table_name FROM information_schema.tables WHERE table_name ILIKE "
                   "ANY(ARRAY['%suitab%','%margin%','%risk_profile%','%investor_profile%','%borrow%','%short%'])")
        await show("1e suitability-ish columns",
                   "SELECT table_schema, table_name, column_name FROM information_schema.columns WHERE column_name "
                   "ILIKE ANY(ARRAY['%suitab%','%margin%','%options_level%','%risk_tolerance%','%borrow%'])")
        await show("models by source/kind", "SELECT source, count(*) FROM portfolio.models GROUP BY 1")
        await show("positions with negative quantity",
                   "SELECT count(*) FROM portfolio.positions WHERE quantity < 0")
        await show("sides in instructions", "SELECT side, count(*) FROM public.execution_instructions GROUP BY 1")
        await show("sides in proposal trades", "SELECT side, count(*) FROM public.rebalance_proposal_trades GROUP BY 1")
        await show("held-away accounts", "SELECT count(*) FROM public.accounts WHERE is_held_away")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
