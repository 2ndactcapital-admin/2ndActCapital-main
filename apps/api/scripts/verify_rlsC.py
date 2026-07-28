"""RLS Batch C verify — Financial/ledger tables (13 tables). MOST SENSITIVE batch.

Proves the RLS policies applied to the 13 financial/ledger tables behave correctly
under the NON-BYPASS ``app_service`` role, using REAL rows — not code inspection —
and that production (the bypass ``postgres`` role) is completely unchanged. This
batch has THREE distinct policy shapes, all already applied by Joe via Supabase:

  (a) STANDARD DIRECT (10 tables — own org_id, NOT NULL). Same NULLIF shape as
      Batches A/B:
        (org_id = NULLIF(current_setting('app.current_org_id', true), '')::uuid)
        OR current_setting('app.is_super_admin', true) = 'true'
      Tables: chart_of_accounts, journal_entries, ownership_change_log,
      posting_templates, spv_documents, spv_status_history, spv_subscriptions,
      spv_transaction_allocations, spv_transactions, spvs.

  (b) INDIRECT (1 table — journal_lines). journal_lines has NO org_id column of
      its own; its policy reaches org via EXISTS against the PARENT journal_entries
      row (joined on entry_id). Same style as Batch A's household_memberships.

  (c) GLOBAL-READ / ORG-WRITE (2 tables — fx_rates, transaction_types). These hold
      REAL global platform reference data (5 fx_rates rows, 16 transaction_types
      rows), all with org_id IS NULL. Their policy is ASYMMETRIC:
        READ  (USING):      org_id IS NULL  OR own-org  OR super_admin
        WRITE (WITH CHECK):                   own-org  OR super_admin   (NO NULL!)
      So a non-super tenant can READ the global rows but can NEVER write a
      global/NULL-org row — only rows scoped to their own org. A super_admin can
      write global rows. This protects platform reference data from tenants.

Live proofs (as the non-bypass app_service role):
  * spvs (direct):          same-org visible / different-org invisible /
                            super_admin bypass / neither-context → zero rows.
  * journal_lines (indirect): same four, PLUS a proof that gating is by the PARENT
                            journal_entries.org_id — the SAME membership row is
                            visible under the parent's org context and invisible
                            under a different org, and each org sees only its OWN
                            parent's lines (household_memberships-style proof).
  * fx_rates + transaction_types (global): (1) the full global row count is
                            readable under ANY org context (org A / org B / no
                            context); (2) a non-super INSERT of a NULL-org row is
                            REJECTED by WITH CHECK; (3) a non-super INSERT of an
                            OWN-org row is ACCEPTED; (4) a super_admin INSERT of a
                            NULL-org (global) row is ACCEPTED. Every write attempt
                            runs inside a transaction that is ALWAYS rolled back —
                            nothing is ever committed to these reference tables and
                            the real 5 + 16 rows are never touched.

Safety for the sensitive reference tables:
  * No test fx_rates / transaction_types row is ever committed (all writes rolled
    back). Test values are deliberately collision-proof: fx quote_ccy 'XTS' /
    as_of_date 2099-01-01 (no real row uses either), tt code prefix 'ZZ_RLSC'.
  * A content hash of ALL fx_rates and ALL transaction_types rows is captured
    before and after the run and asserted identical — the real reference data is
    proven byte-for-byte unchanged.
  * Teardown (at start AND end) removes only clearly-test rows by stable id /
    'XTS' / 'ZZ_RLSC%' and asserts zero leftover TEST rows, never touching the
    real reference data.

Two DSNs:
  * DATABASE_URL             — the ORIGINAL, RLS-BYPASSING ``postgres`` role.
                               Seeding, structural checks, the "bypass role
                               unchanged" assertions, the before/after reference
                               hash, teardown.
  * APP_SERVICE_DATABASE_URL — the NON-BYPASS ``app_service`` role, supplied by
                               Joe at test time ONLY. Never hardcoded, never
                               written to any file. Runs the live RLS isolation +
                               global-reference read/write assertions. If absent,
                               those are SKIPPED (not failed).

This script changes NOTHING about production. DATABASE_URL is not modified
anywhere; the live app remains on the ``postgres`` role. Pass/fail only, no
interactive prompts, idempotent — teardown at start AND end by stable ids.

Run (full):
  DATABASE_URL=...  APP_SERVICE_DATABASE_URL=...  python scripts/verify_rlsC.py
Run (partial, app_service skipped):
  DATABASE_URL=...  python scripts/verify_rlsC.py
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import asyncpg

DATABASE_URL = os.environ.get("DATABASE_URL")
if not DATABASE_URL:
    print("[SKIP] DATABASE_URL not set — skipping verify_rlsC")
    sys.exit(0)

# app_service DSN read from the environment at test time ONLY. Several names
# accepted so Joe can pick whichever is convenient. NEVER committed.
APP_SERVICE_URL = (
    os.environ.get("APP_SERVICE_DATABASE_URL")
    or os.environ.get("DATABASE_URL_APP_SERVICE")
    or os.environ.get("RLS_APP_SERVICE_DATABASE_URL")
)

# ── The full batch ──────────────────────────────────────────────────────────
DIRECT_TABLES = [
    "chart_of_accounts", "journal_entries", "ownership_change_log",
    "posting_templates", "spv_documents", "spv_status_history",
    "spv_subscriptions", "spv_transaction_allocations", "spv_transactions",
    "spvs",
]
INDIRECT_TABLES = ["journal_lines"]
GLOBAL_TABLES = ["fx_rates", "transaction_types"]
BATCH_TABLES = DIRECT_TABLES + INDIRECT_TABLES + GLOBAL_TABLES  # 13

# ── Stable test identifiers (deleted by exact id at teardown) ───────────────
ORG_A = "99000000-0000-0000-0000-0000000ca0a0"   # the "home" org
ORG_B = "99000000-0000-0000-0000-0000000cb0b0"   # a different org

DEAL_A = "99000000-0000-0000-0000-00000ca0d001"
DEAL_B = "99000000-0000-0000-0000-00000cb0d001"
SPV_A = "99000000-0000-0000-0000-00000ca05001"   # spvs row (ORG_A), the row under test

COA_A = "99000000-0000-0000-0000-00000ca0c001"   # chart_of_accounts (ORG_A)
COA_B = "99000000-0000-0000-0000-00000cb0c001"   # chart_of_accounts (ORG_B)

JE_A = "99000000-0000-0000-0000-00000ca0e001"    # journal_entries (ORG_A) — parent of JL_A
JE_B = "99000000-0000-0000-0000-00000cb0e001"    # journal_entries (ORG_B) — parent of JL_B
JL_A = "99000000-0000-0000-0000-00000ca0f001"    # journal_lines under JE_A (org via parent = ORG_A)
JL_B = "99000000-0000-0000-0000-00000cb0f001"    # journal_lines under JE_B (org via parent = ORG_B)
VEHICLE = "99000000-0000-0000-0000-00000c000001"  # journal_entries.vehicle_id (no FK)

# fx_rates / transaction_types test WRITE values — used ONLY inside rolled-back
# transactions, so never committed. Collision-proof against the real reference
# data (real fx quotes are CAD/CHF/EUR/GBP/JPY; real tt codes never start ZZ_).
FX_TEST_ID = "99000000-0000-0000-0000-00000cff0001"
FX_TEST_QUOTE = "XTS"           # ISO 4217 "test" code — no real fx row uses it
FX_TEST_DATE = "2099-01-01"     # far-future — no real fx row uses it
TT_CODE_NULL = "ZZ_RLSC_GLOBAL"  # attempted global (NULL-org) write
TT_CODE_ORG = "ZZ_RLSC_ORG"      # attempted own-org write

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
        "($1,'RLSC Verify A','rlsc-verify-a'), ($2,'RLSC Verify B','rlsc-verify-b') "
        "ON CONFLICT (id) DO NOTHING",
        ORG_A, ORG_B,
    )
    # deals — spvs.deal_id is NOT NULL FK deals(id). One per org.
    await conn.execute(
        "INSERT INTO deals (id, org_id, name) VALUES "
        "($1,$3,'RLSC Deal A'), ($2,$4,'RLSC Deal B') "
        "ON CONFLICT (id) DO NOTHING",
        DEAL_A, DEAL_B, ORG_A, ORG_B,
    )
    # spvs (direct org_id) — the ORG_A row under test.
    await conn.execute(
        "INSERT INTO spvs (id, org_id, deal_id, name) VALUES "
        "($1,$2,$3,'RLSC SPV A') ON CONFLICT (id) DO NOTHING",
        SPV_A, ORG_A, DEAL_A,
    )
    # chart_of_accounts (direct org_id) — journal_lines.account_id NOT NULL FK.
    await conn.execute(
        "INSERT INTO chart_of_accounts "
        "(id, org_id, code, name, account_type, normal_balance) VALUES "
        "($1,$3,'1000','RLSC Cash A','ASSET','D'),"
        "($2,$4,'1000','RLSC Cash B','ASSET','D') "
        "ON CONFLICT (id) DO NOTHING",
        COA_A, COA_B, ORG_A, ORG_B,
    )
    # journal_entries (direct org_id) — parents of the journal_lines rows.
    await conn.execute(
        "INSERT INTO journal_entries (id, org_id, vehicle_id, entry_date) VALUES "
        "($1,$3,$5,'2026-01-01'::date),"
        "($2,$4,$5,'2026-01-01'::date) "
        "ON CONFLICT (id) DO NOTHING",
        JE_A, JE_B, ORG_A, ORG_B, VEHICLE,
    )
    # journal_lines (INDIRECT — no org_id; org reached via parent JE). One per
    # org, each hanging off that org's own journal_entry. debit>0/credit=0 to
    # satisfy the jl_one_side CHECK.
    await conn.execute(
        "INSERT INTO journal_lines "
        "(id, entry_id, line_no, account_id, debit, credit) VALUES "
        "($1,$3,1,$5,100,0),"
        "($2,$4,1,$6,200,0) "
        "ON CONFLICT (id) DO NOTHING",
        JL_A, JL_B, JE_A, JE_B, COA_A, COA_B,
    )


async def teardown(conn):
    # FK-safe: children before parents; nothing here references organizations
    # except via the chain below.
    await conn.execute(
        "DELETE FROM journal_lines WHERE id = ANY($1::uuid[])", [JL_A, JL_B])
    await conn.execute(
        "DELETE FROM journal_entries WHERE id = ANY($1::uuid[])", [JE_A, JE_B])
    await conn.execute(
        "DELETE FROM chart_of_accounts WHERE id = ANY($1::uuid[])", [COA_A, COA_B])
    await conn.execute(
        "DELETE FROM spvs WHERE id = ANY($1::uuid[])", [SPV_A])
    await conn.execute(
        "DELETE FROM deals WHERE id = ANY($1::uuid[])", [DEAL_A, DEAL_B])
    # Defensive: remove any TEST reference rows a crashed prior run might have
    # committed. These predicates match ONLY test rows, never the real 5/16.
    await conn.execute(
        "DELETE FROM fx_rates WHERE base_ccy='USD' AND quote_ccy=$1 "
        "AND as_of_date=$2::date",
        FX_TEST_QUOTE, FX_TEST_DATE,
    )
    await conn.execute(
        "DELETE FROM transaction_types WHERE code LIKE 'ZZ_RLSC%'")
    await conn.execute(
        "DELETE FROM organizations WHERE id = ANY($1::uuid[])", [ORG_A, ORG_B])


# ── Reference-data content hashes (as postgres — sees ALL rows) ─────────────
async def ref_hashes(conn):
    """md5 over every row of the two reference tables, ordered by id. Captured
    before and after the run to prove the real 5 + 16 rows are untouched."""
    fx = await conn.fetchval(
        "SELECT md5(coalesce(string_agg(t::text, '|' ORDER BY id), '')) "
        "FROM fx_rates t")
    tt = await conn.fetchval(
        "SELECT md5(coalesce(string_agg(t::text, '|' ORDER BY id), '')) "
        "FROM transaction_types t")
    fx_n = await conn.fetchval("SELECT count(*) FROM fx_rates")
    tt_n = await conn.fetchval("SELECT count(*) FROM transaction_types")
    fx_null = await conn.fetchval(
        "SELECT count(*) FROM fx_rates WHERE org_id IS NULL")
    tt_null = await conn.fetchval(
        "SELECT count(*) FROM transaction_types WHERE org_id IS NULL")
    return {"fx": fx, "tt": tt, "fx_n": fx_n, "tt_n": tt_n,
            "fx_null": fx_null, "tt_null": tt_null}


# ── Structural checks (as postgres) ─────────────────────────────────────────
async def structural_checks(conn):
    print("\n=== Structural — all 13 batch tables (pg_class / pg_policy) ===")
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
    (ok if len(found) == 13 else fail)(
        f"[struct] all 13 batch tables present (found {len(found)})")

    pols = {p["tablename"]: p for p in await conn.fetch(
        "SELECT tablename, qual, with_check FROM pg_policies "
        "WHERE tablename = ANY($1::text[])", BATCH_TABLES)}

    # (a) the 10 direct tables: direct org_id NULLIF shape on BOTH qual+with_check
    bad = []
    for t in DIRECT_TABLES:
        p = pols.get(t)
        q = (p["qual"] or "").lower() if p else ""
        w = (p["with_check"] or "").lower() if p else ""
        for clause in (q, w):
            if not ("org_id = (nullif(current_setting('app.current_org_id'"
                    in clause and "is_super_admin" in clause):
                bad.append(t)
                break
    (ok if not bad else fail)(
        "[struct] all 10 DIRECT tables use the org_id NULLIF pattern + "
        "super_admin escape on read AND write" + (f" (offenders: {bad})" if bad else ""))

    # (b) journal_lines: indirect via parent journal_entries; no org_id column.
    jl = pols.get("journal_lines")
    jlq = (jl["qual"] or "").lower() if jl else ""
    has_org_col = await conn.fetchval(
        "SELECT count(*) FROM information_schema.columns "
        "WHERE table_schema='public' AND table_name='journal_lines' "
        "AND column_name='org_id'")
    if ("exists" in jlq and "journal_entries" in jlq and "entry_id" in jlq
            and "org_id" in jlq and has_org_col == 0):
        ok("[struct] journal_lines policy reaches org via EXISTS against parent "
           "journal_entries.org_id (joined on entry_id); journal_lines itself has "
           "NO org_id column")
    else:
        fail("[struct] journal_lines parent-EXISTS policy + no own org_id column",
             f"exists={'exists' in jlq} je={'journal_entries' in jlq} "
             f"entry_id={'entry_id' in jlq} own_org_col={has_org_col}")

    # (c) fx_rates + transaction_types: ASYMMETRIC. READ allows org_id IS NULL;
    #     WRITE (with_check) does NOT allow org_id IS NULL.
    for t in GLOBAL_TABLES:
        p = pols.get(t)
        q = (p["qual"] or "").lower() if p else ""
        w = (p["with_check"] or "").lower() if p else ""
        read_allows_null = "org_id is null" in q
        write_allows_null = "org_id is null" in w
        write_scoped = ("org_id = (nullif(current_setting('app.current_org_id'"
                        in w and "is_super_admin" in w)
        if read_allows_null and not write_allows_null and write_scoped:
            ok(f"[struct] {t}: ASYMMETRIC — READ allows org_id IS NULL (global); "
               f"WRITE (with_check) does NOT allow NULL (own-org / super only)")
        else:
            fail(f"[struct] {t}: asymmetric global-read / org-write policy",
                 f"read_null={read_allows_null} write_null={write_allows_null} "
                 f"write_scoped={write_scoped}")


# ── Bypass role (postgres) unaffected — Task 3 ──────────────────────────────
async def bypass_checks(conn, base):
    print("\n=== Bypass role (postgres) unaffected — production unchanged ===")
    await conn.execute(
        "SELECT set_config('app.current_org_id','',true), "
        "       set_config('app.is_super_admin','false',true)")
    # Sample of the batch: with NO context, bypass role still sees every seeded row.
    checks = [
        ("spvs", "id = ANY($1::uuid[])", [[SPV_A]], 1),
        ("journal_entries", "id = ANY($1::uuid[])", [[JE_A, JE_B]], 2),
        ("journal_lines", "id = ANY($1::uuid[])", [[JL_A, JL_B]], 2),
        ("chart_of_accounts", "id = ANY($1::uuid[])", [[COA_A, COA_B]], 2),
    ]
    for table, where, params, expect in checks:
        got = await conn.fetchval(
            f"SELECT count(*) FROM {table} WHERE {where}", *params)
        (ok if got == expect else fail)(
            f"[bypass] postgres sees all {expect} seeded {table} rows with NO "
            f"context (got {got})")

    # Global reference tables: bypass role with NO context still sees everything.
    got = await conn.fetchval("SELECT count(*) FROM fx_rates")
    (ok if got == base["fx_n"] else fail)(
        f"[bypass] postgres sees all {base['fx_n']} fx_rates rows with no context "
        f"(got {got}) — RLS inert for the bypass role")
    got = await conn.fetchval("SELECT count(*) FROM transaction_types")
    (ok if got == base["tt_n"] else fail)(
        f"[bypass] postgres sees all {base['tt_n']} transaction_types rows with no "
        f"context (got {got}) — RLS inert for the bypass role")

    # Even under a WRONG org context, bypass still sees the ORG_A journal_lines —
    # proves RLS is genuinely inert for production, not coincidentally matching.
    await conn.execute("SELECT set_config('app.current_org_id',$1,true)", ORG_B)
    got = await conn.fetchval(
        "SELECT count(*) FROM journal_lines WHERE id = $1", JL_A)
    await conn.execute("SELECT set_config('app.current_org_id','',true)")
    (ok if got == 1 else fail)(
        f"[bypass] postgres still sees the ORG_A journal_line even under ORG_B "
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


# ── Live RLS isolation — spvs (direct) + journal_lines (indirect) ───────────
async def isolation_checks(conn):
    print("\n=== Live isolation — spvs (direct org_id) ===")
    sql = "SELECT count(*) FROM spvs WHERE id = $1"
    c = await _count(conn, sql, [SPV_A], org_id=ORG_A)
    (ok if c == 1 else fail)(
        f"[rls] spvs: SAME-org context (ORG_A) sees the row (got {c}, expect 1)")
    c = await _count(conn, sql, [SPV_A], org_id=ORG_B)
    (ok if c == 0 else fail)(
        f"[rls] spvs: DIFFERENT-org context (ORG_B) does NOT see the ORG_A row "
        f"(got {c}, expect 0)")
    c = await _count(conn, sql, [SPV_A], is_super=True)
    (ok if c == 1 else fail)(
        f"[rls] spvs: super_admin sees the row regardless of org (got {c}, expect 1)")
    c = await _count(conn, sql, [SPV_A])
    (ok if c == 0 else fail)(
        f"[rls] spvs: NEITHER context set → zero rows, safe default-deny "
        f"(got {c}, expect 0)")

    print("\n=== Live isolation — journal_lines (INDIRECT via parent journal_entries) ===")
    jl_sql = "SELECT count(*) FROM journal_lines WHERE id = $1"
    # JL_A's org is determined solely by its parent JE_A (ORG_A).
    c = await _count(conn, jl_sql, [JL_A], org_id=ORG_A)
    (ok if c == 1 else fail)(
        f"[rls] journal_lines: line VISIBLE when context org matches the PARENT "
        f"journal_entry's org (ORG_A) (got {c}, expect 1)")
    c = await _count(conn, jl_sql, [JL_A], org_id=ORG_B)
    (ok if c == 0 else fail)(
        f"[rls] journal_lines: SAME line INVISIBLE under a different context org "
        f"(ORG_B) — gating is by the PARENT journal_entry, not any column on "
        f"journal_lines (got {c}, expect 0)")
    c = await _count(conn, jl_sql, [JL_A], is_super=True)
    (ok if c == 1 else fail)(
        f"[rls] journal_lines: super_admin sees the line regardless of org "
        f"(got {c}, expect 1)")
    c = await _count(conn, jl_sql, [JL_A])
    (ok if c == 0 else fail)(
        f"[rls] journal_lines: NEITHER context set → zero rows, safe default-deny "
        f"(got {c}, expect 0)")
    # Each org sees ONLY its own parent's line — proves the EXISTS resolves the
    # correct parent, not a blanket rule.
    c = await _count(conn, jl_sql, [JL_B], org_id=ORG_B)
    (ok if c == 1 else fail)(
        f"[rls] journal_lines: ORG_B context sees ORG_B's OWN line (parent JE_B) "
        f"(got {c}, expect 1) — parent-EXISTS resolves the right parent")


# ── Live global-reference read + write — fx_rates + transaction_types ───────
async def global_reference_checks(conn, base):
    specs = [
        {
            "table": "fx_rates", "null_count": base["fx_null"],
            "insert": "INSERT INTO fx_rates "
                      "(id, org_id, base_ccy, quote_ccy, rate, as_of_date) "
                      "VALUES ($1,$2,'USD',$3,1.2345,$4::date)",
            "params_null": [FX_TEST_ID, None, FX_TEST_QUOTE, FX_TEST_DATE],
            "params_org": [FX_TEST_ID, ORG_A, FX_TEST_QUOTE, FX_TEST_DATE],
        },
        {
            "table": "transaction_types", "null_count": base["tt_null"],
            "insert": "INSERT INTO transaction_types "
                      "(id, org_id, code, label, category, direction) "
                      "VALUES (gen_random_uuid(),$1,$2,'RLSC Verify','test','none')",
            "params_null": [None, TT_CODE_NULL],
            "params_org": [ORG_A, TT_CODE_ORG],
        },
    ]
    for s in specs:
        t = s["table"]
        print(f"\n=== Live global-reference — {t} ===")
        read_sql = f"SELECT count(*) FROM {t} WHERE org_id IS NULL"
        # (1) READ: the full global row count is visible under ANY context.
        a = await _count(conn, read_sql, [], org_id=ORG_A)
        b = await _count(conn, read_sql, [], org_id=ORG_B)
        n = await _count(conn, read_sql, [])  # no context, just authenticated
        expect = s["null_count"]
        if a == b == n == expect:
            ok(f"[rls] {t}: all {expect} global (NULL-org) rows readable under ANY "
               f"context — ORG_A={a}, ORG_B={b}, no-context={n}")
        else:
            fail(f"[rls] {t}: global rows readable under any org context",
                 f"ORG_A={a} ORG_B={b} no-context={n} expect={expect}")

        # (2) WRITE: non-super INSERT of a NULL-org row is REJECTED by WITH CHECK.
        err = await _attempt_write(conn, s["insert"], s["params_null"], org_id=ORG_A)
        (ok if _is_rls_violation(err) else fail)(
            f"[rls] {t}: non-super-admin INSERT of a NULL-org (global) row is "
            f"REJECTED by WITH CHECK" +
            ("" if _is_rls_violation(err) else f" (got: {err!r})"))

        # (3) WRITE: non-super INSERT of an OWN-org row is ACCEPTED (rolled back).
        err = await _attempt_write(conn, s["insert"], s["params_org"], org_id=ORG_A)
        (ok if err is None else fail)(
            f"[rls] {t}: non-super-admin CAN INSERT an OWN-org (ORG_A) row — "
            f"WITH CHECK accepts org-scoped writes (rolled back)" +
            ("" if err is None else f" (got: {err!r})"))

        # (4) WRITE: super_admin INSERT of a NULL-org (global) row is ACCEPTED.
        err = await _attempt_write(conn, s["insert"], s["params_null"], is_super=True)
        (ok if err is None else fail)(
            f"[rls] {t}: super_admin CAN INSERT a NULL-org (global) row — "
            f"platform reference data is editable by super_admin (rolled back)" +
            ("" if err is None else f" (got: {err!r})"))


# ── app_service connection gate ─────────────────────────────────────────────
async def app_service_stage(base):
    if not APP_SERVICE_URL:
        for lbl in ("spvs isolation (4 checks)",
                    "journal_lines parent-gated isolation (5 checks)",
                    "fx_rates global read + 3 write checks",
                    "transaction_types global read + 3 write checks"):
            skip(f"[rls] {lbl}", "APP_SERVICE_DATABASE_URL not set")
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
    finally:
        await conn.close()


async def db_main():
    conn = await asyncpg.connect(
        DATABASE_URL, ssl="require", statement_cache_size=0)
    try:
        base = await ref_hashes(conn)  # BEFORE snapshot of reference tables
        print("=== Reference-data baseline (as postgres) ===")
        (ok if base["fx_n"] == 5 else fail)(
            f"[ref] fx_rates has the expected 5 real rows (got {base['fx_n']})")
        (ok if base["tt_n"] == 16 else fail)(
            f"[ref] transaction_types has the expected 16 real rows "
            f"(got {base['tt_n']})")
        (ok if base["fx_null"] == base["fx_n"] else fail)(
            f"[ref] all fx_rates rows are global (org_id IS NULL) "
            f"({base['fx_null']}/{base['fx_n']})")
        (ok if base["tt_null"] == base["tt_n"] else fail)(
            f"[ref] all transaction_types rows are global (org_id IS NULL) "
            f"({base['tt_null']}/{base['tt_n']})")
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
        after = await ref_hashes(conn)
        print("\n=== Reference-data integrity — real rows UNTOUCHED ===")
        (ok if after["fx"] == base["fx"] and after["fx_n"] == base["fx_n"] else fail)(
            f"[ref] the {base['fx_n']} real fx_rates rows are byte-for-byte "
            f"unchanged after the run (count {base['fx_n']}→{after['fx_n']}, "
            f"hash {'match' if after['fx'] == base['fx'] else 'DIFF'})")
        (ok if after["tt"] == base["tt"] and after["tt_n"] == base["tt_n"] else fail)(
            f"[ref] the {base['tt_n']} real transaction_types rows are byte-for-byte "
            f"unchanged after the run (count {base['tt_n']}→{after['tt_n']}, "
            f"hash {'match' if after['tt'] == base['tt'] else 'DIFF'})")

        await teardown(conn)
        print("\n=== Teardown — zero leftover TEST rows ===")
        leftovers = 0
        leftovers += await conn.fetchval(
            "SELECT count(*) FROM organizations WHERE id = ANY($1::uuid[])",
            [ORG_A, ORG_B])
        leftovers += await conn.fetchval(
            "SELECT count(*) FROM deals WHERE id = ANY($1::uuid[])",
            [DEAL_A, DEAL_B])
        leftovers += await conn.fetchval(
            "SELECT count(*) FROM spvs WHERE id = ANY($1::uuid[])", [SPV_A])
        leftovers += await conn.fetchval(
            "SELECT count(*) FROM chart_of_accounts WHERE id = ANY($1::uuid[])",
            [COA_A, COA_B])
        leftovers += await conn.fetchval(
            "SELECT count(*) FROM journal_entries WHERE id = ANY($1::uuid[])",
            [JE_A, JE_B])
        leftovers += await conn.fetchval(
            "SELECT count(*) FROM journal_lines WHERE id = ANY($1::uuid[])",
            [JL_A, JL_B])
        # And ZERO test reference rows (there should never have been any — all
        # write attempts were rolled back — but assert it explicitly).
        fx_test = await conn.fetchval(
            "SELECT count(*) FROM fx_rates WHERE base_ccy='USD' AND quote_ccy=$1 "
            "AND as_of_date=$2::date", FX_TEST_QUOTE, FX_TEST_DATE)
        tt_test = await conn.fetchval(
            "SELECT count(*) FROM transaction_types WHERE code LIKE 'ZZ_RLSC%'")
        leftovers += fx_test + tt_test
        (ok if leftovers == 0 else fail)(
            f"[teardown] zero leftover TEST rows across all batch tables "
            f"(found {leftovers}; fx_test={fx_test}, tt_test={tt_test})")

        # Re-confirm the real reference counts are still exactly 5 and 16 post-teardown.
        final = await ref_hashes(conn)
        (ok if final["fx_n"] == base["fx_n"] and final["tt_n"] == base["tt_n"] else fail)(
            f"[teardown] real reference data intact post-teardown — fx_rates="
            f"{final['fx_n']} (expect {base['fx_n']}), transaction_types="
            f"{final['tt_n']} (expect {base['tt_n']})")
    finally:
        await conn.close()


def main():
    print("=== verify_rlsC — RLS Batch C (13 financial/ledger tables) ===")
    asyncio.run(db_main())

    print(f"\n=== RESULT: {passed} passed, {failed} failed, {skipped} skipped ===")
    print("NOTE: DATABASE_URL was NOT modified anywhere in this batch. The live "
          "app still connects as the RLS-bypassing 'postgres' role; production "
          "behavior is unchanged. Every fx_rates / transaction_types write attempt "
          "ran inside a rolled-back transaction — the real 5 + 16 reference rows "
          "were never committed to, and their content hash is proven unchanged. "
          "The app_service assertions prove the policies for the future non-bypass "
          "connection ONLY.")
    if failed:
        print("FAIL")
        sys.exit(1)
    if skipped:
        print("PASS (with skips — supply APP_SERVICE_DATABASE_URL for the full "
              "live RLS isolation + global-reference read/write assertions)")
    else:
        print("PASS")
    sys.exit(0)


if __name__ == "__main__":
    main()
