"""verify_hollisfix.py — the assistant's actions ignored the caller's identity.

WHAT THIS PROVES (against the DEPLOYED database, under the REAL non-bypassing
role, with the REAL visibility engines — nothing stubbed):

  [Task 0] The connection role genuinely does NOT bypass RLS
           (``rolbypassrls = false``), asserted explicitly. Every isolation
           claim below is worthless without this, so it runs first and the
           script aborts if it fails.

  [Task 1] REPRODUCE THE ORIGINAL BUG. The pre-fix query shapes — scope by
           ``org_id``, no caller predicate — are run verbatim against a REAL
           org-1 entity as a member who has NO grant to it, and they return the
           row. The exposure is demonstrated, not asserted from a code reading,
           before any fix is shown to close it.

  [Task 2] EXCLUSION. ``entity.show_hierarchy`` and ``portfolio.show_allocation``
           both refuse that same entity for that same ungranted member.

  [Task 3] INCLUSION — the other half, and the one a naive "it returned nothing"
           check would miss. A member holding a REAL ``delegate_grants`` row to
           that entity still gets the data. A gate that denies everyone passes
           Task 2 and fails here.

  [Task 4] SCHEMA DRIFT. The pre-fix ``find_my_investment`` SQL raises
           ``UndefinedColumnError`` on the deployed schema (reproduced), the
           fixed handler runs clean, AND its result set is asserted EQUAL to the
           same predicate run directly in SQL — so "returns nothing" cannot pass
           as "correctly scoped".

  [Task 5] WRITE PATHS. ``crm.draft_note`` and ``spv.subscribe`` both refuse an
           out-of-scope ``entity_id`` at the CONFIRM handler — the boundary a
           hand-rolled POST /assistant/confirm actually hits, since
           ``ConfirmBody.proposed_action`` is client data. Each refusal is
           backed by an exact before/after row count on the table it would have
           written, so "raised but wrote anyway" cannot pass.

  [Task 6] The granted member CAN still save a note — inclusion on the write
           side too — and that row is torn down.

  [Teardown] By fixture id, with an exact before/after count as the backstop.
           Never an unconditional delete of anything org-owned.

WHAT IT DELIBERATELY DOES NOT PROVE:

  * Anything about maker-checker on ``spv.subscribe``. That path still executes
    on the member's own single confirmation; the fix narrows WHOSE entity can be
    named, not WHO approves. That is an open design decision, recorded in the
    sprint notes, not something this script should quietly claim is closed.
  * Anything about idempotency, tier enforcement, loop-cap escalation, or the
    absent eval gate. All still open.
  * That ``is_staff`` is trustworthy. ``services.permissions.is_staff`` returns
    True when the token carries no roles claim, so a rolesless caller takes the
    STAFF branch of every gate here. That default is platform-wide and out of
    scope; this script pins the MEMBER branch, which is where the exposure was.

Run:  python3 apps/api/scripts/verify_hollisfix.py
"""
import asyncio
import pathlib
import sys
from uuid import UUID

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from _db_bootstrap import bootstrap_async  # noqa: E402  (also puts apps/api on sys.path)

import asyncpg  # noqa: E402

ORG_ID = UUID("00000000-0000-0000-0000-000000000001")

MARKER = "HOLLISFIX_VERIFY"
U_GRANTED = UUID("99000000-0000-0000-0000-00000f1x0001")
U_UNGRANTED = UUID("99000000-0000-0000-0000-00000f1x0002")
ALL_FIXTURE_USERS = [U_GRANTED, U_UNGRANTED]

_ok = True
_n_pass = 0
_n_fail = 0
_finds: list[str] = []


def check(passed: bool, label: str, detail: str = "") -> bool:
    assert isinstance(passed, bool), (
        f"check() received passed={passed!r} (type {type(passed).__name__}), "
        f"not a bool, for label={label!r}"
    )
    global _ok, _n_pass, _n_fail
    print(f"{'[PASS]' if passed else '[FAIL]'} {label}" + (f"  — {detail}" if detail else ""))
    if passed:
        _n_pass += 1
    else:
        _n_fail += 1
        _ok = False
    return passed


def find(label: str, detail: str = "") -> None:
    print(f"[FIND] {label}" + (f"  — {detail}" if detail else ""))
    _finds.append(label)


def _sub(uid: UUID) -> str:
    return f"hollisfix_{uid.hex}"


# ═══════════════════════════════════════════════════════════════════════════
# Pre-fix query shapes, preserved verbatim. These are the BUG, kept here on
# purpose so Task 1 can demonstrate it rather than describe it. Do not "tidy"
# them — their whole value is being byte-for-byte what shipped.
# ═══════════════════════════════════════════════════════════════════════════
PREFIX_HIERARCHY_ROOT_SQL = """
    SELECT id, display_name, entity_type
    FROM entities
    WHERE id = $1
      AND org_id = $2
      AND valid_to IS NULL
"""

PREFIX_FIND_INVESTMENT_SQL = """
    SELECT mi.id, mi.status, mi.current_stage, mi.committed_amount,
           mi.currency, d.name AS deal_name, d.deal_type, d.taxonomy_key
    FROM member_investments mi
    JOIN deals d ON d.id = mi.deal_id
    JOIN entities e ON e.id = mi.entity_id
    WHERE e.org_id = $1
    ORDER BY mi.created_at DESC
    LIMIT 5
"""


async def main_async() -> int:
    url = await bootstrap_async()
    if not url:
        print("[abort] no working DATABASE_URL")
        return 1

    from services.database import get_pool, set_rls_context, reset_rls_context
    from services.assistant_actions._visibility import (
        EntityNotVisible,
        visible_entity_ids,
    )
    from services.assistant_actions.crm import _save_note
    from services.assistant_actions.entity_graph import _show_hierarchy_handler
    from services.assistant_actions.portfolio import _find_investment, _show_allocation
    from services.assistant_actions.spv import _execute_subscribe
    from services.delegate_grants import grant_delegate

    pool = await get_pool()
    tokens = set_rls_context(str(ORG_ID), False)

    grant_id = None
    created_note_ids: list = []

    try:
        # ───────────────────────────────────────────────────────────────────
        # Task 0 — the role must not bypass RLS, or nothing below proves anything
        # ───────────────────────────────────────────────────────────────────
        async with pool.acquire() as conn:
            role_row = await conn.fetchrow(
                "SELECT current_user AS u, "
                "(SELECT rolbypassrls FROM pg_roles WHERE rolname = current_user) AS bypass"
            )
        if not check(
            role_row["bypass"] is False,
            "Task 0 — connection role does not bypass RLS",
            f"current_user={role_row['u']} rolbypassrls={role_row['bypass']}",
        ):
            print("[abort] isolation claims are unprovable under a bypassing role")
            return 1

        # ───────────────────────────────────────────────────────────────────
        # Fixtures — two members, one real entity, one real delegate grant
        # ───────────────────────────────────────────────────────────────────
        async with pool.acquire() as conn:
            target = await conn.fetchrow(
                """
                SELECT id, display_name FROM entities
                WHERE org_id = $1 AND valid_to IS NULL AND system_to IS NULL
                ORDER BY created_at
                LIMIT 1
                """,
                ORG_ID,
            )
            if target is None:
                print("[abort] org 1 has no active entity to test against")
                return 1
            target_id = str(target["id"])

            for uid in ALL_FIXTURE_USERS:
                sub = _sub(uid)
                await conn.execute(
                    """INSERT INTO users (id, org_id, email, full_name, auth0_sub, role, is_active)
                       VALUES ($1, $2, $3, $4, $5, 'member', true)
                       ON CONFLICT (auth0_sub) DO UPDATE
                         SET org_id = EXCLUDED.org_id, is_active = true""",
                    uid, ORG_ID, f"{sub}@test.local", f"{MARKER} {sub}", sub,
                )

        grant_id = await grant_delegate(
            pool, ORG_ID,
            principal_entity_id=target_id,
            scope="view_only",
            delegate_user_id=U_GRANTED,
            granted_by=U_GRANTED,
        )

        vis_granted = await visible_entity_ids(pool, str(ORG_ID), str(U_GRANTED), False)
        vis_ungranted = await visible_entity_ids(pool, str(ORG_ID), str(U_UNGRANTED), False)
        check(
            target_id in vis_granted and target_id not in vis_ungranted,
            "Fixtures — granted member sees the target, ungranted member does not",
            f"granted={len(vis_granted)} ids, ungranted={len(vis_ungranted)} ids",
        )

        # ───────────────────────────────────────────────────────────────────
        # Task 1 — reproduce the ORIGINAL exposure
        # ───────────────────────────────────────────────────────────────────
        async with pool.acquire() as conn:
            leaked = await conn.fetchrow(PREFIX_HIERARCHY_ROOT_SQL, target_id, ORG_ID)
        check(
            leaked is not None,
            "Task 1 — pre-fix org-only query RETURNS an entity no member may see",
            f"{leaked['display_name'] if leaked else 'None'} "
            f"({target_id}) reachable with org_id alone",
        )

        async with pool.acquire() as conn:
            org_total = await conn.fetchval(
                "SELECT COUNT(*) FROM entities WHERE org_id = $1 "
                "AND valid_to IS NULL AND system_to IS NULL",
                ORG_ID,
            )
        find(
            "Blast radius of the pre-fix shape",
            f"{org_total} active org-1 entities were each reachable by id; "
            f"the ungranted member's correct visible set is {len(vis_ungranted)}",
        )

        # ───────────────────────────────────────────────────────────────────
        # Task 2 — EXCLUSION
        # ───────────────────────────────────────────────────────────────────
        res = await _show_hierarchy_handler(
            pool=pool, user_id=str(U_UNGRANTED), org_id=str(ORG_ID),
            is_staff=False, entity_id=target_id,
        )
        check(
            res.get("data") is None and "not within your visible set" in res.get("text", ""),
            "Task 2 — show_hierarchy refuses the ungranted member",
            res.get("text", "")[:70],
        )

        res = await _show_allocation(
            pool=pool, user_id=str(U_UNGRANTED), org_id=str(ORG_ID),
            is_staff=False, selector_type="entity", entity_id=target_id,
        )
        check(
            res.get("data") == {} and "not within your visible set" in res.get("text", ""),
            "Task 2 — show_allocation refuses the ungranted member",
            res.get("text", "")[:70],
        )

        # ───────────────────────────────────────────────────────────────────
        # Task 3 — INCLUSION (the half a blanket denial would fake)
        # ───────────────────────────────────────────────────────────────────
        res = await _show_hierarchy_handler(
            pool=pool, user_id=str(U_GRANTED), org_id=str(ORG_ID),
            is_staff=False, entity_id=target_id,
        )
        check(
            res.get("data") is not None and res["data"].get("tree") is not None,
            "Task 3 — show_hierarchy STILL SERVES the granted member",
            f"tree root={res.get('data', {}).get('tree', {}).get('display_name')}",
        )

        res = await _show_allocation(
            pool=pool, user_id=str(U_GRANTED), org_id=str(ORG_ID),
            is_staff=False, selector_type="entity", entity_id=target_id,
        )
        check(
            "not within your visible set" not in res.get("text", ""),
            "Task 3 — show_allocation STILL SERVES the granted member",
            res.get("text", "")[:70],
        )

        # ───────────────────────────────────────────────────────────────────
        # Task 4 — schema drift: reproduce, fix, and prove the scope EXACTLY
        # ───────────────────────────────────────────────────────────────────
        drift_exc = None
        try:
            async with pool.acquire() as conn:
                await conn.fetch(PREFIX_FIND_INVESTMENT_SQL, ORG_ID)
        except asyncpg.exceptions.UndefinedColumnError as exc:
            drift_exc = exc
        check(
            drift_exc is not None,
            "Task 4 — pre-fix find_my_investment SQL raises UndefinedColumnError",
            str(drift_exc).split("\n")[0][:80] if drift_exc else "it did NOT raise",
        )

        async with pool.acquire() as conn:
            probe_user = await conn.fetchval(
                "SELECT user_id FROM member_investments WHERE org_id = $1 "
                "AND user_id IS NOT NULL ORDER BY created_at DESC LIMIT 1",
                ORG_ID,
            )
        if probe_user is None:
            find(
                "Task 4 scope-equality skipped",
                "member_investments has no row with a user_id in org 1 — the "
                "handler is still proven to RUN below, but the equality check "
                "needs real data and is not faked",
            )
            probe_user = U_UNGRANTED

        res = await _find_investment(
            pool=pool, user_id=str(probe_user), org_id=str(ORG_ID),
            is_staff=False, query="",
        )
        handler_ids = {i["id"] for i in res["data"]["investments"]}

        async with pool.acquire() as conn:
            expected = await conn.fetch(
                "SELECT mi.id FROM member_investments mi "
                "JOIN deals d ON d.id = mi.deal_id "
                "WHERE mi.org_id = $1 AND mi.user_id = $2 "
                "ORDER BY mi.created_at DESC LIMIT 5",
                ORG_ID, probe_user,
            )
            foreign = await conn.fetchval(
                "SELECT COUNT(*) FROM member_investments "
                "WHERE org_id = $1 AND user_id IS DISTINCT FROM $2",
                ORG_ID, probe_user,
            )
        check(
            handler_ids == {str(r["id"]) for r in expected},
            "Task 4 — fixed handler's rows EQUAL the same predicate run in SQL",
            f"{len(handler_ids)} rows, and {foreign} other-member rows excluded",
        )

        # ───────────────────────────────────────────────────────────────────
        # Task 5 — WRITE paths refuse, and provably wrote nothing
        # ───────────────────────────────────────────────────────────────────
        async with pool.acquire() as conn:
            notes_before = await conn.fetchval(
                "SELECT COUNT(*) FROM entity_notes WHERE org_id = $1 AND entity_id = $2",
                ORG_ID, target_id,
            )
        refused = False
        try:
            await _save_note(
                pool=pool, user_id=str(U_UNGRANTED), org_id=str(ORG_ID),
                is_staff=False, choice_value="save",
                entity_id=target_id, draft_text=f"{MARKER} should never persist",
            )
        except EntityNotVisible:
            refused = True
        async with pool.acquire() as conn:
            notes_after = await conn.fetchval(
                "SELECT COUNT(*) FROM entity_notes WHERE org_id = $1 AND entity_id = $2",
                ORG_ID, target_id,
            )
        check(
            refused and notes_after == notes_before,
            "Task 5 — crm.draft_note confirm refuses AND writes nothing",
            f"refused={refused} entity_notes {notes_before}->{notes_after}",
        )

        async with pool.acquire() as conn:
            subs_before = await conn.fetchval(
                "SELECT COUNT(*) FROM spv_subscriptions WHERE org_id = $1 AND entity_id = $2",
                ORG_ID, target_id,
            )
            any_spv = await conn.fetchval(
                "SELECT id FROM spvs WHERE org_id = $1 LIMIT 1", ORG_ID
            )
        refused = False
        try:
            await _execute_subscribe(
                pool=pool, user_id=str(U_UNGRANTED), org_id=str(ORG_ID),
                is_staff=False, choice_value="confirm",
                spv_id=str(any_spv) if any_spv else str(ORG_ID),
                entity_id=target_id, commitment_amount=1.0,
            )
        except EntityNotVisible:
            refused = True
        async with pool.acquire() as conn:
            subs_after = await conn.fetchval(
                "SELECT COUNT(*) FROM spv_subscriptions WHERE org_id = $1 AND entity_id = $2",
                ORG_ID, target_id,
            )
        check(
            refused and subs_after == subs_before,
            "Task 5 — spv.subscribe confirm refuses AND commits no capital",
            f"refused={refused} spv_subscriptions {subs_before}->{subs_after}",
        )
        find(
            "spv.subscribe still has NO maker-checker step",
            "the gate above narrows WHOSE entity may be named; a granted "
            "member's single confirm still executes the commitment directly",
        )

        # ───────────────────────────────────────────────────────────────────
        # Task 6 — inclusion on the write side
        # ───────────────────────────────────────────────────────────────────
        wrote = await _save_note(
            pool=pool, user_id=str(U_GRANTED), org_id=str(ORG_ID),
            is_staff=False, choice_value="save",
            entity_id=target_id, draft_text=f"{MARKER} fixture note",
        )
        note_id = (wrote.get("result") or {}).get("id")
        if note_id:
            created_note_ids.append(note_id)
        async with pool.acquire() as conn:
            persisted = await conn.fetchval(
                "SELECT note_text FROM entity_notes WHERE id = $1", note_id
            ) if note_id else None
        check(
            persisted is not None and MARKER in persisted,
            "Task 6 — granted member's note is written and re-read independently",
            f"note_id={note_id}",
        )

    finally:
        # ───────────────────────────────────────────────────────────────────
        # Teardown — by fixture id only, with an exact count as the backstop
        # ───────────────────────────────────────────────────────────────────
        try:
            async with pool.acquire() as conn:
                if created_note_ids:
                    await conn.execute(
                        "DELETE FROM entity_notes WHERE id = ANY($1::uuid[])",
                        [UUID(n) for n in created_note_ids],
                    )
                if grant_id:
                    await conn.execute(
                        "DELETE FROM delegate_grants WHERE id = $1", UUID(grant_id)
                    )
                await conn.execute(
                    "DELETE FROM users WHERE id = ANY($1::uuid[])", ALL_FIXTURE_USERS
                )
                left_users = await conn.fetchval(
                    "SELECT COUNT(*) FROM users WHERE id = ANY($1::uuid[])",
                    ALL_FIXTURE_USERS,
                )
                left_grants = await conn.fetchval(
                    "SELECT COUNT(*) FROM delegate_grants WHERE id = $1",
                    UUID(grant_id),
                ) if grant_id else 0
                left_notes = await conn.fetchval(
                    "SELECT COUNT(*) FROM entity_notes WHERE note_text LIKE $1",
                    f"%{MARKER}%",
                )
            check(
                left_users == 0 and left_grants == 0 and left_notes == 0,
                "Teardown — zero fixture rows remain",
                f"users={left_users} grants={left_grants} notes={left_notes}",
            )
        except Exception as exc:  # noqa: BLE001
            check(False, "Teardown — clean", f"raised {exc}")
        reset_rls_context(tokens)

    print(f"\n{'=' * 70}\nTOTAL: {_n_pass} PASS, {_n_fail} FAIL, {len(_finds)} FIND\n{'=' * 70}")
    return 0 if _ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main_async()))
