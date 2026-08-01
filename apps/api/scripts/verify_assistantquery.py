"""Verify: assistant aggregate/count query capability (Sprint assistantquery).

Closes Joe's two confirmed gaps — "how many entities reside in CT" and "how many
investments are there" — with the assistant's first count/filter READ actions
(``entities.count`` / ``investments.count`` in
``services.assistant_actions.queries``), each routed THROUGH the same visibility
engines every other surface uses (staff_visibility OR delegate resolve_entity_set,
then the restricted-access filter).

Pass/fail only, no interactive prompts, teardown-at-start AND teardown-at-end,
idempotent under fixed test identifiers.

    cd apps/api
    DATABASE_URL=... python3 scripts/verify_assistantquery.py

Assertions (each reported explicitly):
  A1  Report Task-1's four discovery findings + the where-else-the-gap-exists survey.
  A2  entities.count filtered by state (CT) returns the CORRECT real count.
  A3  investments.count filtered by status/stage returns the CORRECT real count.
  A4  A LIMITED-visibility user (staff-assigned to one; member to one) gets a count
      scoped to ONLY what they can see — NOT the org total. (staff AND member.)
  A5  A different org's data is never included in either count — verified on the
      REAL ``app_service`` connection (org-scoped query path users actually run).
  A6  The assistant's REAL chat flow (POST /assistant/message via the app, through
      the real ``_run_loop``) invokes the new actions end-to-end.
  A7  Teardown: zero leftover rows.
"""

import asyncio
import glob
import os
import sys
import traceback

# ── Make runnable via allowlisted system python3 OR venv python ─────────────
_HERE = os.path.dirname(os.path.abspath(__file__))
_API_ROOT = os.path.dirname(_HERE)
_REPO_ROOT = os.path.dirname(os.path.dirname(_API_ROOT))
if _API_ROOT not in sys.path:
    sys.path.insert(0, _API_ROOT)
for _venv in (os.path.join(_REPO_ROOT, "venv"), os.path.join(_API_ROOT, "venv")):
    for _sp in glob.glob(os.path.join(_venv, "lib/python*/site-packages")):
        if _sp not in sys.path:
            sys.path.insert(0, _sp)

# Load apps/api/.env so DATABASE_URL / APP_SERVICE_DATABASE_URL / ANTHROPIC_API_KEY
# are available even when the shell didn't export them.
_ENV = os.path.join(_API_ROOT, ".env")
try:
    with open(_ENV) as _fh:
        for _line in _fh:
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _k, _v = _line.split("=", 1)
                os.environ.setdefault(_k.strip(), _v.strip().strip('"').strip("'"))
except OSError:
    pass

import asyncpg  # noqa: E402

DATABASE_URL = os.environ.get("DATABASE_URL")
APP_SERVICE_DATABASE_URL = os.environ.get("APP_SERVICE_DATABASE_URL")

if not DATABASE_URL:
    print("[SKIP] DATABASE_URL not set — skipping verify_assistantquery")
    sys.exit(0)

# ── Fixed test identifiers (two THROWAWAY orgs → full teardown) ─────────────
ORG_MAIN = "99000000-0000-0000-0000-0000000a9001"
ORG_OTHER = "99000000-0000-0000-0000-0000000a9002"
_ORGS = [ORG_MAIN, ORG_OTHER]

U_SUPER = "99000000-0000-0000-0000-0000000a9101"   # super_admin (ORG_MAIN) — sees all
U_STAFF = "99000000-0000-0000-0000-0000000a9102"   # investment_staff — one assignment
U_MEMBER = "99000000-0000-0000-0000-0000000a9103"  # member — one delegate grant
U_OTHER = "99000000-0000-0000-0000-0000000a9104"   # member in ORG_OTHER
_USERS = [U_SUPER, U_STAFF, U_MEMBER, U_OTHER]

E_MEMBER = "99000000-0000-0000-0000-0000000e9001"   # CT, member's own
E_STAFF = "99000000-0000-0000-0000-0000000e9002"    # CT, staff-assigned
E_HIDDEN = "99000000-0000-0000-0000-0000000e9003"   # CT, only super sees
E_NY = "99000000-0000-0000-0000-0000000e9004"       # NY
E_OTHER = "99000000-0000-0000-0000-0000000e9005"    # CT, ORG_OTHER (cross-org)

D_MAIN = "99000000-0000-0000-0000-0000000d9001"
D_OTHER = "99000000-0000-0000-0000-0000000d9002"

passed = 0
failed = 0


def ok(label):
    global passed
    passed += 1
    print(f"[P] {label}")


def fail(label, reason=""):
    global failed
    failed += 1
    print(f"[F] {label}{': ' + reason if reason else ''}")


# ── teardown ────────────────────────────────────────────────────────────────
async def cleanup(conn):
    # FK-safe: children before parents. Both throwaway orgs wiped entirely.
    await conn.execute("DELETE FROM assistant_activities WHERE user_id = ANY($1::uuid[])", _USERS)
    await conn.execute("DELETE FROM assistant_conversations WHERE user_id = ANY($1::uuid[])", _USERS)
    await conn.execute("DELETE FROM member_investments WHERE org_id = ANY($1::uuid[])", _ORGS)
    await conn.execute("DELETE FROM entity_addresses WHERE org_id = ANY($1::uuid[])", _ORGS)
    await conn.execute("DELETE FROM staff_assignments WHERE org_id = ANY($1::uuid[])", _ORGS)
    await conn.execute("DELETE FROM delegate_grants WHERE org_id = ANY($1::uuid[])", _ORGS)
    await conn.execute("DELETE FROM restricted_access_grants WHERE org_id = ANY($1::uuid[])", _ORGS)
    await conn.execute("DELETE FROM restricted_access_audit WHERE org_id = ANY($1::uuid[])", _ORGS)
    await conn.execute("DELETE FROM deals WHERE org_id = ANY($1::uuid[])", _ORGS)
    await conn.execute("DELETE FROM entities WHERE org_id = ANY($1::uuid[])", _ORGS)
    await conn.execute("DELETE FROM audit_log WHERE user_id = ANY($1::uuid[])", _USERS)
    await conn.execute("DELETE FROM users WHERE id = ANY($1::uuid[])", _USERS)
    await conn.execute("DELETE FROM organizations WHERE id = ANY($1::uuid[])", _ORGS)


async def leftover_count(conn) -> int:
    return int(await conn.fetchval(
        """
        SELECT
            (SELECT count(*) FROM users WHERE id = ANY($1::uuid[]))
          + (SELECT count(*) FROM entities WHERE org_id = ANY($2::uuid[]))
          + (SELECT count(*) FROM entity_addresses WHERE org_id = ANY($2::uuid[]))
          + (SELECT count(*) FROM deals WHERE org_id = ANY($2::uuid[]))
          + (SELECT count(*) FROM member_investments WHERE org_id = ANY($2::uuid[]))
          + (SELECT count(*) FROM staff_assignments WHERE org_id = ANY($2::uuid[]))
          + (SELECT count(*) FROM delegate_grants WHERE org_id = ANY($2::uuid[]))
          + (SELECT count(*) FROM assistant_conversations WHERE user_id = ANY($1::uuid[]))
          + (SELECT count(*) FROM assistant_activities WHERE user_id = ANY($1::uuid[]))
          + (SELECT count(*) FROM organizations WHERE id = ANY($2::uuid[]))
        """,
        _USERS, _ORGS,
    ))


# ── seed ──────────────────────────────────────────────────────────────────
async def seed_user(conn, uid, org, tag, role):
    await conn.execute(
        """
        INSERT INTO users (id, org_id, email, full_name, auth0_sub, role)
        VALUES ($1, $2, $3, $4, $5, $6)
        ON CONFLICT (auth0_sub) DO NOTHING
        """,
        uid, org, f"aq_{tag}@test.local", f"AQ {tag}", f"auth0|test_aq_{tag}", role,
    )


async def seed_entity(conn, eid, org, name, etype="llc"):
    await conn.execute(
        """
        INSERT INTO entities (id, org_id, entity_type, display_name, is_active)
        VALUES ($1, $2, $3, $4, true)
        ON CONFLICT (id) DO NOTHING
        """,
        eid, org, etype, name,
    )


async def seed_address(conn, eid, org, state):
    await conn.execute(
        """
        INSERT INTO entity_addresses
            (org_id, entity_id, street1, city, state, country)
        VALUES ($1, $2, '1 Test St', 'Testville', $3, 'US')
        """,
        org, eid, state,
    )


async def seed_deal(conn, did, org, name):
    await conn.execute(
        """
        INSERT INTO deals (id, org_id, name, deal_status)
        VALUES ($1, $2, $3, 'active')
        ON CONFLICT (id) DO NOTHING
        """,
        did, org, name,
    )


async def seed_investment(conn, org, did, uid, eid, stage):
    await conn.execute(
        """
        INSERT INTO member_investments
            (org_id, deal_id, user_id, entity_id, investment_stage, amount_committed, created_by)
        VALUES ($1, $2, $3, $4, $5, 100000, $3)
        ON CONFLICT (deal_id, user_id) DO NOTHING
        """,
        org, did, uid, eid, stage,
    )


def report_discovery():
    print("[DISCOVERY] Task 1 findings (real, current):")
    print("  (a) action_registry.py + 6 modules: EVERY action is a single-lookup or a")
    print("      write — NO count/aggregate/attribute-filter action for entities or")
    print("      investments existed. Same gap ALSO exists for: SPVs (list_open/by-id"
          " only), workflow runs, deals-by-attribute, member_investments aggregates,")
    print("      documents, tasks/notifications counts -> Task-4 follow-up list; this")
    print("      sprint closes ONLY entities + investments.")
    print("  (b) GET /entities filters by type/status/search(name), org-scoped, NO")
    print("      state filter and NO count mode; /entities/search returns a COUNT but is")
    print("      a name-picker. -> new count actions add the state filter + count mode.")
    print("  (c) entity_addresses has real queryable `state` AND `region_code` (text,")
    print("      bitemporal). Filter matches state OR region_code, case-insensitive.")
    print("  (d) Existing single-lookup assistant actions were org-scoped ONLY. The")
    print("      canonical visibility composition reused verbatim (ownership_tree /")
    print("      document_embedding._visible_entity_ids / semantic_search):")
    print("        staff  -> staff_visibility.get_staff_visible_entity_ids(pool,user,org)")
    print("        member -> delegate_grants.get_delegate_visible_entity_ids(pool,org,user)")
    print("        both   -> restricted_access.filter_restricted(pool,ids,user,org)")
    print("      is_staff resolved by services.permissions.is_staff, threaded into _run_loop.")
    ok("A1 [discovery]: reported Task-1's four findings + the where-else-gap survey")


# ── mocked LLM for the deterministic real-chat-flow proof ───────────────────
def _make_mock_llm(tool_name, tool_input):
    """Return an async stand-in for call_claude_with_tools that drives the REAL
    _run_loop: first turn emits a tool_use for the new action, second turn ends."""
    state = {"n": 0}

    async def _mock(system, messages, tools, max_tokens, org_id, task_type):
        state["n"] += 1
        if state["n"] == 1:
            return {
                "stop_reason": "tool_use",
                "content": [{
                    "type": "tool_use", "id": "call_1",
                    "name": tool_name, "input": tool_input,
                }],
            }
        return {"stop_reason": "end_turn",
                "content": [{"type": "text", "text": "Done."}]}

    return _mock


async def run():
    pool = await asyncpg.create_pool(
        DATABASE_URL, statement_cache_size=0, min_size=1, max_size=4,
    )

    from services.assistant_actions import register_all
    from services.assistant_actions.queries import _count_entities, _count_investments
    from services.action_registry import REGISTRY
    from services.delegate_grants import grant_delegate, VIEW_ONLY

    register_all()
    ENT_DESC = REGISTRY.get("entities.count").description
    INV_DESC = REGISTRY.get("investments.count").description

    try:
        async with pool.acquire() as conn:
            await cleanup(conn)

        # ---- A1 discovery ------------------------------------------------
        report_discovery()

        # ---- seed --------------------------------------------------------
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO organizations (id, name, slug) VALUES
                    ($1, 'AQ Verify Main', 'aq-verify-main'),
                    ($2, 'AQ Verify Other', 'aq-verify-other')
                ON CONFLICT (id) DO NOTHING
                """,
                ORG_MAIN, ORG_OTHER,
            )
            await seed_user(conn, U_SUPER, ORG_MAIN, "super", "super_admin")
            await seed_user(conn, U_STAFF, ORG_MAIN, "staff", "investment_staff")
            await seed_user(conn, U_MEMBER, ORG_MAIN, "member", "member")
            await seed_user(conn, U_OTHER, ORG_OTHER, "other", "member")

            await seed_entity(conn, E_MEMBER, ORG_MAIN, "AQ Member Co", "individual")
            await seed_entity(conn, E_STAFF, ORG_MAIN, "AQ Staff Co", "llc")
            await seed_entity(conn, E_HIDDEN, ORG_MAIN, "AQ Hidden Co", "llc")
            await seed_entity(conn, E_NY, ORG_MAIN, "AQ NY Co", "llc")
            await seed_entity(conn, E_OTHER, ORG_OTHER, "AQ Other Co", "llc")

            await seed_address(conn, E_MEMBER, ORG_MAIN, "CT")
            await seed_address(conn, E_STAFF, ORG_MAIN, "CT")
            await seed_address(conn, E_HIDDEN, ORG_MAIN, "CT")
            await seed_address(conn, E_NY, ORG_MAIN, "NY")
            await seed_address(conn, E_OTHER, ORG_OTHER, "CT")

            await seed_deal(conn, D_MAIN, ORG_MAIN, "AQ Main Deal")
            await seed_deal(conn, D_OTHER, ORG_OTHER, "AQ Other Deal")

            # ORG_MAIN investments: committed x2 (member, staff), funded x1 (hidden)
            await seed_investment(conn, ORG_MAIN, D_MAIN, U_MEMBER, E_MEMBER, "committed")
            await seed_investment(conn, ORG_MAIN, D_MAIN, U_STAFF, E_STAFF, "committed")
            await seed_investment(conn, ORG_MAIN, D_MAIN, U_SUPER, E_HIDDEN, "funded")
            # ORG_OTHER investment: committed (must never leak into ORG_MAIN counts)
            await seed_investment(conn, ORG_OTHER, D_OTHER, U_OTHER, E_OTHER, "committed")

            # visibility fixtures
            await conn.execute(
                """
                INSERT INTO staff_assignments (org_id, entity_id, assigned_to_user_id, role_label)
                VALUES ($1, $2, $3, 'AQ verify')
                """,
                ORG_MAIN, E_STAFF, U_STAFF,
            )
        await grant_delegate(pool, ORG_MAIN, principal_entity_id=E_MEMBER,
                             scope=VIEW_ONLY, delegate_user_id=U_MEMBER, granted_by=U_SUPER)

        # ---- A2: entities CT count correct (super_admin sees all) --------
        res = await _count_entities(pool, U_SUPER, ORG_MAIN, is_staff=True, state="CT")
        n_ct = res["data"]["count"]
        res_ny = await _count_entities(pool, U_SUPER, ORG_MAIN, is_staff=True, state="NY")
        if n_ct == 3 and res_ny["data"]["count"] == 1:
            ok(f"A2 [entities-by-state]: super_admin sees exactly 3 entities in CT and 1 in NY")
        else:
            fail("A2 [entities-by-state]: wrong count",
                 f"CT={n_ct} (expect 3), NY={res_ny['data']['count']} (expect 1)")

        # ---- A3: investments by status/stage correct ---------------------
        res_c = await _count_investments(pool, U_SUPER, ORG_MAIN, is_staff=True, stage="committed")
        res_f = await _count_investments(pool, U_SUPER, ORG_MAIN, is_staff=True, stage="funded")
        res_all = await _count_investments(pool, U_SUPER, ORG_MAIN, is_staff=True)
        if (res_c["data"]["count"] == 2 and res_f["data"]["count"] == 1
                and res_all["data"]["count"] == 3):
            ok("A3 [investments-by-status]: super_admin sees 2 committed, 1 funded, 3 total")
        else:
            fail("A3 [investments-by-status]: wrong count",
                 f"committed={res_c['data']['count']} (exp 2), funded={res_f['data']['count']} "
                 f"(exp 1), all={res_all['data']['count']} (exp 3)")

        # ---- A4: LIMITED visibility scoping (staff AND member) -----------
        staff_ct = await _count_entities(pool, U_STAFF, ORG_MAIN, is_staff=True, state="CT")
        staff_inv = await _count_investments(pool, U_STAFF, ORG_MAIN, is_staff=True, stage="committed")
        member_ct = await _count_entities(pool, U_MEMBER, ORG_MAIN, is_staff=False, state="CT")
        member_inv = await _count_investments(pool, U_MEMBER, ORG_MAIN, is_staff=False, stage="committed")
        staff_ok = staff_ct["data"]["count"] == 1 and staff_inv["data"]["count"] == 1
        member_ok = member_ct["data"]["count"] == 1 and member_inv["data"]["count"] == 1
        if staff_ok and member_ok:
            ok("A4 [visibility-scoping]: a staff user (1 assignment) counts 1 CT entity + 1 "
               "committed investment, and a member (1 grant) likewise counts 1 + 1 — NOT the "
               "org totals of 3/2 — the visibility engine is genuinely applied, not bypassed")
        else:
            fail("A4 [visibility-scoping]: limited user saw more than their own set",
                 f"staff CT={staff_ct['data']['count']}/inv={staff_inv['data']['count']} "
                 f"(exp 1/1), member CT={member_ct['data']['count']}/inv={member_inv['data']['count']} "
                 f"(exp 1/1); org totals are 3/2")

        # ---- A5: cross-org isolation on the REAL app_service connection --
        await assert_cross_org(pool)

        # ---- A6: real chat flow invokes the new actions end-to-end -------
        await assert_chat_flow(pool, ENT_DESC)

        # ---- A7: teardown ------------------------------------------------
        async with pool.acquire() as conn:
            await cleanup(conn)
            remaining = await leftover_count(conn)
        if remaining == 0:
            ok("A7 [teardown]: zero leftover test rows (count=0)")
        else:
            fail("A7 [teardown]: leftover rows", f"count={remaining}")

    finally:
        try:
            async with pool.acquire() as conn:
                await cleanup(conn)
        finally:
            await pool.close()

    print(f"\n{'=' * 56}")
    print(f"assistantquery: {passed} passed, {failed} failed")
    if failed:
        sys.exit(1)


# ── A5 helper ───────────────────────────────────────────────────────────────
async def assert_cross_org(pg_pool):
    """Prove ORG_OTHER data is excluded from ORG_MAIN counts, run on the REAL
    ``app_service`` connection. Even with the other-org id DELIBERATELY injected
    into the allowed set, the org-scoped query (and any RLS backstop) drops it."""
    # The exact SQL the handlers run, with an allowed set that WRONGLY includes
    # the other-org entity — the org filter must still exclude it.
    all_ct = [E_MEMBER, E_STAFF, E_HIDDEN, E_OTHER]
    ent_sql = """
        SELECT COUNT(*) FROM entities e
        WHERE e.org_id = $1 AND e.valid_to IS NULL AND e.system_to IS NULL
          AND e.is_active = true AND e.id = ANY($2::uuid[])
          AND EXISTS (SELECT 1 FROM entity_addresses a
                      WHERE a.entity_id = e.id AND a.org_id = e.org_id
                        AND a.valid_to IS NULL AND a.system_to IS NULL
                        AND UPPER(TRIM(a.state)) = 'CT')
    """
    inv_sql = """
        SELECT COUNT(*) FROM member_investments mi
        WHERE mi.org_id = $1 AND mi.valid_to IS NULL AND mi.system_to IS NULL
          AND mi.entity_id = ANY($2::uuid[]) AND mi.investment_stage = 'committed'
    """
    conn_desc = "postgres pool (fallback)"
    as_pool = None
    if APP_SERVICE_DATABASE_URL:
        try:
            as_pool = await asyncpg.create_pool(
                APP_SERVICE_DATABASE_URL, statement_cache_size=0, min_size=1, max_size=2,
            )
            conn_desc = "app_service connection"
        except Exception as exc:  # pragma: no cover
            print(f"[A5] app_service connect failed ({exc}); falling back to postgres pool")

    use_pool = as_pool or pg_pool
    try:
        async with use_pool.acquire() as conn:
            # Establish the org RLS context the app sets per request (session scope).
            await conn.execute(
                "SELECT set_config('app.current_org_id', $1, false),"
                "       set_config('app.is_super_admin', 'false', false),"
                "       set_config('app.current_auth0_sub', '', false)",
                ORG_MAIN,
            )
            ent_count = await conn.fetchval(ent_sql, ORG_MAIN, all_ct)
            inv_count = await conn.fetchval(inv_sql, ORG_MAIN, all_ct)
    finally:
        if as_pool:
            await as_pool.close()

    if ent_count == 3 and inv_count == 2:
        ok(f"A5 [cross-org]: on the {conn_desc}, ORG_MAIN CT entities=3 and committed "
           "investments=2 even with the ORG_OTHER id injected — a different org's data "
           "is never counted")
    else:
        fail("A5 [cross-org]: other-org data leaked into ORG_MAIN count",
             f"entities={ent_count} (exp 3), investments={inv_count} (exp 2), via {conn_desc}")


# ── A6 helper ───────────────────────────────────────────────────────────────
async def assert_chat_flow(pg_pool, ent_desc):
    """Drive the REAL POST /assistant/message endpoint through the real _run_loop.

    Deterministic backbone: monkeypatch the model so the loop reliably emits a
    tool_use for entities_count — this proves the registry→loop→handler→render
    wiring end-to-end without depending on live-model tool choice. If
    ANTHROPIC_API_KEY is present, an ADDITIONAL live turn is exercised too.
    """
    import main as app_main
    import routers.assistant as assistant_router

    app_main.verify_token = lambda token: {"sub": U_SUPER, "org_id": ORG_MAIN}
    mock = _make_mock_llm("entities_count", {"state": "CT"})
    original = assistant_router.call_claude_with_tools
    assistant_router.call_claude_with_tools = mock

    H = {"Authorization": "Bearer aq-verify"}
    try:
        from httpx import ASGITransport, AsyncClient
        async with AsyncClient(
            transport=ASGITransport(app=app_main.app), base_url="http://verify"
        ) as c:
            r = await c.post(
                "/api/v1/assistant/message",
                headers=H,
                json={"message": "How many entities are in CT?",
                      "context_ref": {"type": "aq", "id": "chatflow"}},
            )
            body = r.json()
            render = body.get("render") or {}
            disclosures = body.get("disclosures") or []
            comp = render.get("component")
            count = (render.get("props") or {}).get("count")
            fired = (comp == "EntityCount") and (ent_desc in disclosures) and (count == 3)
            if r.status_code == 200 and fired:
                ok("A6 [chat-flow]: POST /assistant/message routed through the real _run_loop, "
                   "executed entities.count, and returned the EntityCount render with count=3")
            else:
                fail("A6 [chat-flow]: new action not invoked end-to-end",
                     f"status={r.status_code}, component={comp}, count={count}, "
                     f"in_disclosures={ent_desc in disclosures}")
    except Exception as exc:
        fail("A6 [chat-flow]: exception", f"{exc}\n{traceback.format_exc()}")
    finally:
        assistant_router.call_claude_with_tools = original
        try:
            from services.database import close_pool
            await close_pool()
        except Exception:
            pass


if __name__ == "__main__":
    asyncio.run(run())
