"""RLS Batch B verify — Entity/CRM tables (15 tables).

Proves the ``*_org_isolation`` policies applied to the 15 entity/CRM tables behave
correctly under the NON-BYPASS ``app_service`` role, using REAL rows — not code
inspection — and that production (the bypass ``postgres`` role) is completely
unchanged. Unlike Batch A (which had one indirect/parent-EXISTS table), ALL 15
tables here carry their OWN denormalized ``org_id`` column, so every policy is
the same direct-org_id NULLIF shape:

  (org_id = NULLIF(current_setting('app.current_org_id', true), '')::uuid)
  OR current_setting('app.is_super_admin', true) = 'true'

The 15 tables (all cmd=ALL, one policy each, direct org_id):
  entities, entity_addresses, entity_attributes, entity_briefs,
  entity_document_tags, entity_documents, entity_employment,
  entity_group_members, entity_groups, entity_holdings, entity_notes,
  entity_ownership, entity_relationships, entity_social_profiles, entity_tax_ids

Four representative tables x four isolation checks each = 16 live assertions:
  entities (the root), entity_relationships (the load-bearing one — see Task 2),
  entity_holdings, entity_notes. Each: same-org visible / different-org invisible
  / super_admin bypass / neither-context-set → zero rows (default-deny).

Task 2 — resolve_entity_set compatibility. ``entity_relationships`` is the SAME
table ``services.entity_graph.resolve_entity_set`` walks for member-side
visibility (ownership + beneficiary look-through, from SOC Phase 1). The new
org-scoped RLS policy must NOT silently truncate that walk. We seed, all in ONE
org, a member with BOTH an ownership edge (member→ownco) AND a beneficiary edge
(member→trust, and trust→llc ownership) and assert that resolve_entity_set,
driven through the REAL production ``_RLSPool`` wrapper (which applies the org
GUC via SET LOCAL on every acquire, exactly as an authenticated request would):
  * run as the NON-BYPASS app_service role with a NORMAL org context, returns the
    full look-through set — both an ownership-reached AND a beneficiary-reached
    entity — and
  * returns EXACTLY the same set the bypass ``postgres`` role returns for the
    same data (no silent truncation from the new policy).

Two DSNs:
  * DATABASE_URL             — the ORIGINAL, RLS-BYPASSING ``postgres`` role.
                               Seeding, structural checks (pg_class/pg_policy),
                               the "bypass role unchanged" assertion, the bypass
                               baseline for resolve_entity_set, teardown.
  * APP_SERVICE_DATABASE_URL — the NON-BYPASS ``app_service`` role, supplied by
                               Joe at test time ONLY. Never hardcoded, never
                               written to any file. Runs the 16 RLS isolation
                               assertions + the app_service leg of Task 2. If
                               absent, those are SKIPPED (not failed).

This script changes NOTHING about production. DATABASE_URL is not modified
anywhere; the live app remains on the ``postgres`` role. Pass/fail only, no
interactive prompts, idempotent — teardown at start AND end by stable ids.

Run (full):
  DATABASE_URL=...  APP_SERVICE_DATABASE_URL=...  python scripts/verify_rlsB.py
Run (partial, app_service skipped):
  DATABASE_URL=...  python scripts/verify_rlsB.py
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import asyncpg

from services.database import (
    _RLSPool,
    _create_asyncpg_pool,
    reset_rls_context,
    set_rls_context,
)
from services.entity_graph import resolve_entity_set

DATABASE_URL = os.environ.get("DATABASE_URL")
if not DATABASE_URL:
    print("[SKIP] DATABASE_URL not set — skipping verify_rlsB")
    sys.exit(0)

# app_service DSN read from the environment at test time ONLY. Several names
# accepted so Joe can pick whichever is convenient. NEVER committed.
APP_SERVICE_URL = (
    os.environ.get("APP_SERVICE_DATABASE_URL")
    or os.environ.get("DATABASE_URL_APP_SERVICE")
    or os.environ.get("RLS_APP_SERVICE_DATABASE_URL")
)

# The full batch — structural assertions cover all 15; RLS isolation assertions
# cover the 4 representatives.
BATCH_TABLES = [
    "entities", "entity_addresses", "entity_attributes", "entity_briefs",
    "entity_document_tags", "entity_documents", "entity_employment",
    "entity_group_members", "entity_groups", "entity_holdings", "entity_notes",
    "entity_ownership", "entity_relationships", "entity_social_profiles",
    "entity_tax_ids",
]

# ── Stable test identifiers (deleted by exact id at teardown) ───────────────
ORG_A = "99000000-0000-0000-0000-0000000ba0a0"   # the "home" org
ORG_B = "99000000-0000-0000-0000-0000000bb0b0"   # a different org

# Entities in ORG_A form the look-through graph for Task 2:
#   member --ownership 100%--> ownco        (ownership-reached)
#   member --beneficiary-----> trust        (beneficiary-reached)
#   trust  --ownership 50%----> llc         (reached through the beneficiary)
E_MEMBER = "99000000-0000-0000-0000-00000ba00001"
E_OWNCO = "99000000-0000-0000-0000-00000ba00002"
E_TRUST = "99000000-0000-0000-0000-00000ba00003"
E_LLC = "99000000-0000-0000-0000-00000ba00004"
# Entities in ORG_B (for isolation + a cross-org relationship row).
E_B = "99000000-0000-0000-0000-00000bb00001"
E_B2 = "99000000-0000-0000-0000-00000bb00002"

# entity_relationships rows.
REL_OWN = "99000000-0000-0000-0000-00000ba10001"   # member→ownco ownership 100 (ORG_A)
REL_BEN = "99000000-0000-0000-0000-00000ba10002"   # member→trust beneficiary   (ORG_A)
REL_TL = "99000000-0000-0000-0000-00000ba10003"    # trust→llc ownership 50     (ORG_A)
REL_B = "99000000-0000-0000-0000-00000bb10001"     # E_B→E_B2 ownership         (ORG_B)

# entity_holdings / entity_notes rows (the other two representatives).
H_A = "99000000-0000-0000-0000-00000ba20001"       # holding on E_MEMBER (ORG_A)
H_B = "99000000-0000-0000-0000-00000bb20001"       # holding on E_B      (ORG_B)
N_A = "99000000-0000-0000-0000-00000ba30001"       # note on E_MEMBER    (ORG_A)
N_B = "99000000-0000-0000-0000-00000bb30001"       # note on E_B         (ORG_B)

# resolve_entity_set(subtree, member) must reach exactly these four entities.
EXPECTED_SET = {E_MEMBER, E_OWNCO, E_TRUST, E_LLC}

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
        "($1,'RLSB Verify A','rlsb-verify-a'), ($2,'RLSB Verify B','rlsb-verify-b') "
        "ON CONFLICT (id) DO NOTHING",
        ORG_A, ORG_B,
    )
    # entities — entity_type does not affect the graph walk; 'individual' for all.
    await conn.execute(
        "INSERT INTO entities (id, org_id, entity_type, display_name) VALUES "
        "($1,$7,'individual','RLSB Member'),"
        "($2,$7,'individual','RLSB OwnCo'),"
        "($3,$7,'individual','RLSB Trust'),"
        "($4,$7,'individual','RLSB LLC'),"
        "($5,$8,'individual','RLSB Entity B'),"
        "($6,$8,'individual','RLSB Entity B2') "
        "ON CONFLICT (id) DO NOTHING",
        E_MEMBER, E_OWNCO, E_TRUST, E_LLC, E_B, E_B2, ORG_A, ORG_B,
    )
    # entity_relationships — the look-through graph (ORG_A) + one ORG_B edge.
    await conn.execute(
        "INSERT INTO entity_relationships "
        "(id, org_id, from_entity_id, to_entity_id, relationship_type, ownership_pct) "
        "VALUES "
        "($1,$9,$2,$3,'ownership',100),"     # member→ownco
        "($4,$9,$2,$5,'beneficiary',NULL),"  # member→trust
        "($6,$9,$5,$7,'ownership',50),"      # trust→llc
        "($8,$10,$11,$12,'ownership',100) "  # E_B→E_B2 (ORG_B)
        "ON CONFLICT (id) DO NOTHING",
        REL_OWN, E_MEMBER, E_OWNCO,
        REL_BEN, E_TRUST,
        REL_TL, E_LLC,
        REL_B, ORG_A, ORG_B, E_B, E_B2,
    )
    # entity_holdings (direct org_id) — one per org, on that org's own entity.
    await conn.execute(
        "INSERT INTO entity_holdings "
        "(id, org_id, entity_id, taxonomy_key, market_value) VALUES "
        "($1,$3,$4,'taxonomy_sc_1',1000),"
        "($2,$5,$6,'taxonomy_sc_1',2000) "
        "ON CONFLICT (id) DO NOTHING",
        H_A, H_B, ORG_A, E_MEMBER, ORG_B, E_B,
    )
    # entity_notes (direct org_id) — one per org, on that org's own entity.
    await conn.execute(
        "INSERT INTO entity_notes "
        "(id, org_id, entity_id, note_text) VALUES "
        "($1,$3,$4,'RLSB note A'),"
        "($2,$5,$6,'RLSB note B') "
        "ON CONFLICT (id) DO NOTHING",
        N_A, N_B, ORG_A, E_MEMBER, ORG_B, E_B,
    )


async def teardown(conn):
    # FK-safe: children (referencing entities) before entities before orgs.
    await conn.execute(
        "DELETE FROM entity_notes WHERE id = ANY($1::uuid[])", [N_A, N_B])
    await conn.execute(
        "DELETE FROM entity_holdings WHERE id = ANY($1::uuid[])", [H_A, H_B])
    await conn.execute(
        "DELETE FROM entity_relationships WHERE id = ANY($1::uuid[])",
        [REL_OWN, REL_BEN, REL_TL, REL_B])
    await conn.execute(
        "DELETE FROM entities WHERE id = ANY($1::uuid[])",
        [E_MEMBER, E_OWNCO, E_TRUST, E_LLC, E_B, E_B2])
    await conn.execute(
        "DELETE FROM organizations WHERE id = ANY($1::uuid[])", [ORG_A, ORG_B])


# ── Structural + bypass-role checks (as postgres) ──────────────────────────
async def structural_checks(conn):
    print("\n=== Structural — all 15 batch tables (pg_class / pg_policy) ===")
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
    (ok if len(found) == 15 else fail)(
        f"[struct] all 15 batch tables present (found {len(found)})"
    )

    # Every batch policy is the direct-org_id NULLIF shape (no parent-EXISTS in
    # this batch — all 15 carry their own org_id).
    pols = await conn.fetch(
        "SELECT tablename, qual FROM pg_policies WHERE tablename = ANY($1::text[])",
        BATCH_TABLES,
    )
    bad = []
    for p in pols:
        q = (p["qual"] or "").lower()
        if not ("org_id" in q and "nullif" in q and "app.current_org_id" in q
                and "is_super_admin" in q):
            bad.append(p["tablename"])
    (ok if not bad else fail)(
        "[struct] all 15 policies use the direct org_id NULLIF pattern + "
        "super_admin escape" + (f" (offenders: {bad})" if bad else "")
    )

    # ── Bypass role (postgres) unaffected ──────────────────────────────────
    print("\n=== Bypass role (postgres) unaffected — production unchanged ===")
    # With NO context set, bypass role sees ALL seeded rows across the sample.
    await conn.execute(
        "SELECT set_config('app.current_org_id','',true), "
        "       set_config('app.is_super_admin','false',true)"
    )
    checks = [
        ("entities", "id = ANY($1::uuid[])",
         [[E_MEMBER, E_OWNCO, E_TRUST, E_LLC, E_B, E_B2]], 6),
        ("entity_relationships", "id = ANY($1::uuid[])",
         [[REL_OWN, REL_BEN, REL_TL, REL_B]], 4),
        ("entity_holdings", "id = ANY($1::uuid[])", [[H_A, H_B]], 2),
        ("entity_notes", "id = ANY($1::uuid[])", [[N_A, N_B]], 2),
    ]
    for table, where, params, expect in checks:
        got = await conn.fetchval(
            f"SELECT count(*) FROM {table} WHERE {where}", *params
        )
        (ok if got == expect else fail)(
            f"[bypass] postgres sees all {expect} seeded {table} rows with NO "
            f"context (got {got})"
        )

    # Even under a WRONG org context, bypass role still sees everything — proves
    # RLS is genuinely inert for the production role, not coincidentally matching.
    await conn.execute("SELECT set_config('app.current_org_id',$1,true)", ORG_B)
    got = await conn.fetchval(
        "SELECT count(*) FROM entity_relationships WHERE id = ANY($1::uuid[])",
        [REL_OWN, REL_BEN, REL_TL],
    )
    await conn.execute("SELECT set_config('app.current_org_id','',true)")
    (ok if got == 3 else fail)(
        f"[bypass] postgres still sees all 3 ORG_A entity_relationships even "
        f"under ORG_B context — RLS fully inert for the bypass role (got {got}, "
        "expect 3)"
    )


# ── RLS isolation assertions (as app_service, non-bypass) ──────────────────
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
    ("entities", "entities (direct org_id — the root)", "id = $1", [E_MEMBER]),
    ("entity_relationships",
     "entity_relationships (direct org_id — resolve_entity_set's table)",
     "id = $1", [REL_OWN]),
    ("entity_holdings", "entity_holdings (direct org_id)", "id = $1", [H_A]),
    ("entity_notes", "entity_notes (direct org_id)", "id = $1", [N_A]),
]


async def rls_isolation_checks(conn):
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


# ── Task 2 — resolve_entity_set through the REAL _RLSPool wrapper ───────────
async def _resolve_via_pool(url, org_id, is_super=False):
    """Drive the production resolve_entity_set through the production _RLSPool
    over ``url``, with the org context applied exactly as an authenticated
    request would (SET LOCAL on every acquire). Returns the set of entity_ids."""
    pool = _RLSPool(await _create_asyncpg_pool(url))
    tokens = set_rls_context(org_id, is_super)
    try:
        rows = await resolve_entity_set(
            pool, org_id, {"type": "subtree", "root_id": E_MEMBER}
        )
        return {r["entity_id"] for r in rows}
    finally:
        reset_rls_context(tokens)
        await pool.close()


async def resolve_entity_set_checks():
    print("\n=== Task 2 — resolve_entity_set compatibility (entity_relationships) ===")

    # Bypass baseline (postgres): the ground-truth look-through set. Uses the
    # same _RLSPool wrapper so the ONLY variable vs. app_service is the role.
    bypass_set = await _resolve_via_pool(DATABASE_URL, ORG_A)
    (ok if bypass_set == EXPECTED_SET else fail)(
        "[task2] bypass (postgres) resolve_entity_set(subtree, member) returns "
        f"the full look-through set {sorted(EXPECTED_SET)} (got "
        f"{sorted(bypass_set)})"
    )
    # Sanity: the baseline genuinely exercises BOTH edge kinds.
    (ok if E_OWNCO in bypass_set else fail)(
        "[task2] bypass baseline includes the OWNERSHIP-reached entity (ownco)")
    (ok if E_TRUST in bypass_set else fail)(
        "[task2] bypass baseline includes the BENEFICIARY-reached entity (trust)")

    if not APP_SERVICE_URL:
        skip("[task2] app_service resolve_entity_set under normal org context",
             "APP_SERVICE_DATABASE_URL not set")
        skip("[task2] app_service set == bypass set (no truncation)",
             "APP_SERVICE_DATABASE_URL not set")
        return

    # The real thing: app_service (non-bypass) with a NORMAL org context — the
    # new entity_relationships policy is fully enforced on every edge query.
    app_set = await _resolve_via_pool(APP_SERVICE_URL, ORG_A)
    (ok if E_OWNCO in app_set else fail)(
        "[task2] app_service (normal ORG_A context) resolve_entity_set includes "
        "an OWNERSHIP-reached entity (ownco) — ownership look-through survives "
        "the new RLS policy")
    (ok if E_TRUST in app_set else fail)(
        "[task2] app_service (normal ORG_A context) resolve_entity_set includes "
        "a BENEFICIARY-reached entity (trust) — beneficiary look-through "
        "survives the new RLS policy")
    (ok if app_set == bypass_set else fail)(
        "[task2] app_service set EXACTLY matches the bypass baseline — the new "
        f"entity_relationships policy does NOT silently truncate the walk "
        f"(app={sorted(app_set)}, bypass={sorted(bypass_set)})")
    (ok if app_set == EXPECTED_SET else fail)(
        f"[task2] app_service set is the full expected look-through "
        f"{sorted(EXPECTED_SET)} (got {sorted(app_set)})")


# ── app_service connection gate (shared by isolation + Task 2 app leg) ──────
async def app_service_stage():
    if not APP_SERVICE_URL:
        for _, label, _, _ in REP:
            for chk in ("same-org visible", "different-org invisible",
                        "super_admin bypass", "neither-set → zero rows"):
                skip(f"[rls] {label} — {chk}", "APP_SERVICE_DATABASE_URL not set")
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
        await rls_isolation_checks(conn)
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
        # The app_service isolation checks + Task 2 run on their own pools/conns;
        # keep the seeded rows alive until after them, then tear down.
        await conn.close()

    await app_service_stage()
    await resolve_entity_set_checks()

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
            "SELECT count(*) FROM entities WHERE id = ANY($1::uuid[])",
            [E_MEMBER, E_OWNCO, E_TRUST, E_LLC, E_B, E_B2])
        leftovers += await conn.fetchval(
            "SELECT count(*) FROM entity_relationships WHERE id = ANY($1::uuid[])",
            [REL_OWN, REL_BEN, REL_TL, REL_B])
        leftovers += await conn.fetchval(
            "SELECT count(*) FROM entity_holdings WHERE id = ANY($1::uuid[])",
            [H_A, H_B])
        leftovers += await conn.fetchval(
            "SELECT count(*) FROM entity_notes WHERE id = ANY($1::uuid[])",
            [N_A, N_B])
        (ok if leftovers == 0 else fail)(
            f"[teardown] zero leftover seeded rows across all tables (found "
            f"{leftovers})"
        )
    finally:
        await conn.close()


def main():
    print("=== verify_rlsB — RLS Batch B (15 entity/CRM tables) ===")
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
              "RLS isolation + Task 2 app_service assertions)")
    else:
        print("PASS")
    sys.exit(0)


if __name__ == "__main__":
    main()
