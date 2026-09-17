"""verify_selfapproval.py — self-approval under a genuinely empty checker
set (selfapproval.structural).

THE PROBLEM: agenticmakerchecker.structural's maker-checker rule (a user may
check a proposal iff they hold review_agent_proposals AND are not its own
maker) is correct, but it makes a single-privileged-user org UNABLE TO EVER
APPROVE ANYTHING once the maker IS that org's only holder of
review_agent_proposals — excluding the maker leaves zero eligible checkers,
and the proposal can never be routed. 2nd Act today has exactly one real
org-scoped holder of review_agent_proposals (org_admin, one user) and zero
holders on every other reviewer role, so this is not a hypothetical edge
case — see the Task 1 findings this script reports live, below.

THE DECISION (already made, not re-litigated here): ALLOW WITH DISCLOSURE.
When the eligible-checker set is genuinely empty after excluding the maker,
permit the self-approval and RECORD it as one. Escalating to org_admin was
rejected (in a lean org that is frequently the same human wearing a second
hat — theatrical separation, not real separation of duties). Blocking
outright was rejected as unusable for a single-advisor tenant.

THE SHAPE: agent_proposals_maker_checker_chk goes from an unconditional
refusal to a CONDITIONAL one:

    reviewed_by IS NULL
    OR reviewed_by <> proposed_by
    OR (self_approved = true AND self_approval_reason IS NOT NULL)

A raw UPDATE setting reviewed_by = proposed_by still fails unless the row
ALSO carries self_approved = true and a non-null self_approval_reason — the
guarantee that a self-check can never slip through silently survives; only
the deliberate, disclosed case opens.

THE CRITICAL GATE: self-approval is permitted only when
services.agent_proposals.has_other_eligible_checker finds the org's checker
set genuinely empty — a COMPUTED fact, never a caller-supplied flag. A
maker with an available reviewer is refused exactly as before, proven below
by running that exact scenario side-by-side with the genuinely-empty one in
the same fixture set (the cross-org proof).

Deliberately scoped to ORG-level review_agent_proposals holders only, NOT
is_super_admin, even though is_eligible_reviewer itself treats super_admin
as universally eligible — see has_other_eligible_checker's own docstring in
services/agent_proposals.py for why folding super_admin into this gate
would make "genuinely empty" nearly unreachable in practice and would
conflate a platform-operator bypass with an org's own fiduciary staffing
question.

WHAT THIS PROVES:
  - Task 1's three findings, reported live against the CURRENT deployed
    state (queried fresh by this script, not copy-pasted from the sprint).
  - A maker WITH another eligible checker available is still refused on
    their own proposal (MakerCheckerError) — proves self-approval is not a
    flag-driven bypass of an available reviewer.
  - A maker with NO other eligible checker CAN self-approve, and the row
    records self_approved = true with the supplied reason.
  - A raw UPDATE setting reviewed_by = proposed_by WITHOUT the flag and
    reason still fails agent_proposals_maker_checker_chk.
  - A raw UPDATE setting the flag but NO reason also fails the same
    constraint.
  - A raw UPDATE setting BOTH the flag and a reason SUCCEEDS — the
    constraint genuinely opens for the deliberate, disclosed case, not just
    genuinely closes for the accidental one.
  - A non-holder is refused (NotEligibleError) regardless of emptiness,
    proven in an org with ZERO review_agent_proposals holders at all — the
    emptiest possible org — so this is not merely "we never reached the
    emptiness check."
  - Cross-org: has_other_eligible_checker computes emptiness per org — one
    fixture org is genuinely empty (self-approval succeeds) while a SECOND
    fixture org with its own available reviewer coexists in the same run
    and is still refused, proving one org's emptiness does not leak into
    another's.
  - No regression: the normal two-party path (a genuine non-maker holder
    approving) still works unchanged — self_approved stays false,
    self_approval_reason stays NULL.
  - Teardown: zero leftover rows, clearing agent_proposals/
    assistant_activities/audit_log before deleting fixture users (the
    child-of-users ordering this sprint documents in
    SPRINT_WORKFLOW_STANDARD.md).

Hydrates DATABASE_URL from Doppler over HTTPS at startup (the
verify_actionregistryfix.py / verify_agenticmakerchecker.py pattern). Never
prints a credential value.

Run:  python3 apps/api/scripts/verify_selfapproval.py
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

ORG_REAL = UUID("00000000-0000-0000-0000-000000000001")  # 2nd Act Capital

REVIEW_PERMISSION = "review_agent_proposals"
EXPECTED_GRANTED_ROLES = {
    "support_staff", "advisor", "investment_committee", "fund_finance",
    "compliance", "org_admin",
}

# ── Fixtures — three orgs, each isolating exactly one variable ─────────────
#   ORG_EMPTY       — one user holds review_agent_proposals: the maker.
#                      Excluding the maker leaves genuinely zero checkers.
#   ORG_NONEMPTY    — two users hold review_agent_proposals: maker + a
#                      real other reviewer. Coexists with ORG_EMPTY in the
#                      same run for the cross-org proof.
#   ORG_ZEROHOLDERS — nobody holds review_agent_proposals at all. Its own
#                      maker holds nothing either — the emptiest possible
#                      org, used to prove "non-holder refused regardless of
#                      emptiness" without any ambiguity about why.
FIXTURE_ORG_EMPTY = UUID("99000000-0000-0000-0000-0000fa0a0001")
FIXTURE_ORG_NONEMPTY = UUID("99000000-0000-0000-0000-0000fa0b0001")
FIXTURE_ORG_ZEROHOLDERS = UUID("99000000-0000-0000-0000-0000fa0c0001")

FIXTURE_ROLE_EMPTY = UUID("99000000-0000-0000-0000-0000fa1a0001")
FIXTURE_ROLE_NONEMPTY = UUID("99000000-0000-0000-0000-0000fa1b0001")

FIXTURE_MAKER_EMPTY = UUID("99000000-0000-0000-0000-0000fa2a0001")
FIXTURE_MAKER_NONEMPTY = UUID("99000000-0000-0000-0000-0000fa2b0001")
FIXTURE_REVIEWER_NONEMPTY = UUID("99000000-0000-0000-0000-0000fa2b0002")
FIXTURE_MAKER_ZEROHOLDERS = UUID("99000000-0000-0000-0000-0000fa2c0001")

ALL_FIXTURE_USERS = [
    FIXTURE_MAKER_EMPTY, FIXTURE_MAKER_NONEMPTY, FIXTURE_REVIEWER_NONEMPTY,
    FIXTURE_MAKER_ZEROHOLDERS,
]
ALL_FIXTURE_ORGS = [FIXTURE_ORG_EMPTY, FIXTURE_ORG_NONEMPTY, FIXTURE_ORG_ZEROHOLDERS]
ALL_FIXTURE_ROLES = [FIXTURE_ROLE_EMPTY, FIXTURE_ROLE_NONEMPTY]

FIXTURE_PROPOSAL_SELFAPPROVE = UUID("99000000-0000-0000-0000-0000fa3a0001")
FIXTURE_PROPOSAL_RAWUPDATE_NOFLAG = UUID("99000000-0000-0000-0000-0000fa3a0002")
FIXTURE_PROPOSAL_RAWUPDATE_FLAGNOREASON = UUID("99000000-0000-0000-0000-0000fa3a0003")
FIXTURE_PROPOSAL_RAWUPDATE_POSITIVE = UUID("99000000-0000-0000-0000-0000fa3a0004")
FIXTURE_PROPOSAL_SELFATTEMPT_REFUSED = UUID("99000000-0000-0000-0000-0000fa3b0001")
FIXTURE_PROPOSAL_NORMAL_TWO_PARTY = UUID("99000000-0000-0000-0000-0000fa3b0002")
FIXTURE_PROPOSAL_NONHOLDER_SELFATTEMPT = UUID("99000000-0000-0000-0000-0000fa3c0001")

ALL_FIXTURE_PROPOSALS = [
    FIXTURE_PROPOSAL_SELFAPPROVE, FIXTURE_PROPOSAL_RAWUPDATE_NOFLAG,
    FIXTURE_PROPOSAL_RAWUPDATE_FLAGNOREASON, FIXTURE_PROPOSAL_RAWUPDATE_POSITIVE,
    FIXTURE_PROPOSAL_SELFATTEMPT_REFUSED, FIXTURE_PROPOSAL_NORMAL_TWO_PARTY,
    FIXTURE_PROPOSAL_NONHOLDER_SELFATTEMPT,
]

# Emails/auth0_sub derived from the FULL user uuid, never a shared prefix
# slice — two fixture uuids sharing their first 8 characters collided on
# users_email_key in a prior sprint.
SUB = {u: f"auth0|selfapproval-{u}" for u in ALL_FIXTURE_USERS}
EMAIL = {u: f"selfapproval-{u}@test.local" for u in ALL_FIXTURE_USERS}

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
# transaction (SET LOCAL inside an explicit transaction, per CLAUDE.md's
# platform_scope() convention, never a bare session-level SET).
#
# Order matters both ways: teardown deletes agent_proposals (and, per the
# new SPRINT_WORKFLOW_STANDARD.md note, assistant_activities/audit_log —
# empty for this sprint's fixtures, but cleared defensively so a future
# change to this script can't silently reintroduce the FK-blocks-user-
# delete failure) BEFORE user_roles/roles/users; seed does the reverse.
# ═══════════════════════════════════════════════════════════════════════════
async def teardown(conn):
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
        await conn.execute(
            "DELETE FROM agent_proposals WHERE id = ANY($1::uuid[]) OR org_id = ANY($2::uuid[])",
            ALL_FIXTURE_PROPOSALS, ALL_FIXTURE_ORGS,
        )
        await conn.execute(
            "DELETE FROM assistant_activities WHERE user_id = ANY($1::uuid[]) "
            "OR proposed_by = ANY($1::uuid[]) OR approved_by = ANY($1::uuid[])",
            ALL_FIXTURE_USERS,
        )
        await conn.execute(
            "DELETE FROM audit_log WHERE user_id = ANY($1::uuid[])", ALL_FIXTURE_USERS
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

        for org_id, slug in (
            (FIXTURE_ORG_EMPTY, "selfapproval-fixture-empty"),
            (FIXTURE_ORG_NONEMPTY, "selfapproval-fixture-nonempty"),
            (FIXTURE_ORG_ZEROHOLDERS, "selfapproval-fixture-zeroholders"),
        ):
            await conn.execute(
                """INSERT INTO organizations (id, name, slug) VALUES ($1, $2, $3)
                   ON CONFLICT (id) DO NOTHING""",
                org_id, f"SelfApproval Fixture Org {slug.rsplit('-', 1)[-1]}", slug,
            )

        review_perm_id = await conn.fetchval(
            "SELECT id FROM permissions WHERE name = $1", REVIEW_PERMISSION
        )

        await conn.execute(
            """INSERT INTO roles (id, org_id, name, description)
               VALUES ($1, $2, 'selfapproval_reviewer', 'verify fixture')
               ON CONFLICT (id) DO NOTHING""",
            FIXTURE_ROLE_EMPTY, FIXTURE_ORG_EMPTY,
        )
        await conn.execute(
            """INSERT INTO roles (id, org_id, name, description)
               VALUES ($1, $2, 'selfapproval_reviewer', 'verify fixture')
               ON CONFLICT (id) DO NOTHING""",
            FIXTURE_ROLE_NONEMPTY, FIXTURE_ORG_NONEMPTY,
        )
        for role_id in (FIXTURE_ROLE_EMPTY, FIXTURE_ROLE_NONEMPTY):
            await conn.execute(
                """INSERT INTO role_permissions (role_id, permission_id) VALUES ($1, $2)
                   ON CONFLICT DO NOTHING""",
                role_id, review_perm_id,
            )

        async def mk_user(uid, org_id):
            sub = SUB[uid]
            await conn.execute(
                """INSERT INTO users (id, org_id, email, full_name, auth0_sub, role, is_active)
                   VALUES ($1, $2, $3, $4, $5, 'member', true)
                   ON CONFLICT (id) DO UPDATE SET org_id = EXCLUDED.org_id""",
                uid, org_id, EMAIL[uid], sub, sub,
            )

        await mk_user(FIXTURE_MAKER_EMPTY, FIXTURE_ORG_EMPTY)
        await mk_user(FIXTURE_MAKER_NONEMPTY, FIXTURE_ORG_NONEMPTY)
        await mk_user(FIXTURE_REVIEWER_NONEMPTY, FIXTURE_ORG_NONEMPTY)
        await mk_user(FIXTURE_MAKER_ZEROHOLDERS, FIXTURE_ORG_ZEROHOLDERS)

        # ORG_EMPTY: the maker is the SOLE holder of review_agent_proposals —
        # excluding themselves leaves genuinely zero eligible checkers.
        await conn.execute(
            "INSERT INTO user_roles (user_id, role_id) VALUES ($1, $2) ON CONFLICT DO NOTHING",
            FIXTURE_MAKER_EMPTY, FIXTURE_ROLE_EMPTY,
        )
        # ORG_NONEMPTY: BOTH the maker and a distinct reviewer hold it —
        # excluding the maker still leaves a real, other eligible checker.
        await conn.execute(
            "INSERT INTO user_roles (user_id, role_id) VALUES ($1, $2) ON CONFLICT DO NOTHING",
            FIXTURE_MAKER_NONEMPTY, FIXTURE_ROLE_NONEMPTY,
        )
        await conn.execute(
            "INSERT INTO user_roles (user_id, role_id) VALUES ($1, $2) ON CONFLICT DO NOTHING",
            FIXTURE_REVIEWER_NONEMPTY, FIXTURE_ROLE_NONEMPTY,
        )
        # ORG_ZEROHOLDERS: no role granting review_agent_proposals exists in
        # this org at all — FIXTURE_MAKER_ZEROHOLDERS gets no user_roles row.

        async def mk_proposal(pid, org_id, maker):
            await conn.execute(
                """INSERT INTO agent_proposals (id, org_id, agent_key, object_type, payload, proposed_by)
                   VALUES ($1, $2, 'authoring', 'finding', '{"note": "verify fixture"}'::jsonb, $3)
                   ON CONFLICT (id) DO NOTHING""",
                pid, org_id, maker,
            )

        await mk_proposal(FIXTURE_PROPOSAL_SELFAPPROVE, FIXTURE_ORG_EMPTY, FIXTURE_MAKER_EMPTY)
        await mk_proposal(FIXTURE_PROPOSAL_RAWUPDATE_NOFLAG, FIXTURE_ORG_EMPTY, FIXTURE_MAKER_EMPTY)
        await mk_proposal(FIXTURE_PROPOSAL_RAWUPDATE_FLAGNOREASON, FIXTURE_ORG_EMPTY, FIXTURE_MAKER_EMPTY)
        await mk_proposal(FIXTURE_PROPOSAL_RAWUPDATE_POSITIVE, FIXTURE_ORG_EMPTY, FIXTURE_MAKER_EMPTY)
        await mk_proposal(FIXTURE_PROPOSAL_SELFATTEMPT_REFUSED, FIXTURE_ORG_NONEMPTY, FIXTURE_MAKER_NONEMPTY)
        await mk_proposal(FIXTURE_PROPOSAL_NORMAL_TWO_PARTY, FIXTURE_ORG_NONEMPTY, FIXTURE_MAKER_NONEMPTY)
        await mk_proposal(FIXTURE_PROPOSAL_NONHOLDER_SELFATTEMPT, FIXTURE_ORG_ZEROHOLDERS, FIXTURE_MAKER_ZEROHOLDERS)


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
            constraint_def = await conn.fetchval(
                "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                "WHERE conname = 'agent_proposals_maker_checker_chk'"
            )
        print(f"[1a] the real, live CHECK constraint definition:\n     {constraint_def}")
        check(
            constraint_def is not None
            and "self_approved" in constraint_def
            and "self_approval_reason" in constraint_def
            and "reviewed_by <> proposed_by" in constraint_def,
            "[1a] agent_proposals_maker_checker_chk is the conditional form — "
            "a self-check still fails unless self_approved/self_approval_reason are both set",
        )
        print(
            "[1a] the real eligibility function (services.agent_proposals."
            "is_eligible_reviewer) is unchanged: holds review_agent_proposals "
            "(or is super_admin) AND is not the maker. The new "
            "has_other_eligible_checker helper (Task 3) is deliberately a "
            "NARROWER, org-scoped-only definition — see its own docstring — "
            "used only to decide whether self-approval is reachable at all, "
            "never substituted for is_eligible_reviewer's own per-candidate check."
        )

        async with conn.transaction():
            await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
            holder_counts = await conn.fetch(
                """SELECT r.name, count(DISTINCT ur.user_id) AS holders
                   FROM roles r
                   JOIN role_permissions rp ON rp.role_id = r.id
                   JOIN permissions p ON p.id = rp.permission_id
                   LEFT JOIN user_roles ur ON ur.role_id = r.id
                   WHERE r.org_id = $1 AND p.name = $2
                   GROUP BY r.name ORDER BY r.name""",
                ORG_REAL, REVIEW_PERMISSION,
            )
            super_admins = await conn.fetch(
                "SELECT id::text AS id, org_id::text AS org_id FROM users WHERE role = 'super_admin'"
            )
        holder_map = {r["name"]: r["holders"] for r in holder_counts}
        print(f"[1b] live review_agent_proposals holder counts by role in the real org: {dict(holder_map)}")
        print(f"[1b] live super_admin accounts: {[(r['id'], r['org_id']) for r in super_admins]}")
        check(holder_map.get("org_admin") == 1, f"[1b] org_admin has exactly 1 holder (got {holder_map.get('org_admin')})")
        non_org_admin_holders = {k: v for k, v in holder_map.items() if k != "org_admin"}
        check(
            all(v == 0 for v in non_org_admin_holders.values()),
            f"[1b] every OTHER reviewer role has zero holders (got {non_org_admin_holders})",
        )
        find(
            "[1b] the non-empty path IS reachable today, but only for one specific user",
            "org_admin's single holder is the sole org-scoped review_agent_proposals "
            "grant in the real org — a proposal made by anyone else already has a "
            "non-empty checker set (that org_admin can check it), so self-approval "
            "only becomes reachable for a proposal THAT user makes. This is exactly "
            "the single-privileged-user scenario the sprint prompt describes, not a "
            "hypothetical: 2nd Act functionally has one person who can both propose "
            "and would otherwise be the only one who could check.",
        )
        find(
            "[1b] two real super_admin accounts exist but are deliberately NOT "
            "counted by has_other_eligible_checker",
            f"both sit in org {ORG_REAL} today ({[r['id'] for r in super_admins]}); "
            "if they were counted, the checker set for that org would almost never "
            "be genuinely empty (super_admin is universally eligible under "
            "is_eligible_reviewer), which would make this gate nearly unreachable "
            "in the one real org that needs it. See services/agent_proposals.py's "
            "has_other_eligible_checker docstring for the design reasoning.",
        )

        marketplace_propose_src = pathlib.Path(
            HERE.parents[1] / "services/assistant_actions/propose.py"
        ).read_text()
        print("[1c] apps/api/services/assistant_actions/propose.py's create_proposal() "
              "call passes proposed_by=user_id — the real, authenticated human principal "
              "an agent runs as, never an agent-name string.")
        check(
            "proposed_by=user_id" in marketplace_propose_src,
            "[1c] propose.py still passes the real user_id as proposed_by",
        )
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
            proposed_by_fk = await conn.fetchval(
                "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                "WHERE conname = 'agent_proposals_proposed_by_fkey'"
            )
        print(f"[1c] live FK: {proposed_by_fk}")
        check(
            proposed_by_fk is not None and "REFERENCES users(id)" in proposed_by_fk,
            "[1c] proposed_by is still a real FK to users(id), never an agent identifier",
        )

        # ── Fixtures + the real functions ────────────────────────────────
        print("\n── Fixtures ──")
        await seed(conn)

        from services.database import close_pool, get_pool, reset_rls_context, set_rls_context
        from services.agent_proposals import (
            MakerCheckerError,
            NotEligibleError,
            SelfApprovalReasonRequiredError,
            has_other_eligible_checker,
            review_proposal,
        )

        await close_pool()
        pool = await get_pool()

        # ── The emptiness gate, called directly ──────────────────────────
        print("\n── [Y] Cross-org: has_other_eligible_checker computed per org ──")
        tokens = set_rls_context(FIXTURE_ORG_EMPTY, False)
        try:
            empty_result = await has_other_eligible_checker(pool, FIXTURE_ORG_EMPTY, FIXTURE_MAKER_EMPTY)
        finally:
            reset_rls_context(tokens)
        check(empty_result is False,
              f"ORG_EMPTY: excluding the sole holder (the maker) leaves genuinely zero "
              f"eligible checkers (got {empty_result})")

        tokens = set_rls_context(FIXTURE_ORG_NONEMPTY, False)
        try:
            nonempty_result = await has_other_eligible_checker(pool, FIXTURE_ORG_NONEMPTY, FIXTURE_MAKER_NONEMPTY)
        finally:
            reset_rls_context(tokens)
        check(nonempty_result is True,
              f"ORG_NONEMPTY (coexisting in the SAME fixture set as ORG_EMPTY, in the "
              f"same run): excluding the maker still leaves the real other reviewer "
              f"(got {nonempty_result}) — ORG_EMPTY's emptiness did not leak here")

        # ── Maker WITH an available checker is still refused ─────────────
        print("\n── [Y] A maker WITH another eligible checker is still refused on self-approval ──")
        tokens = set_rls_context(FIXTURE_ORG_NONEMPTY, False)
        raised = None
        try:
            async with pool.acquire() as review_conn:
                await review_proposal(
                    pool, review_conn, FIXTURE_ORG_NONEMPTY, FIXTURE_PROPOSAL_SELFATTEMPT_REFUSED,
                    reviewed_by=FIXTURE_MAKER_NONEMPTY, decision="approved",
                    self_approval_reason="attempting to self-approve despite an available reviewer",
                )
        except MakerCheckerError as exc:
            raised = exc
        finally:
            reset_rls_context(tokens)
        check(raised is not None,
              f"MakerCheckerError raised even though a self_approval_reason was supplied — "
              f"the reason argument is not itself a bypass (got {raised})")
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
            unchanged = await conn.fetchrow(
                "SELECT status, reviewed_by, self_approved, self_approval_reason "
                "FROM agent_proposals WHERE id = $1", FIXTURE_PROPOSAL_SELFATTEMPT_REFUSED,
            )
        check(unchanged["status"] == "pending" and unchanged["reviewed_by"] is None
              and unchanged["self_approved"] is False and unchanged["self_approval_reason"] is None,
              f"the refused row is genuinely unchanged, not silently half-applied: {dict(unchanged)}")

        # ── Maker with NO available checker CAN self-approve ─────────────
        print("\n── [Y] A maker with NO other eligible checker CAN self-approve, recorded ──")
        tokens = set_rls_context(FIXTURE_ORG_EMPTY, False)
        try:
            async with pool.acquire() as review_conn:
                result_row = await review_proposal(
                    pool, review_conn, FIXTURE_ORG_EMPTY, FIXTURE_PROPOSAL_SELFAPPROVE,
                    reviewed_by=FIXTURE_MAKER_EMPTY, decision="approved",
                    self_approval_reason="single-advisor org — no other eligible checker exists",
                )
        finally:
            reset_rls_context(tokens)
        check(
            result_row["status"] == "approved"
            and result_row["reviewed_by"] == str(FIXTURE_MAKER_EMPTY)
            and result_row["proposed_by"] == str(FIXTURE_MAKER_EMPTY)
            and result_row["self_approved"] is True
            and result_row["self_approval_reason"] == "single-advisor org — no other eligible checker exists",
            f"review_proposal() returned the self-approved row: {result_row}",
        )
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
            persisted = await conn.fetchrow(
                "SELECT status, reviewed_by::text AS reviewed_by, self_approved, self_approval_reason "
                "FROM agent_proposals WHERE id = $1", FIXTURE_PROPOSAL_SELFAPPROVE,
            )
        check(
            persisted is not None and persisted["status"] == "approved"
            and persisted["reviewed_by"] == str(FIXTURE_MAKER_EMPTY)
            and persisted["self_approved"] is True
            and persisted["self_approval_reason"],
            f"UPDATE genuinely persisted, re-read from a separate raw connection: {dict(persisted) if persisted else None}",
        )

        # ── Self-approval without a reason is refused before any UPDATE ──
        print("\n── [Y] Self-approval with an empty reason is refused (app layer) ──")
        tokens = set_rls_context(FIXTURE_ORG_EMPTY, False)
        raised = None
        try:
            async with pool.acquire() as review_conn:
                await review_proposal(
                    pool, review_conn, FIXTURE_ORG_EMPTY, FIXTURE_PROPOSAL_RAWUPDATE_NOFLAG,
                    reviewed_by=FIXTURE_MAKER_EMPTY, decision="approved",
                )
        except SelfApprovalReasonRequiredError as exc:
            raised = exc
        finally:
            reset_rls_context(tokens)
        check(raised is not None, f"SelfApprovalReasonRequiredError raised with no reason supplied (got {raised})")

        # ── Non-holder refused regardless of emptiness ────────────────────
        print("\n── [Y] A non-holder is refused regardless of emptiness ──")
        tokens = set_rls_context(FIXTURE_ORG_ZEROHOLDERS, False)
        empty_zero = await has_other_eligible_checker(pool, FIXTURE_ORG_ZEROHOLDERS, FIXTURE_MAKER_ZEROHOLDERS)
        check(empty_zero is False, f"ORG_ZEROHOLDERS has ZERO review_agent_proposals holders at all — "
                                    f"the emptiest possible org (got {empty_zero})")
        raised = None
        try:
            async with pool.acquire() as review_conn:
                await review_proposal(
                    pool, review_conn, FIXTURE_ORG_ZEROHOLDERS, FIXTURE_PROPOSAL_NONHOLDER_SELFATTEMPT,
                    reviewed_by=FIXTURE_MAKER_ZEROHOLDERS, decision="approved",
                    self_approval_reason="attempting self-approval without holding the permission at all",
                )
        except NotEligibleError as exc:
            raised = exc
        finally:
            reset_rls_context(tokens)
        check(raised is not None,
              f"NotEligibleError raised even though the checker set is genuinely empty AND "
              f"a reason was supplied — holding REVIEW_PERMISSION is checked BEFORE emptiness, "
              f"never bypassed by either (got {raised})")

        # ── DB-level backstop: raw UPDATEs, bypassing the service layer ───
        print("\n── [Y] Raw UPDATE without flag+reason still fails the CHECK constraint ──")
        db_raised = None
        try:
            async with conn.transaction():
                await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
                await conn.execute(
                    "UPDATE agent_proposals SET reviewed_by = proposed_by, status = 'approved' WHERE id = $1",
                    FIXTURE_PROPOSAL_RAWUPDATE_NOFLAG,
                )
        except asyncpg.CheckViolationError as exc:
            db_raised = exc
        check(db_raised is not None,
              f"a raw UPDATE setting reviewed_by = proposed_by with self_approved still "
              f"false hits agent_proposals_maker_checker_chk (got {db_raised})")

        print("\n── [Y] Raw UPDATE with the flag but NO reason also fails ──")
        db_raised = None
        try:
            async with conn.transaction():
                await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
                await conn.execute(
                    "UPDATE agent_proposals SET reviewed_by = proposed_by, status = 'approved', "
                    "self_approved = true WHERE id = $1",
                    FIXTURE_PROPOSAL_RAWUPDATE_FLAGNOREASON,
                )
        except asyncpg.CheckViolationError as exc:
            db_raised = exc
        check(db_raised is not None,
              f"a raw UPDATE setting self_approved = true with self_approval_reason still "
              f"NULL hits agent_proposals_maker_checker_chk (got {db_raised})")

        print("\n── [Y] Raw UPDATE with BOTH the flag and a reason genuinely succeeds ──")
        db_raised = None
        try:
            async with conn.transaction():
                await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
                await conn.execute(
                    "UPDATE agent_proposals SET reviewed_by = proposed_by, status = 'approved', "
                    "self_approved = true, self_approval_reason = 'raw-SQL disclosure proof' WHERE id = $1",
                    FIXTURE_PROPOSAL_RAWUPDATE_POSITIVE,
                )
        except asyncpg.CheckViolationError as exc:
            db_raised = exc
        check(db_raised is None,
              f"the constraint genuinely OPENS for the deliberate, disclosed case at the "
              f"database level, independent of any application code (got {db_raised})")
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
            positive_row = await conn.fetchrow(
                "SELECT status, reviewed_by, self_approved, self_approval_reason "
                "FROM agent_proposals WHERE id = $1", FIXTURE_PROPOSAL_RAWUPDATE_POSITIVE,
            )
        check(positive_row["status"] == "approved" and positive_row["self_approved"] is True,
              f"the raw-SQL self-approval genuinely persisted: {dict(positive_row)}")

        # ── No regression: the normal two-party path is unaffected ────────
        print("\n── [Y] No regression: the normal two-party path still works unchanged ──")
        tokens = set_rls_context(FIXTURE_ORG_NONEMPTY, False)
        try:
            async with pool.acquire() as review_conn:
                normal_row = await review_proposal(
                    pool, review_conn, FIXTURE_ORG_NONEMPTY, FIXTURE_PROPOSAL_NORMAL_TWO_PARTY,
                    reviewed_by=FIXTURE_REVIEWER_NONEMPTY, decision="approved",
                    review_notes="verify: approved by an eligible non-maker",
                )
        finally:
            reset_rls_context(tokens)
        check(
            normal_row["status"] == "approved"
            and normal_row["reviewed_by"] == str(FIXTURE_REVIEWER_NONEMPTY)
            and normal_row["self_approved"] is False
            and normal_row["self_approval_reason"] is None,
            f"a genuine non-maker reviewer still approves normally, with self_approved "
            f"staying false and self_approval_reason staying NULL: {normal_row}",
        )

    finally:
        try:
            await teardown(conn)
            async with conn.transaction():
                await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
                leftovers = await conn.fetchval(
                    """SELECT (SELECT count(*) FROM agent_proposals WHERE org_id = ANY($1::uuid[]))
                            + (SELECT count(*) FROM assistant_activities WHERE user_id = ANY($2::uuid[])
                                                                          OR proposed_by = ANY($2::uuid[])
                                                                          OR approved_by = ANY($2::uuid[]))
                            + (SELECT count(*) FROM audit_log WHERE user_id = ANY($2::uuid[]))
                            + (SELECT count(*) FROM user_roles WHERE user_id = ANY($2::uuid[]))
                            + (SELECT count(*) FROM role_permissions WHERE role_id = ANY($3::uuid[]))
                            + (SELECT count(*) FROM roles WHERE id = ANY($3::uuid[]))
                            + (SELECT count(*) FROM users WHERE id = ANY($2::uuid[]))
                            + (SELECT count(*) FROM organizations WHERE id = ANY($1::uuid[]))""",
                    ALL_FIXTURE_ORGS, ALL_FIXTURE_USERS, ALL_FIXTURE_ROLES,
                )
            check(leftovers == 0, f"[Y] TEARDOWN: zero leftover fixture rows, "
                                   f"audit_log/assistant_activities cleared before users (found {leftovers})")

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
