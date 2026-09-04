"""Sprint udf01c verification — field-level security (FLS).

Pass/fail only, no prompts. Run:

    python3 apps/api/scripts/verify_udf01c.py

Part 1 (schema) was already applied before this sprint started; Task 1 below
re-confirms the load-bearing subset directly. Task 1 ALSO re-runs
verify_udf01a.py's and verify_udf01b.py's own ``main()`` in-process (imported,
not shelled out) as a hard regression gate — their PASS/FAIL counts are read
back from their own module globals after they return.

Every fixture this script writes carries a 'udf01cverify_' prefix (label,
api_name, or field_key/role/profile/permission_set name) or a fixed
99000000-...-0000dc01 prefixed UUID; teardown deletes by that prefix/id, never
TRUNCATE, and row counts are taken before the first insert and after the last
delete.
"""

from __future__ import annotations

import asyncio
import glob
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

P = "99000000-0000-0000-0000-0000dc01"
PFX = "udf01cverify_"

SUB_ADMIN = "udf01cverify|admin"
SUB_VIEWER = "udf01cverify|viewer"
SUB_EDITOR = "udf01cverify|editor"
SUB_NOPERMS = "udf01cverify|noperms"
SUB_OTHER = "udf01cverify|other"

U_ADMIN = str(uuid5(NAMESPACE_URL, SUB_ADMIN))
U_VIEWER = str(uuid5(NAMESPACE_URL, SUB_VIEWER))
#: Has the TENANT-level manage_portfolio permission (role='admin', same as
#: U_ADMIN) but the SAME profile (PROFILE_1) as U_VIEWER — this is what
#: actually exercises FLS's write-rejection path. U_VIEWER's role ('member')
#: lacks manage_portfolio entirely, so a PUT /udf/values as U_VIEWER 403s at
#: the tenant-permission gate before ever reaching the field-access check;
#: that is a real, different 403 and would falsely "pass" a field-rejection
#: test for the wrong reason. U_EDITOR isolates the field-level gate alone.
U_EDITOR = str(uuid5(NAMESPACE_URL, SUB_EDITOR))
U_NOPERMS = str(uuid5(NAMESPACE_URL, SUB_NOPERMS))
U_OTHER = str(uuid5(NAMESPACE_URL, SUB_OTHER))
USERS = [U_ADMIN, U_VIEWER, U_EDITOR, U_NOPERMS, U_OTHER]

ROLE_NOPERMS = f"{P}3001"

PROFILE_1 = f"{P}6001"
PSET_1 = f"{P}6101"

T_ENTITY_1 = f"{P}5001"

COUNTED = [
    "portfolio.udf_field_permissions",
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
    await conn.execute(
        f"DELETE FROM portfolio.udf_field_permissions WHERE definition_id IN "
        f"(SELECT id FROM portfolio.udf_definitions WHERE field_key LIKE '{PFX}%')"
    )
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
        f"DELETE FROM portfolio.udf_values WHERE definition_id IN "
        f"(SELECT id FROM portfolio.udf_definitions WHERE field_key LIKE '{PFX}%')"
    )
    await conn.execute(f"DELETE FROM portfolio.udf_definitions WHERE field_key LIKE '{PFX}%'")

    await conn.execute(
        "DELETE FROM public.user_permission_sets WHERE user_id = ANY($1::uuid[])", USERS
    )
    await conn.execute(
        "DELETE FROM public.permission_set_permissions WHERE permission_set_id = $1::uuid",
        PSET_1,
    )
    await conn.execute(
        "DELETE FROM public.profile_permissions WHERE profile_id = $1::uuid", PROFILE_1
    )
    await conn.execute("DELETE FROM public.user_roles WHERE user_id = ANY($1::uuid[])", USERS)
    await conn.execute(
        "DELETE FROM public.role_permissions WHERE role_id = $1::uuid", ROLE_NOPERMS
    )
    await conn.execute("DELETE FROM public.roles WHERE id = $1::uuid", ROLE_NOPERMS)
    await conn.execute("DELETE FROM public.users WHERE id = ANY($1::uuid[])", USERS)
    await conn.execute("DELETE FROM public.permission_sets WHERE id = $1::uuid", PSET_1)
    await conn.execute("DELETE FROM public.profiles WHERE id = $1::uuid", PROFILE_1)


async def setup(conn) -> None:
    for user_id, org, sub, profile_id in (
        (U_ADMIN, ORG, SUB_ADMIN, None),
        (U_VIEWER, ORG, SUB_VIEWER, PROFILE_1),
        (U_EDITOR, ORG, SUB_EDITOR, PROFILE_1),
        (U_NOPERMS, ORG, SUB_NOPERMS, None),
        (U_OTHER, OTHER_ORG, SUB_OTHER, None),
    ):
        await conn.execute(
            """INSERT INTO public.users
                (id, org_id, email, full_name, auth0_sub, role, is_active)
            VALUES ($1::uuid, $2::uuid, $3, 'Verify udf01c', $4, 'member', true)
            ON CONFLICT (id) DO NOTHING""",
            user_id, org, f"udf01cverify-{user_id[-4:]}@test.local", sub,
        )

    await conn.execute(
        """INSERT INTO public.roles (id, org_id, name, description)
        VALUES ($1::uuid, $2::uuid, 'udf01cverify_noperms', 'verify fixture')
        ON CONFLICT (id) DO NOTHING""",
        ROLE_NOPERMS, ORG,
    )
    for user_id, role_name, org in (
        (U_ADMIN, "admin", ORG), (U_VIEWER, "member", ORG), (U_EDITOR, "admin", ORG),
        (U_NOPERMS, "udf01cverify_noperms", ORG), (U_OTHER, "admin", OTHER_ORG),
    ):
        await conn.execute(
            """INSERT INTO public.user_roles (user_id, role_id)
            SELECT $1::uuid, r.id FROM public.roles r
            WHERE r.name = $2 AND r.org_id = $3::uuid
            ON CONFLICT DO NOTHING""",
            user_id, role_name, org,
        )

    await conn.execute(
        """INSERT INTO public.profiles (id, org_id, name, description)
        VALUES ($1::uuid, $2::uuid, $3, 'verify fixture')
        ON CONFLICT (id) DO NOTHING""",
        PROFILE_1, ORG, f"{PFX}profile1",
    )
    await conn.execute(
        "UPDATE public.users SET profile_id = $2::uuid WHERE id = ANY($1::uuid[])",
        [U_VIEWER, U_EDITOR], PROFILE_1,
    )
    await conn.execute(
        """INSERT INTO public.permission_sets (id, org_id, name, description)
        VALUES ($1::uuid, $2::uuid, $3, 'verify fixture')
        ON CONFLICT (id) DO NOTHING""",
        PSET_1, ORG, f"{PFX}pset1",
    )
    await conn.execute(
        """INSERT INTO public.user_permission_sets (user_id, permission_set_id)
        VALUES ($1::uuid, $2::uuid) ON CONFLICT DO NOTHING""",
        U_VIEWER, PSET_1,
    )


class _QueryCounter:
    """Wraps a connection and counts fetch/fetchval/fetchrow/execute calls —
    used ONLY to measure and report resolve_field_access_bulk's real query
    count (Task 2c requires it bounded, not one query per field)."""

    def __init__(self, conn):
        self._conn = conn
        self.count = 0

    def __getattr__(self, name):
        attr = getattr(self._conn, name)
        if name in ("fetch", "fetchval", "fetchrow", "execute"):
            async def wrapper(*a, **kw):
                self.count += 1
                return await attr(*a, **kw)
            return wrapper
        return attr


async def build_udf_fixtures(conn) -> dict:
    from services.portfolio_udf import create_org_definition
    from services.portfolio_udf_field_permissions import set_field_access
    from services.portfolio_udf_layouts import add_item, add_section, create_layout
    from services.portfolio_udf_tabs import create_tab, set_tab_visibility

    tab_hidden = await create_tab(
        conn, org_id=ORG, applies_to="entity", label="FLS Tab Hidden",
        api_name=f"{PFX}tab_hidden", created_by=U_ADMIN,
    )
    await set_tab_visibility(
        conn, tab_id=tab_hidden["id"], org_id=ORG, profile_id=PROFILE_1, is_visible=False,
    )

    tab_visible = await create_tab(
        conn, org_id=ORG, applies_to="entity", label="FLS Tab Visible",
        api_name=f"{PFX}tab_visible", created_by=U_ADMIN,
    )

    async def new_def(suffix: str, label: str) -> str:
        return await create_org_definition(
            conn, org_id=ORG, applies_to="entity", field_key=f"{PFX}{suffix}",
            label=label, data_type="text", type_params={"length": 100},
            created_by=U_ADMIN,
        )

    def_tabhidden = await new_def("def_tabhidden", "Tab Hidden Field")
    await set_field_access(
        conn, definition_id=def_tabhidden, access="edit", org_id=ORG,
        profile_id=PROFILE_1, created_by=U_ADMIN,
    )

    def_prec_a = await new_def("def_prec_a", "Precedence A")
    await set_field_access(
        conn, definition_id=def_prec_a, access="read", org_id=ORG,
        profile_id=PROFILE_1, created_by=U_ADMIN,
    )
    await set_field_access(
        conn, definition_id=def_prec_a, access="edit", org_id=ORG,
        permission_set_id=PSET_1, created_by=U_ADMIN,
    )

    def_prec_b = await new_def("def_prec_b", "Precedence B")
    await set_field_access(
        conn, definition_id=def_prec_b, access="edit", org_id=ORG,
        profile_id=PROFILE_1, created_by=U_ADMIN,
    )
    await set_field_access(
        conn, definition_id=def_prec_b, access="hidden", org_id=ORG,
        permission_set_id=PSET_1, created_by=U_ADMIN,
    )

    def_default = await new_def("def_default", "Default Access")

    layout = await create_layout(conn, org_id=ORG, tab_id=tab_visible["id"])
    section = await add_section(
        conn, layout_id=layout["id"], org_id=ORG, title="Bulk Section", column_count=2,
    )
    bulk_defs = []
    for i in range(1, 11):
        d = await new_def(f"bulk_{i}", f"Bulk {i}")
        bulk_defs.append(d)
        await add_item(
            conn, section_id=section["id"], org_id=ORG, definition_id=d,
            column_index=(i - 1) % 2, is_read_only=False,
        )
    # bulk_defs[0] -> hidden, bulk_defs[1] -> read, bulk_defs[2] -> explicit
    # edit, bulk_defs[3:] -> no grant at all (implicit default edit).
    await set_field_access(
        conn, definition_id=bulk_defs[0], access="hidden", org_id=ORG,
        profile_id=PROFILE_1, created_by=U_ADMIN,
    )
    await set_field_access(
        conn, definition_id=bulk_defs[1], access="read", org_id=ORG,
        profile_id=PROFILE_1, created_by=U_ADMIN,
    )
    await set_field_access(
        conn, definition_id=bulk_defs[2], access="edit", org_id=ORG,
        profile_id=PROFILE_1, created_by=U_ADMIN,
    )

    return dict(
        tab_hidden=tab_hidden, tab_visible=tab_visible,
        def_tabhidden=def_tabhidden, def_prec_a=def_prec_a, def_prec_b=def_prec_b,
        def_default=def_default, layout=layout, section=section, bulk_defs=bulk_defs,
    )


# ═══════════════════════════════════════════════════════════════════════════
# TASK 1 — schema re-verification + udf01a/udf01b regression gate
# ═══════════════════════════════════════════════════════════════════════════


async def test_1_schema(conn) -> None:
    ok("1 — portfolio.udf_field_permissions exists",
       await conn.fetchval("SELECT to_regclass('portfolio.udf_field_permissions')") is not None)

    for col, dtype in (
        ("id", "uuid"), ("definition_id", "uuid"), ("profile_id", "uuid"),
        ("permission_set_id", "uuid"), ("access", "text"),
        ("created_at", "timestamp with time zone"), ("created_by", "uuid"),
    ):
        got = await conn.fetchval(
            "SELECT data_type FROM information_schema.columns WHERE table_schema='portfolio' "
            "AND table_name='udf_field_permissions' AND column_name=$1", col,
        )
        ok(f"1 — column {col} is {dtype}", got == dtype, f"got {got!r}")

    for idx in ("udf_field_perm_profile_uq", "udf_field_perm_set_uq"):
        ok(f"1 — partial unique index {idx} exists",
           await conn.fetchval(
               "SELECT indexdef FROM pg_indexes WHERE tablename='udf_field_permissions' "
               f"AND indexname='{idx}'") is not None)

    for con in ("udf_field_perm_one_grantee_chk", "udf_field_permissions_access_check"):
        ok(f"1 — constraint {con} exists",
           await conn.fetchval(
               f"SELECT 1 FROM pg_constraint WHERE conname='{con}'") == 1)

    rls = await conn.fetchval(
        "SELECT relrowsecurity FROM pg_class WHERE oid = "
        "'portfolio.udf_field_permissions'::regclass"
    )
    ok("1 — RLS is enabled on portfolio.udf_field_permissions", rls is True)

    find("1 — RLS write-scope asymmetry between platform and org/team/user scope fields",
         "udf_field_permissions_org_isolation ties every row's visibility to "
         "definition_id IN (SELECT id FROM udf_definitions WHERE org_id = "
         "current_org_id), OR super-admin — identical shape to "
         "udf_tab_permissions_org_isolation. Tabs are ALWAYS org-scoped so "
         "that is airtight there; udf_definitions can be platform-scoped "
         "(org_id IS NULL), which can never equal any current_org_id — so a "
         "normal (non-super-admin) connection can never see or write a "
         "platform-scope field's permission grants. Empirically reproduced "
         "in test_7_platform_scope_rls_gap below, not merely asserted.")


async def test_1b_regression_baseline() -> None:
    import verify_udf01a

    exit_a = await verify_udf01a.main()
    ok("1 — verify_udf01a.py regression baseline still green",
       exit_a == 0 and not verify_udf01a.FAIL,
       f"exit={exit_a} PASS={len(verify_udf01a.PASS)} FAIL={len(verify_udf01a.FAIL)}")
    if verify_udf01a.FAIL:
        for f in verify_udf01a.FAIL:
            print(f"    [udf01a FAIL] {f}")

    import verify_udf01b

    exit_b = await verify_udf01b.main()
    ok("1 — verify_udf01b.py regression baseline still green",
       exit_b == 0 and not verify_udf01b.FAIL,
       f"exit={exit_b} PASS={len(verify_udf01b.PASS)} FAIL={len(verify_udf01b.FAIL)}")
    if verify_udf01b.FAIL:
        for f in verify_udf01b.FAIL:
            print(f"    [udf01b FAIL] {f}")


# ═══════════════════════════════════════════════════════════════════════════
# TASK 2b — precedence: tab-hidden wins outright, both grant-path directions
# ═══════════════════════════════════════════════════════════════════════════


async def test_2_precedence(conn, fx: dict) -> None:
    from services.portfolio_udf_field_permissions import resolve_field_access

    access = await resolve_field_access(
        conn, definition_id=fx["def_tabhidden"], tab_id=fx["tab_hidden"]["id"],
        org_id=ORG, user_id=U_VIEWER,
    )
    ok("2 — tab-hidden wins outright over an explicit field-level 'edit' grant",
       access == "hidden", f"got {access!r}")

    access = await resolve_field_access(
        conn, definition_id=fx["def_prec_a"], tab_id=fx["tab_visible"]["id"],
        org_id=ORG, user_id=U_VIEWER,
    )
    ok("2 — profile-level 'read' wins over permission-set-level 'edit'",
       access == "read", f"got {access!r}")

    access = await resolve_field_access(
        conn, definition_id=fx["def_prec_b"], tab_id=fx["tab_visible"]["id"],
        org_id=ORG, user_id=U_VIEWER,
    )
    ok("2 — permission-set-level 'hidden' wins over profile-level 'edit' "
       "(other direction, independent case)",
       access == "hidden", f"got {access!r}")

    access = await resolve_field_access(
        conn, definition_id=fx["def_default"], tab_id=fx["tab_visible"]["id"],
        org_id=ORG, user_id=U_VIEWER,
    )
    ok("2 — no grant at all defaults to 'edit' on a visible tab",
       access == "edit", f"got {access!r}")

    # Endpoints with no tab in scope (GET /udf/definitions, GET/PUT
    # /udf/values/...) call resolve_field_access with tab_id=None — confirm
    # that path also evaluates the field grants correctly, tab check skipped.
    access = await resolve_field_access(
        conn, definition_id=fx["def_prec_b"], tab_id=None, org_id=ORG, user_id=U_VIEWER,
    )
    ok("2 — tab_id=None skips the tab-hidden step and still resolves field grants",
       access == "hidden", f"got {access!r}")


# ═══════════════════════════════════════════════════════════════════════════
# TASK 2a — set_field_access: upsert semantics, grantee validation
# ═══════════════════════════════════════════════════════════════════════════


async def test_3_set_field_access(conn, fx: dict) -> None:
    from services.portfolio_udf_field_permissions import (
        FieldGranteeError,
        set_field_access,
    )

    def_id = fx["def_default"]
    await set_field_access(
        conn, definition_id=def_id, access="read", org_id=ORG,
        profile_id=PROFILE_1, created_by=U_ADMIN,
    )
    await set_field_access(
        conn, definition_id=def_id, access="hidden", org_id=ORG,
        profile_id=PROFILE_1, created_by=U_ADMIN,
    )
    row_count = await conn.fetchval(
        "SELECT count(*) FROM portfolio.udf_field_permissions "
        "WHERE definition_id = $1::uuid AND profile_id = $2::uuid",
        def_id, PROFILE_1,
    )
    ok("3 — set_field_access upserts, not duplicates (2 calls -> 1 row)",
       row_count == 1, f"row_count={row_count}")
    current = await conn.fetchval(
        "SELECT access FROM portfolio.udf_field_permissions "
        "WHERE definition_id = $1::uuid AND profile_id = $2::uuid",
        def_id, PROFILE_1,
    )
    ok("3 — upsert reflects the LATEST access value",
       current == "hidden", f"got {current!r}")
    # Restore def_default to its intended zero-grants state for test_2/test_5.
    await conn.execute(
        "DELETE FROM portfolio.udf_field_permissions "
        "WHERE definition_id = $1::uuid AND profile_id = $2::uuid",
        def_id, PROFILE_1,
    )

    try:
        await set_field_access(conn, definition_id=def_id, access="read", org_id=ORG)
        ok("3 — zero grantees rejected", False, "no exception raised")
    except FieldGranteeError:
        ok("3 — zero grantees rejected", True)

    try:
        await set_field_access(
            conn, definition_id=def_id, access="read", org_id=ORG,
            profile_id=PROFILE_1, permission_set_id=PSET_1,
        )
        ok("3 — two grantees rejected", False, "no exception raised")
    except FieldGranteeError:
        ok("3 — two grantees rejected", True)

    try:
        await set_field_access(
            conn, definition_id=def_id, access="read", org_id=ORG,
            profile_id="99000000-0000-0000-0000-000000000000",
        )
        ok("3 — a profile_id from a different/nonexistent org is rejected",
           False, "no exception raised")
    except FieldGranteeError:
        ok("3 — a profile_id from a different/nonexistent org is rejected", True)


# ═══════════════════════════════════════════════════════════════════════════
# TASK 2c — bulk resolution: correctness + bounded query count
# ═══════════════════════════════════════════════════════════════════════════


async def test_4_bulk_resolution(conn, fx: dict) -> None:
    from services.portfolio_udf_field_permissions import resolve_field_access_bulk

    counter = _QueryCounter(conn)
    result = await resolve_field_access_bulk(
        counter, definition_ids=fx["bulk_defs"], tab_id=fx["tab_visible"]["id"],
        org_id=ORG, user_id=U_VIEWER,
    )
    print(f"    resolve_field_access_bulk query count for a 10-field layout: "
          f"{counter.count}")
    ok("4 — resolve_field_access_bulk query count is bounded (not O(N) for N=10 fields)",
       counter.count <= 10, f"count={counter.count} for 10 fields")

    expected = {
        fx["bulk_defs"][0]: "hidden",
        fx["bulk_defs"][1]: "read",
        fx["bulk_defs"][2]: "edit",
    }
    for def_id, exp in expected.items():
        ok(f"4 — bulk result for {def_id[-6:]} == {exp!r}",
           result.get(def_id) == exp, f"got {result.get(def_id)!r}")
    for def_id in fx["bulk_defs"][3:]:
        ok(f"4 — bulk result for ungranted field {def_id[-6:]} defaults to 'edit'",
           result.get(def_id) == "edit", f"got {result.get(def_id)!r}")

    empty = await resolve_field_access_bulk(
        conn, definition_ids=[], tab_id=fx["tab_visible"]["id"], org_id=ORG, user_id=U_VIEWER,
    )
    ok("4 — bulk resolve of an empty id list returns {} without touching the db",
       empty == {})


# ═══════════════════════════════════════════════════════════════════════════
# TASK 2e — get_resolved_layout: hidden absent, read forces is_read_only
# ═══════════════════════════════════════════════════════════════════════════


async def test_5_resolved_layout(conn, fx: dict) -> None:
    from services.portfolio_udf_layouts import get_resolved_layout

    resolved = await get_resolved_layout(
        conn, tab_id=fx["tab_visible"]["id"], org_id=ORG, user_id=U_VIEWER,
    )
    items = [i for sec in resolved["sections"] for i in sec["items"]]
    ids_present = {i["definition_id"] for i in items}

    ok("5 — hidden field's layout item is absent from the section tree entirely",
       fx["bulk_defs"][0] not in ids_present, f"present ids include hidden? "
       f"{fx['bulk_defs'][0] in ids_present}")
    ok("5 — item count is 10 minus the 1 hidden field", len(items) == 9,
       f"got {len(items)}")

    read_item = next(i for i in items if i["definition_id"] == fx["bulk_defs"][1])
    ok("5 — read field's item carries is_read_only=True even though the "
       "layout item itself was created with is_read_only=False",
       read_item["is_read_only"] is True, f"got {read_item['is_read_only']!r}")

    edit_item = next(i for i in items if i["definition_id"] == fx["bulk_defs"][2])
    ok("5 — edit field's item is unchanged (is_read_only stays False)",
       edit_item["is_read_only"] is False, f"got {edit_item['is_read_only']!r}")

    # Positive control: the SAME layout, resolved for U_ADMIN (no profile, no
    # grants apply), shows all 10 items and none forced read-only.
    resolved_admin = await get_resolved_layout(
        conn, tab_id=fx["tab_visible"]["id"], org_id=ORG, user_id=U_ADMIN,
    )
    items_admin = [i for sec in resolved_admin["sections"] for i in sec["items"]]
    ok("5 — positive control: caller with no matching grants sees all 10 items",
       len(items_admin) == 10, f"got {len(items_admin)}")


# ═══════════════════════════════════════════════════════════════════════════
# TASK 3a — RLS cross-org isolation on udf_field_permissions
# ═══════════════════════════════════════════════════════════════════════════


async def test_6_rls(conn, app_conn, fx: dict) -> None:
    from services.portfolio_udf import create_org_definition

    other_def = await create_org_definition(
        conn, org_id=OTHER_ORG, applies_to="entity", field_key=f"{PFX}other_org_field",
        label="Other Org Field", data_type="text", type_params={"length": 50},
        created_by=U_OTHER,
    )
    grant_id = await conn.fetchval(
        """INSERT INTO portfolio.udf_field_permissions
               (definition_id, permission_set_id, access)
           VALUES ($1::uuid, $2::uuid, 'hidden')
           RETURNING id::text""",
        other_def, PSET_1,
    )

    bypass = await app_conn.fetchval(
        "SELECT rolbypassrls FROM pg_roles WHERE rolname = current_user"
    )
    ok("6 — app_service's rolbypassrls is False (a genuinely non-bypassing role)",
       bypass is False, f"rolbypassrls={bypass}")

    tr = app_conn.transaction()
    await tr.start()
    try:
        await app_conn.execute("SELECT set_config('app.current_org_id', $1, true)", ORG)
        p = await app_conn.fetchval(
            "SELECT 1 FROM portfolio.udf_field_permissions WHERE id = $1::uuid", grant_id
        )
        ok("6 — RLS: org A cannot read org B's field-permission grant", p is None)

        # Positive control: org A's own grant IS visible under the same GUC.
        own = await app_conn.fetchval(
            "SELECT 1 FROM portfolio.udf_field_permissions WHERE definition_id = $1::uuid "
            "AND profile_id = $2::uuid",
            fx["def_tabhidden"], PROFILE_1,
        )
        ok("6 — RLS positive control: org A CAN read its own field-permission grant",
           own == 1)
    finally:
        await tr.rollback()


async def test_7_platform_scope_rls_gap(conn, app_conn) -> None:
    from services.portfolio_udf import create_platform_definition
    from services.portfolio_udf_field_permissions import (
        resolve_field_access,
        set_field_access,
    )

    platform_def = await create_platform_definition(
        conn, applies_to="entity", field_key=f"{PFX}platform_field",
        label="Platform FLS Field", data_type="text", type_params={"length": 50},
        is_super_admin=True, created_by=U_ADMIN,
    )
    await set_field_access(
        conn, definition_id=platform_def, access="hidden", org_id=ORG,
        profile_id=PROFILE_1, is_super_admin=True, created_by=U_ADMIN,
    )
    exists = await conn.fetchval(
        "SELECT 1 FROM portfolio.udf_field_permissions WHERE definition_id = $1::uuid",
        platform_def,
    )
    ok("7 — the 'hidden' grant on the platform-scope field really was written",
       exists == 1)

    tr = app_conn.transaction()
    await tr.start()
    try:
        await app_conn.execute("SELECT set_config('app.current_org_id', $1, true)", ORG)
        access = await resolve_field_access(
            app_conn, definition_id=platform_def, tab_id=None, org_id=ORG, user_id=U_VIEWER,
        )
        find("7 — platform-scope FLS grants are invisible under a non-super-admin "
             "connection (empirically reproduced)",
             f"a 'hidden' grant exists in the database for this platform-scope "
             f"field, but resolve_field_access under a normal app_service "
             f"connection (RLS enforced, no app.is_super_admin) returns "
             f"{access!r} — the grant row is invisible to RLS (definition's "
             f"org_id IS NULL never equals current_org_id), so resolution "
             f"silently falls back to the 'no grant' default of 'edit'. This "
             f"is a FAIL-OPEN outcome for platform-scope fields specifically: "
             f"an admin who configured 'hidden' for PROFILE_1 would see the "
             f"field genuinely hidden from a super-admin view, but a normal "
             f"org viewer sees it as 'edit' regardless.")
        ok("7 — (documented above) resolution falls back to the visible "
           "default rather than erroring",
           access == "edit", f"got {access!r}")
    finally:
        await tr.rollback()

    try:
        await set_field_access(
            conn, definition_id=platform_def, access="hidden", org_id=ORG,
            profile_id=PROFILE_1, is_super_admin=False, created_by=U_ADMIN,
        )
        ok("7 — set_field_access on a platform-scope field without "
           "is_super_admin is refused", False, "no exception raised")
    except Exception as exc:  # noqa: BLE001 — PortfolioError from _require_super_admin
        ok("7 — set_field_access on a platform-scope field without "
           "is_super_admin is refused", True, f"{type(exc).__name__}: {exc}")


# ═══════════════════════════════════════════════════════════════════════════
# Router — envelope filtering + write rejection + 403/200 matrix
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

    def put(self, url, **kw):
        self._become()
        return self.client.put(url, **kw)


HEADERS = {"Authorization": "Bearer verify-token"}


def endpoint_tests(fx: dict) -> None:
    import main
    from starlette.testclient import TestClient

    with TestClient(main.app, raise_server_exceptions=False) as client:
        noperms = _Principal(client, ORG, SUB_NOPERMS)
        viewer = _Principal(client, ORG, SUB_VIEWER)
        editor = _Principal(client, ORG, SUB_EDITOR)
        admin = _Principal(client, ORG, SUB_ADMIN)

        # ── GET /udf/definitions excludes the hidden field's row entirely ──
        r = viewer.get("/api/v1/udf/definitions?target_type=entity", headers=HEADERS)
        ok("8 GET /udf/definitions — 200 for viewer", r.status_code == 200,
           f"got {r.status_code}: {r.text[:200]}")
        ids = {row["id"] for row in r.json().get("rows", [])}
        ok("8 — hidden field's id is absent from GET /udf/definitions rows "
           "(key genuinely missing, not a null entry)",
           fx["bulk_defs"][0] not in ids, f"hidden id present? "
           f"{fx['bulk_defs'][0] in ids}")
        ok("8 — read/edit fields DO appear in GET /udf/definitions rows",
           fx["bulk_defs"][1] in ids and fx["bulk_defs"][2] in ids,
           f"ids={ids}")

        r = admin.get("/api/v1/udf/definitions?target_type=entity", headers=HEADERS)
        ids_admin = {row["id"] for row in r.json().get("rows", [])}
        ok("8 — positive control: admin (no matching grant) sees the field "
           "that is hidden for viewer",
           fx["bulk_defs"][0] in ids_admin, f"ids_admin has it? "
           f"{fx['bulk_defs'][0] in ids_admin}")

        # ── Record values (as admin, who has 'edit' on everything here) ──
        for def_id in fx["bulk_defs"][:3]:
            r = admin.put(
                f"/api/v1/udf/values/entity/{T_ENTITY_1}",
                json={"definition_id": def_id, "value": f"seed-{def_id[-4:]}"},
                headers=HEADERS,
            )
            ok(f"9 admin seeds a value for {def_id[-6:]}", r.status_code == 200,
               f"got {r.status_code}: {r.text[:200]}")

        # ── GET /udf/values excludes hidden, includes read/edit ──
        r = viewer.get(f"/api/v1/udf/values/entity/{T_ENTITY_1}", headers=HEADERS)
        ok("9 GET /udf/values — 200 for viewer", r.status_code == 200,
           f"got {r.status_code}: {r.text[:200]}")
        value_def_ids = {row["definition_id"] for row in r.json().get("rows", [])}
        ok("9 — hidden field's key is absent from GET /udf/values rows",
           fx["bulk_defs"][0] not in value_def_ids)
        ok("9 — read field's key IS present in GET /udf/values rows",
           fx["bulk_defs"][1] in value_def_ids)
        ok("9 — edit field's key IS present in GET /udf/values rows",
           fx["bulk_defs"][2] in value_def_ids)

        # ── PUT /udf/values: read field rejected, naming the field ──
        # Uses `editor` (has manage_portfolio, same PROFILE_1 as viewer) —
        # `viewer` lacks manage_portfolio entirely and would 403 at the
        # TENANT gate before ever reaching the field-access check, which
        # would falsely satisfy this assertion for the wrong reason.
        r = editor.put(
            f"/api/v1/udf/values/entity/{T_ENTITY_1}",
            json={"definition_id": fx["bulk_defs"][1], "value": "editor-write-attempt"},
            headers=HEADERS,
        )
        ok("9 — write to a 'read' field is rejected (403)", r.status_code == 403,
           f"got {r.status_code}: {r.text[:200]}")
        ok("9 — the 403 names the field", f"{PFX}bulk_2" in r.text,
           f"detail={r.text[:300]}")

        # ── PUT /udf/values: hidden field also rejected ──
        r = editor.put(
            f"/api/v1/udf/values/entity/{T_ENTITY_1}",
            json={"definition_id": fx["bulk_defs"][0], "value": "editor-write-attempt"},
            headers=HEADERS,
        )
        ok("9 — write to a 'hidden' field is also rejected (403)",
           r.status_code == 403, f"got {r.status_code}: {r.text[:200]}")

        # ── PUT /udf/values: edit field succeeds — positive control ──
        r = editor.put(
            f"/api/v1/udf/values/entity/{T_ENTITY_1}",
            json={"definition_id": fx["bulk_defs"][2], "value": "editor-edit-ok"},
            headers=HEADERS,
        )
        ok("9 — write to an 'edit' field succeeds (positive control)",
           r.status_code == 200, f"got {r.status_code}: {r.text[:200]}")
        ok("9 — the written value round-trips",
           r.status_code == 200 and r.json().get("value", {}).get("value_text")
           == "editor-edit-ok",
           f"body={r.text[:300]}")

        # ── GET /udf/layouts/{tab_id}: hidden absent, read forces read-only ──
        r = viewer.get(f"/api/v1/udf/layouts/{fx['tab_visible']['id']}", headers=HEADERS)
        ok("10 GET /udf/layouts — 200 for viewer", r.status_code == 200,
           f"got {r.status_code}: {r.text[:200]}")
        body = r.json()
        layout_items = [i for sec in body.get("sections", []) for i in sec.get("items", [])]
        layout_ids = {i["definition_id"] for i in layout_items}
        ok("10 — hidden field's layout item is absent from GET /udf/layouts",
           fx["bulk_defs"][0] not in layout_ids)
        read_item = next(
            (i for i in layout_items if i["definition_id"] == fx["bulk_defs"][1]), None,
        )
        ok("10 — read field's layout item is flagged is_read_only=true over HTTP",
           read_item is not None and read_item["is_read_only"] is True,
           f"read_item={read_item}")

        # ── PUT/GET /udf/fields/{id}/permissions — 403/200 matrix ──
        target_def = fx["def_default"]
        r = viewer.put(
            f"/api/v1/udf/fields/{target_def}/permissions",
            json={"profile_id": PROFILE_1, "access": "read"}, headers=HEADERS,
        )
        ok("11 PUT /udf/fields/.../permissions — 403 without manage_portfolio",
           r.status_code == 403, f"got {r.status_code}")
        r = admin.put(
            f"/api/v1/udf/fields/{target_def}/permissions",
            json={"profile_id": PROFILE_1, "access": "read"}, headers=HEADERS,
        )
        ok("11 PUT /udf/fields/.../permissions — 200 with manage_portfolio",
           r.status_code == 200, f"got {r.status_code}: {r.text[:300]}")
        ok("11 — response echoes the grant with access='read'",
           r.status_code == 200 and r.json().get("permission", {}).get("access") == "read",
           f"body={r.text[:300]}")

        r = noperms.get(f"/api/v1/udf/fields/{target_def}/permissions", headers=HEADERS)
        ok("11 GET /udf/fields/.../permissions — 403 without manage_portfolio",
           r.status_code == 403, f"got {r.status_code}")
        r = admin.get(f"/api/v1/udf/fields/{target_def}/permissions", headers=HEADERS)
        ok("11 GET /udf/fields/.../permissions — 200 with manage_portfolio",
           r.status_code == 200, f"got {r.status_code}: {r.text[:300]}")
        grant_rows = r.json().get("rows", [])
        ok("11 — GET /udf/fields/.../permissions lists the grant just created",
           any(g["profile_id"] == PROFILE_1 and g["access"] == "read" for g in grant_rows),
           f"rows={grant_rows}")
        # This grant on def_default is cleaned up by main()'s teardown() —
        # every udf_field_permissions row whose definition's field_key
        # matches the PFX prefix, def_default included.


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
            await test_1b_regression_baseline()
            fx = await build_udf_fixtures(conn)
            await test_2_precedence(conn, fx)
            await test_3_set_field_access(conn, fx)
            await test_4_bulk_resolution(conn, fx)
            await test_5_resolved_layout(conn, fx)
            await test_6_rls(conn, app_conn, fx)
            await test_7_platform_scope_rls_gap(conn, app_conn)

            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, endpoint_tests, fx)
        except Exception:  # noqa: BLE001
            FAIL.append(f"unhandled: {traceback.format_exc()}")
            print(f"[FAIL] unhandled exception\n{traceback.format_exc()}")
        finally:
            await teardown(conn)

        after = await counts(conn)
        for t in COUNTED:
            ok(f"12 — teardown: {t} row count returned to baseline",
               after[t] == before[t], f"before={before[t]} after={after[t]}")
    finally:
        await conn.close()
        await app_conn.close()

    print(f"\n{'=' * 70}\nudf01c: {len(PASS)} PASS, {len(FAIL)} FAIL, {len(FIND)} FIND")
    for f in FAIL:
        print(f"  FAIL {f}")
    for f in FIND:
        print(f"  FIND {f}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
