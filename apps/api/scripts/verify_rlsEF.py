"""RLS Batch E+F verify — Assistant/Notifications/Audit (14) + Config/Reference
(4) = 18 tables. FINAL batch of the RLS rollout.

Proves the RLS policies applied to the 18 tables in this combined final batch
behave correctly under the NON-BYPASS ``app_service`` role, using REAL rows — not
code inspection — and that production (the bypass ``postgres`` role) is completely
unchanged. This batch has TWO policy shapes, both already applied by Joe via
Supabase MCP (all 18 tables confirmed RLS enabled + exactly 1 policy each):

  (a) STANDARD DIRECT (17 tables — own org_id, NOT NULL). Same NULLIF shape as
      Batches A/B/D, applied identically on BOTH qual (USING) and with_check:
        (org_id = NULLIF(current_setting('app.current_org_id', true), '')::uuid)
        OR current_setting('app.is_super_admin', true) = 'true'
      Tables: assistant_activities, assistant_action_catalog,
      assistant_autonomy_prefs, assistant_conversations, profile_conversations,
      dashboard_briefs, member_todos, notifications, notification_recipients,
      notification_delivery_log, user_notification_preferences, audit_log,
      compliance_records, compliance_override_requests, config, org_settings,
      roles.

  (b) GLOBAL-READ / ORG-WRITE (1 table — reference_data). Holds REAL global
      platform reference data (155 rows across 11 distinct lists — countries,
      currencies, months, etc.), all with org_id IS NULL. Same asymmetric pattern
      as Batch C's fx_rates / transaction_types:
        READ  (USING):      org_id IS NULL  OR own-org  OR super_admin
        WRITE (WITH CHECK):                   own-org  OR super_admin   (NO NULL!)
      So a non-super tenant can READ the global rows but can NEVER write a
      global/NULL-org row — only rows scoped to their own org. A super_admin can
      write global rows. This protects platform reference data from tenants.

Live proofs (as the non-bypass app_service role):
  * 4 representative STANDARD tables — assistant_activities, audit_log,
    org_settings, member_todos — each getting the full four-way isolation matrix:
    same-org visible / different-org invisible / super_admin bypass /
    neither-context → zero rows. 4 tables x 4 checks = 16 live assertions.
  * reference_data (global): (1) the full 155 global (NULL-org) rows are readable
    under ANY org context (org A / org B / no context); (2) a non-super INSERT of
    a NULL-org row is REJECTED by WITH CHECK; (3) a non-super INSERT of an OWN-org
    row is ACCEPTED; (4) a super_admin INSERT of a NULL-org (global) row is
    ACCEPTED. Every write attempt runs inside a transaction that is ALWAYS rolled
    back — nothing is ever committed to reference_data and the real 155 rows are
    never touched.
  * Maker-checker on assistant_activities — proven STILL correct UNDER the new
    org-isolation RLS policy (app_service, org context = ORG_A): a self-approval
    (approved_by = proposed_by) is REJECTED by the
    assistant_activities_maker_checker_chk CHECK constraint; a different-approver
    (approved_by <> proposed_by) is ACCEPTED. Both run inside rolled-back
    transactions — nothing is committed.

Maker-checker constraint (introspected from the live DB, not assumed):
  CHECK ((approved_by IS NULL) OR (proposed_by IS NULL)
         OR (approved_by <> proposed_by))

Safety for the sensitive reference table:
  * No test reference_data row is ever committed (all writes rolled back). Test
    rows use a collision-proof list_key prefix 'zz_rlsef%' — no real row uses it.
  * A content hash of ALL reference_data rows is captured before and after the run
    and asserted identical — the real 155 rows are proven byte-for-byte unchanged.
  * Teardown (at start AND end) removes only clearly-test rows by list_key prefix
    'zz_rlsef%' and asserts the real reference_data count is exactly 155, never
    touching the real reference data.

Two DSNs:
  * DATABASE_URL             — the ORIGINAL, RLS-BYPASSING ``postgres`` role.
                               Seeding, structural checks, the "bypass role
                               unchanged" assertions, the before/after reference
                               hash, teardown.
  * APP_SERVICE_DATABASE_URL — the NON-BYPASS ``app_service`` role, supplied by
                               Joe at test time ONLY. Never hardcoded, never
                               written to any file. Runs the live RLS isolation +
                               global-reference read/write + maker-checker
                               assertions. If absent, those are SKIPPED (not
                               failed).

This script changes NOTHING about production. DATABASE_URL is not modified
anywhere; the live app remains on the ``postgres`` role. The connection switch to
app_service is a separate, deliberate, manual step for later — NOT part of this
sprint, even though this is the final RLS batch. Pass/fail only, no interactive
prompts, idempotent — teardown at start AND end by stable ids / test prefix.

Run (full):
  DATABASE_URL=...  APP_SERVICE_DATABASE_URL=...  python scripts/verify_rlsEF.py
Run (partial, app_service skipped):
  DATABASE_URL=...  python scripts/verify_rlsEF.py
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import asyncpg

DATABASE_URL = os.environ.get("DATABASE_URL")
if not DATABASE_URL:
    print("[SKIP] DATABASE_URL not set — skipping verify_rlsEF")
    sys.exit(0)

# app_service DSN read from the environment at test time ONLY. Several names
# accepted so Joe can pick whichever is convenient. NEVER committed.
APP_SERVICE_URL = (
    os.environ.get("APP_SERVICE_DATABASE_URL")
    or os.environ.get("DATABASE_URL_APP_SERVICE")
    or os.environ.get("RLS_APP_SERVICE_DATABASE_URL")
)

# ── The full batch ──────────────────────────────────────────────────────────
STANDARD_TABLES = [
    "assistant_activities", "assistant_action_catalog", "assistant_autonomy_prefs",
    "assistant_conversations", "profile_conversations", "dashboard_briefs",
    "member_todos", "notifications", "notification_recipients",
    "notification_delivery_log", "user_notification_preferences", "audit_log",
    "compliance_records", "compliance_override_requests", "config",
    "org_settings", "roles",
]  # 17
GLOBAL_TABLES = ["reference_data"]  # 1
BATCH_TABLES = STANDARD_TABLES + GLOBAL_TABLES  # 18

REFERENCE_EXPECTED_ROWS = 155   # real global rows today (all org_id IS NULL)
REFERENCE_EXPECTED_LISTS = 11   # distinct list_key values

# ── Stable test identifiers (deleted by exact id at teardown) ───────────────
ORG_A = "99000000-0000-0000-0000-0000000ea0a0"   # the "home" org
ORG_B = "99000000-0000-0000-0000-0000000eb0b0"   # a different org

USER_A = "99000000-0000-0000-0000-00000ea0e001"  # users row (ORG_A)
USER_B = "99000000-0000-0000-0000-00000eb0e001"  # users row (ORG_B)

AA_A = "99000000-0000-0000-0000-00000ea0a001"    # assistant_activities (ORG_A) — under test
AA_B = "99000000-0000-0000-0000-00000eb0a001"    # assistant_activities (ORG_B)
AUD_A = "99000000-0000-0000-0000-00000ea0d001"   # audit_log (ORG_A) — under test
AUD_B = "99000000-0000-0000-0000-00000eb0d001"   # audit_log (ORG_B)
OS_A = "99000000-0000-0000-0000-00000ea05001"    # org_settings (ORG_A) — under test
OS_B = "99000000-0000-0000-0000-00000eb05001"    # org_settings (ORG_B)
MT_A = "99000000-0000-0000-0000-00000ea0c001"    # member_todos (ORG_A) — under test
MT_B = "99000000-0000-0000-0000-00000eb0c001"    # member_todos (ORG_B)

# The four representative STANDARD tables + the id of the ORG_A row under test.
REPRESENTATIVE = [
    ("assistant_activities", AA_A, "assistant maker-checker table"),
    ("audit_log", AUD_A, "audit trail"),
    ("org_settings", OS_A, "config/settings"),
    ("member_todos", MT_A, "member-facing todos"),
]

# reference_data test WRITE values — used ONLY inside rolled-back transactions, so
# never committed. Collision-proof against the real 155 rows: no real row uses a
# 'zz_rlsef' list_key.
RD_TEST_ID = "99000000-0000-0000-0000-00000eff0001"
RD_TEST_LIST = "zz_rlsef_list"
RD_TEST_CODE = "ZZTEST"

# assistant_activities maker-checker test WRITE values (rolled back, never committed).
AA_MC_ID = "99000000-0000-0000-0000-00000eff00c1"

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
        "($1,'RLSEF Verify A','rlsef-verify-a'), ($2,'RLSEF Verify B','rlsef-verify-b') "
        "ON CONFLICT (id) DO NOTHING",
        ORG_A, ORG_B,
    )
    # users — FK target for assistant_activities.user_id / proposed_by / approved_by
    # and member_todos.user_id. One per org; email is unique so use test addresses.
    await conn.execute(
        "INSERT INTO users (id, org_id, email, full_name, role) VALUES "
        "($1,$3,'rlsef-a@verify.invalid','RLSEF User A','member'),"
        "($2,$4,'rlsef-b@verify.invalid','RLSEF User B','member') "
        "ON CONFLICT (id) DO NOTHING",
        USER_A, USER_B, ORG_A, ORG_B,
    )
    # assistant_activities (direct org_id) — user_id/action_key/title NOT NULL.
    # proposed_by/approved_by left NULL on the seeded rows (maker-checker satisfied).
    await conn.execute(
        "INSERT INTO assistant_activities (id, org_id, user_id, action_key, title) VALUES "
        "($1,$3,$5,'rlsef.noop','RLSEF Activity A'),"
        "($2,$4,$6,'rlsef.noop','RLSEF Activity B') "
        "ON CONFLICT (id) DO NOTHING",
        AA_A, AA_B, ORG_A, ORG_B, USER_A, USER_B,
    )
    # audit_log (direct org_id) — action NOT NULL; user_id nullable but set here.
    await conn.execute(
        "INSERT INTO audit_log (id, org_id, user_id, action) VALUES "
        "($1,$3,$5,'rlsef.test'),"
        "($2,$4,$6,'rlsef.test') "
        "ON CONFLICT (id) DO NOTHING",
        AUD_A, AUD_B, ORG_A, ORG_B, USER_A, USER_B,
    )
    # org_settings (direct org_id) — setting_key/setting_value(jsonb) NOT NULL;
    # unique (org_id, setting_key).
    await conn.execute(
        "INSERT INTO org_settings (id, org_id, setting_key, setting_value) VALUES "
        "($1,$3,'rlsef_test','{\"v\":1}'::jsonb),"
        "($2,$4,'rlsef_test','{\"v\":1}'::jsonb) "
        "ON CONFLICT (id) DO NOTHING",
        OS_A, OS_B, ORG_A, ORG_B,
    )
    # member_todos (direct org_id) — user_id/category/title NOT NULL.
    await conn.execute(
        "INSERT INTO member_todos (id, org_id, user_id, category, title) VALUES "
        "($1,$3,$5,'rlsef','RLSEF Todo A'),"
        "($2,$4,$6,'rlsef','RLSEF Todo B') "
        "ON CONFLICT (id) DO NOTHING",
        MT_A, MT_B, ORG_A, ORG_B, USER_A, USER_B,
    )


async def teardown(conn):
    # FK-safe: the four standard rows reference users/organizations (and entities,
    # unused here); none reference each other. Delete them, then any test
    # reference_data, then users, then organizations.
    await conn.execute(
        "DELETE FROM assistant_activities WHERE id = ANY($1::uuid[])", [AA_A, AA_B, AA_MC_ID])
    await conn.execute(
        "DELETE FROM audit_log WHERE id = ANY($1::uuid[])", [AUD_A, AUD_B])
    await conn.execute(
        "DELETE FROM org_settings WHERE id = ANY($1::uuid[])", [OS_A, OS_B])
    await conn.execute(
        "DELETE FROM member_todos WHERE id = ANY($1::uuid[])", [MT_A, MT_B])
    # Defensive: remove any TEST reference rows a crashed prior run might have
    # committed. This predicate matches ONLY test rows, never the real 155.
    await conn.execute(
        "DELETE FROM reference_data WHERE list_key LIKE 'zz_rlsef%'")
    await conn.execute(
        "DELETE FROM users WHERE id = ANY($1::uuid[])", [USER_A, USER_B])
    await conn.execute(
        "DELETE FROM organizations WHERE id = ANY($1::uuid[])", [ORG_A, ORG_B])


# ── reference_data content hash (as postgres — sees ALL rows) ───────────────
async def ref_hash(conn):
    """md5 over every reference_data row, ordered by id. Captured before and after
    the run to prove the real 155 rows are untouched."""
    h = await conn.fetchval(
        "SELECT md5(coalesce(string_agg(t::text, '|' ORDER BY id), '')) "
        "FROM reference_data t")
    n = await conn.fetchval("SELECT count(*) FROM reference_data")
    n_null = await conn.fetchval(
        "SELECT count(*) FROM reference_data WHERE org_id IS NULL")
    n_lists = await conn.fetchval(
        "SELECT count(DISTINCT list_key) FROM reference_data")
    return {"hash": h, "n": n, "n_null": n_null, "n_lists": n_lists}


# ── Structural checks (as postgres) ─────────────────────────────────────────
async def structural_checks(conn):
    print("\n=== Structural — all 18 batch tables (pg_class / pg_policy) ===")
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
    (ok if len(found) == 18 else fail)(
        f"[struct] all 18 batch tables present (found {len(found)})")

    pols = {p["tablename"]: p for p in await conn.fetch(
        "SELECT tablename, qual, with_check FROM pg_policies "
        "WHERE tablename = ANY($1::text[])", BATCH_TABLES)}

    # (a) the 17 standard tables: direct org_id NULLIF shape on BOTH qual+with_check
    bad = []
    for t in STANDARD_TABLES:
        p = pols.get(t)
        q = (p["qual"] or "").lower() if p else ""
        w = (p["with_check"] or "").lower() if p else ""
        for clause in (q, w):
            if not ("org_id = (nullif(current_setting('app.current_org_id'"
                    in clause and "is_super_admin" in clause):
                bad.append(t)
                break
    (ok if not bad else fail)(
        "[struct] all 17 STANDARD tables use the direct org_id NULLIF pattern + "
        "super_admin escape on read AND write" + (f" (offenders: {bad})" if bad else ""))

    # Confirm each standard table actually has its own org_id column (direct).
    cols = {r["table_name"] for r in await conn.fetch(
        "SELECT table_name FROM information_schema.columns "
        "WHERE table_schema='public' AND column_name='org_id' "
        "AND table_name = ANY($1::text[])", STANDARD_TABLES)}
    missing = [t for t in STANDARD_TABLES if t not in cols]
    (ok if not missing else fail)(
        "[struct] all 17 standard tables have their own org_id column (direct policy)" +
        (f" (missing: {missing})" if missing else ""))

    # (b) reference_data: ASYMMETRIC. READ allows org_id IS NULL; WRITE
    #     (with_check) does NOT allow org_id IS NULL.
    p = pols.get("reference_data")
    q = (p["qual"] or "").lower() if p else ""
    w = (p["with_check"] or "").lower() if p else ""
    read_allows_null = "org_id is null" in q
    write_allows_null = "org_id is null" in w
    write_scoped = ("org_id = (nullif(current_setting('app.current_org_id'"
                    in w and "is_super_admin" in w)
    if read_allows_null and not write_allows_null and write_scoped:
        ok("[struct] reference_data: ASYMMETRIC — READ allows org_id IS NULL "
           "(global); WRITE (with_check) does NOT allow NULL (own-org / super only)")
    else:
        fail("[struct] reference_data: asymmetric global-read / org-write policy",
             f"read_null={read_allows_null} write_null={write_allows_null} "
             f"write_scoped={write_scoped}")

    # Maker-checker CHECK constraint still present on assistant_activities.
    mc = await conn.fetchval(
        "SELECT pg_get_constraintdef(con.oid) FROM pg_constraint con "
        "JOIN pg_class c ON c.oid=con.conrelid "
        "JOIN pg_namespace n ON n.oid=c.relnamespace "
        "WHERE n.nspname='public' AND c.relname='assistant_activities' "
        "AND con.conname='assistant_activities_maker_checker_chk'")
    if mc and "approved_by <> proposed_by" in mc.replace(" ", " "):
        ok("[struct] assistant_activities_maker_checker_chk CHECK constraint present "
           "(approved_by <> proposed_by) — coexists with the org-isolation RLS policy")
    else:
        fail("[struct] assistant_activities maker-checker CHECK constraint present",
             f"def={mc!r}")


# ── Bypass role (postgres) unaffected — Task 4 ─────────────────────────────
async def bypass_checks(conn, base):
    print("\n=== Bypass role (postgres) unaffected — production unchanged ===")
    await conn.execute(
        "SELECT set_config('app.current_org_id','',true), "
        "       set_config('app.is_super_admin','false',true)")
    # Sample spanning BOTH policy shapes: the 4 standard tables (both org rows) +
    # reference_data. With NO context the bypass role still sees every seeded row.
    checks = [
        ("assistant_activities", [AA_A, AA_B], 2),
        ("audit_log", [AUD_A, AUD_B], 2),
        ("org_settings", [OS_A, OS_B], 2),
        ("member_todos", [MT_A, MT_B], 2),
    ]
    for table, ids, expect in checks:
        got = await conn.fetchval(
            f"SELECT count(*) FROM {table} WHERE id = ANY($1::uuid[])", ids)
        (ok if got == expect else fail)(
            f"[bypass] postgres sees all {expect} seeded {table} rows with NO "
            f"context (got {got})")

    # reference_data (global shape): bypass role with NO context still sees all 155.
    got = await conn.fetchval("SELECT count(*) FROM reference_data")
    (ok if got == base["n"] else fail)(
        f"[bypass] postgres sees all {base['n']} reference_data rows with no context "
        f"(got {got}) — RLS inert for the bypass role")

    # Even under a WRONG org context (ORG_B), the bypass role still sees the ORG_A
    # assistant_activity — proves RLS is genuinely inert for production, not merely
    # coincidentally matching the set org.
    await conn.execute("SELECT set_config('app.current_org_id',$1,true)", ORG_B)
    got = await conn.fetchval(
        "SELECT count(*) FROM assistant_activities WHERE id = $1", AA_A)
    await conn.execute("SELECT set_config('app.current_org_id','',true)")
    (ok if got == 1 else fail)(
        f"[bypass] postgres still sees the ORG_A assistant_activity even under ORG_B "
        f"context — RLS fully inert for the bypass role (got {got}, expect 1)")


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


async def _attempt_write(conn, sql, params, **ctx):
    """Run a write under the given RLS context inside a transaction that is ALWAYS
    rolled back — nothing is ever committed. Returns None on success, else the
    raised PostgresError."""
    tr = conn.transaction()
    await tr.start()
    try:
        await _apply(conn, **ctx)
        await conn.execute(sql, *params)
        return None
    except asyncpg.PostgresError as e:
        return e
    finally:
        await tr.rollback()


def _is_rls_violation(err):
    return err is not None and "row-level security" in str(err).lower()


def _is_maker_checker_violation(err):
    return err is not None and "maker_checker" in str(err).lower()


# ── Live RLS isolation — 4 representative STANDARD tables x 4 checks = 16 ────
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


# ── Live global-reference read + write — reference_data ─────────────────────
async def global_reference_checks(conn, base):
    print("\n=== Live global-reference — reference_data ===")
    insert = ("INSERT INTO reference_data (id, org_id, list_key, code, label) "
              "VALUES ($1,$2,$3,$4,'RLSEF Verify')")
    read_sql = "SELECT count(*) FROM reference_data WHERE org_id IS NULL"

    # (1) READ: the full 155 global row count is visible under ANY context.
    a = await _count(conn, read_sql, [], org_id=ORG_A)
    b = await _count(conn, read_sql, [], org_id=ORG_B)
    n = await _count(conn, read_sql, [])  # no context, just authenticated
    expect = base["n_null"]
    if a == b == n == expect:
        ok(f"[rls] reference_data: all {expect} global (NULL-org) rows readable "
           f"under ANY context — ORG_A={a}, ORG_B={b}, no-context={n}")
    else:
        fail("[rls] reference_data: global rows readable under any org context",
             f"ORG_A={a} ORG_B={b} no-context={n} expect={expect}")

    # (2) WRITE: non-super INSERT of a NULL-org row is REJECTED by WITH CHECK.
    err = await _attempt_write(
        conn, insert, [RD_TEST_ID, None, RD_TEST_LIST, RD_TEST_CODE], org_id=ORG_A)
    (ok if _is_rls_violation(err) else fail)(
        "[rls] reference_data: non-super-admin INSERT of a NULL-org (global) row is "
        "REJECTED by WITH CHECK" +
        ("" if _is_rls_violation(err) else f" (got: {err!r})"))

    # (3) WRITE: non-super INSERT of an OWN-org row is ACCEPTED (rolled back).
    err = await _attempt_write(
        conn, insert, [RD_TEST_ID, ORG_A, RD_TEST_LIST, RD_TEST_CODE], org_id=ORG_A)
    (ok if err is None else fail)(
        "[rls] reference_data: non-super-admin CAN INSERT an OWN-org (ORG_A) row — "
        "WITH CHECK accepts org-scoped writes (rolled back)" +
        ("" if err is None else f" (got: {err!r})"))

    # (4) WRITE: super_admin INSERT of a NULL-org (global) row is ACCEPTED.
    err = await _attempt_write(
        conn, insert, [RD_TEST_ID, None, RD_TEST_LIST, RD_TEST_CODE], is_super=True)
    (ok if err is None else fail)(
        "[rls] reference_data: super_admin CAN INSERT a NULL-org (global) row — "
        "platform reference data is editable by super_admin (rolled back)" +
        ("" if err is None else f" (got: {err!r})"))


# ── Live maker-checker under the new RLS policy — assistant_activities ──────
async def maker_checker_checks(conn):
    print("\n=== Live maker-checker — assistant_activities (under new RLS policy) ===")
    # Both writes run as app_service with org_id=ORG_A so the RLS WITH CHECK passes;
    # what we are proving is the maker-checker CHECK constraint still fires (or not)
    # INDEPENDENTLY of, and alongside, the org-isolation policy. Both rolled back.
    insert = ("INSERT INTO assistant_activities "
              "(id, org_id, user_id, action_key, title, proposed_by, approved_by) "
              "VALUES ($1,$2,$3,'rlsef.mc','RLSEF MakerChecker',$4,$5)")

    # Self-approval: approved_by = proposed_by = USER_A → REJECTED by CHECK.
    err = await _attempt_write(
        conn, insert, [AA_MC_ID, ORG_A, USER_A, USER_A, USER_A], org_id=ORG_A)
    (ok if _is_maker_checker_violation(err) else fail)(
        "[rls] assistant_activities: SELF-approval (approved_by = proposed_by) is "
        "REJECTED by the maker-checker CHECK constraint, under the new org-isolation "
        "RLS context" +
        ("" if _is_maker_checker_violation(err) else f" (got: {err!r})"))

    # Different approver: proposed_by = USER_A, approved_by = USER_B → ACCEPTED.
    err = await _attempt_write(
        conn, insert, [AA_MC_ID, ORG_A, USER_A, USER_A, USER_B], org_id=ORG_A)
    (ok if err is None else fail)(
        "[rls] assistant_activities: DIFFERENT-approver (approved_by <> proposed_by) "
        "is ACCEPTED — maker-checker allows a genuine second approver, RLS WITH CHECK "
        "passes for the in-org write (rolled back)" +
        ("" if err is None else f" (got: {err!r})"))


# ── app_service connection gate ─────────────────────────────────────────────
async def app_service_stage(base):
    if not APP_SERVICE_URL:
        for table, _, _ in REPRESENTATIVE:
            skip(f"[rls] {table} isolation (4 checks)",
                 "APP_SERVICE_DATABASE_URL not set")
        skip("[rls] reference_data global read + 3 write checks",
             "APP_SERVICE_DATABASE_URL not set")
        skip("[rls] assistant_activities maker-checker (2 checks)",
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
        await global_reference_checks(conn, base)
        await maker_checker_checks(conn)
    finally:
        await conn.close()


async def db_main():
    conn = await asyncpg.connect(
        DATABASE_URL, ssl="require", statement_cache_size=0)
    try:
        base = await ref_hash(conn)  # BEFORE snapshot of reference_data
        print("=== Reference-data baseline (as postgres) ===")
        (ok if base["n"] == REFERENCE_EXPECTED_ROWS else fail)(
            f"[ref] reference_data has the expected {REFERENCE_EXPECTED_ROWS} real "
            f"rows (got {base['n']})")
        (ok if base["n_null"] == base["n"] else fail)(
            f"[ref] all reference_data rows are global (org_id IS NULL) "
            f"({base['n_null']}/{base['n']})")
        (ok if base["n_lists"] == REFERENCE_EXPECTED_LISTS else fail)(
            f"[ref] reference_data spans the expected {REFERENCE_EXPECTED_LISTS} "
            f"distinct lists (got {base['n_lists']})")
        await seed(conn)
        await structural_checks(conn)
        await bypass_checks(conn, base)
    finally:
        # Keep seeded rows alive for the app_service stage; teardown afterwards.
        await conn.close()

    await app_service_stage(base)

    # Final teardown + reference-data integrity, on a fresh bypass connection.
    conn = await asyncpg.connect(
        DATABASE_URL, ssl="require", statement_cache_size=0)
    try:
        after = await ref_hash(conn)
        print("\n=== Reference-data integrity — real rows UNTOUCHED ===")
        (ok if after["hash"] == base["hash"] and after["n"] == base["n"] else fail)(
            f"[ref] the {base['n']} real reference_data rows are byte-for-byte "
            f"unchanged after the run (count {base['n']}→{after['n']}, "
            f"hash {'match' if after['hash'] == base['hash'] else 'DIFF'})")

        await teardown(conn)
        print("\n=== Teardown — zero leftover TEST rows ===")
        leftovers = 0
        leftovers += await conn.fetchval(
            "SELECT count(*) FROM assistant_activities WHERE id = ANY($1::uuid[])",
            [AA_A, AA_B, AA_MC_ID])
        leftovers += await conn.fetchval(
            "SELECT count(*) FROM audit_log WHERE id = ANY($1::uuid[])",
            [AUD_A, AUD_B])
        leftovers += await conn.fetchval(
            "SELECT count(*) FROM org_settings WHERE id = ANY($1::uuid[])",
            [OS_A, OS_B])
        leftovers += await conn.fetchval(
            "SELECT count(*) FROM member_todos WHERE id = ANY($1::uuid[])",
            [MT_A, MT_B])
        leftovers += await conn.fetchval(
            "SELECT count(*) FROM users WHERE id = ANY($1::uuid[])",
            [USER_A, USER_B])
        leftovers += await conn.fetchval(
            "SELECT count(*) FROM organizations WHERE id = ANY($1::uuid[])",
            [ORG_A, ORG_B])
        # And ZERO test reference rows (there should never have been any — all
        # write attempts were rolled back — but assert it explicitly).
        rd_test = await conn.fetchval(
            "SELECT count(*) FROM reference_data WHERE list_key LIKE 'zz_rlsef%'")
        leftovers += rd_test
        (ok if leftovers == 0 else fail)(
            f"[teardown] zero leftover TEST rows across all batch tables "
            f"(found {leftovers}; reference_data test rows={rd_test})")

        # Re-confirm the real reference_data count is still exactly 155 post-teardown.
        final = await ref_hash(conn)
        (ok if final["n"] == REFERENCE_EXPECTED_ROWS else fail)(
            f"[teardown] real reference_data intact post-teardown — "
            f"{final['n']} rows (expect {REFERENCE_EXPECTED_ROWS})")
    finally:
        await conn.close()


def main():
    print("=== verify_rlsEF — RLS Batch E+F (18 assistant/notif/audit + config/ref "
          "tables), FINAL batch ===")
    asyncio.run(db_main())

    print(f"\n=== RESULT: {passed} passed, {failed} failed, {skipped} skipped ===")
    print("NOTE: DATABASE_URL was NOT modified anywhere in this batch. The live "
          "app still connects as the RLS-bypassing 'postgres' role; production "
          "behavior is unchanged. Every reference_data write attempt ran inside a "
          "rolled-back transaction — the real 155 reference rows were never "
          "committed to, and their content hash is proven unchanged. Both "
          "maker-checker writes were also rolled back. Although this is the LAST "
          "RLS batch, switching the app's connection to the non-bypass app_service "
          "role remains a SEPARATE, deliberate, manual step for later — NOT part of "
          "this sprint. The app_service assertions prove the policies for that "
          "future non-bypass connection ONLY.")
    if failed:
        print("FAIL")
        sys.exit(1)
    if skipped:
        print("PASS (with skips — supply APP_SERVICE_DATABASE_URL for the full "
              "live RLS isolation + global-reference + maker-checker assertions)")
    else:
        print("PASS")
    sys.exit(0)


if __name__ == "__main__":
    main()
