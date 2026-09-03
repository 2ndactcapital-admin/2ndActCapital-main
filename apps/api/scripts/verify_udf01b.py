"""Sprint udf01b verification — tabs, tab permissions, layout model.

Pass/fail only, no prompts. Run:

    python3 apps/api/scripts/verify_udf01b.py

Part 1 (schema) was already applied before this sprint started; Task 1 below
re-confirms the load-bearing subset directly. Task 1 ALSO re-runs
verify_udf01a.py's own ``main()`` in-process (imported, not shelled out) as a
hard regression gate — its PASS/FAIL counts are read back from its own module
globals after it returns.

Every fixture this script writes carries an 'udf01bverify_' prefix (label,
api_name, or role/profile/permission_set name) or a fixed 99000000-...-0000db
prefixed UUID; teardown deletes by that prefix/id, never TRUNCATE, and row
counts are taken before the first insert and after the last delete.
"""

from __future__ import annotations

import asyncio
import glob
import json
import sys
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
API_DIR = HERE.parent
for _site in sorted(glob.glob(str(API_DIR / "venv/lib/python3*/site-packages"))):
    if _site not in sys.path:
        sys.path.insert(0, _site)
for _path in (str(HERE), str(API_DIR)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from _db_connect import admin_dsn, app_service_dsn, connect  # noqa: E402

from uuid import NAMESPACE_URL, uuid5  # noqa: E402

ORG = "00000000-0000-0000-0000-000000000001"
OTHER_ORG = "bb347258-8f28-4f49-8cc9-e29ccad82884"

P = "99000000-0000-0000-0000-0000db01"
PFX = "udf01bverify_"

SUB_ADMIN = "udf01bverify|admin"
SUB_VIEWER = "udf01bverify|viewer"
SUB_VIEWER2 = "udf01bverify|viewer2"
SUB_DEFAULT = "udf01bverify|defaultvis"
SUB_NOPERMS = "udf01bverify|noperms"
SUB_OTHER = "udf01bverify|other"

U_ADMIN = str(uuid5(NAMESPACE_URL, SUB_ADMIN))
U_VIEWER = str(uuid5(NAMESPACE_URL, SUB_VIEWER))
U_VIEWER2 = str(uuid5(NAMESPACE_URL, SUB_VIEWER2))
U_DEFAULT = str(uuid5(NAMESPACE_URL, SUB_DEFAULT))
U_NOPERMS = str(uuid5(NAMESPACE_URL, SUB_NOPERMS))
U_OTHER = str(uuid5(NAMESPACE_URL, SUB_OTHER))
USERS = [U_ADMIN, U_VIEWER, U_VIEWER2, U_DEFAULT, U_NOPERMS, U_OTHER]

ROLE_NOPERMS = f"{P}3001"

PROFILE_1 = f"{P}6001"
PROFILE_2 = f"{P}6002"
PROFILE_3 = f"{P}6003"
PSET_1 = f"{P}6101"
PSET_2 = f"{P}6102"

COUNTED = [
    "portfolio.udf_tabs", "portfolio.udf_tab_permissions", "portfolio.udf_layouts",
    "portfolio.udf_layout_sections", "portfolio.udf_layout_items",
    "portfolio.udf_definitions", "portfolio.udf_values", "portfolio.udf_tag_assignments",
    "portfolio.udf_definition_audit",
    "public.users", "public.roles", "public.role_permissions", "public.user_roles",
    "public.org_settings",
    "public.profiles", "public.permission_sets", "public.profile_permissions",
    "public.permission_set_permissions", "public.user_permission_sets",
]

PASS: list[str] = []
FAIL: list[str] = []
FIND: list[str] = []


def ok(label: str, condition: bool, detail: str = "") -> bool:
    (PASS if condition else FAIL).append(f"{label}: {detail}" if detail else label)
    print(f"{'[PASS]' if condition else '[FAIL]'} {label}"
          + (f" — {detail}" if detail else ""))
    return condition


def find(label: str, detail: str) -> None:
    FIND.append(f"{label}: {detail}")
    print(f"[FIND] {label} — {detail}")


# ═══════════════════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════════════════


async def counts(conn) -> dict[str, int]:
    return {t: await conn.fetchval(f"SELECT count(*) FROM {t}") for t in COUNTED}


async def teardown(conn) -> None:
    # Layouts cascade to sections/items (ON DELETE CASCADE on both FKs).
    await conn.execute(
        f"DELETE FROM portfolio.udf_layouts WHERE tab_id IN "
        f"(SELECT id FROM portfolio.udf_tabs WHERE api_name LIKE '{PFX}%')"
    )
    await conn.execute(
        f"DELETE FROM portfolio.udf_tab_permissions WHERE tab_id IN "
        f"(SELECT id FROM portfolio.udf_tabs WHERE api_name LIKE '{PFX}%')"
    )
    await conn.execute(f"DELETE FROM portfolio.udf_tabs WHERE api_name LIKE '{PFX}%'")

    await conn.execute(
        f"DELETE FROM portfolio.udf_definition_audit WHERE definition_id IN "
        f"(SELECT id FROM portfolio.udf_definitions WHERE field_key LIKE '{PFX}%')"
    )
    await conn.execute(
        f"DELETE FROM portfolio.udf_tag_assignments WHERE definition_id IN "
        f"(SELECT id FROM portfolio.udf_definitions WHERE field_key LIKE '{PFX}%')"
    )
    await conn.execute(
        f"DELETE FROM portfolio.udf_values WHERE definition_id IN "
        f"(SELECT id FROM portfolio.udf_definitions WHERE field_key LIKE '{PFX}%')"
    )
    await conn.execute(f"DELETE FROM portfolio.udf_definitions WHERE field_key LIKE '{PFX}%'")

    await conn.execute(
        "DELETE FROM public.org_settings WHERE org_id = $1::uuid "
        "AND setting_key IN ('crm.udf.max_custom_tabs', "
        "'crm.udf.max_sections_per_layout', 'crm.udf.max_items_per_section')",
        ORG,
    )

    await conn.execute(
        "DELETE FROM public.user_permission_sets WHERE user_id = ANY($1::uuid[])", USERS
    )
    await conn.execute(
        "DELETE FROM public.permission_set_permissions WHERE permission_set_id = ANY($1::uuid[])",
        [PSET_1, PSET_2],
    )
    await conn.execute(
        "DELETE FROM public.profile_permissions WHERE profile_id = ANY($1::uuid[])",
        [PROFILE_1, PROFILE_2, PROFILE_3],
    )

    await conn.execute("DELETE FROM public.user_roles WHERE user_id = ANY($1::uuid[])", USERS)
    await conn.execute(
        "DELETE FROM public.role_permissions WHERE role_id = $1::uuid", ROLE_NOPERMS
    )
    await conn.execute("DELETE FROM public.roles WHERE id = $1::uuid", ROLE_NOPERMS)
    # Users hold profile_id FKs into profiles — must go before profiles/permission_sets
    # are deleted, or users_profile_id_fkey refuses the profile delete.
    await conn.execute("DELETE FROM public.users WHERE id = ANY($1::uuid[])", USERS)

    await conn.execute(
        "DELETE FROM public.permission_sets WHERE id = ANY($1::uuid[])", [PSET_1, PSET_2]
    )
    await conn.execute(
        "DELETE FROM public.profiles WHERE id = ANY($1::uuid[])",
        [PROFILE_1, PROFILE_2, PROFILE_3],
    )


async def setup(conn) -> None:
    for user_id, org, sub, profile_id in (
        (U_ADMIN, ORG, SUB_ADMIN, None),
        (U_VIEWER, ORG, SUB_VIEWER, PROFILE_1),
        (U_VIEWER2, ORG, SUB_VIEWER2, PROFILE_2),
        (U_DEFAULT, ORG, SUB_DEFAULT, PROFILE_3),
        (U_NOPERMS, ORG, SUB_NOPERMS, None),
        (U_OTHER, OTHER_ORG, SUB_OTHER, None),
    ):
        await conn.execute(
            """INSERT INTO public.users
                (id, org_id, email, full_name, auth0_sub, role, is_active)
            VALUES ($1::uuid, $2::uuid, $3, 'Verify udf01b', $4, 'member', true)
            ON CONFLICT (id) DO NOTHING""",
            user_id, org, f"udf01bverify-{user_id[-4:]}@test.local", sub,
        )

    await conn.execute(
        """INSERT INTO public.roles (id, org_id, name, description)
        VALUES ($1::uuid, $2::uuid, 'udf01bverify_noperms', 'verify fixture')
        ON CONFLICT (id) DO NOTHING""",
        ROLE_NOPERMS, ORG,
    )
    # Deliberately zero role_permissions rows for ROLE_NOPERMS — the real
    # per-permission lookup refuses U_NOPERMS, not the zero-roles default-allow
    # (see udf01a's own SUB_NOPERMS fixture for the same reasoning).
    for user_id, role_name, org in (
        (U_ADMIN, "admin", ORG), (U_VIEWER, "member", ORG),
        (U_VIEWER2, "member", ORG), (U_DEFAULT, "member", ORG),
        (U_NOPERMS, "udf01bverify_noperms", ORG), (U_OTHER, "admin", OTHER_ORG),
    ):
        await conn.execute(
            """INSERT INTO public.user_roles (user_id, role_id)
            SELECT $1::uuid, r.id FROM public.roles r
            WHERE r.name = $2 AND r.org_id = $3::uuid
            ON CONFLICT DO NOTHING""",
            user_id, role_name, org,
        )

    for profile_id, name in (
        (PROFILE_1, f"{PFX}profile1"), (PROFILE_2, f"{PFX}profile2"),
        (PROFILE_3, f"{PFX}profile3"),
    ):
        await conn.execute(
            """INSERT INTO public.profiles (id, org_id, name, description)
            VALUES ($1::uuid, $2::uuid, $3, 'verify fixture')
            ON CONFLICT (id) DO NOTHING""",
            profile_id, ORG, name,
        )
    for user_id, profile_id in (
        (U_VIEWER, PROFILE_1), (U_VIEWER2, PROFILE_2), (U_DEFAULT, PROFILE_3),
    ):
        await conn.execute(
            "UPDATE public.users SET profile_id = $2::uuid WHERE id = $1::uuid",
            user_id, profile_id,
        )

    for pset_id, name in ((PSET_1, f"{PFX}pset1"), (PSET_2, f"{PFX}pset2")):
        await conn.execute(
            """INSERT INTO public.permission_sets (id, org_id, name, description)
            VALUES ($1::uuid, $2::uuid, $3, 'verify fixture')
            ON CONFLICT (id) DO NOTHING""",
            pset_id, ORG, name,
        )
    for user_id, pset_id in ((U_VIEWER, PSET_1), (U_VIEWER2, PSET_2)):
        await conn.execute(
            """INSERT INTO public.user_permission_sets (user_id, permission_set_id)
            VALUES ($1::uuid, $2::uuid) ON CONFLICT DO NOTHING""",
            user_id, pset_id,
        )


# ═══════════════════════════════════════════════════════════════════════════
# TASK 1 — schema re-verification + udf01a regression gate
# ═══════════════════════════════════════════════════════════════════════════


async def test_1_schema(conn) -> None:
    for tbl in (
        "udf_tabs", "udf_tab_permissions", "udf_layouts",
        "udf_layout_sections", "udf_layout_items",
    ):
        ok(f"1 — portfolio.{tbl} exists",
           await conn.fetchval(f"SELECT to_regclass('portfolio.{tbl}')") is not None)

    for idx in (
        "udf_tab_api_name_uq", "udf_tab_perm_profile_uq", "udf_tab_perm_set_uq",
        "udf_layout_one_per_tab_uq", "udf_layout_item_def_per_section_uq",
    ):
        ok(f"1 — index {idx} exists",
           await conn.fetchval(
               f"SELECT indexdef FROM pg_indexes WHERE tablename LIKE 'udf_%' "
               f"AND indexname='{idx}'") is not None)

    for con in (
        "udf_tab_applies_to_chk", "udf_tab_perm_one_grantee_chk",
        "udf_layout_sections_column_count_check", "udf_layout_items_column_index_check",
        "udf_layout_items_col_span_check",
    ):
        ok(f"1 — constraint {con} exists",
           await conn.fetchval(
               f"SELECT 1 FROM pg_constraint WHERE conname='{con}'") == 1)

    for tbl, col in (("profiles", "id"), ("permission_sets", "id")):
        dt = await conn.fetchval(
            "SELECT data_type FROM information_schema.columns "
            "WHERE table_schema='public' AND table_name=$1 AND column_name=$2",
            tbl, col,
        )
        ok(f"1 — public.{tbl}.{col} is uuid", dt == "uuid", f"got {dt!r}")

    used = await conn.fetchval(
        "SELECT count(*) FROM portfolio.udf_definitions WHERE record_type_id IS NOT NULL"
    )
    find("1 — record_type_id usage",
         f"{used} udf_definitions row(s) have a non-null record_type_id "
         f"(reserved column, no reader/writer besides the column's own "
         f"existence — confirmed still unused by app code via grep)")

    find("1 — udf_tabs has no bi-temporal columns",
         "unlike udf_definitions, udf_tabs carries no valid_from/valid_to/"
         "system_from/system_to; label updates are plain in-place UPDATEs, "
         "and there is no udf_tab_audit table (none of the 5 Part 1 objects "
         "is an audit table) — tab lifecycle changes are not logged")


async def test_1b_udf01a_baseline() -> None:
    import verify_udf01a

    exit_code = await verify_udf01a.main()
    ok("1 — verify_udf01a.py regression baseline still green",
       exit_code == 0 and not verify_udf01a.FAIL,
       f"exit={exit_code} PASS={len(verify_udf01a.PASS)} FAIL={len(verify_udf01a.FAIL)}")
    if verify_udf01a.FAIL:
        for f in verify_udf01a.FAIL:
            print(f"    [udf01a FAIL] {f}")


# ═══════════════════════════════════════════════════════════════════════════
# TASK 2b — tab CRUD, cap enforcement, immutability, soft delete
# ═══════════════════════════════════════════════════════════════════════════


async def test_2_cap_enforcement(conn) -> None:
    from services.portfolio_udf_tabs import TabCapError, create_tab

    await conn.execute(
        """INSERT INTO public.org_settings (org_id, setting_key, setting_value)
        VALUES ($1::uuid, 'crm.udf.max_custom_tabs', '2'::jsonb)
        ON CONFLICT (org_id, setting_key) DO UPDATE SET setting_value = '2'::jsonb""",
        ORG,
    )
    t1 = await create_tab(
        conn, org_id=ORG, applies_to="entity", label="Cap Tab 1",
        api_name=f"{PFX}cap_tab_1", created_by=U_ADMIN,
    )
    t2 = await create_tab(
        conn, org_id=ORG, applies_to="entity", label="Cap Tab 2",
        api_name=f"{PFX}cap_tab_2", created_by=U_ADMIN,
    )
    ok("3 — 2 tabs created under cap=2", t1 is not None and t2 is not None)
    try:
        await create_tab(
            conn, org_id=ORG, applies_to="entity", label="Cap Tab 3",
            api_name=f"{PFX}cap_tab_3", created_by=U_ADMIN,
        )
        ok("3 — (N+1)th active tab rejected", False, "no exception raised")
    except TabCapError as exc:
        msg = str(exc)
        ok("3 — (N+1)th active tab rejected naming count and limit",
           "2" in msg, f"message={msg!r}")
    finally:
        # Raise the cap back out of the way — NOT back to the platform default
        # (3), which later tests would still blow through: this test alone
        # leaves 2 active 'entity' tabs behind for the rest of the run, and
        # visibility/col_span/the HTTP matrix all create more 'entity' tabs on
        # top of those. The override is deleted entirely in teardown().
        await conn.execute(
            """INSERT INTO public.org_settings (org_id, setting_key, setting_value)
            VALUES ($1::uuid, 'crm.udf.max_custom_tabs', '100'::jsonb)
            ON CONFLICT (org_id, setting_key) DO UPDATE SET setting_value = '100'::jsonb""",
            ORG,
        )


async def test_3_api_name_immutable(conn) -> None:
    from services.portfolio_udf_tabs import TabImmutableError, create_tab, update_tab

    tab = await create_tab(
        conn, org_id=ORG, applies_to="position", label="Immutable Test",
        api_name=f"{PFX}immutable_test", created_by=U_ADMIN,
    )
    updated = await update_tab(conn, tab_id=tab["id"], org_id=ORG, changes={"label": "Renamed"})
    ok("4 — label is mutable", updated["label"] == "Renamed", f"got {updated['label']!r}")
    try:
        await update_tab(
            conn, tab_id=tab["id"], org_id=ORG,
            changes={"api_name": f"{PFX}different_name"},
        )
        ok("4 — api_name is immutable", False, "no exception raised")
    except TabImmutableError:
        ok("4 — api_name is immutable", True)


async def test_4_soft_delete_reversible(conn) -> None:
    from services.portfolio_udf_tabs import (
        create_tab, get_tab, list_active_tabs, soft_delete_tab, undelete_tab,
    )

    tab = await create_tab(
        conn, org_id=ORG, applies_to="valuation", label="Delete Reversible",
        api_name=f"{PFX}delete_reversible", created_by=U_ADMIN,
    )
    await soft_delete_tab(conn, tab_id=tab["id"], org_id=ORG)
    ok("5 — soft-deleted tab is hidden from get_tab",
       await get_tab(conn, tab_id=tab["id"]) is None)
    active = await list_active_tabs(conn, org_id=ORG, applies_to="valuation")
    ok("5 — soft-deleted tab excluded from list_active_tabs",
       tab["id"] not in {t["id"] for t in active})
    restored = await undelete_tab(conn, tab_id=tab["id"], org_id=ORG)
    ok("5 — undelete reverses soft delete", restored["deleted_at"] is None)
    ok("5 — undeleted tab visible again via get_tab",
       (await get_tab(conn, tab_id=tab["id"])) is not None)


async def test_5_soft_delete_blocked_by_layout(conn) -> None:
    from services.portfolio_udf import create_org_definition
    from services.portfolio_udf_layouts import add_item, add_section, create_layout
    from services.portfolio_udf_tabs import TabReferencedError, create_tab, soft_delete_tab

    tab = await create_tab(
        conn, org_id=ORG, applies_to="asset", label="Blocked Delete",
        api_name=f"{PFX}blocked_delete", created_by=U_ADMIN,
    )
    def_id = await create_org_definition(
        conn, org_id=ORG, applies_to="asset", field_key=f"{PFX}blocked_delete_field",
        label="Blocked Delete Field", data_type="text", type_params={"length": 50},
        created_by=U_ADMIN,
    )
    layout = await create_layout(conn, org_id=ORG, tab_id=tab["id"])
    section = await add_section(conn, layout_id=layout["id"], org_id=ORG, title="Sec 1")
    await add_item(conn, section_id=section["id"], org_id=ORG, definition_id=def_id)

    try:
        await soft_delete_tab(conn, tab_id=tab["id"], org_id=ORG)
        ok("6 — soft delete blocked by non-empty layout", False, "no exception raised")
    except TabReferencedError as exc:
        ok("6 — soft delete blocked by non-empty layout, references reported",
           bool(exc.references), f"references={exc.references}")

    find("6 — no remove_section function",
         "Task 2d's endpoint/function list has add_section but no remove_section "
         "(and no route for it) — a tab that has ever had a section added can "
         "never clear get_tab_references down to zero and can never be "
         "soft-deleted again through the API this sprint built. Not fixed here: "
         "not in the sprint's explicit function list.")


# ═══════════════════════════════════════════════════════════════════════════
# TASK 2c — tab visibility resolution
# ═══════════════════════════════════════════════════════════════════════════


async def test_6_resolve_visibility(conn) -> None:
    from services.portfolio_udf_tabs import (
        create_tab, resolve_tab_visibility, set_tab_visibility,
    )

    tab_a = await create_tab(
        conn, org_id=ORG, applies_to="entity", label="Visibility Case A",
        api_name=f"{PFX}vis_case_a", created_by=U_ADMIN,
    )
    await set_tab_visibility(
        conn, tab_id=tab_a["id"], org_id=ORG, profile_id=PROFILE_1, is_visible=False,
    )
    await set_tab_visibility(
        conn, tab_id=tab_a["id"], org_id=ORG, permission_set_id=PSET_1, is_visible=True,
    )
    visible_a = await resolve_tab_visibility(conn, tab_id=tab_a["id"], org_id=ORG, user_id=U_VIEWER)
    ok("7a — profile-level hidden wins over permission-set-level visible",
       visible_a is False, f"resolve_tab_visibility returned {visible_a}")

    tab_b = await create_tab(
        conn, org_id=ORG, applies_to="entity", label="Visibility Case B",
        api_name=f"{PFX}vis_case_b", created_by=U_ADMIN,
    )
    await set_tab_visibility(
        conn, tab_id=tab_b["id"], org_id=ORG, profile_id=PROFILE_2, is_visible=True,
    )
    await set_tab_visibility(
        conn, tab_id=tab_b["id"], org_id=ORG, permission_set_id=PSET_2, is_visible=False,
    )
    visible_b = await resolve_tab_visibility(conn, tab_id=tab_b["id"], org_id=ORG, user_id=U_VIEWER2)
    ok("7b — permission-set-level hidden wins over profile-level visible",
       visible_b is False, f"resolve_tab_visibility returned {visible_b}")

    tab_c = await create_tab(
        conn, org_id=ORG, applies_to="entity", label="Visibility Case C",
        api_name=f"{PFX}vis_case_c", created_by=U_ADMIN,
    )
    visible_c = await resolve_tab_visibility(conn, tab_id=tab_c["id"], org_id=ORG, user_id=U_DEFAULT)
    ok("7c — no grant row at all defaults to visible", visible_c is True,
       f"resolve_tab_visibility returned {visible_c}")


# ═══════════════════════════════════════════════════════════════════════════
# TASK 2d — layout CRUD: col_span gate, spacer items, caps
# ═══════════════════════════════════════════════════════════════════════════


async def test_7_col_span(conn) -> None:
    from services.portfolio_udf import create_org_definition
    from services.portfolio_udf_layouts import (
        LayoutColSpanError, add_item, add_section, create_layout,
    )
    from services.portfolio_udf_tabs import create_tab

    tab = await create_tab(
        conn, org_id=ORG, applies_to="entity", label="Col Span Test",
        api_name=f"{PFX}colspan_test", created_by=U_ADMIN,
    )
    layout = await create_layout(conn, org_id=ORG, tab_id=tab["id"])
    section = await add_section(conn, layout_id=layout["id"], org_id=ORG)

    long_def = await create_org_definition(
        conn, org_id=ORG, applies_to="entity", field_key=f"{PFX}colspan_long",
        label="Long Text Field", data_type="long_text", type_params={"length": 500},
        created_by=U_ADMIN,
    )
    text_def = await create_org_definition(
        conn, org_id=ORG, applies_to="entity", field_key=f"{PFX}colspan_text",
        label="Text Field", data_type="text", type_params={"length": 50},
        created_by=U_ADMIN,
    )

    item = await add_item(
        conn, section_id=section["id"], org_id=ORG, definition_id=long_def, col_span=2,
    )
    ok("8a — col_span=2 accepted for a long_text definition",
       item["col_span"] == 2)
    try:
        await add_item(
            conn, section_id=section["id"], org_id=ORG, definition_id=text_def, col_span=2,
        )
        ok("8b — col_span=2 rejected for a text definition", False, "no exception raised")
    except LayoutColSpanError:
        ok("8b — col_span=2 rejected for a text definition", True)

    return section["id"]


async def test_8_spacer_item(conn, section_id: str) -> None:
    from services.portfolio_udf_layouts import add_item, get_resolved_layout
    from services.portfolio_udf_tabs import get_tab

    spacer = await add_item(
        conn, section_id=section_id, org_id=ORG, definition_id=None,
        col_span=2, column_index=1,
    )
    ok("9 — spacer item (definition_id NULL) created", spacer["definition_id"] is None)

    tab_id = await conn.fetchval(
        "SELECT l.tab_id::text FROM portfolio.udf_layout_sections s "
        "JOIN portfolio.udf_layouts l ON l.id = s.layout_id WHERE s.id = $1::uuid",
        section_id,
    )
    resolved = await get_resolved_layout(conn, tab_id=tab_id, org_id=ORG)
    found = [i for sec in resolved["sections"] for i in sec["items"] if i["id"] == spacer["id"]]
    ok("9 — spacer item appears in get_resolved_layout output",
       len(found) == 1 and found[0]["definition_id"] is None and found[0]["label"] is None,
       f"found={found}")


async def test_9_layout_caps(conn) -> None:
    from services.portfolio_udf import create_org_definition
    from services.portfolio_udf_layouts import (
        LayoutCapError, add_item, add_section, create_layout,
    )
    from services.portfolio_udf_tabs import create_tab

    await conn.execute(
        """INSERT INTO public.org_settings (org_id, setting_key, setting_value)
        VALUES ($1::uuid, 'crm.udf.max_sections_per_layout', '1'::jsonb)
        ON CONFLICT (org_id, setting_key) DO UPDATE SET setting_value = '1'::jsonb""",
        ORG,
    )
    await conn.execute(
        """INSERT INTO public.org_settings (org_id, setting_key, setting_value)
        VALUES ($1::uuid, 'crm.udf.max_items_per_section', '1'::jsonb)
        ON CONFLICT (org_id, setting_key) DO UPDATE SET setting_value = '1'::jsonb""",
        ORG,
    )

    tab = await create_tab(
        conn, org_id=ORG, applies_to="commitment", label="Cap Layout Test",
        api_name=f"{PFX}cap_layout_test", created_by=U_ADMIN,
    )
    layout = await create_layout(conn, org_id=ORG, tab_id=tab["id"])
    section = await add_section(conn, layout_id=layout["id"], org_id=ORG)
    ok("10a — 1 section created under max_sections_per_layout=1", section is not None)
    try:
        await add_section(conn, layout_id=layout["id"], org_id=ORG)
        ok("10a — 2nd section rejected by max_sections_per_layout", False, "no exception raised")
    except LayoutCapError:
        ok("10a — 2nd section rejected by max_sections_per_layout", True)

    cap_def = await create_org_definition(
        conn, org_id=ORG, applies_to="commitment", field_key=f"{PFX}cap_item_field",
        label="Cap Item Field", data_type="text", type_params={"length": 20},
        created_by=U_ADMIN,
    )
    item = await add_item(conn, section_id=section["id"], org_id=ORG, definition_id=cap_def)
    ok("10b — 1 item created under max_items_per_section=1", item is not None)
    try:
        await add_item(conn, section_id=section["id"], org_id=ORG, definition_id=None)
        ok("10b — 2nd item rejected by max_items_per_section", False, "no exception raised")
    except LayoutCapError:
        ok("10b — 2nd item rejected by max_items_per_section", True)


# ═══════════════════════════════════════════════════════════════════════════
# TASK 3a — RLS cross-org isolation
# ═══════════════════════════════════════════════════════════════════════════


async def test_10_rls(conn, app_conn) -> None:
    from services.portfolio_udf_layouts import add_item, add_section, create_layout
    from services.portfolio_udf_tabs import create_tab

    other_tab = await create_tab(
        conn, org_id=OTHER_ORG, applies_to="entity", label="Other Org Tab",
        api_name=f"{PFX}other_org_tab", created_by=U_OTHER,
    )
    other_layout = await create_layout(conn, org_id=OTHER_ORG, tab_id=other_tab["id"])
    other_section = await add_section(conn, layout_id=other_layout["id"], org_id=OTHER_ORG)
    await add_item(conn, section_id=other_section["id"], org_id=OTHER_ORG, definition_id=None)

    bypass = await app_conn.fetchval(
        "SELECT rolbypassrls FROM pg_roles WHERE rolname = current_user"
    )
    ok("11 — app_service's rolbypassrls is False (a genuinely non-bypassing role)",
       bypass is False, f"rolbypassrls={bypass}")

    tr = app_conn.transaction()
    await tr.start()
    try:
        await app_conn.execute("SELECT set_config('app.current_org_id', $1, true)", ORG)

        t = await app_conn.fetchval(
            "SELECT 1 FROM portfolio.udf_tabs WHERE id = $1::uuid", other_tab["id"]
        )
        ok("11 — RLS: org A cannot read org B's tab", t is None)

        l_ = await app_conn.fetchval(
            "SELECT 1 FROM portfolio.udf_layouts WHERE id = $1::uuid", other_layout["id"]
        )
        ok("11 — RLS: org A cannot read org B's layout", l_ is None)

        s = await app_conn.fetchval(
            "SELECT 1 FROM portfolio.udf_layout_sections WHERE id = $1::uuid",
            other_section["id"],
        )
        ok("11 — RLS: org A cannot read org B's layout section", s is None)

        i = await app_conn.fetchval(
            "SELECT 1 FROM portfolio.udf_layout_items WHERE section_id = $1::uuid",
            other_section["id"],
        )
        ok("11 — RLS: org A cannot read org B's layout item", i is None)

        # Positive control: org A's OWN tab IS visible under the same GUC.
        own_tab = await create_tab(
            app_conn, org_id=ORG, applies_to="entity", label="RLS Own Tab",
            api_name=f"{PFX}rls_own_tab", created_by=U_ADMIN,
        )
        own = await app_conn.fetchval(
            "SELECT 1 FROM portfolio.udf_tabs WHERE id = $1::uuid", own_tab["id"]
        )
        ok("11 — RLS positive control: org A CAN read its own tab", own == 1)
    finally:
        await tr.rollback()

    # udf_tab_permissions cross-org check, using a grant on OTHER_ORG's tab.
    # Raw INSERT (not set_tab_visibility) — the grantee FK just needs to exist
    # as a row; the point here is RLS on udf_tab_permissions itself, not
    # set_tab_visibility's org-ownership validation (covered elsewhere).
    grant_id = await conn.fetchval(
        """INSERT INTO portfolio.udf_tab_permissions
               (tab_id, permission_set_id, is_visible)
           VALUES ($1::uuid, $2::uuid, false)
           RETURNING id::text""",
        other_tab["id"], PSET_2,
    )
    tr2 = app_conn.transaction()
    await tr2.start()
    try:
        await app_conn.execute("SELECT set_config('app.current_org_id', $1, true)", ORG)
        p = await app_conn.fetchval(
            "SELECT 1 FROM portfolio.udf_tab_permissions WHERE id = $1::uuid", grant_id
        )
        ok("11 — RLS: org A cannot read org B's tab_permissions grant", p is None)
    finally:
        await tr2.rollback()


# ═══════════════════════════════════════════════════════════════════════════
# Router — envelope shape + visibility filtering + every endpoint 403/200 (12)
# ═══════════════════════════════════════════════════════════════════════════


class _Principal:
    __slots__ = ("client", "org_id", "sub")

    def __init__(self, client, org_id: str, sub: str):
        self.client = client
        self.org_id = org_id
        self.sub = sub

    def _become(self) -> None:
        import main
        sub, org_id = self.sub, self.org_id
        main.verify_token = lambda _token: {
            "sub": sub, "email": f"{sub}@test.local", "org_id": org_id,
        }

    def get(self, url, **kw):
        self._become()
        return self.client.get(url, **kw)

    def post(self, url, **kw):
        self._become()
        return self.client.post(url, **kw)

    def patch(self, url, **kw):
        self._become()
        return self.client.patch(url, **kw)

    def put(self, url, **kw):
        self._become()
        return self.client.put(url, **kw)

    def delete(self, url, **kw):
        self._become()
        return self.client.delete(url, **kw)


HEADERS = {"Authorization": "Bearer verify-token"}


def endpoint_tests() -> None:
    import main
    from starlette.testclient import TestClient

    with TestClient(main.app, raise_server_exceptions=False) as client:
        noperms = _Principal(client, ORG, SUB_NOPERMS)
        viewer = _Principal(client, ORG, SUB_VIEWER)
        viewer2 = _Principal(client, ORG, SUB_VIEWER2)
        admin = _Principal(client, ORG, SUB_ADMIN)

        # ── 403/200 matrix ──────────────────────────────────────────────
        r = noperms.get("/api/v1/udf/tabs?applies_to=entity", headers=HEADERS)
        ok("12 GET /udf/tabs — 403 without view_portfolio", r.status_code == 403,
           f"got {r.status_code}")
        r = viewer.get("/api/v1/udf/tabs?applies_to=entity", headers=HEADERS)
        ok("12 GET /udf/tabs — 200 with view_portfolio", r.status_code == 200,
           f"got {r.status_code}: {r.text[:200]}")

        body = {
            "applies_to": "entity", "label": "HTTP Tab", "api_name": f"{PFX}http_tab",
        }
        r = viewer.post("/api/v1/udf/tabs", json=body, headers=HEADERS)
        ok("12 POST /udf/tabs — 403 without manage_portfolio", r.status_code == 403,
           f"got {r.status_code}")
        r = admin.post("/api/v1/udf/tabs", json=body, headers=HEADERS)
        ok("12 POST /udf/tabs — 201 with manage_portfolio", r.status_code == 201,
           f"got {r.status_code}: {r.text[:300]}")
        tab_id = r.json().get("tab", {}).get("id") if r.status_code == 201 else None

        if tab_id:
            r = viewer.patch(f"/api/v1/udf/tabs/{tab_id}", json={"label": "nope"},
                              headers=HEADERS)
            ok("12 PATCH /udf/tabs/{id} — 403 without manage_portfolio",
               r.status_code == 403, f"got {r.status_code}")
            r = admin.patch(f"/api/v1/udf/tabs/{tab_id}", json={"label": "HTTP Tab Renamed"},
                             headers=HEADERS)
            ok("12 PATCH /udf/tabs/{id} — 200 with manage_portfolio",
               r.status_code == 200, f"got {r.status_code}: {r.text[:300]}")

            r = viewer.post(f"/api/v1/udf/tabs/{tab_id}/deactivate", headers=HEADERS)
            ok("12 POST .../deactivate — 403 without manage_portfolio",
               r.status_code == 403, f"got {r.status_code}")
            r = admin.post(f"/api/v1/udf/tabs/{tab_id}/deactivate", headers=HEADERS)
            ok("12 POST .../deactivate — 200 with manage_portfolio",
               r.status_code == 200, f"got {r.status_code}: {r.text[:300]}")

            r = viewer.post(f"/api/v1/udf/tabs/{tab_id}/reactivate", headers=HEADERS)
            ok("12 POST .../reactivate — 403 without manage_portfolio",
               r.status_code == 403, f"got {r.status_code}")
            r = admin.post(f"/api/v1/udf/tabs/{tab_id}/reactivate", headers=HEADERS)
            ok("12 POST .../reactivate — 200 with manage_portfolio",
               r.status_code == 200, f"got {r.status_code}: {r.text[:300]}")

            r = viewer.delete(f"/api/v1/udf/tabs/{tab_id}", headers=HEADERS)
            ok("12 DELETE /udf/tabs/{id} — 403 without manage_portfolio",
               r.status_code == 403, f"got {r.status_code}")
            r = admin.delete(f"/api/v1/udf/tabs/{tab_id}", headers=HEADERS)
            ok("12 DELETE /udf/tabs/{id} — 200 with manage_portfolio",
               r.status_code == 200, f"got {r.status_code}: {r.text[:300]}")

            r = viewer.post(f"/api/v1/udf/tabs/{tab_id}/undelete", headers=HEADERS)
            ok("12 POST .../undelete — 403 without manage_portfolio",
               r.status_code == 403, f"got {r.status_code}")
            r = admin.post(f"/api/v1/udf/tabs/{tab_id}/undelete", headers=HEADERS)
            ok("12 POST .../undelete — 200 with manage_portfolio",
               r.status_code == 200, f"got {r.status_code}: {r.text[:300]}")

            r = viewer.put(f"/api/v1/udf/tabs/{tab_id}/permissions",
                            json={"profile_id": PROFILE_1, "is_visible": True},
                            headers=HEADERS)
            ok("12 PUT .../permissions — 403 without manage_portfolio",
               r.status_code == 403, f"got {r.status_code}")
            r = admin.put(f"/api/v1/udf/tabs/{tab_id}/permissions",
                           json={"profile_id": PROFILE_1, "is_visible": True},
                           headers=HEADERS)
            ok("12 PUT .../permissions — 200 with manage_portfolio",
               r.status_code == 200, f"got {r.status_code}: {r.text[:300]}")

            r = noperms.get(f"/api/v1/udf/layouts/{tab_id}", headers=HEADERS)
            ok("12 GET /udf/layouts/{id} — 403 without view_portfolio",
               r.status_code == 403, f"got {r.status_code}")
            r = admin.get(f"/api/v1/udf/layouts/{tab_id}", headers=HEADERS)
            ok("12 GET /udf/layouts/{id} — 200 with view_portfolio",
               r.status_code == 200, f"got {r.status_code}: {r.text[:300]}")

            # Envelope shape assertion — PositionsGrid-compatible key names.
            envelope = r.json()
            ok("13 — GET /udf/layouts response has 'permissions' key with 'can_write'",
               isinstance(envelope.get("permissions"), dict)
               and "can_write" in envelope["permissions"],
               f"keys={list(envelope.keys())}")
            ok("13 — GET /udf/layouts response has 'vocabularies' key with "
               "'editable'/'inline_editable'",
               isinstance(envelope.get("vocabularies"), dict)
               and "editable" in envelope["vocabularies"]
               and "inline_editable" in envelope["vocabularies"],
               f"vocabularies={envelope.get('vocabularies')}")
            ok("13 — GET /udf/layouts response has 'rows' and 'sections' keys",
               "rows" in envelope and "sections" in envelope,
               f"keys={list(envelope.keys())}")

            r = viewer.post(f"/api/v1/udf/layouts/{tab_id}/sections",
                             json={"title": "HTTP Section"}, headers=HEADERS)
            ok("12 POST .../sections — 403 without manage_portfolio",
               r.status_code == 403, f"got {r.status_code}")
            r = admin.post(f"/api/v1/udf/layouts/{tab_id}/sections",
                            json={"title": "HTTP Section"}, headers=HEADERS)
            ok("12 POST .../sections — 201 with manage_portfolio",
               r.status_code == 201, f"got {r.status_code}: {r.text[:300]}")
            section_id = r.json().get("section", {}).get("id") if r.status_code == 201 else None

            item_id = None
            if section_id:
                r = viewer.post(
                    f"/api/v1/udf/layouts/{tab_id}/sections/{section_id}/items",
                    json={"definition_id": None}, headers=HEADERS,
                )
                ok("12 POST .../items — 403 without manage_portfolio",
                   r.status_code == 403, f"got {r.status_code}")
                r = admin.post(
                    f"/api/v1/udf/layouts/{tab_id}/sections/{section_id}/items",
                    json={"definition_id": None}, headers=HEADERS,
                )
                ok("12 POST .../items — 201 with manage_portfolio",
                   r.status_code == 201, f"got {r.status_code}: {r.text[:300]}")
                item_id = r.json().get("item", {}).get("id") if r.status_code == 201 else None

            if item_id:
                r = viewer.patch(f"/api/v1/udf/layouts/{tab_id}/items/{item_id}",
                                  json={"display_order": 5}, headers=HEADERS)
                ok("12 PATCH .../items/{id} — 403 without manage_portfolio",
                   r.status_code == 403, f"got {r.status_code}")
                r = admin.patch(f"/api/v1/udf/layouts/{tab_id}/items/{item_id}",
                                 json={"display_order": 5}, headers=HEADERS)
                ok("12 PATCH .../items/{id} — 200 with manage_portfolio",
                   r.status_code == 200, f"got {r.status_code}: {r.text[:300]}")

                r = viewer.delete(f"/api/v1/udf/layouts/{tab_id}/items/{item_id}",
                                   headers=HEADERS)
                ok("12 DELETE .../items/{id} — 403 without manage_portfolio",
                   r.status_code == 403, f"got {r.status_code}")
                r = admin.delete(f"/api/v1/udf/layouts/{tab_id}/items/{item_id}",
                                   headers=HEADERS)
                ok("12 DELETE .../items/{id} — 200 with manage_portfolio",
                   r.status_code == 200, f"got {r.status_code}: {r.text[:300]}")

        # ── GET /udf/tabs excludes a tab hidden for the caller's permission set ──
        body2 = {"applies_to": "position", "label": "Hidden Tab",
                  "api_name": f"{PFX}hidden_tab_http"}
        r = admin.post("/api/v1/udf/tabs", json=body2, headers=HEADERS)
        hidden_tab_id = r.json().get("tab", {}).get("id") if r.status_code == 201 else None
        if hidden_tab_id:
            r = admin.put(f"/api/v1/udf/tabs/{hidden_tab_id}/permissions",
                           json={"profile_id": PROFILE_1, "is_visible": False},
                           headers=HEADERS)
            ok("14 — hide tab for PROFILE_1 via HTTP", r.status_code == 200,
               f"got {r.status_code}: {r.text[:200]}")

            r = viewer.get("/api/v1/udf/tabs?applies_to=position", headers=HEADERS)
            ids = {t["id"] for t in r.json().get("rows", [])}
            ok("14 — GET /udf/tabs excludes tab hidden for caller's profile",
               hidden_tab_id not in ids, f"rows ids={ids}")

            r = admin.get("/api/v1/udf/tabs?applies_to=position", headers=HEADERS)
            ids_admin = {t["id"] for t in r.json().get("rows", [])}
            ok("14 — positive control: GET /udf/tabs includes it for a caller "
               "with no such grant",
               hidden_tab_id in ids_admin, f"rows ids={ids_admin}")

            # ── GET /udf/layouts/{tab_id} on a hidden tab -> 403 ──────────
            r = admin.put(f"/api/v1/udf/tabs/{hidden_tab_id}/permissions",
                           json={"permission_set_id": PSET_2, "is_visible": False},
                           headers=HEADERS)
            ok("15 — hide tab for PSET_2 via HTTP", r.status_code == 200,
               f"got {r.status_code}: {r.text[:200]}")
            r = viewer2.get(f"/api/v1/udf/layouts/{hidden_tab_id}", headers=HEADERS)
            ok("15 — GET /udf/layouts/{tab_id} on a hidden tab returns 403, "
               "not an empty layout",
               r.status_code == 403, f"got {r.status_code}: {r.text[:200]}")
            r = admin.get(f"/api/v1/udf/layouts/{hidden_tab_id}", headers=HEADERS)
            ok("15 — positive control: GET /udf/layouts/{tab_id} 200 for a "
               "caller with no such grant",
               r.status_code == 200, f"got {r.status_code}: {r.text[:200]}")


# ═══════════════════════════════════════════════════════════════════════════
# main
# ═══════════════════════════════════════════════════════════════════════════


async def main() -> int:
    admin, admin_prov = await admin_dsn()
    app, app_prov = await app_service_dsn()
    if not admin:
        print(f"[FAIL] no working admin DSN: {admin_prov}")
        return 1
    if not app:
        print(f"[FAIL] no working app_service DSN: {app_prov}")
        return 1
    print(f"admin via {admin_prov}\napp_service via {app_prov}")

    conn = await connect(admin)
    app_conn = await connect(app)
    try:
        await teardown(conn)  # a previous crashed run must not poison the counts
        before = await counts(conn)

        await setup(conn)
        try:
            await test_1_schema(conn)
            await test_1b_udf01a_baseline()
            await test_2_cap_enforcement(conn)
            await test_3_api_name_immutable(conn)
            await test_4_soft_delete_reversible(conn)
            await test_5_soft_delete_blocked_by_layout(conn)
            await test_6_resolve_visibility(conn)
            section_id = await test_7_col_span(conn)
            await test_8_spacer_item(conn, section_id)
            await test_9_layout_caps(conn)
            await test_10_rls(conn, app_conn)

            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, endpoint_tests)
        except Exception:  # noqa: BLE001
            FAIL.append(f"unhandled: {traceback.format_exc()}")
            print(f"[FAIL] unhandled exception\n{traceback.format_exc()}")
        finally:
            await teardown(conn)

        after = await counts(conn)
        for t in COUNTED:
            ok(f"16 — teardown: {t} row count returned to baseline",
               after[t] == before[t], f"before={before[t]} after={after[t]}")
    finally:
        await conn.close()
        await app_conn.close()

    print(f"\n{'=' * 70}\nudf01b: {len(PASS)} PASS, {len(FAIL)} FAIL, {len(FIND)} FIND")
    for f in FAIL:
        print(f"  FAIL {f}")
    for f in FIND:
        print(f"  FIND {f}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
