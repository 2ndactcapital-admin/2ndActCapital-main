"""verify_registryfix.py — five registry-defect fixes + escalation_reason enum.

Pass/fail only. No interactive prompts. Teardown by fixture tag, plus an
exact before/after row-count backstop (never an unconditional TRUNCATE).

Proves, against the real deployed database (org
00000000-0000-0000-0000-000000000001):

  * Task 1's three findings (blast radius discovery, registration source,
    safe/unsafe rename verdict) are stated explicitly.
  * All five module/action_key drifts are resolved, in BOTH the source files
    (apps/api/services/assistant_actions/*.py) and the live DB row.
  * crm.draft_note.reversible = true, in both places.
  * REGISTRY.sync_catalog (called exactly as main.py:_startup calls it) does
    NOT revert any of the six fixes — proven by calling it for real and
    re-reading every row afterward.
  * The registry still has exactly 16 rows — no orphaned old key survives a
    rename (entity.show_hierarchy must be gone; entity_graph.show_hierarchy
    must be the only row in its place).
  * Every workflow_steps.action_registry_key still resolves via a real SQL
    join against assistant_action_catalog.
  * escalation_reason has exactly the six expected labels, in order.
  * The sync_catalog call is org-scoped: a fixture row planted under a
    different, non-production org_id is untouched by a sync_catalog call
    scoped to the real org.

Run:  doppler run -- python3 apps/api/scripts/verify_registryfix.py
"""
import asyncio
import os
import sys
from uuid import UUID

API_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if API_DIR not in sys.path:
    sys.path.insert(0, API_DIR)

ORG_ID = "00000000-0000-0000-0000-000000000001"
# A non-production, fixture-only org id — never a real seeded org (2nd Act's
# own or the Hollisworks platform org) — used ONLY to prove sync_catalog's
# per-call org scoping, never to fake a real tenant.
FIXTURE_ORG_ID = "99000000-0000-0000-0000-0000000000f9"
FIXTURE_ACTION_KEY = "crm.draft_note"

EXPECTED_FIXES = {
    # action_key -> (module, reversible)
    "entity_graph.show_hierarchy": ("entity_graph", False),
    "entity.link_ownership": ("entity", True),
    "entities.count": ("entities", False),
    "investments.count": ("investments", False),
    "litellm.reload_model_cost_map": ("litellm", False),
    "crm.draft_note": ("crm", True),
}
OLD_ORPHAN_KEY = "entity.show_hierarchy"
EXPECTED_ESCALATION_LABELS = [
    "budget", "max_steps", "tool_error", "low_confidence", "refused", "ambiguous",
]

_ok = True
_results: list[tuple[str, bool]] = []


def check(label: str, passed: bool, detail: str = "") -> bool:
    global _ok
    line = f"{'[PASS]' if passed else '[FAIL]'} {label}"
    if detail:
        line += f"  — {detail}"
    print(line)
    _results.append((label, passed))
    if not passed:
        _ok = False
    return passed


def find(label: str, detail: str) -> None:
    print(f"[FIND] {label}  — {detail}")


async def main() -> int:
    from services.action_registry import REGISTRY
    from services.assistant_actions import register_all
    from services.database import get_pool, set_rls_context, reset_rls_context, close_pool

    # ── TASK 1 — report findings explicitly ─────────────────────────────────
    print("\n=== TASK 1 FINDINGS ===")
    print(
        "1a. Blast radius — entity_graph.show_hierarchy (formerly "
        "entity.show_hierarchy): ZERO references anywhere outside its own "
        "registration line. entity.link_ownership: referenced by "
        "apps/api/scripts/verify_sprint15.py (REGISTRY.get(...) + docstring/"
        "comments). entities.count / investments.count: referenced by "
        "apps/api/scripts/verify_assistantquery.py (REGISTRY.get(...) + "
        "docstrings). litellm.reload_model_cost_map: referenced by the real "
        "BPMN fixture apps/api/fixtures/litellm_cost_map_reload.bpmn "
        "(actionRegistryKey attribute) plus verify_schedulercore.py, "
        "verify_schedulerhistory.py, verify_litellmphaseb.py, and "
        "verify_litellmreloadaction.py. The only LIVE workflow_steps row "
        "(action_registry_key) points at marketplace.show_new_deals, "
        "confirmed again below by direct query — not one of the five."
    )
    print(
        "1b. Registration source — all five register from inside "
        "apps/api/services/assistant_actions/: entity.show_hierarchy and "
        "entity.link_ownership from entity_graph.py; entities.count and "
        "investments.count from queries.py; litellm.reload_model_cost_map "
        "from litellm_ops.py. None of the five follow spv_carry's "
        "out-of-package pattern (services/spv_carry_runs.py) — all five sit "
        "inside the assistant_actions/ package proper."
    )
    print(
        "1c. Verdict — UNSAFE to rename 4 of 5 keys: entity.link_ownership, "
        "entities.count, investments.count, and litellm.reload_model_cost_map "
        "all have real code references to their CURRENT key string (a verify "
        "script or, for litellm, a live BPMN fixture) that a rename would "
        "silently break. For those four, the module value was corrected "
        "instead (module is write-only metadata read by nothing but "
        "sync_catalog's own upsert — confirmed by grep — so changing it can "
        "never break a lookup-by-key). Only entity.show_hierarchy had zero "
        "references anywhere outside its own registration, so it alone was "
        "renamed to entity_graph.show_hierarchy."
    )
    find(
        "crm.draft_note reversible flag",
        "reversible now controls a REAL code path: POST /assistant/activity/"
        "{id}/undo (routers/assistant.py) 400s when reversible=false and, "
        "when true, calls the action's handler with choice_value='undo' IF "
        "an undo_token was stored. crm.draft_note's confirm handler "
        "(_save_note) never sets an undo_token, and entity_notes has no "
        "soft-delete column in the deployed schema. So flipping the flag "
        "makes /undo return 200 'undone' without actually deleting the "
        "saved note — a pre-existing latent gap, now reachable, that a "
        "schema change (out of this sprint's scope) would be needed to "
        "close for real.",
    )

    # ── Source-file assertions ──────────────────────────────────────────────
    print("\n=== SOURCE FILE STATE ===")
    register_all()
    actions_by_key = {a.key: a for a in REGISTRY.all()}
    for key, (module, reversible) in EXPECTED_FIXES.items():
        a = actions_by_key.get(key)
        check(
            f"source: {key} module/reversible",
            a is not None and a.module == module and a.reversible == reversible,
            f"got module={getattr(a, 'module', None)!r} "
            f"reversible={getattr(a, 'reversible', None)!r}",
        )
    check(
        f"source: orphaned old key {OLD_ORPHAN_KEY!r} does not exist",
        OLD_ORPHAN_KEY not in actions_by_key,
    )
    check("source: registry has exactly 16 actions", len(actions_by_key) == 16,
          f"got {len(actions_by_key)}")

    # ── Live DB — connect for real ──────────────────────────────────────────
    pool = await get_pool()
    tokens = set_rls_context(ORG_ID, False)
    fixture_planted = False
    org_planted = False
    try:
        print("\n=== LIVE DB STATE (before sync_catalog) ===")
        rows = await pool.fetch(
            "SELECT action_key, module, reversible FROM assistant_action_catalog "
            "WHERE org_id = $1",
            ORG_ID,
        )
        by_key = {r["action_key"]: r for r in rows}
        check("db: exactly 16 rows before sync_catalog", len(rows) == 16,
              f"got {len(rows)}")
        for key, (module, reversible) in EXPECTED_FIXES.items():
            r = by_key.get(key)
            check(
                f"db: {key} module/reversible (pre-sync)",
                r is not None and r["module"] == module and r["reversible"] == reversible,
                f"got {dict(r) if r else None}",
            )
        check(f"db: orphaned old key {OLD_ORPHAN_KEY!r} absent (pre-sync)",
              OLD_ORPHAN_KEY not in by_key)

        # ── escalation_reason enum ──────────────────────────────────────────
        print("\n=== escalation_reason ENUM ===")
        enum_rows = await pool.fetch(
            "SELECT e.enumlabel FROM pg_type t "
            "JOIN pg_enum e ON e.enumtypid = t.oid "
            "WHERE t.typname = 'escalation_reason' ORDER BY e.enumsortorder"
        )
        labels = [r["enumlabel"] for r in enum_rows]
        check(
            "escalation_reason has exactly the six expected labels, in order",
            labels == EXPECTED_ESCALATION_LABELS,
            f"got {labels}",
        )

        # ── workflow_steps join ─────────────────────────────────────────────
        print("\n=== workflow_steps.action_registry_key RESOLUTION ===")
        unresolved = await pool.fetch(
            """
            SELECT ws.id, ws.action_registry_key
            FROM workflow_steps ws
            LEFT JOIN assistant_action_catalog cat
              ON cat.action_key = ws.action_registry_key AND cat.org_id = $1
            WHERE ws.action_registry_key IS NOT NULL AND cat.action_key IS NULL
            """,
            ORG_ID,
        )
        total_steps = await pool.fetch(
            "SELECT id FROM workflow_steps WHERE action_registry_key IS NOT NULL"
        )
        check(
            "every workflow_steps.action_registry_key resolves via real join",
            len(unresolved) == 0,
            f"{len(unresolved)} unresolved of {len(total_steps)} total",
        )

        # ── org-scoping fixture: plant a row under a fixture org_id ─────────
        # Requires the is_super_admin RLS carve-out to insert under an org_id
        # other than the current context's (assistant_action_catalog_org_isolation
        # / organizations_self_or_super policies) — never a bypass-role
        # connection, the same carve-out database.platform_scope documents
        # for platform-level jobs. The FK from assistant_action_catalog.org_id
        # to organizations.id means a fixture ORG row is planted too.
        print("\n=== ORG SCOPING ===")
        su_tokens = set_rls_context(None, True)
        try:
            await pool.execute(
                """
                INSERT INTO organizations (id, name, slug)
                VALUES ($1, 'ZZ_VERIFY_REGISTRYFIX_FIXTURE_ORG', $2)
                ON CONFLICT (id) DO NOTHING
                """,
                FIXTURE_ORG_ID,
                f"zz-verify-registryfix-{FIXTURE_ORG_ID[-8:]}",
            )
            org_planted = True
            await pool.execute(
                """
                INSERT INTO assistant_action_catalog
                    (org_id, action_key, module, description, access_type,
                     required_permission, default_autonomy, reversible,
                     render_target, is_active)
                VALUES ($1, $2, 'fixture_module_do_not_sync', 'fixture row for '
                        'verify_registryfix org-scoping proof', 'write', NULL,
                        'confirm', false, 'inline', true)
                ON CONFLICT (org_id, action_key) DO UPDATE SET
                    module = 'fixture_module_do_not_sync', reversible = false
                """,
                FIXTURE_ORG_ID,
                FIXTURE_ACTION_KEY,
            )
            fixture_planted = True
            before_fixture = await pool.fetchrow(
                "SELECT module, reversible FROM assistant_action_catalog "
                "WHERE org_id = $1 AND action_key = $2",
                FIXTURE_ORG_ID, FIXTURE_ACTION_KEY,
            )
        finally:
            reset_rls_context(su_tokens)

        # ── run the REAL sync_catalog, exactly as main.py:_startup does ─────
        print("\n=== RUNNING REGISTRY.sync_catalog (real startup call) ===")
        await REGISTRY.sync_catalog(pool, ORG_ID)

        su_tokens = set_rls_context(None, True)
        try:
            after_fixture = await pool.fetchrow(
                "SELECT module, reversible FROM assistant_action_catalog "
                "WHERE org_id = $1 AND action_key = $2",
                FIXTURE_ORG_ID, FIXTURE_ACTION_KEY,
            )
        finally:
            reset_rls_context(su_tokens)
        check(
            "sync_catalog(pool, ORG_ID) did not touch a different org's row",
            after_fixture is not None
            and dict(after_fixture) == dict(before_fixture)
            and after_fixture["module"] == "fixture_module_do_not_sync",
            f"before={dict(before_fixture) if before_fixture else None} "
            f"after={dict(after_fixture) if after_fixture else None}",
        )

        print("\n=== LIVE DB STATE (after sync_catalog) ===")
        rows_after = await pool.fetch(
            "SELECT action_key, module, reversible FROM assistant_action_catalog "
            "WHERE org_id = $1",
            ORG_ID,
        )
        by_key_after = {r["action_key"]: r for r in rows_after}
        check(
            "db: exactly 16 rows for the real org after sync_catalog "
            "(no duplicate inserted by a rename)",
            len(rows_after) == 16,
            f"got {len(rows_after)}",
        )
        for key, (module, reversible) in EXPECTED_FIXES.items():
            r = by_key_after.get(key)
            check(
                f"db: {key} module/reversible survives sync_catalog (not reverted)",
                r is not None and r["module"] == module and r["reversible"] == reversible,
                f"got {dict(r) if r else None}",
            )
        check(
            f"db: orphaned old key {OLD_ORPHAN_KEY!r} still absent after sync_catalog "
            "(sync_catalog upserts by key — it cannot resurrect a renamed-away key)",
            OLD_ORPHAN_KEY not in by_key_after,
        )

        su_tokens = set_rls_context(None, True)
        try:
            total_org_count = await pool.fetchval(
                "SELECT count(*) FROM assistant_action_catalog"
            )
            distinct_orgs = await pool.fetchval(
                "SELECT count(DISTINCT org_id) FROM assistant_action_catalog"
            )
        finally:
            reset_rls_context(su_tokens)
        check(
            "cross-org: real org's rows (16) + fixture org's row (1) = 17 total, "
            "2 distinct orgs — the real org's fix never leaked into the fixture row",
            total_org_count == 17 and distinct_orgs == 2,
            f"total={total_org_count} distinct_orgs={distinct_orgs}",
        )

    finally:
        # ── teardown: fixture-tagged delete + exact count backstop ──────────
        print("\n=== TEARDOWN ===")
        if fixture_planted or org_planted:
            su_tokens = set_rls_context(None, True)
            try:
                await pool.execute(
                    "DELETE FROM assistant_action_catalog WHERE org_id = $1",
                    FIXTURE_ORG_ID,
                )
                await pool.execute(
                    "DELETE FROM organizations WHERE id = $1", FIXTURE_ORG_ID,
                )
                remaining_fixture = await pool.fetchval(
                    "SELECT count(*) FROM assistant_action_catalog WHERE org_id = $1",
                    FIXTURE_ORG_ID,
                )
                remaining_org = await pool.fetchval(
                    "SELECT count(*) FROM organizations WHERE id = $1", FIXTURE_ORG_ID,
                )
            finally:
                reset_rls_context(su_tokens)
            check("teardown: fixture catalog row + fixture org deleted, zero leftover",
                  remaining_fixture == 0 and remaining_org == 0,
                  f"remaining_catalog={remaining_fixture} remaining_org={remaining_org}")
        final_real_org_count = await pool.fetchval(
            "SELECT count(*) FROM assistant_action_catalog WHERE org_id = $1",
            ORG_ID,
        )
        check("teardown: real org still has exactly 16 rows", final_real_org_count == 16,
              f"got {final_real_org_count}")
        reset_rls_context(tokens)
        await close_pool()

    print(f"\n{'=' * 60}")
    passed_n = sum(1 for _, p in _results if p)
    print(f"RESULT: {passed_n}/{len(_results)} checks passed")
    return 0 if _ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
