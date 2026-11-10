"""_verify_scope — count LIVE-WRITTEN tables by FIXTURE ids only (VERIFYFIX1; docs/VERIFY_RULES.md rule 6).

WHY
---
26 regression-set verifies compared a WHOLE-table ``count(*)`` of ``public.audit_log`` (and other tables real
activity writes to) before and after their run. The nightly portfolio refresh (~07:00 UTC), the scheduler, a real
sign-in or a person using the app writes real rows mid-run, and the verify then reported them as its own leftovers
(visibility1 fix 1: 778 → 780 from two real 'manual_asset_relinked' rows). staffroles1 / the cash track count those
tables over fixture rows only; this module is that rule, shared.

WHAT A CHECK KEEPS
------------------
"<table> back at its pre-test count" still proves the run left no row behind and removed what it made — over the
rows that CAN be the verify's: a fixture org's, or written by / for a fixture user. A real row is not the verify's
to count (the real-row fingerprints prove real data unchanged).

``count_sql(table, orgs, users)`` returns the statement: scoped for a table in LIVE_WRITTEN, the whole table for any
other (a table nothing live writes keeps its whole-table count — the stronger check). Ids are validated as UUIDs
and inlined as a literal ``uuid[]``, so the statement takes no parameters and drops into any existing
``platform(conn, sql, fetch="fetchval")`` call unchanged.

Self-contained: stdlib only.
"""
from __future__ import annotations

from typing import Iterable
from uuid import UUID

#: table -> (org column, user columns). Columns from docs/schema_snapshot.sql (2026-11-10).
LIVE_WRITTEN: dict[str, tuple[str | None, tuple[str, ...]]] = {
    "public.audit_log": ("org_id", ("user_id",)),
    "public.sod_decisions": ("org_id", ("checker_id",)),
    "public.payment_events": ("org_id", ("actor_id",)),
    "public.workflow_runs": ("org_id", ("started_by",)),
    "public.workflow_run_steps": ("org_id", ()),
    "public.domain_events": ("org_id", ("created_by",)),
    "public.member_todos": ("org_id", ("user_id",)),
    "public.notifications": ("org_id", ("created_by",)),
    "public.ai_decision_log": ("org_id", ()),
    "public.user_identities": ("org_id", ("user_id",)),
    "public.assistant_conversations": ("org_id", ("user_id",)),
    "public.ai_daily_runs": (None, ("started_by",)),
}

#: the three the regression gate names explicitly (verify_verifyfix1 (3)).
GATED = ("public.audit_log", "public.sod_decisions", "public.payment_events")


def qualify(table: str) -> str:
    t = table.strip().strip('"')
    return t if "." in t else f"public.{t}"


def is_live_written(table: str) -> bool:
    return qualify(table) in LIVE_WRITTEN


def _arr(ids: Iterable) -> str:
    vals = [str(UUID(str(i))) for i in ids]  # raises on anything that is not a uuid — never inlines free text
    return "'{" + ",".join(vals) + "}'::uuid[]"


def predicate(table: str, orgs: Iterable, users: Iterable = (), extra: str | None = None) -> str:
    """The fixture-only WHERE predicate for a live-written table (KeyError for any other table)."""
    org_col, user_cols = LIVE_WRITTEN[qualify(table)]
    orgs, users = list(orgs or ()), list(users or ())
    parts = []
    if org_col and orgs:
        parts.append(f"{org_col} = ANY({_arr(orgs)})")
    if users:
        parts.extend(f"{c} = ANY({_arr(users)})" for c in user_cols)
    if extra:
        parts.append(f"({extra})")
    if not parts:
        raise ValueError(f"no fixture ids to scope {table} by")
    return " OR ".join(parts)


def count_sql(table: str, orgs: Iterable, users: Iterable = (), extra: str | None = None) -> str:
    """``SELECT count(*)`` over the fixture rows of a live-written table; over the whole table otherwise."""
    t = qualify(table)
    if t in LIVE_WRITTEN:
        return f"SELECT count(*) FROM {table} WHERE {predicate(t, orgs, users, extra)}"
    return f"SELECT count(*) FROM {table}"
