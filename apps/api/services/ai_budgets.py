"""LiteLLM Phase G — spend budgets: a per-org monthly cap + a separate
Hollisworks-wide ceiling. Both DEGRADE to the org's safe model at the cap,
never hard-stop (see services.extraction._execute_chain's budget block).

TASK 1 FINDINGS (see docs/LITELLM_INTEGRATION_DESIGN_V1.md §8 for the full
writeup; migrations/litellmphaseg_budgets.sql's own header records the same
four findings against the schema they justify):

  1a. Native LiteLLM budgets (``max_budget``/``budget_duration`` on
      ``/key/generate`` and ``/team/new``) are real, live, CORE (non-
      Enterprise) features — proved live by creating and deleting a real
      budgeted key and a real budgeted team. They do NOT map onto this
      platform: every real AI call authenticates to LiteLLM with the ONE
      shared ``LITELLM_MASTER_KEY`` (D1a/D1b — org isolation is by
      DEPLOYMENT, never by which key calls it), so there is no per-org key
      or team on the wire to attach a native budget to. Even attaching one
      to the master key itself (a real option, for an aggregate cap) would
      still be the wrong SHAPE: LiteLLM's own at-cap behaviour is a hard
      rejection, not a degrade. Native budgets are not used anywhere here.
  1b. STALENESS — the load-bearing compromise. Every number this module
      reads at AI-call time (:func:`is_org_over_cap`,
      :func:`is_platform_over_ceiling`) comes from a CACHE
      (``org_ai_spend_cache`` / ``platform_ai_controls``), refreshed by
      :func:`sync_org_spend` / :func:`sync_platform_spend` — meant to run
      periodically (``apps/api/scripts/sync_ai_spend.py``, target: every 5
      minutes via a Render Cron Job, NOT yet wired to Render — the same
      real, documented gap the workflow scheduler's own tick has). The AI
      call path itself makes ZERO calls to LiteLLM's admin API. Real
      staleness window: up to one sync interval (~5 minutes once the cron
      is deployed) plus LiteLLM's own spend-log flush lag (seconds, per
      design doc §14.1). Accepted deliberately: this is a soft
      billing-threshold control, not a security boundary. A few minutes of
      bounded, small-dollar overage at the tail of a monthly budget is a
      far better trade than a synchronous admin-API round trip on every
      single AI call — which would add real latency to every call and
      create a brand-new failure mode (what happens to an AI call when
      LiteLLM's admin API is slow or unreachable?).
  1c. ``GET /global/spend/tags?start_date=X&end_date=Y`` (confirmed live, a
      real CORE endpoint — ``/global/spend/report`` is Enterprise-only on
      this self-hosted OSS instance, confirmed live via HTTP 400) returns
      spend aggregated PER TAG over a date range:
      ``{"spend_per_tag": [{"name": "org:<uuid>", "spend": ..., "log_count":
      ...}, ...]}``. D1b's attribution tags are genuinely queryable in
      AGGREGATE — see :func:`services.litellm_credentials.spend_by_tag`.
  1d. Per-org budget CONFIG lives in ``org_settings``
      (``ai.budget.monthly_usd`` / ``ai.budget.warning_pct``) — the same
      per-org convention every other ``ai.*`` key uses, which gets
      org_admin-can-write / plain-member-403 for free from the EXISTING
      generic settings PUT (``services.org_settings.set_setting`` /
      ``can_manage_org_settings``) — no new write endpoint. The
      Hollisworks-wide ceiling is genuinely platform-scoped (no org axis),
      so its config extends ``platform_ai_controls`` (Phase F's established
      home for platform-scoped AI controls) with a second row.

DEGRADE, NOT HARD-STOP. Both :func:`is_org_over_cap` and
:func:`is_platform_over_ceiling` are read by
``services.extraction._execute_chain`` to force the org's own safe model
(``ai.model.default``) onto the attempt list — never to refuse the call.
Both are FAIL-OPEN on any error (mirrors ``services.model_catalog.
disabled_model_ids``'s own discipline): a broken budget lookup must never
turn into every AI call in the org degrading or failing.

WHO PAYS DECIDES WHAT COUNTS. A per-org budget counts ALL of that org's
attributable spend (tag ``org:<org_id>``) regardless of credential source —
an org that supplies its own provider key still wants a soft ceiling on its
own total AI usage. The Hollisworks ceiling counts ONLY spend against the
shared platform key (tags ``usage:hollisworks_platform`` +
``usage:platform_on_behalf_of_org``) — an org's OWN key is billed by the
provider directly to that org, never to Hollisworks, so it must never count
against Hollisworks' own ceiling.
"""
from __future__ import annotations

from datetime import date

from services.org_settings import get_setting

BUDGET_MONTHLY_USD_KEY = "ai.budget.monthly_usd"
BUDGET_WARNING_PCT_KEY = "ai.budget.warning_pct"
DEFAULT_WARNING_PCT = 80

PLATFORM_CEILING_KEY = "hollisworks_spend_ceiling"


class AIBudgetError(RuntimeError):
    """The platform ceiling row is missing, or a write was rejected."""


def current_period_start(today: date | None = None) -> date:
    """The current monthly budget period — the first of the current UTC
    month. A plain calendar-month cache key; there is no bi-temporal history
    here (Rule 3 does not apply — this is a cache, not a ledger)."""
    d = today or date.today()
    return d.replace(day=1)


def _org_tag(org_id) -> str:
    return f"org:{org_id}"


# ── per-org budget ───────────────────────────────────────────────────────


async def get_org_budget_config(conn, org_id) -> dict:
    monthly_usd = await get_setting(conn, org_id, BUDGET_MONTHLY_USD_KEY)
    warning_pct = await get_setting(conn, org_id, BUDGET_WARNING_PCT_KEY)
    return {
        "monthly_usd": float(monthly_usd) if monthly_usd is not None else None,
        "warning_pct": float(warning_pct) if warning_pct is not None else DEFAULT_WARNING_PCT,
    }


async def get_org_spend_status(conn, org_id) -> dict:
    """Config + cached current-period spend, for the read endpoint. Never
    refreshes the cache itself — see module docstring on staleness."""
    config = await get_org_budget_config(conn, org_id)
    row = await conn.fetchrow(
        "SELECT period_start, spend_usd, cache_updated_at "
        "FROM org_ai_spend_cache WHERE org_id = $1",
        org_id,
    )
    period = current_period_start()
    if row is None or row["period_start"] != period:
        spend, cache_updated_at = 0.0, None
    else:
        spend, cache_updated_at = float(row["spend_usd"]), row["cache_updated_at"]

    monthly_usd = config["monthly_usd"]
    pct_used = (spend / monthly_usd * 100.0) if monthly_usd else None
    return {
        "period_start": period.isoformat(),
        "spend_usd": spend,
        "monthly_usd": monthly_usd,
        "warning_pct": config["warning_pct"],
        "pct_used": pct_used,
        "over_warning": bool(monthly_usd and pct_used is not None and pct_used >= config["warning_pct"]),
        "over_cap": bool(monthly_usd and spend >= monthly_usd),
        "cache_updated_at": cache_updated_at.isoformat() if cache_updated_at else None,
    }


async def sync_org_spend(conn, org_id) -> dict:
    """Refresh org_id's cached spend for the CURRENT period from LiteLLM's
    live admin API, firing the warning/cap alert AT MOST ONCE PER PERIOD.

    Meant to be called by the periodic sync job
    (apps/api/scripts/sync_ai_spend.py) — NEVER by
    services.extraction._execute_chain, which only ever READS the cache
    (:func:`is_org_over_cap`). This is the one function in this module that
    makes a real HTTP call to LiteLLM's admin API.
    """
    from services.litellm_credentials import spend_by_tag
    from services.workflow_todos import create_budget_cap_alert, create_budget_warning_alert

    period = current_period_start()
    tags = spend_by_tag(period.isoformat(), date.today().isoformat())
    spend = float(tags.get(_org_tag(org_id), {}).get("spend", 0.0))

    existing = await conn.fetchrow(
        "SELECT period_start, warning_alerted_at, cap_alerted_at "
        "FROM org_ai_spend_cache WHERE org_id = $1",
        org_id,
    )
    same_period = existing is not None and existing["period_start"] == period
    warning_alerted_at = existing["warning_alerted_at"] if same_period else None
    cap_alerted_at = existing["cap_alerted_at"] if same_period else None

    await conn.execute(
        """
        INSERT INTO org_ai_spend_cache
            (org_id, period_start, spend_usd, cache_updated_at,
             warning_alerted_at, cap_alerted_at)
        VALUES ($1, $2, $3, now(), $4, $5)
        ON CONFLICT (org_id) DO UPDATE SET
            period_start = EXCLUDED.period_start,
            spend_usd = EXCLUDED.spend_usd,
            cache_updated_at = now(),
            warning_alerted_at = EXCLUDED.warning_alerted_at,
            cap_alerted_at = EXCLUDED.cap_alerted_at
        """,
        org_id, period, spend, warning_alerted_at, cap_alerted_at,
    )

    config = await get_org_budget_config(conn, org_id)
    monthly_usd = config["monthly_usd"]
    if monthly_usd:
        warning_threshold = monthly_usd * config["warning_pct"] / 100.0
        if spend >= monthly_usd and cap_alerted_at is None:
            await create_budget_cap_alert(conn, org_id=org_id, spend_usd=spend, budget_usd=monthly_usd)
            await conn.execute(
                "UPDATE org_ai_spend_cache SET cap_alerted_at = now() WHERE org_id = $1",
                org_id,
            )
        elif spend >= warning_threshold and warning_alerted_at is None:
            await create_budget_warning_alert(
                conn, org_id=org_id, spend_usd=spend, budget_usd=monthly_usd,
                warning_pct=config["warning_pct"],
            )
            await conn.execute(
                "UPDATE org_ai_spend_cache SET warning_alerted_at = now() WHERE org_id = $1",
                org_id,
            )

    return await get_org_spend_status(conn, org_id)


async def is_org_over_cap(org_id) -> bool:
    """Fail-OPEN, cache-only read (zero HTTP calls) — this is what makes it
    safe for services.extraction._execute_chain to call on EVERY AI call.
    Any lookup error, or "never synced this period", resolves to False —
    a broken/cold cache must never itself cause a degrade."""
    if org_id is None:
        return False
    try:
        from services.database import get_pool

        pool = await get_pool()
        async with pool.acquire() as conn:
            monthly_usd = await get_setting(conn, org_id, BUDGET_MONTHLY_USD_KEY)
            if not monthly_usd:
                return False
            row = await conn.fetchrow(
                "SELECT spend_usd, period_start FROM org_ai_spend_cache WHERE org_id = $1",
                org_id,
            )
        if row is None or row["period_start"] != current_period_start():
            return False
        return float(row["spend_usd"]) >= float(monthly_usd)
    except Exception as exc:  # noqa: BLE001
        print(f"is_org_over_cap failed for org {org_id}, treating as under budget: {exc}")
        return False


# ── Hollisworks-wide ceiling ─────────────────────────────────────────────


async def get_platform_ceiling_status(conn) -> dict:
    row = await conn.fetchrow(
        "SELECT enabled, numeric_value, warning_pct, period_start, "
        "cached_spend_usd, cache_updated_at FROM platform_ai_controls "
        "WHERE key = $1",
        PLATFORM_CEILING_KEY,
    )
    if row is None:
        raise AIBudgetError(
            "platform_ai_controls has no 'hollisworks_spend_ceiling' row — "
            "the litellmphaseg migration has not been applied to this "
            "database."
        )
    period = current_period_start()
    if row["period_start"] != period:
        spend, cache_updated_at = 0.0, None
    else:
        spend = float(row["cached_spend_usd"] or 0)
        cache_updated_at = row["cache_updated_at"]

    ceiling = float(row["numeric_value"]) if row["numeric_value"] is not None else None
    warning_pct = float(row["warning_pct"]) if row["warning_pct"] is not None else DEFAULT_WARNING_PCT
    pct_used = (spend / ceiling * 100.0) if ceiling else None
    return {
        "enabled": row["enabled"],
        "ceiling_usd": ceiling,
        "warning_pct": warning_pct,
        "period_start": period.isoformat(),
        "spend_usd": spend,
        "pct_used": pct_used,
        "over_warning": bool(ceiling and pct_used is not None and pct_used >= warning_pct),
        "over_cap": bool(ceiling and spend >= ceiling),
        "cache_updated_at": cache_updated_at.isoformat() if cache_updated_at else None,
    }


async def set_platform_ceiling(conn, *, ceiling_usd: float | None, warning_pct: float | None, updated_by) -> dict:
    """Caller (the router) is responsible for the super_admin app-layer gate
    — RLS is the second, real line of defense on platform_ai_controls'
    UPDATE policy, exactly like services.platform_ai_controls.
    set_force_anthropic_bypass. ``enabled`` tracks whether a ceiling is
    genuinely configured (``ceiling_usd is not None``) — clearing it
    (``ceiling_usd=None``) reverts to "no ceiling", the default state."""
    enabled = ceiling_usd is not None
    row = await conn.fetchrow(
        """
        UPDATE platform_ai_controls
        SET enabled = $2, numeric_value = $3, warning_pct = $4,
            updated_at = now(), updated_by = $5
        WHERE key = $1
        RETURNING enabled, numeric_value, warning_pct, updated_at, updated_by
        """,
        PLATFORM_CEILING_KEY, enabled, ceiling_usd, warning_pct, updated_by,
    )
    if row is None:
        raise AIBudgetError(
            "UPDATE matched zero rows on platform_ai_controls for "
            "'hollisworks_spend_ceiling'. AMBIGUOUS, same as "
            "services.platform_ai_controls.set_force_anthropic_bypass: "
            "EITHER the migration has not been applied, OR this "
            "connection's RLS context lacks super-admin."
        )
    return dict(row)


async def sync_platform_spend(conn) -> dict:
    """Refresh the Hollisworks-wide ceiling's cached spend for the CURRENT
    period, firing the warning/cap alert AT MOST ONCE PER PERIOD. Counts
    ONLY spend against the shared platform key (usage:hollisworks_platform
    + usage:platform_on_behalf_of_org) — an org's own BYOK spend is billed
    to that org, never to Hollisworks, and must never count here."""
    from services.litellm_credentials import spend_by_tag
    from services.workflow_todos import (
        create_platform_ceiling_cap_alert,
        create_platform_ceiling_warning_alert,
    )

    period = current_period_start()
    tags = spend_by_tag(period.isoformat(), date.today().isoformat())
    spend = (
        float(tags.get("usage:hollisworks_platform", {}).get("spend", 0.0))
        + float(tags.get("usage:platform_on_behalf_of_org", {}).get("spend", 0.0))
    )

    existing = await conn.fetchrow(
        "SELECT period_start, warning_alerted_at, cap_alerted_at, enabled, "
        "numeric_value, warning_pct FROM platform_ai_controls WHERE key = $1",
        PLATFORM_CEILING_KEY,
    )
    if existing is None:
        raise AIBudgetError(
            "platform_ai_controls has no 'hollisworks_spend_ceiling' row — "
            "the litellmphaseg migration has not been applied to this "
            "database."
        )
    same_period = existing["period_start"] == period
    warning_alerted_at = existing["warning_alerted_at"] if same_period else None
    cap_alerted_at = existing["cap_alerted_at"] if same_period else None

    await conn.execute(
        """
        UPDATE platform_ai_controls
        SET period_start = $2, cached_spend_usd = $3, cache_updated_at = now(),
            warning_alerted_at = $4, cap_alerted_at = $5
        WHERE key = $1
        """,
        PLATFORM_CEILING_KEY, period, spend, warning_alerted_at, cap_alerted_at,
    )

    ceiling_usd = float(existing["numeric_value"]) if existing["numeric_value"] is not None else None
    warning_pct = float(existing["warning_pct"]) if existing["warning_pct"] is not None else DEFAULT_WARNING_PCT
    if existing["enabled"] and ceiling_usd:
        warning_threshold = ceiling_usd * warning_pct / 100.0
        if spend >= ceiling_usd and cap_alerted_at is None:
            await create_platform_ceiling_cap_alert(conn, spend_usd=spend, ceiling_usd=ceiling_usd)
            await conn.execute(
                "UPDATE platform_ai_controls SET cap_alerted_at = now() WHERE key = $1",
                PLATFORM_CEILING_KEY,
            )
        elif spend >= warning_threshold and warning_alerted_at is None:
            await create_platform_ceiling_warning_alert(
                conn, spend_usd=spend, ceiling_usd=ceiling_usd, warning_pct=warning_pct,
            )
            await conn.execute(
                "UPDATE platform_ai_controls SET warning_alerted_at = now() WHERE key = $1",
                PLATFORM_CEILING_KEY,
            )

    return await get_platform_ceiling_status(conn)


async def is_platform_over_ceiling() -> bool:
    """Fail-OPEN, cache-only read — same discipline as is_org_over_cap."""
    try:
        from services.database import get_pool

        pool = await get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT enabled, numeric_value, cached_spend_usd, period_start "
                "FROM platform_ai_controls WHERE key = $1",
                PLATFORM_CEILING_KEY,
            )
        if row is None or not row["enabled"] or row["numeric_value"] is None:
            return False
        if row["period_start"] != current_period_start():
            return False
        return float(row["cached_spend_usd"] or 0) >= float(row["numeric_value"])
    except Exception as exc:  # noqa: BLE001
        print(f"is_platform_over_ceiling failed, treating as under ceiling: {exc}")
        return False
