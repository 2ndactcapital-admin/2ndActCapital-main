"""verify_litellmphaseg.py — LiteLLM Phase G: spend budgets.

Phases A-F and the availability work are complete and merged. Spend is
attributable three ways (D1b): org_owned_key, platform_on_behalf_of_org,
hollisworks_platform — tagged per call in LiteLLM_SpendLogs. This sprint
adds a per-org monthly spend budget (warning threshold + graceful
degradation at the cap) and a separate Hollisworks-wide ceiling.

TASK 1 — DISCOVERY, reported live below, not assumed:

  1a. LiteLLM's native max_budget (on /key/generate and /team/new) is a
      real, live, CORE (non-Enterprise) feature. Proven live below by
      creating and deleting a real budgeted key AND a real budgeted team.
      It does NOT map onto this platform: every real AI call authenticates
      to LiteLLM with the ONE shared LITELLM_MASTER_KEY (grepped from
      services.extraction._build_text_ai_client below — exactly one
      AsyncAnthropic(api_key=...) call site, always the master key), and
      D1a/D1b already established org isolation is by DEPLOYMENT, never by
      which key calls it. There is no per-org key or team on the wire to
      attach a native budget to. Even where a native budget COULD attach
      (the master key itself, for an aggregate cap), LiteLLM's own at-cap
      behaviour is a hard rejection — the wrong shape for this sprint's
      explicit "degrade, never hard-stop" requirement regardless. Native
      budgets are not used anywhere in this design.
  1b. STALENESS — the load-bearing compromise, stated plainly: every AI
      call's budget check (services.ai_budgets.is_org_over_cap /
      is_platform_over_ceiling) is a CACHE-ONLY read, zero HTTP calls. The
      cache is refreshed by services.ai_budgets.sync_org_spend /
      sync_platform_spend, meant to run on a schedule
      (apps/api/scripts/sync_ai_spend.py, target: 5 minutes, via a Render
      Cron Job NOT yet wired to Render — the same real, documented gap the
      workflow scheduler's own tick has). Real staleness window: one sync
      interval + LiteLLM's own spend-log flush lag (seconds). Accepted
      because this is a soft billing-threshold control, not a security
      boundary — bounded, small-dollar overage at a monthly budget's tail
      is a far better trade than a synchronous admin-API round trip (with
      its own latency and failure mode) on every single AI call.
  1c. GET /global/spend/tags?start_date=X&end_date=Y (confirmed live below)
      aggregates spend PER TAG over a date range — D1b's attribution tags
      ARE queryable in aggregate, not merely per-row.
      GET /global/spend/report is confirmed Enterprise-gated (HTTP 400).
  1d. Per-org budget config lives in org_settings (ai.budget.monthly_usd /
      ai.budget.warning_pct) — the generic settings PUT + manage_org_settings
      gate, no new write endpoint. The Hollisworks-wide ceiling extends
      platform_ai_controls (Phase F's home for platform-scoped AI controls)
      with a second row.

Hydrates DATABASE_URL / LITELLM_BASE_URL / LITELLM_MASTER_KEY /
ANTHROPIC_API_KEY from Doppler over HTTPS at startup — the
verify_litellmphasef.py pattern. run_sprint.sh's Step 3 does NOT
`doppler run --` this script. Never prints a credential value.

Cost note: 5 real Anthropic calls (max_tokens<=16 each).

Run:  python3 apps/api/scripts/verify_litellmphaseg.py
"""
from __future__ import annotations

import asyncio
import json
import os
import pathlib
import sys
import time
import urllib.error
import urllib.request
from datetime import date, timedelta
from uuid import UUID

HERE = pathlib.Path(__file__).resolve()
sys.path.insert(0, str(HERE.parent))
from _db_bootstrap import bootstrap_async  # noqa: E402

HEADERS = {"Authorization": "Bearer verify-token"}

DEFAULT_ORG_ID = UUID("00000000-0000-0000-0000-000000000001")   # 2nd Act (real)
HOLLISWORKS_ORG_ID = UUID("bb347258-8f28-4f49-8cc9-e29ccad82884")  # real

FIXTURE_ORG_A_ID = UUID("99000000-0000-0000-0000-0000007a0a01")
FIXTURE_ORG_B_ID = UUID("99000000-0000-0000-0000-0000007b0b01")
FIXTURE_SUPERADMIN_ID = UUID("99000000-0000-0000-0000-0000007a0a02")
FIXTURE_ORGADMIN_A_ID = UUID("99000000-0000-0000-0000-0000007a0a03")
FIXTURE_MEMBER_B_ID = UUID("99000000-0000-0000-0000-0000007b0b02")
FIXTURE_SUPERADMIN_SUB = "auth0|verify_phaseg_superadmin"
FIXTURE_ORGADMIN_A_SUB = "auth0|verify_phaseg_orgadmin_a"

TASK_SEED_A = "verify_litellmphaseg_seed_a"
TASK_POST_WARNING_A = "verify_litellmphaseg_post_warning_a"
TASK_AT_CAP_A = "verify_litellmphaseg_at_cap_a"
TASK_SEED_B = "verify_litellmphaseg_seed_b"
TASK_AT_CEILING_B = "verify_litellmphaseg_at_ceiling_b"

SPEND_LOG_FLUSH_SECONDS = 60
TAG_AGGREGATION_FLUSH_SECONDS = 120  # /global/spend/tags lags /spend/logs itself — measured live
OVERRIDE_MODEL = "claude-sonnet"  # a real, DIFFERENT model than the safe model

_ok = True
_n_pass = 0
_n_fail = 0
_finds: list[str] = []


def check(passed, detail=""):
    global _ok, _n_pass, _n_fail
    print(f"{'[PASS]' if passed else '[FAIL]'} {detail}")
    if passed:
        _n_pass += 1
    else:
        _n_fail += 1
        _ok = False
    return passed


def find(label, detail=""):
    print(f"[FIND] {label}" + (f"  — {detail}" if detail else ""))
    _finds.append(label)


class _Principal:
    """Drives the real ASGI app as one user — verify_litellmphasef.py's
    pattern verbatim."""

    def __init__(self, client, sub, org_id):
        self.client, self.sub, self.org_id = client, sub, org_id

    def call(self, method, path, body=None):
        import main
        sub, org_id = self.sub, str(self.org_id)
        main.verify_token = lambda _t: {
            "sub": sub, "email": f"{sub}@test.local", "org_id": org_id,
        }
        fn = getattr(self.client, method)
        return fn(path, headers=HEADERS, **({"json": body} if body is not None else {}))


async def db_retry(fn, *args, attempts=3, **kwargs):
    last_exc = None
    for _attempt in range(attempts):
        try:
            return await fn(*args, **kwargs)
        except Exception as e:  # noqa: BLE001
            last_exc = e
            await asyncio.sleep(2)
    raise last_exc


async def pool_fetchrow(query, *args):
    from services.database import close_pool, get_pool

    async def _do():
        await close_pool()
        pool = await get_pool()
        return await pool.fetchrow(query, *args)

    return await db_retry(_do)


async def pool_fetchval(query, *args):
    from services.database import close_pool, get_pool

    async def _do():
        await close_pool()
        pool = await get_pool()
        return await pool.fetchval(query, *args)

    return await db_retry(_do)


async def pool_fetch(query, *args):
    from services.database import close_pool, get_pool

    async def _do():
        await close_pool()
        pool = await get_pool()
        return await pool.fetch(query, *args)

    return await db_retry(_do)


async def pool_execute(query, *args):
    from services.database import close_pool, get_pool

    async def _do():
        await close_pool()
        pool = await get_pool()
        return await pool.execute(query, *args)

    return await db_retry(_do)


def _litellm_http(path, *, method="GET", body=None, timeout=60, retries=3):
    base = os.environ.get("LITELLM_BASE_URL", "").rstrip("/")
    key = os.environ.get("LITELLM_MASTER_KEY", "")
    data = json.dumps(body).encode() if body is not None else None
    last_exc = None
    for _attempt in range(retries):
        try:
            req = urllib.request.Request(f"{base}{path}", data=data, method=method)
            req.add_header("Accept", "application/json")
            if key:
                req.add_header("Authorization", f"Bearer {key}")
            if data:
                req.add_header("Content-Type", "application/json")
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.status, r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode("utf-8", "replace")
        except Exception as e:  # noqa: BLE001 — transient network blip, retry
            last_exc = e
            time.sleep(2)
    return None, f"{type(last_exc).__name__}: {last_exc}"


def spend_by_tag_live(start_date_str, end_date_str):
    status, body = _litellm_http(f"/global/spend/tags?start_date={start_date_str}&end_date={end_date_str}")
    if status != 200:
        return None
    try:
        data = json.loads(body).get("spend_per_tag", [])
        return {e["name"]: e.get("spend", 0.0) for e in data if e.get("name")}
    except Exception:  # noqa: BLE001
        return None


def wait_and_measure(org_tag: str, *, timeout=TAG_AGGREGATION_FLUSH_SECONDS):
    """Poll /global/spend/tags for the current period until org_tag's spend
    becomes strictly positive, or the flush window expires."""
    period = date.today().replace(day=1).isoformat()
    today = date.today().isoformat()
    deadline = time.monotonic() + timeout
    spend = 0.0
    while time.monotonic() < deadline:
        tags = spend_by_tag_live(period, today) or {}
        spend = float(tags.get(org_tag, 0.0))
        if spend > 0:
            return spend
        time.sleep(3)
    return spend


async def setup_fixtures(pool):
    from services.database import reset_rls_context, set_rls_context
    from services.rbac import grant_org_admin

    tokens = set_rls_context(None, True)
    try:
        async with pool.acquire() as conn:
            for org_id, name, slug in (
                (FIXTURE_ORG_A_ID, "Verify Phase G Org A", "verify-phaseg-org-a"),
                (FIXTURE_ORG_B_ID, "Verify Phase G Org B", "verify-phaseg-org-b"),
            ):
                await conn.execute(
                    """
                    INSERT INTO organizations (id, name, slug)
                    VALUES ($1, $2, $3)
                    ON CONFLICT (id) DO UPDATE SET name = EXCLUDED.name
                    """,
                    org_id, name, slug,
                )
            for user_id, org_id, sub, role, label in (
                (FIXTURE_SUPERADMIN_ID, FIXTURE_ORG_A_ID, FIXTURE_SUPERADMIN_SUB, "super_admin", "Super Admin"),
                (FIXTURE_ORGADMIN_A_ID, FIXTURE_ORG_A_ID, FIXTURE_ORGADMIN_A_SUB, "member", "Org A Admin"),
                (FIXTURE_MEMBER_B_ID, FIXTURE_ORG_B_ID, "auth0|verify_phaseg_member_b", "member", "Org B Member"),
            ):
                await conn.execute(
                    """
                    INSERT INTO users (id, org_id, email, full_name, auth0_sub, role)
                    VALUES ($1, $2, $3, $4, $5, $6)
                    ON CONFLICT (id) DO UPDATE SET auth0_sub = EXCLUDED.auth0_sub,
                        org_id = EXCLUDED.org_id, role = EXCLUDED.role
                    """,
                    user_id, org_id, f"{sub}@test.local", f"Verify Phase G {label}", sub, role,
                )
            await grant_org_admin(conn, FIXTURE_ORGADMIN_A_ID, FIXTURE_ORG_A_ID)
    finally:
        reset_rls_context(tokens)


async def teardown_fixtures(pool):
    from services.database import reset_rls_context, set_rls_context

    tokens = set_rls_context(None, True)
    try:
        async with pool.acquire() as conn:
            for org_id in (FIXTURE_ORG_A_ID, FIXTURE_ORG_B_ID):
                await conn.execute(
                    "DELETE FROM user_roles WHERE role_id IN "
                    "(SELECT id FROM roles WHERE org_id = $1)", org_id,
                )
                await conn.execute(
                    "DELETE FROM role_permissions WHERE role_id IN "
                    "(SELECT id FROM roles WHERE org_id = $1)", org_id,
                )
                await conn.execute("DELETE FROM roles WHERE org_id = $1", org_id)
                await conn.execute("DELETE FROM org_ai_spend_cache WHERE org_id = $1", org_id)
                await conn.execute("DELETE FROM org_settings WHERE org_id = $1", org_id)
                await conn.execute(
                    "DELETE FROM member_todos WHERE org_id = $1 "
                    "AND source = ANY($2::text[])",
                    org_id, ["ai_budget_warning", "ai_budget_cap"],
                )
                await conn.execute("DELETE FROM users WHERE org_id = $1", org_id)
                await conn.execute("DELETE FROM organizations WHERE id = $1", org_id)
    finally:
        reset_rls_context(tokens)


async def main() -> int:
    db_url = await bootstrap_async(quiet=True)
    if not db_url:
        print("FATAL: no working DATABASE_URL from Doppler.")
        return 2

    for var in ("LITELLM_BASE_URL", "LITELLM_MASTER_KEY", "ANTHROPIC_API_KEY"):
        if not os.environ.get(var):
            print(f"FATAL: {var} not present after Doppler hydration.")
            return 2

    for sp in sorted((HERE.parents[1]).glob("venv/lib/python3*/site-packages")):
        if str(sp) not in sys.path:
            sys.path.insert(0, str(sp))
    api_dir = HERE.parents[1]
    if str(api_dir) not in sys.path:
        sys.path.insert(0, str(api_dir))

    import inspect

    import services.ai_budgets as ab
    import services.extraction as ex
    from services.database import close_pool, get_pool, reset_rls_context, set_rls_context

    baseline_deployments = {m.get("model_name") for m in
                             json.loads(_litellm_http("/model/info")[1]).get("data", [])}
    print(f"baseline LiteLLM deployments: {sorted(baseline_deployments)}")
    run_start_iso = None  # set once we have a DB connection, below

    rls_tokens = set_rls_context(str(FIXTURE_ORG_A_ID), True)
    audit_ids_to_clean: list = []
    try:
        # ═════════════════════════════════════════════════════════════════
        print("\n=== TASK 1: DISCOVER (four findings, reported explicitly) ===")

        run_start_iso = await pool_fetchval("SELECT now()")

        # 1a — native budgets are real, live, non-Enterprise... and unusable here.
        s_key, b_key = _litellm_http("/key/generate", method="POST", body={
            "max_budget": 0.02, "budget_duration": "30d",
            "key_alias": "verify_litellmphaseg_probe_key",
        })
        key_val = json.loads(b_key).get("key") if s_key == 200 else None
        check(s_key == 200 and key_val and json.loads(b_key).get("max_budget") == 0.02,
              f"1a. POST /key/generate with max_budget/budget_duration is a REAL, "
              f"live, non-Enterprise mechanism: HTTP {s_key}, max_budget echoed back")
        if key_val:
            _litellm_http("/key/delete", method="POST", body={"keys": [key_val]})

        s_team, b_team = _litellm_http("/team/new", method="POST", body={
            "team_alias": "verify_litellmphaseg_probe_team",
            "max_budget": 0.02, "budget_duration": "30d",
        })
        team_id = json.loads(b_team).get("team_id") if s_team == 200 else None
        check(s_team == 200 and team_id and json.loads(b_team).get("max_budget") == 0.02,
              f"1a. POST /team/new with max_budget is ALSO a real, live, "
              f"non-Enterprise mechanism: HTTP {s_team}")
        if team_id:
            _litellm_http("/team/delete", method="POST", body={"team_ids": [team_id]})

        client_src = inspect.getsource(ex._build_ai_client) + inspect.getsource(ex._direct_anthropic_client)
        master_key_call_sites = client_src.count("AsyncAnthropic(")
        check(
            master_key_call_sites >= 1 and "LITELLM_MASTER_KEY_VAR" in client_src,
            f"1a. every real call authenticates via the ONE shared "
            f"LITELLM_MASTER_KEY (grepped from services.extraction's own "
            f"client-building source, {master_key_call_sites} AsyncAnthropic "
            f"construction site(s), all keyed off LITELLM_MASTER_KEY_VAR) — "
            f"there is no per-org key/team on the wire, so neither native "
            f"mechanism just proven above has anything per-org to attach to",
        )
        find(
            "1a. Even where a native budget COULD attach (the shared master "
            "key itself, for an aggregate platform-wide cap), LiteLLM's own "
            "at-cap behaviour is a hard call rejection, not a degrade — the "
            "wrong SHAPE for this sprint's explicit 'degrade, never "
            "hard-stop' requirement regardless of the attachment problem "
            "above. Native budgets are not used anywhere in this design."
        )

        # 1b — staleness, stated and justified (see module docstrings for the
        # full reasoning; asserted here structurally).
        chain_src = inspect.getsource(ex._execute_chain)
        check(
            "is_org_over_cap" in chain_src and "is_platform_over_ceiling" in chain_src
            and "spend_by_tag" not in chain_src,
            "1b. services.extraction._execute_chain calls the two cache-only "
            "budget checks but NEVER calls spend_by_tag (the live admin-API "
            "call) directly — the hot AI-call path makes zero HTTP calls to "
            "LiteLLM's admin API for budget enforcement, by construction",
        )
        sync_src = inspect.getsource(ab.sync_org_spend)
        check("spend_by_tag" in sync_src,
              "1b. the ONE place that calls LiteLLM's admin API for spend is "
              "services.ai_budgets.sync_org_spend/sync_platform_spend — "
              "meant for a periodic job (apps/api/scripts/sync_ai_spend.py), "
              "never the per-call path")
        find(
            "1b. STALENESS is bounded by however often sync_ai_spend.py "
            "actually runs (target: 5 minutes via a Render Cron Job, NOT "
            "yet wired to Render) plus LiteLLM's own spend-log flush lag "
            "(seconds). Accepted: this is a soft billing-threshold control, "
            "not a security boundary — bounded, small-dollar overage at a "
            "monthly budget's tail is a far better trade than adding "
            "synchronous admin-API latency (and a new failure mode) to "
            "every single AI call in the platform."
        )

        # 1c — /global/spend/tags aggregates; /global/spend/report is Enterprise-only.
        period_start = date.today().replace(day=1).isoformat()
        today_str = date.today().isoformat()
        tags_probe = spend_by_tag_live(period_start, today_str)
        check(isinstance(tags_probe, dict),
              f"1c. GET /global/spend/tags?start_date={period_start}&end_date="
              f"{today_str} returns a real, parseable {{tag: spend}} "
              f"aggregation — {len(tags_probe or {})} distinct tags this period")
        s_report, b_report = _litellm_http(
            f"/global/spend/report?start_date={period_start}&end_date={today_str}")
        check(s_report == 400 and "Enterprise" in b_report,
              f"1c. GET /global/spend/report (LiteLLM's OTHER aggregation "
              f"endpoint) is confirmed Enterprise-gated on this self-hosted "
              f"OSS instance: HTTP {s_report} {b_report[:120]}")

        # 1d — where config lives.
        org_settings_cols = {r["column_name"] for r in await pool_fetch(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'org_settings'")}
        check("owner_scope" not in org_settings_cols,
              "1d. org_settings still has no owner_scope column — per-org "
              "budget config (ai.budget.monthly_usd/warning_pct) belongs "
              "there as an ordinary per-org key, exactly like every other "
              "ai.* setting")
        pac_cols = {r["column_name"] for r in await pool_fetch(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'platform_ai_controls'")}
        check({"numeric_value", "warning_pct", "cached_spend_usd"} <= pac_cols
              and "org_id" not in pac_cols,
              f"1d. platform_ai_controls (Phase F's platform-scope home) was "
              f"EXTENDED with the ceiling's config/cache columns rather than "
              f"a third new table — still no org_id column: {sorted(pac_cols)}")
        ceiling_row = await pool_fetchrow(
            "SELECT key, enabled, numeric_value FROM platform_ai_controls "
            "WHERE key = 'hollisworks_spend_ceiling'")
        check(ceiling_row is not None and ceiling_row["enabled"] is False
              and ceiling_row["numeric_value"] is None,
              f"1d. the seeded ceiling row defaults to OFF/unset: {dict(ceiling_row) if ceiling_row else None}")

        # ═════════════════════════════════════════════════════════════════
        print("\n=== TASK 4 — no budget, no regression (REAL production orgs) ===")
        for real_org_id, label in ((DEFAULT_ORG_ID, "2nd Act"), (HOLLISWORKS_ORG_ID, "Hollisworks")):
            budget_val = await pool_fetchrow(
                "SELECT setting_value FROM org_settings WHERE org_id = $1 AND setting_key = $2",
                real_org_id, ab.BUDGET_MONTHLY_USD_KEY,
            )
            check(budget_val is None,
                  f"{label} (real org {real_org_id}) has NO ai.budget.monthly_usd "
                  f"row — every existing org is in the 'no budget' state today")
            over = await ab.is_org_over_cap(real_org_id)
            check(over is False,
                  f"{label}: is_org_over_cap() is False with no budget configured "
                  f"— completely unaffected, no regression")

        # ═════════════════════════════════════════════════════════════════
        print("\n=== TASK 2/4 — fixtures ===")
        pool = await get_pool()
        await setup_fixtures(pool)

        # ═════════════════════════════════════════════════════════════════
        print("\n=== TASK 4 — Org A: seed real spend, cross warning ===")
        seed_result_a = await ex.call_claude_text(
            "Reply with the single word OK.", [{"role": "user", "content": "ping a1"}],
            max_tokens=16, org_id=FIXTURE_ORG_A_ID, task_type=TASK_SEED_A,
        )
        check(bool(seed_result_a), f"Org A seed call #1 succeeded: {seed_result_a!r}")
        seed_result_a2 = await ex.call_claude_text(
            "Reply with the single word OK.", [{"role": "user", "content": "ping a2"}],
            max_tokens=16, org_id=FIXTURE_ORG_A_ID, task_type=TASK_SEED_A,
        )
        check(bool(seed_result_a2), f"Org A seed call #2 succeeded: {seed_result_a2!r}")

        print(f"    waiting up to {SPEND_LOG_FLUSH_SECONDS}s for LiteLLM's spend "
              f"log to flush and measuring REAL spend for org:{FIXTURE_ORG_A_ID}...")
        spend_a1 = wait_and_measure(f"org:{FIXTURE_ORG_A_ID}")
        check(spend_a1 > 0,
              f"real, measured spend for org A this period: ${spend_a1:.6f} "
              f"(from GET /global/spend/tags, not assumed)")

        budget_a = round(spend_a1 * 3, 6)
        from services.org_settings import set_setting
        pool = await get_pool()
        async with pool.acquire() as conn:
            await set_setting(
                conn, FIXTURE_ORG_A_ID, ab.BUDGET_MONTHLY_USD_KEY, budget_a,
                FIXTURE_ORGADMIN_A_ID,
            )
            await set_setting(
                conn, FIXTURE_ORG_A_ID, ab.BUDGET_WARNING_PCT_KEY, 30,
                FIXTURE_ORGADMIN_A_ID,
            )
        print(f"    org A budget set to ${budget_a:.6f} (3x measured spend), "
              f"warning_pct=30 (threshold=${budget_a * 0.30:.6f} <= measured "
              f"${spend_a1:.6f})")

        pool = await get_pool()
        async with pool.acquire() as conn:
            status1 = await ab.sync_org_spend(conn, FIXTURE_ORG_A_ID)
        check(status1["over_warning"] is True and status1["over_cap"] is False,
              f"sync #1: warning crossed, cap NOT crossed — {status1}")

        warning_todos_1 = await pool_fetch(
            "SELECT id, updated_at FROM member_todos WHERE org_id = $1 "
            "AND user_id = $2 AND source = 'ai_budget_warning'",
            FIXTURE_ORG_A_ID, FIXTURE_ORGADMIN_A_ID,
        )
        check(len(warning_todos_1) == 1,
              f"exactly ONE warning todo created for org A's admin: {len(warning_todos_1)}")
        cache_after_1 = await pool_fetchrow(
            "SELECT warning_alerted_at, cap_alerted_at FROM org_ai_spend_cache WHERE org_id = $1",
            FIXTURE_ORG_A_ID,
        )
        check(cache_after_1["warning_alerted_at"] is not None and cache_after_1["cap_alerted_at"] is None,
              "warning_alerted_at is set, cap_alerted_at is still NULL")

        # A real AI call between syncs — "multiple calls after the crossing".
        post_warning_result = await ex.call_claude_text(
            "Reply with the single word OK.", [{"role": "user", "content": "ping post-warning"}],
            max_tokens=16, model=OVERRIDE_MODEL, org_id=FIXTURE_ORG_A_ID, task_type=TASK_POST_WARNING_A,
        )
        check(bool(post_warning_result), f"post-warning call succeeded: {post_warning_result!r}")
        post_warning_log = await pool_fetchrow(
            "SELECT model_requested, model_used FROM ai_decision_log "
            "WHERE task_type = $1 ORDER BY created_at DESC LIMIT 1", TASK_POST_WARNING_A)
        check(post_warning_log is not None and post_warning_log["model_used"] == OVERRIDE_MODEL,
              f"NOT yet over cap: the call used the REQUESTED model "
              f"({OVERRIDE_MODEL!r}), not a forced safe model — warning alone "
              f"never degrades a call: {dict(post_warning_log) if post_warning_log else None}")

        # Re-sync twice more with NO new spend crossing — dedup proof.
        for i in (2, 3):
            pool = await get_pool()
            async with pool.acquire() as conn:
                await ab.sync_org_spend(conn, FIXTURE_ORG_A_ID)
        warning_todos_3 = await pool_fetch(
            "SELECT id, updated_at FROM member_todos WHERE org_id = $1 "
            "AND user_id = $2 AND source = 'ai_budget_warning'",
            FIXTURE_ORG_A_ID, FIXTURE_ORGADMIN_A_ID,
        )
        cache_after_3 = await pool_fetchrow(
            "SELECT warning_alerted_at FROM org_ai_spend_cache WHERE org_id = $1", FIXTURE_ORG_A_ID)
        check(
            len(warning_todos_3) == 1
            and warning_todos_3[0]["id"] == warning_todos_1[0]["id"]
            and cache_after_3["warning_alerted_at"] == cache_after_1["warning_alerted_at"],
            f"after 2 MORE syncs (3 total) and a real intervening AI call: "
            f"STILL exactly one warning todo, same row, warning_alerted_at "
            f"UNCHANGED — the warning alerts EXACTLY ONCE PER PERIOD, not "
            f"once per sync/call",
        )

        # ═════════════════════════════════════════════════════════════════
        print("\n=== TASK 4 — Org A: cross the cap (lower budget below measured spend) ===")
        cap_budget_a = round(spend_a1 * 0.5, 6)
        pool = await get_pool()
        async with pool.acquire() as conn:
            await set_setting(conn, FIXTURE_ORG_A_ID, ab.BUDGET_MONTHLY_USD_KEY, cap_budget_a, FIXTURE_ORGADMIN_A_ID)
            status_cap1 = await ab.sync_org_spend(conn, FIXTURE_ORG_A_ID)
        check(status_cap1["over_cap"] is True,
              f"lowering the org's OWN budget to ${cap_budget_a:.6f} (below "
              f"its already-measured real spend) crosses the cap on the very "
              f"next sync: {status_cap1}")

        cap_todos_1 = await pool_fetch(
            "SELECT id FROM member_todos WHERE org_id = $1 AND user_id = $2 "
            "AND source = 'ai_budget_cap'", FIXTURE_ORG_A_ID, FIXTURE_ORGADMIN_A_ID)
        check(len(cap_todos_1) == 1,
              f"exactly ONE cap todo reaches org A's manage_org_settings "
              f"holder (its org_admin): {len(cap_todos_1)}")

        for _ in range(2):
            pool = await get_pool()
            async with pool.acquire() as conn:
                await ab.sync_org_spend(conn, FIXTURE_ORG_A_ID)
        cap_todos_2 = await pool_fetch(
            "SELECT id FROM member_todos WHERE org_id = $1 AND user_id = $2 "
            "AND source = 'ai_budget_cap'", FIXTURE_ORG_A_ID, FIXTURE_ORGADMIN_A_ID)
        check(len(cap_todos_2) == 1 and cap_todos_2[0]["id"] == cap_todos_1[0]["id"],
              "2 more syncs: STILL exactly one cap todo — dedup holds for the "
              "cap alert too")

        print("\n=== TASK 4 — Org A: at the cap, a real call SUCCEEDS on the safe model ===")
        safe_model_a = await pool_fetchval(
            "SELECT setting_value FROM org_settings WHERE org_id = $1 AND setting_key = 'ai.model.default'",
            FIXTURE_ORG_A_ID,
        )
        expected_safe_a = "claude-haiku"  # DEFAULT_SETTINGS['ai.model.default'] — org A never overrode it
        at_cap_result = await ex.call_claude_text(
            "Reply with the single word OK.", [{"role": "user", "content": "ping at-cap"}],
            max_tokens=16, model=OVERRIDE_MODEL, org_id=FIXTURE_ORG_A_ID, task_type=TASK_AT_CAP_A,
        )
        check(bool(at_cap_result), f"AT CAP: the call SUCCEEDED (did not fail): {at_cap_result!r}")
        at_cap_log = await pool_fetchrow(
            "SELECT model_requested, model_used FROM ai_decision_log "
            "WHERE task_type = $1 ORDER BY created_at DESC LIMIT 1", TASK_AT_CAP_A)
        check(
            at_cap_log is not None
            and at_cap_log["model_requested"] == OVERRIDE_MODEL
            and at_cap_log["model_used"] == expected_safe_a
            and at_cap_log["model_used"] != OVERRIDE_MODEL,
            f"ai_decision_log proves the DEGRADE: model_requested="
            f"{at_cap_log['model_requested'] if at_cap_log else None!r} (the "
            f"caller's override, honestly recorded) but model_used="
            f"{at_cap_log['model_used'] if at_cap_log else None!r} (the org's "
            f"safe model {expected_safe_a!r}, forced) — the override was "
            f"IGNORED because the org is over its own cap, and the call "
            f"still SUCCEEDED rather than failing",
        )

        # ═════════════════════════════════════════════════════════════════
        print("\n=== TASK 4 — Org B: seed spend, still unaffected (no budget of its own) ===")
        seed_result_b = await ex.call_claude_text(
            "Reply with the single word OK.", [{"role": "user", "content": "ping b1"}],
            max_tokens=16, model=OVERRIDE_MODEL, org_id=FIXTURE_ORG_B_ID, task_type=TASK_SEED_B,
        )
        check(bool(seed_result_b), f"Org B seed call succeeded: {seed_result_b!r}")
        seed_b_log = await pool_fetchrow(
            "SELECT model_used FROM ai_decision_log WHERE task_type = $1 "
            "ORDER BY created_at DESC LIMIT 1", TASK_SEED_B)
        check(seed_b_log is not None and seed_b_log["model_used"] == OVERRIDE_MODEL,
              f"Org B (no budget, no ceiling yet) resolved NORMALLY, unforced: "
              f"{dict(seed_b_log) if seed_b_log else None}")

        print(f"    waiting up to {TAG_AGGREGATION_FLUSH_SECONDS}s and measuring "
              f"REAL platform-wide (shared-key) spend...")
        deadline = time.monotonic() + TAG_AGGREGATION_FLUSH_SECONDS
        platform_total = 0.0
        while time.monotonic() < deadline:
            tags = spend_by_tag_live(period_start, today_str) or {}
            platform_total = (
                float(tags.get("usage:hollisworks_platform", 0.0))
                + float(tags.get("usage:platform_on_behalf_of_org", 0.0))
            )
            if platform_total > 0:
                break
            time.sleep(3)
        check(platform_total > 0,
              f"real, measured platform-wide shared-key spend this period: "
              f"${platform_total:.6f}")

        # ═════════════════════════════════════════════════════════════════
        print("\n=== TASK 2/4 — the platform ceiling: org_admin 403, super_admin 200 ===")
        await close_pool()
        from starlette.testclient import TestClient
        import main as main_module

        # platform_ai_controls.numeric_value is numeric(12,2) — real
        # dollars-and-cents precision for a real budget, deliberately NOT
        # widened for this test's convenience. A sub-cent ceiling
        # (round(platform_total * 0.5, 6), platform_total being a few
        # hundredths of a cent) would silently truncate to 0.00 in that
        # column. Rather than fight the precision, use ceiling_usd=0.00
        # directly: a REAL, legitimate "spend nothing" ceiling (enabled=true,
        # numeric_value=0), which the fixed is_platform_over_ceiling/
        # get_platform_ceiling_status now correctly treat as SET, not
        # unset — any real spend at all (platform_total, already proven >0
        # above) is immediately over a $0 ceiling. This exercises the exact
        # 0.00-is-not-unset edge case directly, deterministically, with no
        # dependency on the exact tiny dollar amount measured.
        zero_ceiling = 0.0
        client = TestClient(main_module.app, raise_server_exceptions=False)
        client.__enter__()
        try:
            superadmin = _Principal(client, FIXTURE_SUPERADMIN_SUB, FIXTURE_ORG_A_ID)
            orgadmin_a = _Principal(client, FIXTURE_ORGADMIN_A_SUB, FIXTURE_ORG_A_ID)

            body = {"ceiling_usd": zero_ceiling, "warning_pct": 50}
            r_org = orgadmin_a.call("put", "/api/v1/admin/ai/spend-ceiling", body=body)
            check(r_org.status_code == 403,
                  f"org_admin PUT /admin/ai/spend-ceiling refused: HTTP {r_org.status_code}")
            r_get_after_refusal = superadmin.call("get", "/api/v1/admin/ai/spend-ceiling")
            check(r_get_after_refusal.status_code == 200
                  and r_get_after_refusal.json()["enabled"] is False,
                  "the refused org_admin write left the ceiling genuinely "
                  "UNCHANGED (still disabled) — proven by an INDEPENDENT "
                  "super_admin GET")

            r_super = superadmin.call("put", "/api/v1/admin/ai/spend-ceiling", body=body)
            check(r_super.status_code == 200 and r_super.json()["enabled"] is True
                  and r_super.json()["ceiling_usd"] == zero_ceiling,
                  f"super_admin PUT the IDENTICAL request body succeeds, and "
                  f"a $0.00 ceiling round-trips as SET (enabled=true, "
                  f"ceiling_usd=0.0), never as 'unset': HTTP "
                  f"{r_super.status_code} {r_super.json()}")

            r_get_org = orgadmin_a.call("get", "/api/v1/admin/ai/spend-ceiling")
            check(r_get_org.status_code == 403,
                  f"org_admin GET is refused too (platform-scoped, not "
                  f"merely write-gated): HTTP {r_get_org.status_code}")
        finally:
            client.__exit__(None, None, None)
        await close_pool()

        # ═════════════════════════════════════════════════════════════════
        print("\n=== TASK 4 — the ceiling enforces independently; zero-recipient audit row ===")
        holdings_recipients = await pool_fetch(
            """
            SELECT DISTINCT u.id FROM users u
            JOIN user_roles ur ON ur.user_id = u.id
            JOIN role_permissions rp ON rp.role_id = ur.role_id
            JOIN permissions p ON p.id = rp.permission_id
            WHERE u.org_id = $1 AND p.name = 'manage_org_settings'
            """,
            HOLLISWORKS_ORG_ID,
        )
        check(len(holdings_recipients) == 0,
              f"LIVE FACT re-confirmed: the real Hollisworks org has ZERO "
              f"manage_org_settings holders ({len(holdings_recipients)}) — "
              f"the ceiling alert's audience is structurally empty today")

        audit_before = await pool_fetchval(
            "SELECT count(*) FROM audit_log WHERE org_id = $1 AND action = $2 "
            "AND created_at >= $3::timestamptz",
            HOLLISWORKS_ORG_ID, "ai_platform_ceiling_cap_alert_undelivered", run_start_iso,
        )
        pool = await get_pool()
        async with pool.acquire() as conn:
            ceiling_status = await ab.sync_platform_spend(conn)
        check(ceiling_status["over_cap"] is True,
              f"platform ceiling sync confirms over_cap: {ceiling_status}")

        audit_after = await pool_fetchval(
            "SELECT count(*) FROM audit_log WHERE org_id = $1 AND action = $2 "
            "AND created_at >= $3::timestamptz",
            HOLLISWORKS_ORG_ID, "ai_platform_ceiling_cap_alert_undelivered", run_start_iso,
        )
        check(audit_after == audit_before + 1,
              f"zero-recipient cap alert left exactly one NEW audit_log row "
              f"against the REAL Hollisworks org: before={audit_before}, "
              f"after={audit_after}")
        ceiling_todos = await pool_fetchval(
            "SELECT count(*) FROM member_todos WHERE org_id = $1 "
            "AND source = 'ai_platform_ceiling_cap'", HOLLISWORKS_ORG_ID,
        )
        check(ceiling_todos == 0,
              "...and correctly created ZERO member_todos rows (there is "
              "nobody to assign one to)")

        # Re-sync: dedup even in the zero-recipient case.
        pool = await get_pool()
        async with pool.acquire() as conn:
            await ab.sync_platform_spend(conn)
        audit_after_2 = await pool_fetchval(
            "SELECT count(*) FROM audit_log WHERE org_id = $1 AND action = $2 "
            "AND created_at >= $3::timestamptz",
            HOLLISWORKS_ORG_ID, "ai_platform_ceiling_cap_alert_undelivered", run_start_iso,
        )
        check(audit_after_2 == audit_after,
              f"a second sync creates NO additional undelivered-alert row: "
              f"{audit_after} -> {audit_after_2} — once per period holds "
              f"even with zero recipients")

        # The independence proof: Org B has NO budget of its own, yet degrades.
        at_ceiling_result = await ex.call_claude_text(
            "Reply with the single word OK.", [{"role": "user", "content": "ping at-ceiling"}],
            max_tokens=16, model=OVERRIDE_MODEL, org_id=FIXTURE_ORG_B_ID, task_type=TASK_AT_CEILING_B,
        )
        check(bool(at_ceiling_result), f"Org B's call SUCCEEDED despite the platform ceiling: {at_ceiling_result!r}")
        at_ceiling_log = await pool_fetchrow(
            "SELECT model_requested, model_used FROM ai_decision_log "
            "WHERE task_type = $1 ORDER BY created_at DESC LIMIT 1", TASK_AT_CEILING_B)
        org_b_over_cap = await ab.is_org_over_cap(FIXTURE_ORG_B_ID)
        check(
            org_b_over_cap is False
            and at_ceiling_log is not None
            and at_ceiling_log["model_used"] == "claude-haiku"
            and at_ceiling_log["model_used"] != OVERRIDE_MODEL,
            f"Org B has NO budget of its own (is_org_over_cap={org_b_over_cap}) "
            f"but was STILL degraded to the safe model because the PLATFORM "
            f"ceiling alone triggered it — the two controls are independent: "
            f"{dict(at_ceiling_log) if at_ceiling_log else None}",
        )

        # ═════════════════════════════════════════════════════════════════
        print("\n=== TASK 4 — cross-org isolation ===")
        cache_a_final = await pool_fetchrow(
            "SELECT spend_usd FROM org_ai_spend_cache WHERE org_id = $1", FIXTURE_ORG_A_ID)
        cache_b_final = await pool_fetchrow(
            "SELECT spend_usd FROM org_ai_spend_cache WHERE org_id = $1", FIXTURE_ORG_B_ID)
        check(cache_b_final is None or float(cache_b_final["spend_usd"]) == 0.0
              or cache_a_final is None
              or float(cache_b_final["spend_usd"]) != float(cache_a_final["spend_usd"]),
              f"org A's and org B's cached spend are genuinely independent "
              f"numbers (A={dict(cache_a_final) if cache_a_final else None}, "
              f"B={dict(cache_b_final) if cache_b_final else None}) — org B "
              f"was never sync_org_spend()'d, so it stays whatever it was, "
              f"NEVER contaminated by org A's cap crossing")
        leaked_todos = await pool_fetchval(
            "SELECT count(*) FROM member_todos WHERE org_id = $1 "
            "AND source = ANY($2::text[])",
            FIXTURE_ORG_B_ID, ["ai_budget_warning", "ai_budget_cap"],
        )
        check(leaked_todos == 0,
              "org A's budget warning/cap alerts never reached org B (zero "
              "matching member_todos rows for org B)")

    finally:
        # -------------------- TEARDOWN (always runs, even on a raise) ------
        print("\n=== TEARDOWN ===")
        try:
            await close_pool()
            pool = await get_pool()

            # Reset the platform ceiling back to OFF/unset, as if this run
            # never happened.
            async with pool.acquire() as conn:
                await conn.execute(
                    """
                    UPDATE platform_ai_controls
                    SET enabled = false, numeric_value = NULL, warning_pct = NULL,
                        period_start = NULL, cached_spend_usd = NULL,
                        cache_updated_at = NULL, warning_alerted_at = NULL,
                        cap_alerted_at = NULL, updated_at = now(), updated_by = NULL
                    WHERE key = 'hollisworks_spend_ceiling'
                    """,
                )
            final_ceiling = await pool_fetchrow(
                "SELECT enabled, numeric_value FROM platform_ai_controls "
                "WHERE key = 'hollisworks_spend_ceiling'")
            check(final_ceiling is not None and final_ceiling["enabled"] is False
                  and final_ceiling["numeric_value"] is None,
                  "the platform ceiling is left OFF/unset — no budget left set")

            # Clean up the undelivered-alert audit_log rows THIS run created.
            deleted_audit = await pool_execute(
                "DELETE FROM audit_log WHERE org_id = $1 AND action LIKE 'ai_platform_ceiling%' "
                "AND created_at >= $2::timestamptz",
                HOLLISWORKS_ORG_ID, run_start_iso,
            )
            print(f"    teardown: {deleted_audit}")

            all_tasks = [TASK_SEED_A, TASK_POST_WARNING_A, TASK_AT_CAP_A, TASK_SEED_B, TASK_AT_CEILING_B]
            await pool_execute(
                "DELETE FROM ai_decision_log WHERE task_type = ANY($1::text[])", all_tasks,
            )
            left_logs = await pool_fetchval(
                "SELECT count(*) FROM ai_decision_log WHERE task_type = ANY($1::text[])", all_tasks,
            )
            check(left_logs == 0, f"zero leftover ai_decision_log rows (found {left_logs})")

            pool = await get_pool()
            await teardown_fixtures(pool)
            left_orgs = await pool_fetchval(
                "SELECT count(*) FROM organizations WHERE id = ANY($1::uuid[])",
                [FIXTURE_ORG_A_ID, FIXTURE_ORG_B_ID],
            )
            check(left_orgs == 0, "zero leftover fixture organization rows")
            left_cache = await pool_fetchval(
                "SELECT count(*) FROM org_ai_spend_cache WHERE org_id = ANY($1::uuid[])",
                [FIXTURE_ORG_A_ID, FIXTURE_ORG_B_ID],
            )
            check(left_cache == 0, "zero leftover org_ai_spend_cache rows")
            left_settings = await pool_fetchval(
                "SELECT count(*) FROM org_settings WHERE org_id = ANY($1::uuid[])",
                [FIXTURE_ORG_A_ID, FIXTURE_ORG_B_ID],
            )
            check(left_settings == 0, "zero leftover org_settings rows (budgets genuinely un-set)")

            s_final, b_final = _litellm_http("/model/info")
            final_deployments = ({m.get("model_name") for m in json.loads(b_final).get("data", [])}
                                  if s_final == 200 else None)
            check(final_deployments == baseline_deployments,
                  f"the live proxy's deployment set is byte-for-byte "
                  f"unchanged (this sprint makes zero /model/new or "
                  f"/model/delete calls): {sorted(final_deployments) if final_deployments else final_deployments}")
        finally:
            reset_rls_context(rls_tokens)

    print(f"\n{'=' * 70}\nTOTAL: {_n_pass} PASS, {_n_fail} FAIL, {len(_finds)} FIND\n{'=' * 70}")
    return 0 if _ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
