"""Test orgs — the one place a scheduled sweep asks "is this a verify fixture?"

``organizations.is_test`` (docs/rep6fix2_part1.sql) marks verify fixture orgs.
A trigger sets it for every id in the reserved verify prefix ``99000000-``, and
a verify script may also set it explicitly at insert.

SCHEDULERS NEVER ACT ON A TEST ORG. A verify teardown that crashed once left
its fixture orgs behind, and the live cron (rebalance_proposals.proposals_tick)
scanned their accounts every tick — 854 drift proposals, plus concurrent writes
that deadlocked the next verify's teardown. Every cross-org sweep therefore
filters with :func:`not_test_org_sql` when it enumerates orgs, and every
per-org sweep entry point refuses a test org unless its caller passes an
explicit ``include_test_orgs=True`` — a flag only a verify script passes, for
its own org, calling the function directly (never through the global tick).
"""

from __future__ import annotations

TEST_ORG_ID_PREFIX = "99000000-"


def not_test_org_sql(org_col: str) -> str:
    """A WHERE fragment excluding test orgs; ``org_col`` is a trusted column
    expression (never user input). Needs the organizations row to be visible —
    every sweep enumerates in platform scope or in the org's own context."""
    return f"NOT EXISTS (SELECT 1 FROM public.organizations _o WHERE _o.id = {org_col} AND _o.is_test)"


async def is_test_org(conn, org_id, *, missing: bool = True) -> bool:
    """True for a verify fixture org. An org row the connection cannot read
    (absent, or hidden by RLS) returns ``missing`` — True by default, so a
    scheduler that cannot tell skips the org rather than acting on it."""
    if org_id is None:
        return missing
    flag = await conn.fetchval("SELECT is_test FROM public.organizations WHERE id = $1::uuid", str(org_id))
    return missing if flag is None else bool(flag)
