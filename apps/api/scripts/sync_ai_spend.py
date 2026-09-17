"""sync_ai_spend.py — LiteLLM Phase G: refresh cached AI spend.

Refreshes every organization's cached AI spend (org_ai_spend_cache) and the
separate Hollisworks-wide ceiling's cache (platform_ai_controls) from
LiteLLM's live admin API (services.ai_budgets.sync_org_spend /
sync_platform_spend), firing the once-per-period warning/cap alerts as
thresholds are crossed.

services.extraction._execute_chain NEVER calls this — it only reads the
cache this script writes (services.ai_budgets.is_org_over_cap /
is_platform_over_ceiling). This script is what keeps that cache fresh.

Meant to run on a schedule — target: every 5 minutes — as a Render Cron
Job. NOT YET WIRED TO RENDER: the same real, documented gap the workflow
scheduler's own tick has (docs/PROJECT_STATUS.md, "Workflow scheduler core
engine" — "Render service NOT yet applied"). Until it is, run manually or
rely on a verify/admin action calling services.ai_budgets directly:

    doppler run -- venv/bin/python apps/api/scripts/sync_ai_spend.py

Hydrates DATABASE_URL / LITELLM_BASE_URL / LITELLM_MASTER_KEY from Doppler
over HTTPS at startup (the verify_litellmphasef.py pattern). Never prints a
credential value.
"""
from __future__ import annotations

import asyncio
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve()
sys.path.insert(0, str(HERE.parent))
from _db_bootstrap import bootstrap_async  # noqa: E402


async def main() -> int:
    db_url = await bootstrap_async(quiet=True)
    if not db_url:
        print("FATAL: no working DATABASE_URL from Doppler.")
        return 2
    import os
    for var in ("LITELLM_BASE_URL", "LITELLM_MASTER_KEY"):
        if not os.environ.get(var):
            print(f"FATAL: {var} not present after Doppler hydration.")
            return 2

    for sp in sorted((HERE.parents[1]).glob("venv/lib/python3*/site-packages")):
        if str(sp) not in sys.path:
            sys.path.insert(0, str(sp))
    api_dir = HERE.parents[1]
    if str(api_dir) not in sys.path:
        sys.path.insert(0, str(api_dir))

    from services.ai_budgets import sync_org_spend, sync_platform_spend
    from services.database import get_pool, platform_scope, reset_rls_context, set_rls_context

    tokens = set_rls_context(None, True)
    ok = True
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            async with platform_scope(conn):
                org_rows = await conn.fetch("SELECT id FROM organizations")
                for r in org_rows:
                    try:
                        status = await sync_org_spend(conn, r["id"])
                        print(f"org {r['id']}: spend=${status['spend_usd']:.4f}")
                    except Exception as exc:  # noqa: BLE001
                        ok = False
                        print(f"org {r['id']}: sync FAILED: {exc}")

                try:
                    platform_status = await sync_platform_spend(conn)
                    print(f"platform ceiling: spend=${platform_status['spend_usd']:.4f} "
                          f"enabled={platform_status['enabled']}")
                except Exception as exc:  # noqa: BLE001
                    ok = False
                    print(f"platform ceiling sync FAILED: {exc}")
    finally:
        reset_rls_context(tokens)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
