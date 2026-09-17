"""verify_agenticmakerchecker.py — agentic substrate: maker-checker rule +
escalation enum wiring (agenticmakerchecker.structural).

Builds the two genuinely-unblocked items from the 15-item agentic design
(docs/CROSS_PROJECT_STATUS_CONSOLIDATED.md). Item #3 (capability annotation
on the action registry) is explicitly NOT in scope.

TASK 1 — DISCOVERY, reported live below (re-confirmed here, not assumed
carried over from the sprint that made the schema changes):

  1a. permissions.name convention is verb_resource, backed by a real
      (resource, action) pair — confirmed against all 32 pre-existing rows
      plus the one this sprint added. No existing permission covered "may
      check an agent's proposal" before this sprint.
  1b. No generic agent-proposal table existed. The prior agenticdiscovery.
      lowrisk sprint (docs/AGENTIC_SUBSTRATE_DISCOVERY.md Task 4) already
      ruled out the three real candidates: assistant_activities (right
      shape, zero rows, HELD/unwired, purpose-built for a member confirming
      their OWN assistant's action rather than a permission-gated third
      party), member_todos (real data, but a flat per-user queue with no
      reviewer-eligibility concept), workflow_run_steps (same shape as
      assistant_activities, zero rows). agent_proposals is new.
  1c. compliance_sr / compliance_jr had exactly one real code reference
      (routers/marketplace.py's _safe_notify_roles compliance-override
      list) plus one dead role-keyed lookup table
      (apps/web/components/assistant/AssistantPanel.jsx's ROLE_POSTURE —
      found DURING this sprint, not in the prior discovery pass, since
      users.role never actually held either literal). Both repointed at
      `compliance`. Confirmed live (below): both roles held zero
      role_permissions grants and zero user_roles holders before deletion.

WHAT THIS PROVES:
  - Task 1's three findings, reported live against the CURRENT deployed
    state (queried fresh by this script, not copy-pasted from the sprint).
  - compliance_sr / compliance_jr are gone; `compliance` exists in their
    place; no source file outside this script's own docstring and the
    migration/discovery-doc historical record still names the old roles.
  - review_agent_proposals exists and is held by EXACTLY the six intended
    roles (set equality — an accidental extra grant fails as loudly as a
    missing one) — never Hollis's role (member), which has none.
  - is_eligible_reviewer (the real function, not a re-implementation)
    returns True for a permission holder who is not the maker.
  - is_eligible_reviewer returns False for that SAME permission holder when
    they ARE the maker — the whole rule — proven by flipping only the
    maker/reviewer relationship, holding the permission grant constant.
  - is_eligible_reviewer returns False for a user with no roles at all
    (proving this deliberately does NOT inherit rbac.has_permission's
    single-admin default-allow bootstrap) and for a user holding an
    unrelated permission.
  - Cross-org: a permission holder in fixture org B cannot check fixture
    org A's proposal.
  - agent_proposals has a policy for every one of the four operations, and
    a real UPDATE through review_proposal() persists — re-read from an
    INDEPENDENT connection, not the one that wrote it.
  - The database's OWN CHECK constraint (agent_proposals_maker_checker_chk)
    refuses a self-check even when services.agent_proposals is bypassed
    entirely (a raw UPDATE).
  - escalation_reason is wired on a real, reachable column, using the
    pre-existing enum, and rejects a bogus value.
  - No regression: a permission untouched by this sprint (view_workflow_runs)
    still resolves exactly as before, by count and by a live has_permission
    call.
  - Teardown: zero leftover fixture rows.

WHAT THIS DELIBERATELY DOES NOT PROVE: that any agent produces a real
proposal in production — no agent-run table exists yet (Workflow Manager
Wave 2 is unbuilt), so this is substrate only, exercised here via fixtures,
not real traffic.

Hydrates DATABASE_URL from Doppler over HTTPS at startup (the
verify_litellmphaseg.py pattern). Never prints a credential value.

Run:  python3 apps/api/scripts/verify_agenticmakerchecker.py
"""
from __future__ import annotations

import asyncio
import pathlib
import sys
from uuid import UUID

HERE = pathlib.Path(__file__).resolve()
sys.path.insert(0, str(HERE.parent))
from _db_bootstrap import bootstrap_async  # noqa: E402

import asyncpg  # noqa: E402

REPO_ROOT = HERE.parents[3]

ORG_REAL = UUID("00000000-0000-0000-0000-000000000001")  # 2nd Act Capital

REVIEW_PERMISSION = "review_agent_proposals"
EXPECTED_GRANTED_ROLES = {
    "support_staff", "advisor", "investment_committee", "fund_finance",
    "compliance", "org_admin",
}
STALE_ROLE_NAMES = ("compliance_sr", "compliance_jr")

# ── Fixtures — two orgs, so cross-org isolation is a genuine, non-bypassing
# proof rather than a same-org coincidence. Real roles only exist in
# ORG_REAL today (Hollisworks has zero), so cross-org eligibility needs its
# own fixture roles/grants independent of production data.
FIXTURE_ORG_A = UUID("99000000-0000-0000-0000-0000ac0a0001")
FIXTURE_ORG_B = UUID("99000000-0000-0000-0000-0000ac0b0001")
FIXTURE_ROLE_REVIEWER_A = UUID("99000000-0000-0000-0000-0000ac0a2001")
FIXTURE_ROLE_REVIEWER_B = UUID("99000000-0000-0000-0000-0000ac0b2001")
FIXTURE_ROLE_OTHERPERM_A = UUID("99000000-0000-0000-0000-0000ac0a2002")

FIXTURE_MAKER_A = UUID("99000000-0000-0000-0000-0000ac0a1001")       # proposes, ALSO holds the review perm
FIXTURE_REVIEWER_A = UUID("99000000-0000-0000-0000-0000ac0a1002")    # holds the review perm, not the maker
FIXTURE_NONHOLDER_A = UUID("99000000-0000-0000-0000-0000ac0a1003")   # zero roles at all
FIXTURE_OTHERPERM_A = UUID("99000000-0000-0000-0000-0000ac0a1004")   # holds an unrelated permission
FIXTURE_REVIEWER_B = UUID("99000000-0000-0000-0000-0000ac0b1001")    # holds the review perm, but in org B

ALL_FIXTURE_USERS = [
    FIXTURE_MAKER_A, FIXTURE_REVIEWER_A, FIXTURE_NONHOLDER_A,
    FIXTURE_OTHERPERM_A, FIXTURE_REVIEWER_B,
]
ALL_FIXTURE_ORGS = [FIXTURE_ORG_A, FIXTURE_ORG_B]
ALL_FIXTURE_ROLES = [FIXTURE_ROLE_REVIEWER_A, FIXTURE_ROLE_REVIEWER_B, FIXTURE_ROLE_OTHERPERM_A]

FIXTURE_PROPOSAL_MAIN = UUID("99000000-0000-0000-0000-0000ac0a3001")   # the real review_proposal() path
FIXTURE_PROPOSAL_SELFCHECK = UUID("99000000-0000-0000-0000-0000ac0a3002")  # for the raw-UPDATE CHECK-constraint proof
FIXTURE_PROPOSAL_ESCALATED = UUID("99000000-0000-0000-0000-0000ac0a3003")  # escalation_reason wiring proof

SUB = {
    FIXTURE_MAKER_A: "auth0|agenticmc_maker_a",
    FIXTURE_REVIEWER_A: "auth0|agenticmc_reviewer_a",
    FIXTURE_NONHOLDER_A: "auth0|agenticmc_nonholder_a",
    FIXTURE_OTHERPERM_A: "auth0|agenticmc_otherperm_a",
    FIXTURE_REVIEWER_B: "auth0|agenticmc_reviewer_b",
}

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


# ═══════════════════════════════════════════════════════════════════════════
# Fixture setup / teardown — raw connection, explicit is_super_admin GUC per
# transaction (this connection is not shared with anything else, but SET
# LOCAL inside an explicit transaction is used throughout regardless, per
# CLAUDE.md's platform_scope() convention, never a bare session-level SET).
# ═══════════════════════════════════════════════════════════════════════════
async def teardown(conn):
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
        await conn.execute(
            "DELETE FROM agent_proposals WHERE org_id = ANY($1::uuid[])", ALL_FIXTURE_ORGS
        )
        await conn.execute(
            "DELETE FROM user_roles WHERE user_id = ANY($1::uuid[])", ALL_FIXTURE_USERS
        )
        await conn.execute(
            "DELETE FROM role_permissions WHERE role_id = ANY($1::uuid[])", ALL_FIXTURE_ROLES
        )
        await conn.execute(
            "DELETE FROM roles WHERE id = ANY($1::uuid[])", ALL_FIXTURE_ROLES
        )
        await conn.execute(
            "DELETE FROM users WHERE id = ANY($1::uuid[])", ALL_FIXTURE_USERS
        )
        await conn.execute(
            "DELETE FROM organizations WHERE id = ANY($1::uuid[])", ALL_FIXTURE_ORGS
        )


async def seed(conn):
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")

        for org_id, slug in ((FIXTURE_ORG_A, "agenticmc-fixture-a"), (FIXTURE_ORG_B, "agenticmc-fixture-b")):
            await conn.execute(
                """INSERT INTO organizations (id, name, slug) VALUES ($1, $2, $3)
                   ON CONFLICT (id) DO NOTHING""",
                org_id, f"AgenticMC Fixture Org {slug[-1].upper()}", slug,
            )

        review_perm_id = await conn.fetchval(
            "SELECT id FROM permissions WHERE name = $1", REVIEW_PERMISSION
        )
        other_perm_id = await conn.fetchval(
            "SELECT id FROM permissions WHERE name = $1", "view_workflow_runs"
        )

        await conn.execute(
            """INSERT INTO roles (id, org_id, name, description)
               VALUES ($1, $2, 'agenticmc_reviewer', 'verify fixture')
               ON CONFLICT (id) DO NOTHING""",
            FIXTURE_ROLE_REVIEWER_A, FIXTURE_ORG_A,
        )
        await conn.execute(
            """INSERT INTO roles (id, org_id, name, description)
               VALUES ($1, $2, 'agenticmc_reviewer', 'verify fixture')
               ON CONFLICT (id) DO NOTHING""",
            FIXTURE_ROLE_REVIEWER_B, FIXTURE_ORG_B,
        )
        await conn.execute(
            """INSERT INTO role_permissions (role_id, permission_id) VALUES ($1, $2)
               ON CONFLICT DO NOTHING""",
            FIXTURE_ROLE_REVIEWER_A, review_perm_id,
        )
        await conn.execute(
            """INSERT INTO role_permissions (role_id, permission_id) VALUES ($1, $2)
               ON CONFLICT DO NOTHING""",
            FIXTURE_ROLE_REVIEWER_B, review_perm_id,
        )

        async def mk_user(uid, org_id):
            sub = SUB[uid]
            await conn.execute(
                """INSERT INTO users (id, org_id, email, full_name, auth0_sub, role, is_active)
                   VALUES ($1, $2, $3, $4, $5, 'member', true)
                   ON CONFLICT (id) DO UPDATE SET org_id = EXCLUDED.org_id""",
                uid, org_id, f"{sub.split('|')[1]}@test.local", sub, sub,
            )

        await mk_user(FIXTURE_MAKER_A, FIXTURE_ORG_A)
        await mk_user(FIXTURE_REVIEWER_A, FIXTURE_ORG_A)
        await mk_user(FIXTURE_NONHOLDER_A, FIXTURE_ORG_A)
        await mk_user(FIXTURE_OTHERPERM_A, FIXTURE_ORG_A)
        await mk_user(FIXTURE_REVIEWER_B, FIXTURE_ORG_B)

        # The MAKER also holds the review permission — this is the whole
        # point of Task 4's proof: holding the permission is not enough.
        await conn.execute(
            "INSERT INTO user_roles (user_id, role_id) VALUES ($1, $2) ON CONFLICT DO NOTHING",
            FIXTURE_MAKER_A, FIXTURE_ROLE_REVIEWER_A,
        )
        await conn.execute(
            "INSERT INTO user_roles (user_id, role_id) VALUES ($1, $2) ON CONFLICT DO NOTHING",
            FIXTURE_REVIEWER_A, FIXTURE_ROLE_REVIEWER_A,
        )
        await conn.execute(
            "INSERT INTO user_roles (user_id, role_id) VALUES ($1, $2) ON CONFLICT DO NOTHING",
            FIXTURE_REVIEWER_B, FIXTURE_ROLE_REVIEWER_B,
        )
        # FIXTURE_OTHERPERM_A holds a real, unrelated permission via a
        # second fixture role — proves "no permission -> refused" isn't
        # just "no role at all -> refused" (a weaker, easier claim).
        await conn.execute(
            """INSERT INTO roles (id, org_id, name, description)
               VALUES ($1, $2, 'agenticmc_otherperm', 'verify fixture')
               ON CONFLICT (id) DO NOTHING""",
            FIXTURE_ROLE_OTHERPERM_A, FIXTURE_ORG_A,
        )
        await conn.execute(
            "INSERT INTO role_permissions (role_id, permission_id) VALUES ($1, $2) ON CONFLICT DO NOTHING",
            FIXTURE_ROLE_OTHERPERM_A, other_perm_id,
        )
        await conn.execute(
            "INSERT INTO user_roles (user_id, role_id) VALUES ($1, $2) ON CONFLICT DO NOTHING",
            FIXTURE_OTHERPERM_A, FIXTURE_ROLE_OTHERPERM_A,
        )

        # The proposal under test: made by FIXTURE_MAKER_A in org A.
        await conn.execute(
            """INSERT INTO agent_proposals (id, org_id, agent_key, object_type, payload, proposed_by)
               VALUES ($1, $2, 'compliance_analyst', 'finding', '{"note": "verify fixture"}'::jsonb, $3)
               ON CONFLICT (id) DO NOTHING""",
            FIXTURE_PROPOSAL_MAIN, FIXTURE_ORG_A, FIXTURE_MAKER_A,
        )
        await conn.execute(
            """INSERT INTO agent_proposals (id, org_id, agent_key, object_type, payload, proposed_by)
               VALUES ($1, $2, 'compliance_analyst', 'finding', '{"note": "verify fixture 2"}'::jsonb, $3)
               ON CONFLICT (id) DO NOTHING""",
            FIXTURE_PROPOSAL_SELFCHECK, FIXTURE_ORG_A, FIXTURE_MAKER_A,
        )


# ═══════════════════════════════════════════════════════════════════════════
async def main() -> int:
    dsn = await bootstrap_async()
    if not dsn:
        print("[SKIP] no working DATABASE_URL — nothing can be proven")
        return 2

    conn = await asyncpg.connect(dsn, statement_cache_size=0, ssl="require")
    try:
        await teardown(conn)

        # ── Task 1 findings, reported live ──────────────────────────────
        print("\n── Task 1 findings (live) ──")
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
            all_perms = await conn.fetch("SELECT name, resource, action FROM permissions ORDER BY name")
        print(f"[1a] {len(all_perms)} permissions total, convention verb_resource "
              f"(e.g. view_workflow_runs, manage_org_settings); review_agent_proposals "
              f"added by this sprint, backed by (resource='agent_proposals', action='review').")
        check(any(r["name"] == REVIEW_PERMISSION for r in all_perms),
              "[1a] review_agent_proposals exists in the live catalog")

        print("[1b] No generic agent-proposal table existed before this sprint — "
              "docs/AGENTIC_SUBSTRATE_DISCOVERY.md Task 4 already surveyed the three "
              "real candidates (assistant_activities, member_todos, workflow_run_steps) "
              "and none encoded permission-gated reviewer eligibility. agent_proposals "
              "is new; confirmed to exist below.")
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
            table_exists = await conn.fetchval(
                "SELECT to_regclass('public.agent_proposals') IS NOT NULL"
            )
        check(table_exists, "[1b] agent_proposals table exists")

        print("[1c] compliance_sr / compliance_jr had exactly one real code reference "
              "(routers/marketplace.py _safe_notify_roles) plus one dead role-keyed "
              "lookup (apps/web AssistantPanel.jsx ROLE_POSTURE, found during this "
              "sprint — users.role never actually held either literal, so it was "
              "unreachable, not load-bearing). Both repointed at `compliance`.")
        marketplace_src = (REPO_ROOT / "apps/api/routers/marketplace.py").read_text()
        assistant_panel_src = (REPO_ROOT / "apps/web/components/assistant/AssistantPanel.jsx").read_text()
        check("compliance_sr" not in marketplace_src and "compliance_jr" not in marketplace_src,
              "[1c] routers/marketplace.py no longer references compliance_sr/compliance_jr")
        check("compliance_sr" not in assistant_panel_src and "compliance_jr" not in assistant_panel_src,
              "[1c] AssistantPanel.jsx no longer references compliance_sr/compliance_jr")
        check('"compliance"' in marketplace_src or "'compliance'" in marketplace_src,
              "[1c] routers/marketplace.py now references the consolidated `compliance` role")

        # ── Compliance consolidation ─────────────────────────────────────
        print("\n── Compliance consolidation ──")
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
            stale = await conn.fetch(
                "SELECT name FROM roles WHERE org_id = $1 AND name = ANY($2::text[])",
                ORG_REAL, list(STALE_ROLE_NAMES),
            )
            compliance_row = await conn.fetchrow(
                "SELECT id FROM roles WHERE org_id = $1 AND name = 'compliance'", ORG_REAL,
            )
        check(len(stale) == 0, f"compliance_sr and compliance_jr no longer exist as roles (found {[r['name'] for r in stale]})")
        check(compliance_row is not None, "the consolidated `compliance` role exists in the real org")

        # ── The review permission — held by exactly the intended roles ──
        print("\n── Review permission grants ──")
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
            granted = await conn.fetch(
                """SELECT r.name FROM role_permissions rp
                   JOIN roles r ON r.id = rp.role_id
                   JOIN permissions p ON p.id = rp.permission_id
                   WHERE r.org_id = $1 AND p.name = $2""",
                ORG_REAL, REVIEW_PERMISSION,
            )
        granted_names = {r["name"] for r in granted}
        check(granted_names == EXPECTED_GRANTED_ROLES,
              f"review_agent_proposals granted to exactly {sorted(EXPECTED_GRANTED_ROLES)} "
              f"(found {sorted(granted_names)})")

        # ── Fixtures + the real function ─────────────────────────────────
        print("\n── Fixtures ──")
        await seed(conn)

        from services.database import close_pool, get_pool, reset_rls_context, set_rls_context
        from services.agent_proposals import (
            MakerCheckerError, NotEligibleError, create_proposal, is_eligible_reviewer,
            review_proposal,
        )

        await close_pool()
        pool = await get_pool()

        print("\n── [Y] create_proposal() — the real propose path an agent would use ──")
        tokens = set_rls_context(FIXTURE_ORG_A, False)
        try:
            async with pool.acquire() as create_conn:
                created_id = await create_proposal(
                    create_conn, str(FIXTURE_ORG_A), agent_key="compliance_analyst",
                    object_type="finding", payload={"note": "created via create_proposal()"},
                    proposed_by=str(FIXTURE_MAKER_A),
                )
        finally:
            reset_rls_context(tokens)
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
            created_row = await conn.fetchrow(
                "SELECT proposed_by::text AS proposed_by, status, escalation_reason "
                "FROM agent_proposals WHERE id = $1::uuid", created_id,
            )
        check(created_row is not None and created_row["proposed_by"] == str(FIXTURE_MAKER_A)
              and created_row["status"] == "pending" and created_row["escalation_reason"] is None,
              f"create_proposal() persisted a real row with the maker recorded: {dict(created_row) if created_row else None}")

        async def eligible_as(candidate_org, candidate_user, target_org, maker):
            tokens = set_rls_context(candidate_org, False)
            try:
                return await is_eligible_reviewer(pool, target_org, candidate_user, maker)
            finally:
                reset_rls_context(tokens)

        print("\n── [Y] A permission-holding non-maker is eligible ──")
        result = await eligible_as(FIXTURE_ORG_A, FIXTURE_REVIEWER_A, FIXTURE_ORG_A, FIXTURE_MAKER_A)
        check(result is True,
              f"FIXTURE_REVIEWER_A holds review_agent_proposals and is not the maker -> eligible (got {result})")

        print("\n── [Y] THE MAKER IS REFUSED on their own proposal despite holding the permission ──")
        result = await eligible_as(FIXTURE_ORG_A, FIXTURE_MAKER_A, FIXTURE_ORG_A, FIXTURE_MAKER_A)
        check(result is False,
              f"FIXTURE_MAKER_A holds the SAME review_agent_proposals grant as FIXTURE_REVIEWER_A "
              f"(see seed()) yet is refused checking their own proposal (got {result})")

        print("\n── [Y] A user without the permission is refused regardless ──")
        result = await eligible_as(FIXTURE_ORG_A, FIXTURE_NONHOLDER_A, FIXTURE_ORG_A, FIXTURE_MAKER_A)
        check(result is False,
              f"FIXTURE_NONHOLDER_A holds ZERO roles at all -> refused, not default-allowed "
              f"(proves this does NOT inherit rbac.has_permission's single-admin bootstrap) (got {result})")
        result = await eligible_as(FIXTURE_ORG_A, FIXTURE_OTHERPERM_A, FIXTURE_ORG_A, FIXTURE_MAKER_A)
        check(result is False,
              f"FIXTURE_OTHERPERM_A holds a real but UNRELATED permission "
              f"(view_workflow_runs) -> still refused (got {result})")

        print("\n── [Y] Cross-org isolation on eligibility ──")
        result = await eligible_as(FIXTURE_ORG_B, FIXTURE_REVIEWER_B, FIXTURE_ORG_A, FIXTURE_MAKER_A)
        check(result is False,
              f"FIXTURE_REVIEWER_B holds review_agent_proposals in ORG B, but org A's "
              f"proposal is out of reach -> refused (got {result})")
        # Sanity check: the SAME reviewer IS eligible for a DIFFERENT maker
        # in their OWN org — confirms the cross-org refusal above is really
        # about the org boundary, not a broken fixture that refuses
        # everything regardless of org (is_eligible_reviewer never looks up
        # the maker's own row, so an arbitrary non-matching id is valid here).
        other_org_b_maker = UUID("99000000-0000-0000-0000-0000ac0bffff")
        result = await eligible_as(FIXTURE_ORG_B, FIXTURE_REVIEWER_B, FIXTURE_ORG_B, other_org_b_maker)
        check(result is True,
              f"sanity: FIXTURE_REVIEWER_B IS eligible for a different maker in their "
              f"OWN org (got {result}) — the cross-org refusal above is genuinely about "
              f"the org boundary, not a fixture that refuses everything")

        # ── review_proposal(): the real UPDATE path ──────────────────────
        print("\n── [Y] agent_proposals: policy per operation, UPDATE genuinely works ──")
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
            policies = await conn.fetch(
                "SELECT cmd FROM pg_policies WHERE tablename = 'agent_proposals'"
            )
        check({p["cmd"] for p in policies} == {"SELECT", "INSERT", "UPDATE", "DELETE"},
              f"agent_proposals has exactly one policy per operation (found {sorted(p['cmd'] for p in policies)})")

        tokens = set_rls_context(FIXTURE_ORG_A, False)
        try:
            async with pool.acquire() as review_conn:
                result_row = await review_proposal(
                    pool, review_conn, FIXTURE_ORG_A, FIXTURE_PROPOSAL_MAIN,
                    reviewed_by=FIXTURE_REVIEWER_A, decision="approved",
                    review_notes="verify: approved by an eligible non-maker",
                )
        finally:
            reset_rls_context(tokens)
        check(result_row["status"] == "approved" and result_row["reviewed_by"] == str(FIXTURE_REVIEWER_A),
              f"review_proposal() returned the updated row: {result_row}")

        # Re-read from an INDEPENDENT connection — real persistence, not an
        # artifact of the same transaction/connection that wrote it.
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
            persisted = await conn.fetchrow(
                "SELECT status, reviewed_by::text AS reviewed_by, reviewed_at FROM agent_proposals WHERE id = $1",
                FIXTURE_PROPOSAL_MAIN,
            )
        check(persisted is not None and persisted["status"] == "approved"
              and persisted["reviewed_by"] == str(FIXTURE_REVIEWER_A) and persisted["reviewed_at"] is not None,
              f"UPDATE genuinely persisted, re-read from a separate raw connection: {dict(persisted) if persisted else None}")

        # ── The Python-level refusal on self-check ───────────────────────
        print("\n── [Y] review_proposal() refuses the maker in Python, before any UPDATE runs ──")
        tokens = set_rls_context(FIXTURE_ORG_A, False)
        raised = None
        try:
            async with pool.acquire() as review_conn:
                await review_proposal(
                    pool, review_conn, FIXTURE_ORG_A, FIXTURE_PROPOSAL_SELFCHECK,
                    reviewed_by=FIXTURE_MAKER_A, decision="approved",
                )
        except MakerCheckerError as exc:
            raised = exc
        finally:
            reset_rls_context(tokens)
        check(raised is not None, f"MakerCheckerError raised for the maker's own proposal (got {raised})")
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
            unchanged = await conn.fetchrow(
                "SELECT status, reviewed_by FROM agent_proposals WHERE id = $1", FIXTURE_PROPOSAL_SELFCHECK,
            )
        check(unchanged["status"] == "pending" and unchanged["reviewed_by"] is None,
              f"the refused row is genuinely UNCHANGED, not silently half-applied: {dict(unchanged)}")

        print("\n── [Y] review_proposal() refuses a non-holder ──")
        tokens = set_rls_context(FIXTURE_ORG_A, False)
        raised = None
        try:
            async with pool.acquire() as review_conn:
                await review_proposal(
                    pool, review_conn, FIXTURE_ORG_A, FIXTURE_PROPOSAL_SELFCHECK,
                    reviewed_by=FIXTURE_NONHOLDER_A, decision="approved",
                )
        except NotEligibleError as exc:
            raised = exc
        finally:
            reset_rls_context(tokens)
        check(raised is not None, f"NotEligibleError raised for a non-holder (got {raised})")

        # ── DB-level backstop: bypass the service layer entirely ─────────
        print("\n── [Y] the database's OWN CHECK constraint refuses a self-check, "
              "even bypassing services.agent_proposals ──")
        db_raised = None
        try:
            async with conn.transaction():
                await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
                await conn.execute(
                    "UPDATE agent_proposals SET reviewed_by = proposed_by, status = 'approved' WHERE id = $1",
                    FIXTURE_PROPOSAL_SELFCHECK,
                )
        except asyncpg.CheckViolationError as exc:
            db_raised = exc
        check(db_raised is not None,
              f"a raw UPDATE setting reviewed_by = proposed_by hits "
              f"agent_proposals_maker_checker_chk regardless of application code (got {db_raised})")
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
            still_pending = await conn.fetchrow(
                "SELECT status, reviewed_by FROM agent_proposals WHERE id = $1", FIXTURE_PROPOSAL_SELFCHECK,
            )
        check(still_pending["status"] == "pending" and still_pending["reviewed_by"] is None,
              f"the CHECK-violating UPDATE left the row genuinely unchanged: {dict(still_pending)}")

        # ── escalation_reason wiring ──────────────────────────────────────
        print("\n── [Y] escalation_reason uses the pre-existing enum ──")
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
            await conn.execute(
                """INSERT INTO agent_proposals
                       (id, org_id, agent_key, object_type, payload, proposed_by, escalation_reason)
                   VALUES ($1, $2, 'compliance_analyst', 'finding', '{}'::jsonb, $3, 'low_confidence')
                   ON CONFLICT (id) DO UPDATE SET escalation_reason = EXCLUDED.escalation_reason""",
                FIXTURE_PROPOSAL_ESCALATED, FIXTURE_ORG_A, FIXTURE_MAKER_A,
            )
            escalated = await conn.fetchval(
                "SELECT escalation_reason FROM agent_proposals WHERE id = $1", FIXTURE_PROPOSAL_ESCALATED,
            )
        check(escalated == "low_confidence", f"escalation_reason column round-trips a real enum value (got {escalated})")

        bogus_raised = None
        try:
            async with conn.transaction():
                await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
                await conn.execute(
                    "UPDATE agent_proposals SET escalation_reason = 'not_a_real_reason' WHERE id = $1",
                    FIXTURE_PROPOSAL_ESCALATED,
                )
        except asyncpg.InvalidTextRepresentationError as exc:
            bogus_raised = exc
        check(bogus_raised is not None, f"a bogus escalation_reason value is rejected by the enum type (got {bogus_raised})")

        # ── No regression on existing permission checks ──────────────────
        print("\n── [Y] No regression on existing permission checks ──")
        from services.rbac import has_permission

        async with conn.transaction():
            await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
            admin_perm_count = await conn.fetchval(
                """SELECT count(*) FROM role_permissions rp JOIN roles r ON r.id = rp.role_id
                   WHERE r.org_id = $1 AND r.name = 'admin'""", ORG_REAL,
            )
            member_perm_count = await conn.fetchval(
                """SELECT count(*) FROM role_permissions rp JOIN roles r ON r.id = rp.role_id
                   WHERE r.org_id = $1 AND r.name = 'member'""", ORG_REAL,
            )
        check(admin_perm_count == 23, f"the pre-existing `admin` role's grant count is unchanged (got {admin_perm_count}, expected 23)")
        check(member_perm_count == 10, f"the pre-existing `member` role's grant count is unchanged (got {member_perm_count}, expected 10)")

        tokens = set_rls_context(FIXTURE_ORG_A, False)
        try:
            still_allowed = await has_permission(pool, FIXTURE_OTHERPERM_A, FIXTURE_ORG_A, "view_workflow_runs")
            still_denied = await has_permission(pool, FIXTURE_MAKER_A, FIXTURE_ORG_A, "view_workflow_runs")
        finally:
            reset_rls_context(tokens)
        check(still_allowed is True, f"has_permission() still grants a permission this fixture actually holds (got {still_allowed})")
        check(still_denied is False, f"has_permission() still refuses a permission this fixture does NOT hold (got {still_denied})")

    finally:
        try:
            await teardown(conn)
            async with conn.transaction():
                await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
                leftovers = await conn.fetchval(
                    """SELECT (SELECT count(*) FROM agent_proposals WHERE org_id = ANY($1::uuid[]))
                            + (SELECT count(*) FROM user_roles WHERE user_id = ANY($2::uuid[]))
                            + (SELECT count(*) FROM role_permissions WHERE role_id = ANY($3::uuid[]))
                            + (SELECT count(*) FROM roles WHERE id = ANY($3::uuid[]))
                            + (SELECT count(*) FROM users WHERE id = ANY($2::uuid[]))
                            + (SELECT count(*) FROM organizations WHERE id = ANY($1::uuid[]))""",
                    ALL_FIXTURE_ORGS, ALL_FIXTURE_USERS, ALL_FIXTURE_ROLES,
                )
            check(leftovers == 0, f"[Y] TEARDOWN: zero leftover fixture rows (found {leftovers})")

            async with conn.transaction():
                await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
                real_grants_intact = await conn.fetch(
                    """SELECT r.name FROM role_permissions rp
                       JOIN roles r ON r.id = rp.role_id
                       JOIN permissions p ON p.id = rp.permission_id
                       WHERE r.org_id = $1 AND p.name = $2""",
                    ORG_REAL, REVIEW_PERMISSION,
                )
            check({r["name"] for r in real_grants_intact} == EXPECTED_GRANTED_ROLES,
                  "teardown removed only fixtures — the real review_agent_proposals grants survive")
        finally:
            from services.database import close_pool
            await close_pool()
            await conn.close()

    print(f"\n{'=' * 70}\nTOTAL: {_n_pass} PASS, {_n_fail} FAIL, {len(_finds)} FIND\n{'=' * 70}")
    return 0 if _ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
