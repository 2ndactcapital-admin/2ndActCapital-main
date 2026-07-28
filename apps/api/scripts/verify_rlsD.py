"""RLS Batch D verify — Deals/Marketplace tables (11 tables).

Proves the RLS policies applied to the 11 deals/marketplace tables behave
correctly under the NON-BYPASS ``app_service`` role, using REAL rows — not code
inspection — and that production (the bypass ``postgres`` role) is completely
unchanged. Every table in this batch has ONE policy shape: STANDARD DIRECT
(own org_id, NOT NULL). Same NULLIF pattern as Batches A/B:

    (org_id = NULLIF(current_setting('app.current_org_id', true), '')::uuid)
    OR current_setting('app.is_super_admin', true) = 'true'

applied identically on BOTH qual (USING) and with_check.

Batch tables (all direct org_id):
  deals, deal_ai_summaries, deal_documents, deal_interest, deal_scores,
  deal_votes, investment_stage_history, member_investments,
  investment_profile_answers, investment_profile_extractions,
  investment_profile_questions.

Live proofs (as the non-bypass app_service role) — 4 representative tables, the
root/parent + the member-facing side + a scoring table + a voting table, each
getting the full four-way isolation matrix:
  * deals              (the root/parent)
  * member_investments (the member-facing side)
  * deal_scores        (per-deal scoring)
  * deal_votes         (member voting)
For each: same-org context sees the row / different-org context does NOT /
super_admin sees regardless of org / neither-context-set → zero rows.
4 tables x 4 checks = 16 live isolation assertions.

Two DSNs:
  * DATABASE_URL             — the ORIGINAL, RLS-BYPASSING ``postgres`` role.
                               Seeding, structural checks, the "bypass role
                               unchanged" assertions, teardown.
  * APP_SERVICE_DATABASE_URL — the NON-BYPASS ``app_service`` role, supplied by
                               Joe at test time ONLY. Never hardcoded, never
                               written to any file. Runs the live RLS isolation
                               assertions. If absent, those are SKIPPED (not
                               failed).

This script changes NOTHING about production. DATABASE_URL is not modified
anywhere; the live app remains on the ``postgres`` role. Pass/fail only, no
interactive prompts, idempotent — teardown at start AND end by stable ids.

Run (full):
  DATABASE_URL=...  APP_SERVICE_DATABASE_URL=...  python scripts/verify_rlsD.py
Run (partial, app_service skipped):
  DATABASE_URL=...  python scripts/verify_rlsD.py
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import asyncpg

DATABASE_URL = os.environ.get("DATABASE_URL")
if not DATABASE_URL:
    print("[SKIP] DATABASE_URL not set — skipping verify_rlsD")
    sys.exit(0)

# app_service DSN read from the environment at test time ONLY. Several names
# accepted so Joe can pick whichever is convenient. NEVER committed.
APP_SERVICE_URL = (
    os.environ.get("APP_SERVICE_DATABASE_URL")
    or os.environ.get("DATABASE_URL_APP_SERVICE")
    or os.environ.get("RLS_APP_SERVICE_DATABASE_URL")
)

# ── The full batch (all STANDARD DIRECT org_id, NOT NULL) ───────────────────
BATCH_TABLES = [
    "deals", "deal_ai_summaries", "deal_documents", "deal_interest",
    "deal_scores", "deal_votes", "investment_stage_history",
    "member_investments", "investment_profile_answers",
    "investment_profile_extractions", "investment_profile_questions",
]  # 11

# ── Stable test identifiers (deleted by exact id at teardown) ───────────────
ORG_A = "99000000-0000-0000-0000-0000000da0a0"   # the "home" org
ORG_B = "99000000-0000-0000-0000-0000000db0b0"   # a different org

USER_A = "99000000-0000-0000-0000-00000da0e001"  # users row (ORG_A)
USER_B = "99000000-0000-0000-0000-00000db0e001"  # users row (ORG_B)

DEAL_A = "99000000-0000-0000-0000-00000da0d001"  # deals row (ORG_A) — under test
DEAL_B = "99000000-0000-0000-0000-00000db0d001"  # deals row (ORG_B)

SCORE_A = "99000000-0000-0000-0000-00000da05001"  # deal_scores (ORG_A) — under test
SCORE_B = "99000000-0000-0000-0000-00000db05001"  # deal_scores (ORG_B)

VOTE_A = "99000000-0000-0000-0000-00000da0f001"   # deal_votes (ORG_A) — under test
VOTE_B = "99000000-0000-0000-0000-00000db0f001"   # deal_votes (ORG_B)

MI_A = "99000000-0000-0000-0000-00000da0a001"     # member_investments (ORG_A) — under test
MI_B = "99000000-0000-0000-0000-00000db0a001"     # member_investments (ORG_B)

# The four representative tables + the id of the ORG_A row under test in each.
REPRESENTATIVE = [
    ("deals", DEAL_A, "the root/parent"),
    ("member_investments", MI_A, "the member-facing side"),
    ("deal_scores", SCORE_A, "per-deal scoring"),
    ("deal_votes", VOTE_A, "member voting"),
]

passed = 0
failed = 0
skipped = 0


def ok(label):
    global passed
    passed += 1
    print(f"[P] {label}")


def fail(label, reason=""):
    global failed
    failed += 1
    print(f"[F] {label}{': ' + reason if reason else ''}")


def skip(label, reason=""):
    global skipped
    skipped += 1
    print(f"[S] {label}{': ' + reason if reason else ''}")


# ── Seed / teardown (as postgres, bypass) ──────────────────────────────────
async def seed(conn):
    await teardown(conn)  # idempotent: clear any prior run first
    await conn.execute(
        "INSERT INTO organizations (id, name, slug) VALUES "
        "($1,'RLSD Verify A','rlsd-verify-a'), ($2,'RLSD Verify B','rlsd-verify-b') "
        "ON CONFLICT (id) DO NOTHING",
        ORG_A, ORG_B,
    )
    # users — deal_votes.user_id / member_investments.user_id NOT NULL FK users(id).
    # One per org. email is unique; use collision-proof test addresses.
    await conn.execute(
        "INSERT INTO users (id, org_id, email, full_name, role) VALUES "
        "($1,$3,'rlsd-a@verify.invalid','RLSD User A','member'),"
        "($2,$4,'rlsd-b@verify.invalid','RLSD User B','member') "
        "ON CONFLICT (id) DO NOTHING",
        USER_A, USER_B, ORG_A, ORG_B,
    )
    # deals (direct org_id) — the ORG_A row under test + an ORG_B sibling. Parent
    # of the score/vote/investment rows. deal_status defaults to 'draft'.
    await conn.execute(
        "INSERT INTO deals (id, org_id, name) VALUES "
        "($1,$3,'RLSD Deal A'), ($2,$4,'RLSD Deal B') "
        "ON CONFLICT (id) DO NOTHING",
        DEAL_A, DEAL_B, ORG_A, ORG_B,
    )
    # deal_scores (direct org_id) — dimension NOT NULL; score within 0..100 CHECK.
    await conn.execute(
        "INSERT INTO deal_scores (id, org_id, deal_id, dimension, score) VALUES "
        "($1,$3,$5,'thesis',80),"
        "($2,$4,$6,'thesis',80) "
        "ON CONFLICT (id) DO NOTHING",
        SCORE_A, SCORE_B, ORG_A, ORG_B, DEAL_A, DEAL_B,
    )
    # deal_votes (direct org_id) — vote must be -1 or 1 (CHECK); user_id NOT NULL.
    await conn.execute(
        "INSERT INTO deal_votes (id, org_id, deal_id, user_id, vote) VALUES "
        "($1,$3,$5,$7,1),"
        "($2,$4,$6,$8,1) "
        "ON CONFLICT (id) DO NOTHING",
        VOTE_A, VOTE_B, ORG_A, ORG_B, DEAL_A, DEAL_B, USER_A, USER_B,
    )
    # member_investments (direct org_id) — user_id NOT NULL; investment_stage
    # defaults to 'interest_indicated'.
    await conn.execute(
        "INSERT INTO member_investments (id, org_id, deal_id, user_id) VALUES "
        "($1,$3,$5,$7),"
        "($2,$4,$6,$8) "
        "ON CONFLICT (id) DO NOTHING",
        MI_A, MI_B, ORG_A, ORG_B, DEAL_A, DEAL_B, USER_A, USER_B,
    )


async def teardown(conn):
    # FK-safe: children (score/vote/investment) before their parent deals, then
    # users, then organizations. investment_stage_history references
    # member_investments but is never seeded here; delete defensively anyway.
    await conn.execute(
        "DELETE FROM investment_stage_history "
        "WHERE member_investment_id = ANY($1::uuid[])", [MI_A, MI_B])
    await conn.execute(
        "DELETE FROM member_investments WHERE id = ANY($1::uuid[])", [MI_A, MI_B])
    await conn.execute(
        "DELETE FROM deal_votes WHERE id = ANY($1::uuid[])", [VOTE_A, VOTE_B])
    await conn.execute(
        "DELETE FROM deal_scores WHERE id = ANY($1::uuid[])", [SCORE_A, SCORE_B])
    await conn.execute(
        "DELETE FROM deals WHERE id = ANY($1::uuid[])", [DEAL_A, DEAL_B])
    await conn.execute(
        "DELETE FROM users WHERE id = ANY($1::uuid[])", [USER_A, USER_B])
    await conn.execute(
        "DELETE FROM organizations WHERE id = ANY($1::uuid[])", [ORG_A, ORG_B])


# ── Structural checks (as postgres) ─────────────────────────────────────────
async def structural_checks(conn):
    print("\n=== Structural — all 11 batch tables (pg_class / pg_policy) ===")
    rows = await conn.fetch(
        "SELECT c.relname, c.relrowsecurity AS rls, "
        "  (SELECT count(*) FROM pg_policy p WHERE p.polrelid = c.oid) AS npol "
        "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE n.nspname = 'public' AND c.relname = ANY($1::text[]) "
        "ORDER BY c.relname",
        BATCH_TABLES,
    )
    found = {r["relname"]: r for r in rows}
    for t in BATCH_TABLES:
        r = found.get(t)
        if r is None:
            fail(f"[struct] {t}: table present in DB")
        elif r["rls"] and r["npol"] >= 1:
            ok(f"[struct] {t}: RLS enabled + {r['npol']} policy")
        else:
            fail(f"[struct] {t}: RLS enabled + >=1 policy",
                 f"rls={r['rls']} npol={r['npol']}")
    (ok if len(found) == 11 else fail)(
        f"[struct] all 11 batch tables present (found {len(found)})")

    # Every table uses the direct org_id NULLIF shape + super_admin escape on
    # BOTH qual (read) and with_check (write).
    pols = {p["tablename"]: p for p in await conn.fetch(
        "SELECT tablename, qual, with_check FROM pg_policies "
        "WHERE tablename = ANY($1::text[])", BATCH_TABLES)}
    bad = []
    for t in BATCH_TABLES:
        p = pols.get(t)
        q = (p["qual"] or "").lower() if p else ""
        w = (p["with_check"] or "").lower() if p else ""
        for clause in (q, w):
            if not ("org_id = (nullif(current_setting('app.current_org_id'"
                    in clause and "is_super_admin" in clause):
                bad.append(t)
                break
    (ok if not bad else fail)(
        "[struct] all 11 tables use the direct org_id NULLIF pattern + "
        "super_admin escape on read AND write" +
        (f" (offenders: {bad})" if bad else ""))

    # Confirm each table actually has its own org_id column (direct, not indirect).
    cols = {r["table_name"] for r in await conn.fetch(
        "SELECT table_name FROM information_schema.columns "
        "WHERE table_schema='public' AND column_name='org_id' "
        "AND table_name = ANY($1::text[])", BATCH_TABLES)}
    missing = [t for t in BATCH_TABLES if t not in cols]
    (ok if not missing else fail)(
        "[struct] all 11 tables have their own org_id column (direct policy)" +
        (f" (missing: {missing})" if missing else ""))


# ── Bypass role (postgres) unaffected — Task 2 ─────────────────────────────
async def bypass_checks(conn):
    print("\n=== Bypass role (postgres) unaffected — production unchanged ===")
    await conn.execute(
        "SELECT set_config('app.current_org_id','',true), "
        "       set_config('app.is_super_admin','false',true)")
    # Sample of the batch: with NO context, the bypass role still sees BOTH the
    # ORG_A and ORG_B seeded rows in each table.
    checks = [
        ("deals", [DEAL_A, DEAL_B], 2),
        ("member_investments", [MI_A, MI_B], 2),
        ("deal_scores", [SCORE_A, SCORE_B], 2),
        ("deal_votes", [VOTE_A, VOTE_B], 2),
    ]
    for table, ids, expect in checks:
        got = await conn.fetchval(
            f"SELECT count(*) FROM {table} WHERE id = ANY($1::uuid[])", ids)
        (ok if got == expect else fail)(
            f"[bypass] postgres sees all {expect} seeded {table} rows with NO "
            f"context (got {got})")

    # Even under a WRONG org context (ORG_B), the bypass role still sees the
    # ORG_A deal — proves RLS is genuinely inert for production, not merely
    # coincidentally matching the set org.
    await conn.execute("SELECT set_config('app.current_org_id',$1,true)", ORG_B)
    got = await conn.fetchval("SELECT count(*) FROM deals WHERE id = $1", DEAL_A)
    await conn.execute("SELECT set_config('app.current_org_id','',true)")
    (ok if got == 1 else fail)(
        f"[bypass] postgres still sees the ORG_A deal even under ORG_B context — "
        f"RLS fully inert for the bypass role (got {got}, expect 1)")


# ── app_service helpers (non-bypass) ────────────────────────────────────────
async def _apply(conn, org_id=None, is_super=False):
    """Mirror services.database._apply_rls_settings: SET LOCAL the GUCs ('' when
    absent) inside the caller's transaction."""
    await conn.execute(
        "SELECT set_config('app.current_org_id', $1, true),"
        "       set_config('app.is_super_admin', $2, true)",
        org_id or "", "true" if is_super else "false")


async def _count(conn, sql, params, **ctx):
    async with conn.transaction():
        await _apply(conn, **ctx)
        return await conn.fetchval(sql, *params)


# ── Live RLS isolation — 4 representative tables x 4 checks = 16 ─────────────
async def isolation_checks(conn):
    for table, row_id, role in REPRESENTATIVE:
        print(f"\n=== Live isolation — {table} (direct org_id, {role}) ===")
        sql = f"SELECT count(*) FROM {table} WHERE id = $1"

        c = await _count(conn, sql, [row_id], org_id=ORG_A)
        (ok if c == 1 else fail)(
            f"[rls] {table}: SAME-org context (ORG_A) sees the row "
            f"(got {c}, expect 1)")

        c = await _count(conn, sql, [row_id], org_id=ORG_B)
        (ok if c == 0 else fail)(
            f"[rls] {table}: DIFFERENT-org context (ORG_B) does NOT see the "
            f"ORG_A row (got {c}, expect 0)")

        c = await _count(conn, sql, [row_id], is_super=True)
        (ok if c == 1 else fail)(
            f"[rls] {table}: super_admin sees the row regardless of org "
            f"(got {c}, expect 1)")

        c = await _count(conn, sql, [row_id])
        (ok if c == 0 else fail)(
            f"[rls] {table}: NEITHER context set → zero rows, safe default-deny "
            f"(got {c}, expect 0)")


# ── app_service connection gate ─────────────────────────────────────────────
async def app_service_stage():
    if not APP_SERVICE_URL:
        for table, _, _ in REPRESENTATIVE:
            skip(f"[rls] {table} isolation (4 checks)",
                 "APP_SERVICE_DATABASE_URL not set")
        return

    conn = await asyncpg.connect(
        APP_SERVICE_URL, ssl="require", statement_cache_size=0)
    try:
        role, bypass = await conn.fetchrow(
            "SELECT current_user, "
            "(SELECT rolbypassrls FROM pg_roles WHERE rolname = current_user)")
        if bypass:
            fail("[rls] app_service DSN is a NON-BYPASS role",
                 f"connected as {role!r} with rolbypassrls=true — supply the "
                 "app_service DSN, not a bypass role")
            return
        ok(f"[rls] connected as non-bypass role {role!r} (rolbypassrls=false)")
        await isolation_checks(conn)
    finally:
        await conn.close()


async def db_main():
    conn = await asyncpg.connect(
        DATABASE_URL, ssl="require", statement_cache_size=0)
    try:
        await seed(conn)
        await structural_checks(conn)
        await bypass_checks(conn)
    finally:
        # Keep seeded rows alive for the app_service stage; teardown afterwards.
        await conn.close()

    await app_service_stage()

    # Final teardown + zero-leftover assertion, on a fresh bypass connection.
    conn = await asyncpg.connect(
        DATABASE_URL, ssl="require", statement_cache_size=0)
    try:
        await teardown(conn)
        print("\n=== Teardown — zero leftover TEST rows ===")
        leftovers = 0
        leftovers += await conn.fetchval(
            "SELECT count(*) FROM member_investments WHERE id = ANY($1::uuid[])",
            [MI_A, MI_B])
        leftovers += await conn.fetchval(
            "SELECT count(*) FROM deal_votes WHERE id = ANY($1::uuid[])",
            [VOTE_A, VOTE_B])
        leftovers += await conn.fetchval(
            "SELECT count(*) FROM deal_scores WHERE id = ANY($1::uuid[])",
            [SCORE_A, SCORE_B])
        leftovers += await conn.fetchval(
            "SELECT count(*) FROM deals WHERE id = ANY($1::uuid[])",
            [DEAL_A, DEAL_B])
        leftovers += await conn.fetchval(
            "SELECT count(*) FROM users WHERE id = ANY($1::uuid[])",
            [USER_A, USER_B])
        leftovers += await conn.fetchval(
            "SELECT count(*) FROM organizations WHERE id = ANY($1::uuid[])",
            [ORG_A, ORG_B])
        (ok if leftovers == 0 else fail)(
            f"[teardown] zero leftover TEST rows across all seeded batch tables "
            f"(found {leftovers})")
    finally:
        await conn.close()


def main():
    print("=== verify_rlsD — RLS Batch D (11 deals/marketplace tables) ===")
    asyncio.run(db_main())

    print(f"\n=== RESULT: {passed} passed, {failed} failed, {skipped} skipped ===")
    print("NOTE: DATABASE_URL was NOT modified anywhere in this batch. The live "
          "app still connects as the RLS-bypassing 'postgres' role; production "
          "behavior is unchanged. The app_service assertions prove the policies "
          "for the future non-bypass connection ONLY.")
    if failed:
        print("FAIL")
        sys.exit(1)
    if skipped:
        print("PASS (with skips — supply APP_SERVICE_DATABASE_URL for the full "
              "live RLS isolation assertions)")
    else:
        print("PASS")
    sys.exit(0)


if __name__ == "__main__":
    main()
