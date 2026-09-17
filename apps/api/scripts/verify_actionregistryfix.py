"""verify_actionregistryfix.py — action registry defects, tiers, propose()
(actionregistryfix.structural).

Pass/fail only. No interactive prompts. Teardown by fixture id, plus an
exact before/after row-count backstop (never an unconditional TRUNCATE).

════════════════════════════ TASK 1 FINDINGS ════════════════════════════

1a. MODULE COLLAPSE BLAST RADIUS — entities / entity / entity_graph module
    values collapse to `entity`. The only thing that changes is the
    ``module`` column/field; every action_key is UNCHANGED
    (entities.count, entity_graph.show_hierarchy, entity.link_ownership all
    keep their existing keys). ``module`` is write-only metadata: grepping
    apps/api/routers and apps/api/services for `.module` on an
    AssistantAction/catalog row turns up exactly one reader —
    ActionRegistry.sync_catalog's own upsert — confirming the finding the
    prior registryfix.structural sprint already made still holds. Because
    action_key never changes, there is no rename blast radius to reason
    about at all: the ONE live workflow_steps.action_registry_key row
    (marketplace.show_new_deals) is untouched, and no BPMN fixture or verify
    script's REGISTRY.get(...) call references any of the three collapsed
    keys by a string that would need to change.

1b. spv.subscribe PERMISSION — the real HTTP endpoint (POST
    /spvs/{spv_id}/subscriptions, routers/spv.py) never required
    manage_deals either: subscribing is a member-initiated action, not a
    staff one (every OTHER route in that file calls
    require_permission(request, "manage_deals"); this one conspicuously
    does not). So the fix is not "make it staff-gated like show_captable" —
    it is "give the assistant path a REAL, named permission instead of
    none at all." indicate_interest (resource=deals, action=interest) is
    the existing, seeded permission for exactly this: a member's own
    self-directed capital commitment, held by the `member` and
    `investment_staff` roles in org 1. spv.show_captable's manage_deals
    gate is, separately, confirmed CORRECT, not a second defect: it mirrors
    the real endpoint's own gate exactly (GET /spvs/{spv_id}/captable also
    calls require_permission(request, "manage_deals")) — left unchanged.

1c. reversible READERS — grepping every router and service for `.reversible`
    turns up exactly one live consumer: routers/assistant.py's
    confirm_action (writes it into assistant_activities) and undo_activity
    (400s when the stored value is false). Both are reachable ONLY through
    POST /assistant/confirm, which itself 400s up front unless
    ``action.access_type == "write"``. No READ action's handler is ever
    invoked through that path — reads execute inline in the LLM loop
    (services/action_registry.py's own module docstring) and never produce
    an assistant_activities row at all. So `reversible` is provably
    meaningless on every one of the 10 reads; left at False (schema-level
    consistency, not a decision anyone made) rather than made nullable —
    a three-state column would make every real consumer (both in
    routers/assistant.py) handle a case that can never actually occur for
    them, for no live benefit.

    crm.draft_note's reversible flag: NOT a live defect to fix in this
    sprint. It was set True by 2999846 (registryfix.structural), then
    reverted to False by 72ba8c0 with the exact reasoning this sprint's
    prompt independently re-derives (no undo_token stored by _save_note,
    entity_notes has no soft-delete column — confirmed still true against
    the current schema snapshot). Current source already reads
    reversible=False with that history in a comment. Reported here per the
    "report the conflict rather than silently choosing" instruction; no
    code change made because the already-correct value would be reverted.

1d. MODULE IS STILL WRITE-ONLY METADATA — re-confirmed live (not assumed
    carried over): grepping apps/api/routers/*.py and apps/api/services/*.py
    for `.module` (excluding action registration sites and
    ActionRegistry.sync_catalog's own upsert) returns zero hits. No
    frontend bundle under apps/web (source, not build output) references
    a `module` field either. Safe to change freely.

═══════════════════════════════ WHAT THIS PROVES ════════════════════════

  * Task 1's four findings (printed above, not assumed).
  * spv.subscribe.required_permission == 'indicate_interest' in source AND
    in the live catalog row; a caller who holds NO permissions is refused
    with a real 403 by the REAL confirm_action function (not a
    re-implementation of its permission check), called with a bona fide
    fake Request; a caller who DOES hold indicate_interest passes that same
    gate (choice_value='none' short-circuits before any domain write, so
    the proof needs no spv/entity fixture).
  * entity.link_ownership gets the identical negative proof (same
    ungated fixture caller, same 403).
  * default_autonomy is gone from the AssistantAction dataclass, from
    every services/assistant_actions/*.py + spv_carry_runs.py registration,
    and from assistant_action_catalog's live columns (information_schema).
  * Every one of the 17 registered actions (16 original + propose) carries
    a tier in {1,2,3}; the Tier-1 set is EXACTLY {spv.subscribe,
    spv.record_transaction, entity.link_ownership,
    spv_carry.propose_from_realization} — set equality, so an accidental
    extra Tier-1 fails as loudly as a missing one.
  * propose() exists, is registered, and is genuinely invocable through the
    real POST /assistant/confirm path: a real agent_proposals row is
    created and re-read from an INDEPENDENT connection afterward.
  * register_all() + a live REGISTRY.sync_catalog call leaves exactly 17
    rows for org 1 — no orphan from the module collapse (there cannot be
    one: action_key never changes) — proven by an exact count, not "some
    rows exist."
  * Every workflow_steps.action_registry_key still resolves via a real SQL
    join against assistant_action_catalog.
  * sync_catalog does not revert any fix — proven by calling the real
    function again and re-reading every affected row afterward.
  * required_permission holds only real permissions (validated against the
    live `permissions` table) — never a role string like the old 'staff'.
  * Cross-org: a fixture row planted directly in a non-production org is
    untouched by a sync_catalog call scoped to org 1.
  * Teardown: zero leftover fixture rows, by an exact count.

Hydrates DATABASE_URL from Doppler over HTTPS at startup
(verify_agenticmakerchecker.py's _db_bootstrap pattern). Never prints a
credential value.

Run:  python3 apps/api/scripts/verify_actionregistryfix.py
"""
from __future__ import annotations

import asyncio
import dataclasses
import pathlib
import sys
from uuid import UUID

HERE = pathlib.Path(__file__).resolve()
sys.path.insert(0, str(HERE.parent))
from _db_bootstrap import bootstrap_async  # noqa: E402

import asyncpg  # noqa: E402

API_DIR = HERE.parents[1]
if str(API_DIR) not in sys.path:
    sys.path.insert(0, str(API_DIR))

ORG_REAL = UUID("00000000-0000-0000-0000-000000000001")  # 2nd Act Capital

# A non-production, fixture-only org — never a real seeded org — used to
# prove sync_catalog's per-call org scoping and to host the permission-gate
# call-path fixtures without touching any real user/role data.
FIXTURE_ORG = UUID("99000000-0000-0000-0000-0000af0f0001")
FIXTURE_ROLE_INTEREST = UUID("99000000-0000-0000-0000-0000af0f2001")

FIXTURE_USER_NOPERM = UUID("99000000-0000-0000-0000-0000af0f1001")   # zero roles
FIXTURE_USER_HASPERM = UUID("99000000-0000-0000-0000-0000af0f1002")  # holds indicate_interest

SUB = {
    FIXTURE_USER_NOPERM: str(FIXTURE_USER_NOPERM),
    FIXTURE_USER_HASPERM: str(FIXTURE_USER_HASPERM),
}

FIXTURE_SENTINEL_ACTION_KEY = "fixture.sentinel"

EXPECTED_TIER1 = {
    "spv.subscribe", "spv.record_transaction", "entity.link_ownership",
    "spv_carry.propose_from_realization",
}
EXPECTED_TOTAL_ACTIONS = 17  # 16 original + propose()
EXPECTED_COLLAPSED_MODULE = {
    "entities.count": "entity",
    "entity_graph.show_hierarchy": "entity",
    "entity.link_ownership": "entity",
}

_ok = True
_n_pass = 0
_n_fail = 0
_finds: list[str] = []


def check(passed: bool, detail: str = "") -> bool:
    global _ok, _n_pass, _n_fail
    print(f"{'[PASS]' if passed else '[FAIL]'} {detail}")
    if passed:
        _n_pass += 1
    else:
        _n_fail += 1
        _ok = False
    return passed


def find(label: str, detail: str = "") -> None:
    print(f"[FIND] {label}" + (f"  — {detail}" if detail else ""))
    _finds.append(label)


# ═══════════════════════════════════════════════════════════════════════════
# Fixture setup / teardown — raw connection, explicit is_super_admin GUC per
# transaction (never a bare session-level SET, per CLAUDE.md's
# platform_scope() convention).
# ═══════════════════════════════════════════════════════════════════════════
async def seed(conn) -> None:
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")

        await conn.execute(
            """INSERT INTO organizations (id, name, slug) VALUES ($1, $2, $3)
               ON CONFLICT (id) DO NOTHING""",
            FIXTURE_ORG, "ActionRegistryFix Fixture Org", "actionregistryfix-fixture",
        )

        interest_perm_id = await conn.fetchval(
            "SELECT id FROM permissions WHERE name = $1", "indicate_interest"
        )
        await conn.execute(
            """INSERT INTO roles (id, org_id, name, description)
               VALUES ($1, $2, 'arf_indicate_interest', 'verify fixture')
               ON CONFLICT (id) DO NOTHING""",
            FIXTURE_ROLE_INTEREST, FIXTURE_ORG,
        )
        await conn.execute(
            """INSERT INTO role_permissions (role_id, permission_id) VALUES ($1, $2)
               ON CONFLICT DO NOTHING""",
            FIXTURE_ROLE_INTEREST, interest_perm_id,
        )

        async def mk_user(uid):
            sub = SUB[uid]
            await conn.execute(
                """INSERT INTO users (id, org_id, email, full_name, auth0_sub, role, is_active)
                   VALUES ($1, $2, $3, $4, $5, 'member', true)
                   ON CONFLICT (id) DO UPDATE SET org_id = EXCLUDED.org_id""",
                uid, FIXTURE_ORG, f"arf-{uid}@test.local", f"ARF fixture {sub[:8]}", sub,
            )

        await mk_user(FIXTURE_USER_NOPERM)
        await mk_user(FIXTURE_USER_HASPERM)

        await conn.execute(
            "INSERT INTO user_roles (user_id, role_id) VALUES ($1, $2) ON CONFLICT DO NOTHING",
            FIXTURE_USER_HASPERM, FIXTURE_ROLE_INTEREST,
        )


async def teardown(conn) -> None:
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
        await conn.execute(
            "DELETE FROM agent_proposals WHERE org_id = $1", FIXTURE_ORG,
        )
        await conn.execute(
            "DELETE FROM assistant_activities WHERE org_id = $1", FIXTURE_ORG,
        )
        await conn.execute(
            "DELETE FROM assistant_action_catalog WHERE org_id = $1", FIXTURE_ORG,
        )
        await conn.execute(
            "DELETE FROM user_roles WHERE user_id = ANY($1::uuid[])",
            [FIXTURE_USER_NOPERM, FIXTURE_USER_HASPERM],
        )
        await conn.execute(
            "DELETE FROM role_permissions WHERE role_id = $1", FIXTURE_ROLE_INTEREST,
        )
        await conn.execute("DELETE FROM roles WHERE id = $1", FIXTURE_ROLE_INTEREST)
        await conn.execute(
            "DELETE FROM audit_log WHERE user_id = ANY($1::uuid[])",
            [FIXTURE_USER_NOPERM, FIXTURE_USER_HASPERM],
        )
        await conn.execute(
            "DELETE FROM users WHERE id = ANY($1::uuid[])",
            [FIXTURE_USER_NOPERM, FIXTURE_USER_HASPERM],
        )
        await conn.execute("DELETE FROM organizations WHERE id = $1", FIXTURE_ORG)


# ═══════════════════════════════════════════════════════════════════════════
# A minimal stand-in for fastapi.Request — every function on the real
# confirm_action call path (services.users.ensure_user, routers.entities.
# get_org_id, services.rbac.get_user_permissions) reads ONLY
# request.state.user, nothing else.
# ═══════════════════════════════════════════════════════════════════════════
class _FakeState:
    def __init__(self, user: dict):
        self.user = user


class _FakeRequest:
    def __init__(self, user: dict):
        self.state = _FakeState(user)


def _claims_for(user_id: UUID) -> dict:
    return {"sub": SUB[user_id], "org_id": str(FIXTURE_ORG)}


async def main() -> int:
    print(__doc__)

    # ── Source-level assertions (no DB needed) ──────────────────────────────
    from services.action_registry import REGISTRY, AssistantAction
    from services.assistant_actions import register_all

    field_names = {f.name for f in dataclasses.fields(AssistantAction)}
    check("default_autonomy" not in field_names,
          "[Y] AssistantAction dataclass no longer has a default_autonomy field")
    check("tier" in field_names,
          "[Y] AssistantAction dataclass has a tier field")

    register_all()
    actions_by_key = {a.key: a for a in REGISTRY.all()}
    check(len(actions_by_key) == EXPECTED_TOTAL_ACTIONS,
          f"[Y] source registry has exactly {EXPECTED_TOTAL_ACTIONS} actions "
          f"(got {len(actions_by_key)})")
    check("propose" in actions_by_key, "[Y] propose() is registered")

    tier1_actual = {k for k, a in actions_by_key.items() if a.tier == 1}
    check(tier1_actual == EXPECTED_TIER1,
          f"[Y] Tier-1 set is exactly correct (got {sorted(tier1_actual)})")
    check(all(a.tier in (1, 2, 3) for a in actions_by_key.values()),
          "[Y] every source action has tier in {1,2,3}")

    for key, expected_module in EXPECTED_COLLAPSED_MODULE.items():
        a = actions_by_key.get(key)
        check(a is not None and a.module == expected_module,
              f"source: {key} module == {expected_module!r} "
              f"(got {getattr(a, 'module', None)!r})")

    subscribe = actions_by_key.get("spv.subscribe")
    check(subscribe is not None and subscribe.required_permission == "indicate_interest",
          f"[Y] source: spv.subscribe.required_permission == 'indicate_interest' "
          f"(got {getattr(subscribe, 'required_permission', None)!r})")
    link = actions_by_key.get("entity.link_ownership")
    check(link is not None and link.required_permission != "staff",
          f"source: entity.link_ownership.required_permission is no longer the role 'staff' "
          f"(got {getattr(link, 'required_permission', None)!r})")
    captable = actions_by_key.get("spv.show_captable")
    check(captable is not None and captable.required_permission == "manage_deals",
          "source: spv.show_captable gate left unchanged (confirmed correct, mirrors "
          "the real endpoint's own gate — see Task 1b)")
    draft_note = actions_by_key.get("crm.draft_note")
    check(draft_note is not None and draft_note.reversible is False,
          "[Y] source: crm.draft_note.reversible == False (already correct — see Task 1c)")

    # ── DB bootstrap ─────────────────────────────────────────────────────────
    url = await bootstrap_async()
    if not url:
        check(False, "could not hydrate a working DATABASE_URL from Doppler")
        print(f"\n{'=' * 70}\nTOTAL: {_n_pass} PASS, {_n_fail} FAIL, {len(_finds)} FIND\n{'=' * 70}")
        return 1

    raw_conn = await asyncpg.connect(url, statement_cache_size=0, ssl="require")
    try:
        await teardown(raw_conn)  # in case a prior run died mid-fixture
        await seed(raw_conn)

        # information_schema: default_autonomy gone, tier present + NOT NULL
        cols = await raw_conn.fetch(
            """
            SELECT column_name, is_nullable FROM information_schema.columns
            WHERE table_name = 'assistant_action_catalog'
              AND column_name IN ('default_autonomy', 'tier')
            """
        )
        by_name = {r["column_name"]: r for r in cols}
        check("default_autonomy" not in by_name,
              "[Y] assistant_action_catalog has no default_autonomy column")
        check("tier" in by_name and by_name["tier"]["is_nullable"] == "NO",
              "[Y] assistant_action_catalog.tier exists and is NOT NULL")

        from services.database import get_pool, set_rls_context, reset_rls_context, close_pool

        pool = await get_pool()
        try:
            # ── sync_catalog for real, scoped to org 1 ──────────────────────
            tokens = set_rls_context(str(ORG_REAL), False)
            try:
                await REGISTRY.sync_catalog(pool, str(ORG_REAL))
                rows = await pool.fetch(
                    "SELECT action_key, module, tier, required_permission, reversible "
                    "FROM assistant_action_catalog WHERE org_id = $1",
                    ORG_REAL,
                )
            finally:
                reset_rls_context(tokens)

            by_key = {r["action_key"]: r for r in rows}
            check(len(rows) == EXPECTED_TOTAL_ACTIONS,
                  f"[Y] db: exactly {EXPECTED_TOTAL_ACTIONS} rows for org 1 after "
                  f"sync_catalog — no orphan from the module collapse (got {len(rows)})")
            check("propose" in by_key, "[Y] db: propose row exists after sync_catalog")

            db_tier1 = {k for k, r in by_key.items() if r["tier"] == 1}
            check(db_tier1 == EXPECTED_TIER1,
                  f"[Y] db: Tier-1 set is exactly correct (got {sorted(db_tier1)})")
            check(all(r["tier"] in (1, 2, 3) for r in by_key.values()),
                  "[Y] db: every row has tier in {1,2,3}")

            for key, expected_module in EXPECTED_COLLAPSED_MODULE.items():
                r = by_key.get(key)
                check(r is not None and r["module"] == expected_module,
                      f"db: {key} module == {expected_module!r} (got {dict(r) if r else None})")

            check(by_key["spv.subscribe"]["required_permission"] == "indicate_interest",
                  "[Y] db: spv.subscribe.required_permission == 'indicate_interest' "
                  "— sync_catalog does not revert the fix")

            # required_permission holds only real permissions, never a role
            bad_perm_rows = await pool.fetch(
                """
                SELECT cat.action_key, cat.required_permission
                FROM assistant_action_catalog cat
                WHERE cat.org_id = $1 AND cat.required_permission IS NOT NULL
                  AND NOT EXISTS (SELECT 1 FROM permissions p WHERE p.name = cat.required_permission)
                """,
                ORG_REAL,
            )
            check(len(bad_perm_rows) == 0,
                  f"[Y] db: every non-null required_permission resolves to a real "
                  f"permission row (found {len(bad_perm_rows)} bad: "
                  f"{[dict(r) for r in bad_perm_rows]})")

            # workflow_steps.action_registry_key resolution
            unresolved = await pool.fetch(
                """
                SELECT ws.id, ws.action_registry_key
                FROM workflow_steps ws
                LEFT JOIN assistant_action_catalog cat
                  ON cat.action_key = ws.action_registry_key AND cat.org_id = $1
                WHERE ws.action_registry_key IS NOT NULL AND cat.id IS NULL
                """,
                ORG_REAL,
            )
            check(len(unresolved) == 0,
                  f"[Y] every workflow_steps.action_registry_key resolves via a real "
                  f"join (found {len(unresolved)} unresolved: {[dict(r) for r in unresolved]})")

            # ── Cross-org isolation ──────────────────────────────────────────
            tokens = set_rls_context(str(FIXTURE_ORG), True)
            try:
                async with pool.acquire() as conn:
                    await conn.execute(
                        """
                        INSERT INTO assistant_action_catalog
                            (org_id, action_key, module, description, access_type,
                             required_permission, tier, reversible, render_target, is_active)
                        VALUES ($1, $2, 'fixture', 'sentinel row', 'read', NULL, 3, false, 'inline', true)
                        ON CONFLICT (org_id, action_key) DO NOTHING
                        """,
                        FIXTURE_ORG, FIXTURE_SENTINEL_ACTION_KEY,
                    )
            finally:
                reset_rls_context(tokens)

            tokens = set_rls_context(str(ORG_REAL), False)
            try:
                await REGISTRY.sync_catalog(pool, str(ORG_REAL))
            finally:
                reset_rls_context(tokens)

            tokens = set_rls_context(str(FIXTURE_ORG), True)
            try:
                fixture_rows = await pool.fetch(
                    "SELECT action_key FROM assistant_action_catalog WHERE org_id = $1",
                    FIXTURE_ORG,
                )
            finally:
                reset_rls_context(tokens)
            check(
                [r["action_key"] for r in fixture_rows] == [FIXTURE_SENTINEL_ACTION_KEY],
                f"[Y] cross-org: a sync_catalog call scoped to org 1 left the fixture "
                f"org's row untouched (got {[r['action_key'] for r in fixture_rows]})",
            )

            # ── Call-path proof: ungated caller genuinely refused ────────────
            from fastapi import HTTPException
            from routers.assistant import confirm_action, ConfirmBody

            async def _try_confirm(user_id: UUID, action_key: str, params: dict,
                                    choice_value: str):
                tokens = set_rls_context(str(FIXTURE_ORG), False)
                try:
                    req = _FakeRequest(_claims_for(user_id))
                    body = ConfirmBody(
                        proposed_action={"action_key": action_key, "params": params,
                                         "rationale": "verify fixture"},
                        choice_value=choice_value,
                    )
                    return await confirm_action(req, body)
                finally:
                    reset_rls_context(tokens)

            refused = False
            try:
                await _try_confirm(
                    FIXTURE_USER_NOPERM, "spv.subscribe",
                    {"spv_id": "00000000-0000-0000-0000-000000000000",
                     "entity_id": "00000000-0000-0000-0000-000000000000",
                     "commitment_amount": 1000.0},
                    "none",
                )
            except HTTPException as exc:
                refused = exc.status_code == 403
            check(refused,
                  "[Y] spv.subscribe: a caller with ZERO permissions is genuinely "
                  "refused (403) by the REAL confirm_action function")

            allowed_response = await _try_confirm(
                FIXTURE_USER_HASPERM, "spv.subscribe",
                {"spv_id": "00000000-0000-0000-0000-000000000000",
                 "entity_id": "00000000-0000-0000-0000-000000000000",
                 "commitment_amount": 1000.0},
                "none",  # short-circuits the handler before any domain write
            )
            check(isinstance(allowed_response, dict) and "activity_id" in allowed_response,
                  "[Y] spv.subscribe: a caller who holds indicate_interest passes the "
                  "SAME gate (choice_value='none' proves this without touching "
                  "spv_subscriptions)")

            refused_link = False
            try:
                await _try_confirm(
                    FIXTURE_USER_NOPERM, "entity.link_ownership",
                    {"from_entity_id": "00000000-0000-0000-0000-000000000000",
                     "to_entity_id": "00000000-0000-0000-0000-000000000000",
                     "ownership_pct": 10.0},
                    "cancel",
                )
            except HTTPException as exc:
                refused_link = exc.status_code == 403
            check(refused_link,
                  "entity.link_ownership: a caller with ZERO permissions is also "
                  "genuinely refused (403) — the old 'staff' role-string gate would "
                  "have refused EVERY caller including a real manage_deals holder; "
                  "this proves the new gate is a real, checkable permission")

            # ── propose() is genuinely invocable end-to-end ──────────────────
            propose_response = await _try_confirm(
                FIXTURE_USER_HASPERM, "propose",
                {"agent_key": "compliance_analyst", "object_type": "verify_fixture",
                 "payload": {"note": "actionregistryfix verify fixture"}},
                "confirm",
            )
            proposal_id = None
            if isinstance(propose_response, dict):
                proposal_id = (propose_response.get("result") or {}).get("proposal_id")
            check(bool(proposal_id),
                  f"[Y] propose(): a real POST /assistant/confirm call created an "
                  f"agent_proposals row (proposal_id={proposal_id!r})")

            if proposal_id:
                # Re-read from an INDEPENDENT connection — never trust the same
                # connection that wrote it.
                independent = await asyncpg.connect(url, statement_cache_size=0, ssl="require")
                try:
                    async with independent.transaction():
                        await independent.execute(
                            "SELECT set_config('app.current_org_id', $1, true)",
                            str(FIXTURE_ORG),
                        )
                        row = await independent.fetchrow(
                            "SELECT org_id, agent_key, object_type, status, proposed_by "
                            "FROM agent_proposals WHERE id = $1::uuid",
                            proposal_id,
                        )
                finally:
                    await independent.close()
                check(
                    row is not None
                    and str(row["org_id"]) == str(FIXTURE_ORG)
                    and row["agent_key"] == "compliance_analyst"
                    and row["status"] == "pending"
                    and str(row["proposed_by"]) == str(FIXTURE_USER_HASPERM),
                    f"[Y] propose(): the row persisted and re-reads correctly from an "
                    f"independent connection (got {dict(row) if row else None})",
                )
        finally:
            await close_pool()

        # ── Teardown ─────────────────────────────────────────────────────────
        await teardown(raw_conn)
        leftover = await raw_conn.fetchval(
            "SELECT count(*) FROM assistant_action_catalog WHERE org_id = $1", FIXTURE_ORG
        )
        leftover += await raw_conn.fetchval(
            "SELECT count(*) FROM agent_proposals WHERE org_id = $1", FIXTURE_ORG
        )
        leftover += await raw_conn.fetchval(
            "SELECT count(*) FROM users WHERE id = ANY($1::uuid[])",
            [FIXTURE_USER_NOPERM, FIXTURE_USER_HASPERM],
        )
        leftover += await raw_conn.fetchval(
            "SELECT count(*) FROM organizations WHERE id = $1", FIXTURE_ORG
        )
        check(leftover == 0, f"[Y] teardown: zero leftover fixture rows (found {leftover})")
    finally:
        await raw_conn.close()

    print(f"\n{'=' * 70}\nTOTAL: {_n_pass} PASS, {_n_fail} FAIL, {len(_finds)} FIND\n{'=' * 70}")
    return 0 if _ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
