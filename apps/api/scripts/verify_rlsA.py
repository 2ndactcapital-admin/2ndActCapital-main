"""RLS Batch A verify — SOC/RBAC tables (14 tables).

Proves the ``*_org_isolation`` policies applied to the 14 SOC/RBAC tables behave
correctly under the NON-BYPASS ``app_service`` role, using REAL rows — not code
inspection — and that production (the bypass ``postgres`` role) is completely
unchanged.

The 14 tables (all cmd=ALL, one policy each):
  13 direct-org_id .. profiles, permission_sets, teams, staff_assignments,
                      restricted_access_grants, restricted_access_audit,
                      trading_authority_grants, delegate_grants,
                      external_access_grants, households, doc_category_proposals,
                      permission_set_permissions (own denormalized org_id),
                      profile_permissions (own denormalized org_id)
  1 indirect ....... household_memberships — NO org_id of its own; its policy
                      reaches org via EXISTS against the PARENT households row's
                      org_id. This is the one place a subtle bug could hide, so
                      it gets a dedicated assertion.

Four representative tables x four isolation checks each = 16 live assertions:
  profiles (direct), permission_set_permissions (direct, denormalized),
  teams (direct), household_memberships (INDIRECT via parent).
  Each: same-org visible / different-org invisible / super_admin bypass /
  neither-context-set → zero rows (default-deny).

Two connections:
  * DATABASE_URL             — the ORIGINAL, RLS-BYPASSING ``postgres`` role.
                               Seeding, structural checks (pg_class/pg_policy),
                               the "bypass role unchanged" assertion, teardown.
  * APP_SERVICE_DATABASE_URL — the NON-BYPASS ``app_service`` role, supplied by
                               Joe at test time ONLY. Never hardcoded, never
                               written to any file. Runs the 16 RLS assertions +
                               the household_memberships parent-org assertion. If
                               absent, those are SKIPPED (not failed) so the rest
                               still runs.

This script changes NOTHING about production. DATABASE_URL is not modified
anywhere; the live app remains on the ``postgres`` role. Pass/fail only, no
interactive prompts, idempotent — teardown at start AND end by stable ids.

Run (full):
  DATABASE_URL=...  APP_SERVICE_DATABASE_URL=...  python scripts/verify_rlsA.py
Run (partial, app_service skipped):
  DATABASE_URL=...  python scripts/verify_rlsA.py
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import asyncpg

DATABASE_URL = os.environ.get("DATABASE_URL")
if not DATABASE_URL:
    print("[SKIP] DATABASE_URL not set — skipping verify_rlsA")
    sys.exit(0)

# app_service DSN read from the environment at test time ONLY. Several names
# accepted so Joe can pick whichever is convenient. NEVER committed.
APP_SERVICE_URL = (
    os.environ.get("APP_SERVICE_DATABASE_URL")
    or os.environ.get("DATABASE_URL_APP_SERVICE")
    or os.environ.get("RLS_APP_SERVICE_DATABASE_URL")
)

# The full batch — structural assertions cover all 14; RLS assertions cover the
# 4 representatives.
BATCH_TABLES = [
    "profiles", "permission_sets", "teams", "staff_assignments",
    "restricted_access_grants", "restricted_access_audit",
    "trading_authority_grants", "delegate_grants", "external_access_grants",
    "households", "doc_category_proposals", "permission_set_permissions",
    "profile_permissions", "household_memberships",
]

# ── Stable test identifiers (deleted by exact id at teardown) ───────────────
ORG_A = "99000000-0000-0000-0000-00000000a0a0"   # the "home" org
ORG_B = "99000000-0000-0000-0000-00000000b0b0"   # a different org

P_A = "99000000-0000-0000-0000-0000000a0101"     # profiles / ORG_A
P_B = "99000000-0000-0000-0000-0000000b0101"     # profiles / ORG_B
T_A = "99000000-0000-0000-0000-0000000a0201"     # teams / ORG_A
T_B = "99000000-0000-0000-0000-0000000b0201"     # teams / ORG_B
PS_A = "99000000-0000-0000-0000-0000000a0301"    # permission_sets / ORG_A
PS_B = "99000000-0000-0000-0000-0000000b0301"    # permission_sets / ORG_B
PSP_A = "99000000-0000-0000-0000-0000000a0401"   # permission_set_permissions / ORG_A
PSP_B = "99000000-0000-0000-0000-0000000b0401"   # permission_set_permissions / ORG_B
HH_A = "99000000-0000-0000-0000-0000000a0501"    # households / ORG_A  (parent)
HH_B = "99000000-0000-0000-0000-0000000b0501"    # households / ORG_B  (parent)
ENT = "99000000-0000-0000-0000-0000000a0601"     # entities / ORG_A (membership target)

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
        "($1,'RLSA Verify A','rlsa-verify-a'), ($2,'RLSA Verify B','rlsa-verify-b') "
        "ON CONFLICT (id) DO NOTHING",
        ORG_A, ORG_B,
    )
    # profiles (direct org_id)
    await conn.execute(
        "INSERT INTO profiles (id, org_id, name) VALUES "
        "($1,$2,'RLSA Profile A'), ($3,$4,'RLSA Profile B') "
        "ON CONFLICT (id) DO NOTHING",
        P_A, ORG_A, P_B, ORG_B,
    )
    # teams (direct org_id)
    await conn.execute(
        "INSERT INTO teams (id, org_id, name) VALUES "
        "($1,$2,'RLSA Team A'), ($3,$4,'RLSA Team B') "
        "ON CONFLICT (id) DO NOTHING",
        T_A, ORG_A, T_B, ORG_B,
    )
    # permission_sets (parent of permission_set_permissions)
    await conn.execute(
        "INSERT INTO permission_sets (id, org_id, name) VALUES "
        "($1,$2,'RLSA PermSet A'), ($3,$4,'RLSA PermSet B') "
        "ON CONFLICT (id) DO NOTHING",
        PS_A, ORG_A, PS_B, ORG_B,
    )
    # permission_set_permissions (direct, denormalized org_id)
    await conn.execute(
        "INSERT INTO permission_set_permissions "
        "(id, org_id, permission_set_id, permission_key) VALUES "
        "($1,$2,$3,'rlsa.read'), ($4,$5,$6,'rlsa.read') "
        "ON CONFLICT (id) DO NOTHING",
        PSP_A, ORG_A, PS_A,
        PSP_B, ORG_B, PS_B,
    )
    # households (parent of household_memberships)
    await conn.execute(
        "INSERT INTO households (id, org_id, name) VALUES "
        "($1,$2,'RLSA Household A'), ($3,$4,'RLSA Household B') "
        "ON CONFLICT (id) DO NOTHING",
        HH_A, ORG_A, HH_B, ORG_B,
    )
    # entities — a single membership target (org only matters for the entity's
    # own RLS, which we do not test here; the membership policy keys off the
    # PARENT household, not the entity).
    await conn.execute(
        "INSERT INTO entities (id, org_id, entity_type, display_name) VALUES "
        "($1,$2,'individual','RLSA Entity') "
        "ON CONFLICT (id) DO NOTHING",
        ENT, ORG_A,
    )
    # household_memberships — NO org_id of its own. HM_A hangs off ORG_A's
    # household; HM_B off ORG_B's household. Same entity in both.
    await conn.execute(
        "INSERT INTO household_memberships (household_id, entity_id) VALUES "
        "($1,$3), ($2,$3) "
        "ON CONFLICT DO NOTHING",
        HH_A, HH_B, ENT,
    )


async def teardown(conn):
    # FK-safe: children before parents.
    await conn.execute(
        "DELETE FROM household_memberships "
        "WHERE household_id = ANY($1::uuid[]) OR entity_id = ANY($2::uuid[])",
        [HH_A, HH_B], [ENT],
    )
    await conn.execute(
        "DELETE FROM permission_set_permissions WHERE id = ANY($1::uuid[])",
        [PSP_A, PSP_B],
    )
    await conn.execute("DELETE FROM entities WHERE id = ANY($1::uuid[])", [ENT])
    await conn.execute("DELETE FROM permission_sets WHERE id = ANY($1::uuid[])",
                       [PS_A, PS_B])
    await conn.execute("DELETE FROM households WHERE id = ANY($1::uuid[])",
                       [HH_A, HH_B])
    await conn.execute("DELETE FROM teams WHERE id = ANY($1::uuid[])", [T_A, T_B])
    await conn.execute("DELETE FROM profiles WHERE id = ANY($1::uuid[])", [P_A, P_B])
    await conn.execute("DELETE FROM organizations WHERE id = ANY($1::uuid[])",
                       [ORG_A, ORG_B])


# ── Structural + bypass-role checks (as postgres) ──────────────────────────
async def structural_checks(conn):
    print("\n=== Structural — all 14 batch tables (pg_class / pg_policy) ===")
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
    (ok if len(found) == 14 else fail)(
        f"[struct] all 14 batch tables present (found {len(found)})"
    )

    # household_memberships policy specifically reaches org via the parent.
    hm_qual = await conn.fetchval(
        "SELECT qual FROM pg_policies "
        "WHERE tablename = 'household_memberships'"
    )
    q = (hm_qual or "").lower()
    if "exists" in q and "households" in q and "org_id" in q:
        ok("[struct] household_memberships policy reaches org via EXISTS against "
           "parent households.org_id (no org_id column of its own)")
    else:
        fail("[struct] household_memberships policy uses parent-EXISTS subquery",
             f"qual={hm_qual!r}")

    # ── Bypass role (postgres) unaffected ──────────────────────────────────
    print("\n=== Bypass role (postgres) unaffected — production unchanged ===")
    # With NO context set, bypass role sees ALL seeded rows across the sample.
    checks = [
        ("profiles", "id = ANY($1::uuid[])", [[P_A, P_B]], 2),
        ("teams", "id = ANY($1::uuid[])", [[T_A, T_B]], 2),
        ("permission_set_permissions", "id = ANY($1::uuid[])", [[PSP_A, PSP_B]], 2),
        ("households", "id = ANY($1::uuid[])", [[HH_A, HH_B]], 2),
        ("household_memberships", "entity_id = $1", [ENT], 2),
    ]
    await conn.execute(
        "SELECT set_config('app.current_org_id','',true), "
        "       set_config('app.is_super_admin','false',true)"
    )
    for table, where, params, expect in checks:
        got = await conn.fetchval(
            f"SELECT count(*) FROM {table} WHERE {where}", *params
        )
        (ok if got == expect else fail)(
            f"[bypass] postgres sees all {expect} seeded {table} rows with NO "
            f"context (got {got})"
        )

    # Even with a WRONG org context, bypass role still sees everything — proves
    # RLS is genuinely inert for the production role, not merely coincidentally
    # matching.
    await conn.execute("SELECT set_config('app.current_org_id',$1,true)", ORG_B)
    got = await conn.fetchval(
        "SELECT count(*) FROM profiles WHERE id = ANY($1::uuid[])", [P_A, P_B]
    )
    await conn.execute("SELECT set_config('app.current_org_id','',true)")
    (ok if got == 2 else fail)(
        f"[bypass] postgres still sees BOTH profiles even under ORG_B context — "
        f"RLS fully inert for the bypass role (got {got}, expect 2)"
    )


# ── RLS assertions (as app_service, non-bypass) ────────────────────────────
async def _apply(conn, org_id=None, is_super=False):
    """Mirror services.database._apply_rls_settings: set the GUCs, '' when
    absent, inside the caller's transaction."""
    await conn.execute(
        "SELECT set_config('app.current_org_id', $1, true),"
        "       set_config('app.is_super_admin', $2, true)",
        org_id or "",
        "true" if is_super else "false",
    )


async def _count(conn, table, where, params, **ctx):
    async with conn.transaction():
        await _apply(conn, **ctx)
        return await conn.fetchval(
            f"SELECT count(*) FROM {table} WHERE {where}", *params
        )


# Representative tables: (table, human label, where clause, params for the ORG_A row)
REP = [
    ("profiles", "profiles (direct org_id)", "id = $1", [P_A]),
    ("permission_set_permissions",
     "permission_set_permissions (direct, denormalized org_id)", "id = $1", [PSP_A]),
    ("teams", "teams (direct org_id)", "id = $1", [T_A]),
    ("household_memberships",
     "household_memberships (INDIRECT via parent households)",
     "household_id = $1 AND entity_id = $2", [HH_A, ENT]),
]


async def rls_checks():
    if not APP_SERVICE_URL:
        for _, label, _, _ in REP:
            for chk in ("same-org visible", "different-org invisible",
                        "super_admin bypass", "neither-set → zero rows"):
                skip(f"[rls] {label} — {chk}", "APP_SERVICE_DATABASE_URL not set")
        skip("[rls] household_memberships parent-org gating",
             "APP_SERVICE_DATABASE_URL not set")
        return

    conn = await asyncpg.connect(
        APP_SERVICE_URL, ssl="require", statement_cache_size=0
    )
    try:
        role, bypass = await conn.fetchrow(
            "SELECT current_user, "
            "(SELECT rolbypassrls FROM pg_roles WHERE rolname = current_user)"
        )
        if bypass:
            fail("[rls] app_service DSN is a NON-BYPASS role",
                 f"connected as {role!r} with rolbypassrls=true — supply the "
                 "app_service DSN, not a bypass role")
            return
        ok(f"[rls] connected as non-bypass role {role!r} (rolbypassrls=false)")

        print("\n=== Isolation — 4 representative tables x 4 checks = 16 ===")
        for table, label, where, params in REP:
            # same-org: ORG_A context sees the ORG_A row
            c = await _count(conn, table, where, params, org_id=ORG_A)
            (ok if c == 1 else fail)(
                f"[rls] {label}: SAME-org context (ORG_A) sees the row "
                f"(got {c}, expect 1)"
            )
            # different-org: ORG_B context does NOT see the ORG_A row
            c = await _count(conn, table, where, params, org_id=ORG_B)
            (ok if c == 0 else fail)(
                f"[rls] {label}: DIFFERENT-org context (ORG_B) does NOT see the "
                f"ORG_A row (got {c}, expect 0)"
            )
            # super_admin: is_super=true (no org) sees the row regardless
            c = await _count(conn, table, where, params, is_super=True)
            (ok if c == 1 else fail)(
                f"[rls] {label}: super_admin sees the row regardless of org "
                f"(got {c}, expect 1)"
            )
            # neither set: default-deny → zero rows (NULLIF path, no '' cast error)
            c = await _count(conn, table, where, params)
            (ok if c == 0 else fail)(
                f"[rls] {label}: NEITHER context set → zero rows, safe "
                f"default-deny (got {c}, expect 0)"
            )

        # ── The tricky one: household_memberships is gated by the PARENT's org,
        # even though the membership row itself has no org_id. Prove a membership
        # is visible iff the CONTEXT matches the PARENT household's org.
        print("\n=== household_memberships — parent-org gating (the tricky one) ===")
        hm_where = "household_id = $1 AND entity_id = $2"
        vis_parent_match = await _count(conn, "household_memberships", hm_where,
                                        [HH_A, ENT], org_id=ORG_A)
        (ok if vis_parent_match == 1 else fail)(
            "[rls] household_memberships: membership VISIBLE when context org "
            f"matches the PARENT household's org (ORG_A) (got {vis_parent_match}, "
            "expect 1)"
        )
        inv_parent_mismatch = await _count(conn, "household_memberships", hm_where,
                                           [HH_A, ENT], org_id=ORG_B)
        (ok if inv_parent_mismatch == 0 else fail)(
            "[rls] household_memberships: same membership INVISIBLE when context "
            f"org (ORG_B) does NOT match the parent's org (got "
            f"{inv_parent_mismatch}, expect 0) — proves gating is by PARENT, not "
            "by a stray/absent org_id on the membership row"
        )
        # Cross-check: ORG_B context DOES see ORG_B's own membership — confirms
        # the parent-EXISTS actually resolves the right parent, not a blanket rule.
        hm_b_vis = await _count(conn, "household_memberships", hm_where,
                                [HH_B, ENT], org_id=ORG_B)
        (ok if hm_b_vis == 1 else fail)(
            "[rls] household_memberships: ORG_B context sees ORG_B's OWN "
            f"membership (parent HH_B) (got {hm_b_vis}, expect 1)"
        )
    finally:
        await conn.close()


async def db_main():
    conn = await asyncpg.connect(
        DATABASE_URL, ssl="require", statement_cache_size=0
    )
    try:
        await seed(conn)
        await structural_checks(conn)
    finally:
        # rls_checks() runs against app_service on its own connection; keep the
        # seeded rows alive until after it, then tear down.
        await conn.close()

    await rls_checks()

    # Final teardown on a fresh bypass connection; then assert zero leftovers.
    conn = await asyncpg.connect(
        DATABASE_URL, ssl="require", statement_cache_size=0
    )
    try:
        await teardown(conn)
        print("\n=== Teardown — zero leftover rows ===")
        leftovers = 0
        leftovers += await conn.fetchval(
            "SELECT count(*) FROM organizations WHERE id = ANY($1::uuid[])",
            [ORG_A, ORG_B])
        leftovers += await conn.fetchval(
            "SELECT count(*) FROM profiles WHERE id = ANY($1::uuid[])", [P_A, P_B])
        leftovers += await conn.fetchval(
            "SELECT count(*) FROM teams WHERE id = ANY($1::uuid[])", [T_A, T_B])
        leftovers += await conn.fetchval(
            "SELECT count(*) FROM permission_sets WHERE id = ANY($1::uuid[])",
            [PS_A, PS_B])
        leftovers += await conn.fetchval(
            "SELECT count(*) FROM permission_set_permissions WHERE id = ANY($1::uuid[])",
            [PSP_A, PSP_B])
        leftovers += await conn.fetchval(
            "SELECT count(*) FROM households WHERE id = ANY($1::uuid[])",
            [HH_A, HH_B])
        leftovers += await conn.fetchval(
            "SELECT count(*) FROM entities WHERE id = ANY($1::uuid[])", [ENT])
        leftovers += await conn.fetchval(
            "SELECT count(*) FROM household_memberships WHERE entity_id = $1", ENT)
        (ok if leftovers == 0 else fail)(
            f"[teardown] zero leftover seeded rows across all tables (found "
            f"{leftovers})"
        )
    finally:
        await conn.close()


def main():
    print("=== verify_rlsA — RLS Batch A (14 SOC/RBAC tables) ===")
    asyncio.run(db_main())

    print(f"\n=== RESULT: {passed} passed, {failed} failed, {skipped} skipped ===")
    print("NOTE: DATABASE_URL was NOT modified anywhere in this batch. The live "
          "app still connects as the RLS-bypassing 'postgres' role; production "
          "behavior is unchanged. The app_service assertions above prove the "
          "policies for the future non-bypass connection ONLY.")
    if failed:
        print("FAIL")
        sys.exit(1)
    if skipped:
        print("PASS (with skips — supply APP_SERVICE_DATABASE_URL for the full "
              "RLS isolation assertions)")
    else:
        print("PASS")
    sys.exit(0)


if __name__ == "__main__":
    main()
