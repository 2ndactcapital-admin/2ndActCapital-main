"""LiteLLM Phase F — Hollisworks-only force-Anthropic emergency bypass.

This is the admin-facing control layer for the ALREADY-EXISTING rollback
mechanism (``services.extraction.LITELLM_DISABLE_VAR`` /
``resolve_transport``). Phase F does not build a second way to call Anthropic
directly — it adds a second, DB-backed DRIVER for the exact same
``TRANSPORT_ANTHROPIC`` branch ``_build_ai_client`` already has. See
``services.extraction.resolve_text_transport`` for how the two drivers
combine (the env var always wins first — it must keep working when the
database itself is unhappy, per its own docstring — this platform toggle is
checked only when the env var is NOT engaged).

SCOPE, deliberately narrow: TEXT calls only. Voyage embeddings have no
direct-Anthropic equivalent, so this table is read ONLY from
``services.extraction``'s text call path, never from
``services.document_embedding``. Embeddings keep routing through LiteLLM
regardless of this flag — see that module's own docstring for the full
reasoning.

``platform_ai_controls`` is genuinely platform-scoped (CLAUDE.md Rule 6): no
org_id column at all, same convention as ``platform_model_catalog``
(services.model_catalog) — org_settings itself has no owner_scope column and
no platform-scope row is possible there (services.litellm_credentials'
own docstring), so a real platform-wide toggle needs its own table, not an
org_settings key stored against the default org as a stand-in.

Reads are open to any caller (RLS: ``USING (true)``) because every real AI
text call must read this at call time regardless of who is calling
(background jobs, cron, an ordinary org member's request) — there is no
org axis to restrict here. Writes are super_admin only, enforced BOTH at
the RLS layer (migration litellmphasef_force_anthropic_bypass.sql) and here/
the router (defense in depth, the same precedent services.model_catalog's
platform_model_catalog already established).
"""
from __future__ import annotations

FORCE_ANTHROPIC_BYPASS_KEY = "force_anthropic_bypass"


class PlatformAIControlError(RuntimeError):
    """A control row is missing, or a write was rejected."""


async def is_force_anthropic_bypass_enabled() -> bool:
    """Fail-SAFE read: any DB error (or a missing row) resolves to False.

    Mirrors ``services.extraction.resolve_model``'s own fail-to-default
    discipline — a broken read must never accidentally engage a platform-
    wide emergency bypass; it must only ever fail toward the platform's
    normal (LiteLLM-routed) behaviour.
    """
    try:
        from services.database import get_pool

        pool = await get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT enabled FROM platform_ai_controls WHERE key = $1",
                FORCE_ANTHROPIC_BYPASS_KEY,
            )
        return bool(row and row["enabled"])
    except Exception as exc:
        print(f"[platform_ai_controls] read failed, treating bypass as OFF: {exc}")
        return False


async def get_force_anthropic_bypass(conn) -> dict:
    """The real row, for the admin GET endpoint. Raises if it is missing —
    that would mean the migration never ran, a real deployment gap worth
    surfacing loudly rather than papering over with an invented default."""
    row = await conn.fetchrow(
        "SELECT key, enabled, updated_at, updated_by FROM platform_ai_controls "
        "WHERE key = $1",
        FORCE_ANTHROPIC_BYPASS_KEY,
    )
    if row is None:
        raise PlatformAIControlError(
            "platform_ai_controls has no 'force_anthropic_bypass' row — the "
            "litellmphasef migration has not been applied to this database."
        )
    return dict(row)


async def set_force_anthropic_bypass(conn, *, enabled: bool, updated_by) -> dict:
    """Toggle the bypass. Caller (the router) is responsible for the
    super_admin app-layer gate — this function is the second, RLS-layer
    line of defense (WITH CHECK on the UPDATE policy), never the only one.

    A real HTTP request through the router already has the right RLS
    context (rls_context_middleware sets app.is_super_admin from the
    caller's own principal). A caller OUTSIDE that request lifecycle — a
    script, a REPL — must set it explicitly first
    (services.database.set_rls_context(None, True)); this function does
    NOT set it itself, so it stays a pure pass-through of whatever context
    the caller already established, never a silent privilege escalation.
    """
    row = await conn.fetchrow(
        """
        UPDATE platform_ai_controls
        SET enabled = $2, updated_at = now(), updated_by = $3
        WHERE key = $1
        RETURNING key, enabled, updated_at, updated_by
        """,
        FORCE_ANTHROPIC_BYPASS_KEY, enabled, updated_by,
    )
    if row is None:
        raise PlatformAIControlError(
            "UPDATE matched zero rows on platform_ai_controls. This is "
            "AMBIGUOUS and this function cannot tell which is true: EITHER "
            "the 'force_anthropic_bypass' row does not exist (the "
            "litellmphasef migration has not been applied to this "
            "database), OR the row exists but this connection's RLS "
            "context lacks super-admin (app.is_super_admin != 'true') — "
            "the UPDATE policy silently matches zero rows in that case "
            "too, identically. Check pg_policies / app.is_super_admin "
            "before assuming the migration is missing."
        )
    return dict(row)
