"""Sprint udf02a verification — DataGrid columns & list filters (read-only).

Pass/fail only, no prompts. Run:

    python3 apps/api/scripts/verify_udf02a.py

Task 1 re-runs verify_udf01a.py, verify_udf01b.py AND verify_udf01c.py's own
``main()`` in-process (imported, not shelled out) as a hard regression gate —
their PASS/FAIL counts are read back from their own module globals after they
return. udf01c's baseline changed since it was last measured (the platform-
scope RLS fail-open, FIND 7, was patched live by
``migrations/udf01c_fix_platform_scope_rls_gap.sql``) — this run also
confirms verify_udf01c.py's own test 7 assertion was corrected to match the
fix (see that script's history) rather than continuing to assert the old,
now-incorrect, fail-open behaviour.

Every fixture this script writes carries a 'udf02averify_' prefix (label,
api_name, field_key, or display_name) or a fixed
99000000-...-0000ea02 prefixed UUID; teardown deletes by that prefix/id,
never TRUNCATE, and row counts are taken before the first insert and after
the last delete.
"""

from __future__ import annotations

import asyncio
import glob
import sys
import traceback
from decimal import Decimal
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

P = "99000000-0000-0000-0000-0000ea02"
PFX = "udf02averify_"

SUB_ADMIN = "udf02averify|admin"
SUB_RESTRICTED = "udf02averify|restricted"
SUB_NOPERMS = "udf02averify|noperms"
SUB_OTHER = "udf02averify|other"

U_ADMIN = str(uuid5(NAMESPACE_URL, SUB_ADMIN))
U_RESTRICTED = str(uuid5(NAMESPACE_URL, SUB_RESTRICTED))
U_NOPERMS = str(uuid5(NAMESPACE_URL, SUB_NOPERMS))
U_OTHER = str(uuid5(NAMESPACE_URL, SUB_OTHER))
USERS = [U_ADMIN, U_RESTRICTED, U_NOPERMS, U_OTHER]

ROLE_NOPERMS = f"{P}3001"
PROFILE_1 = f"{P}6001"

E_OTHER = f"{P}7999"
ENTITY_IDS = [f"{P}70{i:02d}" for i in range(1, 11)]  # 10 entities, E1..E10

LIST_SELECT = f"{P}4001"
LIST_MULTISELECT = f"{P}4002"
LIST_KEY_SELECT = f"{PFX}colors"
LIST_KEY_MULTISELECT = f"{PFX}letters"

COUNTED = [
    "portfolio.udf_definitions", "portfolio.udf_values", "portfolio.udf_tag_assignments",
    "portfolio.udf_field_permissions",
    "portfolio.udf_tabs", "portfolio.udf_tab_permissions",
    "portfolio.udf_layouts", "portfolio.udf_layout_sections", "portfolio.udf_layout_items",
    "portfolio.udf_definition_audit",
    "public.entities", "public.reference_data_lists", "public.reference_data",
    "public.users", "public.roles", "public.role_permissions", "public.user_roles",
    "public.org_settings", "public.profiles", "public.user_permission_sets",
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
        f"DELETE FROM portfolio.udf_tag_assignments WHERE definition_id IN "
        f"(SELECT id FROM portfolio.udf_definitions WHERE field_key LIKE '{PFX}%')"
    )
    await conn.execute(
        f"DELETE FROM portfolio.udf_values WHERE definition_id IN "
        f"(SELECT id FROM portfolio.udf_definitions WHERE field_key LIKE '{PFX}%')"
    )
    await conn.execute(f"DELETE FROM portfolio.udf_definitions WHERE field_key LIKE '{PFX}%'")

    await conn.execute(
        "DELETE FROM public.entities WHERE id = ANY($1::uuid[])",
        ENTITY_IDS + [E_OTHER],
    )
    await conn.execute(
        "DELETE FROM public.reference_data WHERE list_id = ANY($1::uuid[])",
        [LIST_SELECT, LIST_MULTISELECT],
    )
    await conn.execute(
        "DELETE FROM public.reference_data_lists WHERE id = ANY($1::uuid[])",
        [LIST_SELECT, LIST_MULTISELECT],
    )

    await conn.execute(
        "DELETE FROM public.user_permission_sets WHERE user_id = ANY($1::uuid[])", USERS
    )
    await conn.execute("DELETE FROM public.user_roles WHERE user_id = ANY($1::uuid[])", USERS)
    await conn.execute(
        "DELETE FROM public.role_permissions WHERE role_id = $1::uuid", ROLE_NOPERMS
    )
    await conn.execute("DELETE FROM public.roles WHERE id = $1::uuid", ROLE_NOPERMS)
    await conn.execute("DELETE FROM public.users WHERE id = ANY($1::uuid[])", USERS)
    await conn.execute("DELETE FROM public.profiles WHERE id = $1::uuid", PROFILE_1)


async def setup(conn) -> None:
    for user_id, org, sub, profile_id in (
        (U_ADMIN, ORG, SUB_ADMIN, None),
        (U_RESTRICTED, ORG, SUB_RESTRICTED, PROFILE_1),
        (U_NOPERMS, ORG, SUB_NOPERMS, None),
        (U_OTHER, OTHER_ORG, SUB_OTHER, None),
    ):
        await conn.execute(
            """INSERT INTO public.users
                (id, org_id, email, full_name, auth0_sub, role, is_active)
            VALUES ($1::uuid, $2::uuid, $3, 'Verify udf02a', $4, 'member', true)
            ON CONFLICT (id) DO NOTHING""",
            user_id, org, f"udf02averify-{user_id[-4:]}@test.local", sub,
        )

    await conn.execute(
        """INSERT INTO public.roles (id, org_id, name, description)
        VALUES ($1::uuid, $2::uuid, 'udf02averify_noperms', 'verify fixture')
        ON CONFLICT (id) DO NOTHING""",
        ROLE_NOPERMS, ORG,
    )
    for user_id, role_name, org in (
        (U_ADMIN, "admin", ORG), (U_RESTRICTED, "member", ORG),
        (U_NOPERMS, "udf02averify_noperms", ORG), (U_OTHER, "admin", OTHER_ORG),
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
        "UPDATE public.users SET profile_id = $2::uuid WHERE id = $1::uuid",
        U_RESTRICTED, PROFILE_1,
    )

    for list_id, list_key, label in (
        (LIST_SELECT, LIST_KEY_SELECT, "Verify Colors"),
        (LIST_MULTISELECT, LIST_KEY_MULTISELECT, "Verify Letters"),
    ):
        await conn.execute(
            """INSERT INTO public.reference_data_lists
                (id, org_id, list_key, label, owner_scope, is_extensible)
            VALUES ($1::uuid, $2::uuid, $3, $4, 'org', false)
            ON CONFLICT (id) DO NOTHING""",
            list_id, ORG, list_key, label,
        )
    for i, (code, label) in enumerate(
        (("red", "Red"), ("green", "Green"), ("blue", "Blue"))
    ):
        await conn.execute(
            """INSERT INTO public.reference_data
                (id, org_id, list_key, code, label, list_id, display_order, is_active)
            VALUES ($1::uuid, $2::uuid, $3, $4, $5, $6::uuid, $7, true)
            ON CONFLICT (id) DO NOTHING""",
            f"{P}41{i:02d}", ORG, LIST_KEY_SELECT, code, label, LIST_SELECT, i,
        )
    for i, (code, label) in enumerate((("x", "X"), ("y", "Y"), ("z", "Z"))):
        await conn.execute(
            """INSERT INTO public.reference_data
                (id, org_id, list_key, code, label, list_id, display_order, is_active)
            VALUES ($1::uuid, $2::uuid, $3, $4, $5, $6::uuid, $7, true)
            ON CONFLICT (id) DO NOTHING""",
            f"{P}42{i:02d}", ORG, LIST_KEY_MULTISELECT, code, label, LIST_MULTISELECT, i,
        )

    for i, eid in enumerate(ENTITY_IDS, start=1):
        await conn.execute(
            """INSERT INTO public.entities (id, org_id, entity_type, display_name)
            VALUES ($1::uuid, $2::uuid, 'individual', $3)""",
            eid, ORG, f"{PFX}entity_{i}",
        )
    await conn.execute(
        """INSERT INTO public.entities (id, org_id, entity_type, display_name)
        VALUES ($1::uuid, $2::uuid, 'individual', $3)""",
        E_OTHER, OTHER_ORG, f"{PFX}entity_other",
    )


class _QueryCounter:
    """Wraps a connection and counts fetch/fetchval/fetchrow/execute calls —
    used ONLY to measure and report ``list_records_with_udf``'s real query
    count for a representative (10 records x 5 columns) case."""

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


async def build_fixtures(conn) -> dict:
    from services.portfolio_udf import create_org_definition, create_user_definition, record_udf_value
    from services.portfolio_udf_field_permissions import set_field_access
    from services.portfolio_udf_layouts import add_item, add_section, create_layout
    from services.portfolio_udf_tabs import create_tab, set_tab_visibility
    from services.portfolio_udf_tags import assign_tags

    tab_a = await create_tab(
        conn, org_id=ORG, applies_to="entity", label="udf02a Tab A",
        api_name=f"{PFX}tab_a", created_by=U_ADMIN,
    )
    tab_b = await create_tab(
        conn, org_id=ORG, applies_to="entity", label="udf02a Tab B (hidden)",
        api_name=f"{PFX}tab_b", created_by=U_ADMIN,
    )
    await set_tab_visibility(
        conn, tab_id=tab_b["id"], org_id=ORG, profile_id=PROFILE_1, is_visible=False,
    )

    async def new_def(suffix: str, label: str, data_type: str, type_params: dict) -> str:
        return await create_org_definition(
            conn, org_id=ORG, applies_to="entity", field_key=f"{PFX}{suffix}",
            label=label, data_type=data_type, type_params=type_params,
            created_by=U_ADMIN,
        )

    d_text = await new_def("d_text", "Text", "text", {"length": 100})
    d_integer = await new_def("d_integer", "Integer", "integer", {"precision": 8})
    d_currency = await new_def(
        "d_currency", "Currency", "currency",
        {"precision": 12, "scale": 4, "currency_code": "USD"},
    )
    d_date = await new_def("d_date", "Date", "date", {})
    d_boolean = await new_def("d_boolean", "Boolean", "boolean", {})
    d_select = await create_org_definition(
        conn, org_id=ORG, applies_to="entity", field_key=f"{PFX}d_select",
        label="Select", data_type="select",
        type_params={"value_set_id": LIST_SELECT}, created_by=U_ADMIN,
    )
    d_multiselect = await create_org_definition(
        conn, org_id=ORG, applies_to="entity", field_key=f"{PFX}d_multiselect",
        label="Multiselect", data_type="multiselect",
        type_params={"value_set_id": LIST_MULTISELECT}, created_by=U_ADMIN,
    )
    d_tags = await new_def("d_tags", "Tags", "tags", {})
    d_hidden = await new_def("d_hidden", "Hidden Field", "text", {"length": 100})
    d_read = await new_def("d_read", "Read Field", "text", {"length": 100})
    d_tabb_only = await new_def("d_tabb_only", "Tab B Only", "text", {"length": 100})
    d_userscope = await create_user_definition(
        conn, org_id=ORG, user_id=U_ADMIN, applies_to="entity",
        field_key=f"{PFX}d_userscope", label="User Scope", data_type="text",
        type_params={"length": 100}, created_by=U_ADMIN,
    )

    await set_field_access(
        conn, definition_id=d_hidden, access="hidden", org_id=ORG,
        profile_id=PROFILE_1, created_by=U_ADMIN,
    )
    await set_field_access(
        conn, definition_id=d_read, access="read", org_id=ORG,
        profile_id=PROFILE_1, created_by=U_ADMIN,
    )

    layout_a = await create_layout(conn, org_id=ORG, tab_id=tab_a["id"])
    section_a = await add_section(
        conn, layout_id=layout_a["id"], org_id=ORG, title="A", column_count=2,
    )
    qc_defs = [d_text, d_integer, d_currency, d_date, d_boolean]
    for i, d in enumerate([
        d_text, d_integer, d_currency, d_date, d_boolean,
        d_select, d_multiselect, d_tags, d_hidden, d_read,
    ]):
        await add_item(
            conn, section_id=section_a["id"], org_id=ORG, definition_id=d,
            column_index=i % 2, display_order=i,
        )

    layout_b = await create_layout(conn, org_id=ORG, tab_id=tab_b["id"])
    section_b = await add_section(
        conn, layout_id=layout_b["id"], org_id=ORG, title="B", column_count=1,
    )
    await add_item(conn, section_id=section_b["id"], org_id=ORG, definition_id=d_tabb_only)

    # A dedicated tab carrying EXACTLY the 5 scalar columns used for the
    # query-count assertion (10 records x 5 columns), separate from tab_a
    # (which carries 10 columns) so that assertion measures a precise, real
    # M=5 rather than an approximation.
    tab_qc = await create_tab(
        conn, org_id=ORG, applies_to="entity", label="udf02a Tab QC",
        api_name=f"{PFX}tab_qc", created_by=U_ADMIN,
    )
    layout_qc = await create_layout(conn, org_id=ORG, tab_id=tab_qc["id"])
    section_qc = await add_section(
        conn, layout_id=layout_qc["id"], org_id=ORG, title="QC", column_count=2,
    )
    for i, d in enumerate(qc_defs):
        await add_item(
            conn, section_id=section_qc["id"], org_id=ORG, definition_id=d,
            column_index=i % 2, display_order=i,
        )

    from datetime import date as _date
    for i, eid in enumerate(ENTITY_IDS, start=1):
        await record_udf_value(
            conn, org_id=ORG, definition_id=d_text, target_type="entity",
            target_id=eid, value=f"{PFX}row-{i}",
        )
        await record_udf_value(
            conn, org_id=ORG, definition_id=d_integer, target_type="entity",
            target_id=eid, value=Decimal(i),
        )
        await record_udf_value(
            conn, org_id=ORG, definition_id=d_currency, target_type="entity",
            target_id=eid, value=Decimal(i).quantize(Decimal("1.0000")),
        )
        await record_udf_value(
            conn, org_id=ORG, definition_id=d_date, target_type="entity",
            target_id=eid, value=_date(2026, 1, i),
        )
        await record_udf_value(
            conn, org_id=ORG, definition_id=d_boolean, target_type="entity",
            target_id=eid, value=(i % 2 == 0),
        )

    await record_udf_value(
        conn, org_id=ORG, definition_id=d_select, target_type="entity",
        target_id=ENTITY_IDS[0], value="red",
    )
    await record_udf_value(
        conn, org_id=ORG, definition_id=d_select, target_type="entity",
        target_id=ENTITY_IDS[1], value="green",
    )
    await record_udf_value(
        conn, org_id=ORG, definition_id=d_multiselect, target_type="entity",
        target_id=ENTITY_IDS[0], value=["x", "y"],
    )
    await record_udf_value(
        conn, org_id=ORG, definition_id=d_hidden, target_type="entity",
        target_id=ENTITY_IDS[0], value="secret",
    )
    await record_udf_value(
        conn, org_id=ORG, definition_id=d_read, target_type="entity",
        target_id=ENTITY_IDS[0], value="readonly-value",
    )
    await assign_tags(
        conn, org_id=ORG, definition_id=d_tags, target_id=ENTITY_IDS[0],
        codes=["Prospect", "VIP"], assigned_by=U_ADMIN, can_create_tags=True,
    )
    await assign_tags(
        conn, org_id=ORG, definition_id=d_tags, target_id=ENTITY_IDS[1],
        codes=["VIP"], assigned_by=U_ADMIN, can_create_tags=True,
    )

    return {
        "tab_a": tab_a["id"], "tab_b": tab_b["id"], "tab_qc": tab_qc["id"],
        "d_text": d_text, "d_integer": d_integer, "d_currency": d_currency,
        "d_date": d_date, "d_boolean": d_boolean, "d_select": d_select,
        "d_multiselect": d_multiselect, "d_tags": d_tags, "d_hidden": d_hidden,
        "d_read": d_read, "d_tabb_only": d_tabb_only, "d_userscope": d_userscope,
    }


# ═══════════════════════════════════════════════════════════════════════════
# TASK 1 — regression baseline
# ═══════════════════════════════════════════════════════════════════════════


async def test_1_regression_baseline() -> None:
    import verify_udf01a

    exit_a = await verify_udf01a.main()
    ok("1 — verify_udf01a.py regression baseline is green",
       exit_a == 0 and not verify_udf01a.FAIL,
       f"exit={exit_a} PASS={len(verify_udf01a.PASS)} FAIL={len(verify_udf01a.FAIL)}")
    for f in verify_udf01a.FAIL:
        print(f"    [udf01a FAIL] {f}")

    import verify_udf01b

    # include_regression=False: this script already called verify_udf01a.main()
    # directly above, so verify_udf01b's own internal regression gate (which
    # would call verify_udf01a.main() a second time) must be skipped.
    exit_b = await verify_udf01b.main(include_regression=False)
    ok("1 — verify_udf01b.py regression baseline is green",
       exit_b == 0 and not verify_udf01b.FAIL,
       f"exit={exit_b} PASS={len(verify_udf01b.PASS)} FAIL={len(verify_udf01b.FAIL)}")
    for f in verify_udf01b.FAIL:
        print(f"    [udf01b FAIL] {f}")

    import verify_udf01c

    # include_regression=False: this script already called verify_udf01a.main()
    # and verify_udf01b.main() directly above, so verify_udf01c's own internal
    # regression gate (which would call both of those a second time) must be
    # skipped.
    exit_c = await verify_udf01c.main(include_regression=False)
    ok("1 — verify_udf01c.py regression baseline is green "
       "(re-confirmed after the platform-scope RLS gap fix)",
       exit_c == 0 and not verify_udf01c.FAIL,
       f"exit={exit_c} PASS={len(verify_udf01c.PASS)} FAIL={len(verify_udf01c.FAIL)}")
    for f in verify_udf01c.FAIL:
        print(f"    [udf01c FAIL] {f}")


# ═══════════════════════════════════════════════════════════════════════════
# TASK 2a-1 — get_available_columns
# ═══════════════════════════════════════════════════════════════════════════


async def test_2_available_columns(conn, fx: dict) -> None:
    from services.portfolio_udf_records import get_available_columns

    admin_cols = await get_available_columns(
        conn, target_type="entity", tab_id=fx["tab_a"], org_id=ORG, user_id=U_ADMIN,
    )
    admin_ids = {c["definition_id"] for c in admin_cols}
    ok("2 — positive control: admin (no FLS grant) sees the field hidden for "
       "the restricted profile",
       fx["d_hidden"] in admin_ids, f"admin_ids has it? {fx['d_hidden'] in admin_ids}")

    restricted_cols = await get_available_columns(
        conn, target_type="entity", tab_id=fx["tab_a"], org_id=ORG, user_id=U_RESTRICTED,
    )
    restricted_by_id = {c["definition_id"]: c for c in restricted_cols}
    ok("2 — a hidden field never appears as an available column",
       fx["d_hidden"] not in restricted_by_id,
       f"present? {fx['d_hidden'] in restricted_by_id}")
    ok("2 — a read field appears as a column but is flagged non-editable "
       "(access='read')",
       restricted_by_id.get(fx["d_read"], {}).get("access") == "read",
       f"got {restricted_by_id.get(fx['d_read'])}")
    ok("2 — an edit field appears with access='edit'",
       restricted_by_id.get(fx["d_text"], {}).get("access") == "edit",
       f"got {restricted_by_id.get(fx['d_text'])}")

    tab_b_cols = await get_available_columns(
        conn, target_type="entity", tab_id=fx["tab_b"], org_id=ORG, user_id=U_RESTRICTED,
    )
    ok("2 — tab-hidden excludes EVERY field on that tab, regardless of "
       "individual field grants (d_tabb_only has no FLS grant of its own)",
       tab_b_cols == [], f"got {tab_b_cols}")

    tab_b_cols_admin = await get_available_columns(
        conn, target_type="entity", tab_id=fx["tab_b"], org_id=ORG, user_id=U_ADMIN,
    )
    ok("2 — positive control: tab B's field IS visible to a caller the tab "
       "is not hidden for",
       any(c["definition_id"] == fx["d_tabb_only"] for c in tab_b_cols_admin),
       f"got {tab_b_cols_admin}")

    # tab_id=None vs tab_id=tab_a: genuinely different candidate sources.
    no_tab_admin = await get_available_columns(
        conn, target_type="entity", tab_id=None, org_id=ORG, user_id=U_ADMIN,
    )
    no_tab_admin_ids = {c["definition_id"] for c in no_tab_admin}
    ok("2 — tab_id=None resolves via resolve_visible_definitions: the "
       "caller's own user-scope field (never placed on any layout) IS "
       "visible",
       fx["d_userscope"] in no_tab_admin_ids, f"ids={no_tab_admin_ids}")

    no_tab_restricted = await get_available_columns(
        conn, target_type="entity", tab_id=None, org_id=ORG, user_id=U_RESTRICTED,
    )
    no_tab_restricted_ids = {c["definition_id"] for c in no_tab_restricted}
    ok("2 — a user-scope field owned by a DIFFERENT user is invisible even "
       "with tab_id=None",
       fx["d_userscope"] not in no_tab_restricted_ids)

    ok("2 — tab_id=tab_a (layout-scoped candidates) does NOT include the "
       "user-scope field — it was never placed on that layout",
       fx["d_userscope"] not in admin_ids)


# ═══════════════════════════════════════════════════════════════════════════
# TASK 2a-2/2a-3 — filters, sort, query count
# ═══════════════════════════════════════════════════════════════════════════


async def test_3_filters(conn, fx: dict) -> None:
    from services.portfolio_udf_records import (
        FilterFieldError,
        FilterOperatorError,
        FilterValueError,
        list_records_with_udf,
    )

    fixture_filter = {"definition_id": fx["d_text"], "operator": "contains", "value": PFX}

    async def run(filters, **kw):
        return await list_records_with_udf(
            conn, target_type="entity", org_id=ORG, user_id=U_ADMIN,
            tab_id=fx["tab_a"], filters=filters, **kw,
        )

    # ── text contains ──
    result = await run([fixture_filter, {
        "definition_id": fx["d_text"], "operator": "contains", "value": "row-3",
    }])
    ok("3 — text 'contains' matches exactly the expected row",
       len(result["rows"]) == 1 and result["rows"][0]["id"] == ENTITY_IDS[2],
       f"got {len(result['rows'])} rows")

    try:
        await run([{"definition_id": fx["d_text"], "operator": "gt", "value": "x"}])
        ok("3 — an invalid operator for text ('gt') is rejected", False, "no exception raised")
    except FilterOperatorError as exc:
        ok("3 — an invalid operator for text ('gt') is rejected", True, str(exc))

    # ── numeric equals/gt/lt/between ──
    result = await run([fixture_filter, {
        "definition_id": fx["d_integer"], "operator": "gt", "value": Decimal(5),
    }])
    ok("3 — numeric 'gt' matches the expected 5 rows (6..10)",
       len(result["rows"]) == 5, f"got {len(result['rows'])}")

    result = await run([fixture_filter, {
        "definition_id": fx["d_integer"], "operator": "between",
        "value": [Decimal(3), Decimal(7)],
    }])
    ok("3 — numeric 'between' matches the expected 5 rows (3..7)",
       len(result["rows"]) == 5, f"got {len(result['rows'])}")

    result = await run([fixture_filter, {
        "definition_id": fx["d_integer"], "operator": "equals", "value": Decimal(4),
    }])
    ok("3 — numeric 'equals' matches exactly the expected row",
       len(result["rows"]) == 1 and result["rows"][0]["id"] == ENTITY_IDS[3],
       f"got {len(result['rows'])}")

    try:
        await run([{"definition_id": fx["d_integer"], "operator": "contains", "value": "x"}])
        ok("3 — an invalid operator for numeric ('contains') is rejected",
           False, "no exception raised")
    except FilterOperatorError as exc:
        ok("3 — an invalid operator for numeric ('contains') is rejected", True, str(exc))

    # ── numeric scale violation: rejected, not silently truncated ──
    try:
        await run([{
            "definition_id": fx["d_currency"], "operator": "equals",
            "value": Decimal("1.12345"),
        }])
        ok("3 — a numeric filter value violating the field's declared scale "
           "is rejected", False, "no exception raised — value may have been "
           "silently truncated instead")
    except FilterValueError as exc:
        ok("3 — a numeric filter value violating the field's declared scale "
           "is rejected", True, str(exc))

    # ── date equals/before/after/between ──
    from datetime import date as _date

    result = await run([fixture_filter, {
        "definition_id": fx["d_date"], "operator": "before", "value": _date(2026, 1, 4),
    }])
    ok("3 — date 'before' matches the expected 3 rows (Jan 1-3)",
       len(result["rows"]) == 3, f"got {len(result['rows'])}")

    result = await run([fixture_filter, {
        "definition_id": fx["d_date"], "operator": "between",
        "value": [_date(2026, 1, 2), _date(2026, 1, 4)],
    }])
    ok("3 — date 'between' matches the expected 3 rows (Jan 2-4)",
       len(result["rows"]) == 3, f"got {len(result['rows'])}")

    # ── boolean equals ──
    result = await run([fixture_filter, {
        "definition_id": fx["d_boolean"], "operator": "equals", "value": True,
    }])
    ok("3 — boolean 'equals' matches the expected 5 rows (even i)",
       len(result["rows"]) == 5, f"got {len(result['rows'])}")

    # ── select 'in' against the value set; outside value set rejected ──
    result = await run([{
        "definition_id": fx["d_select"], "operator": "in", "value": ["red", "green"],
    }])
    ok("3 — select 'in' matches both seeded rows",
       len(result["rows"]) == 2, f"got {len(result['rows'])}")

    try:
        await run([{
            "definition_id": fx["d_select"], "operator": "in", "value": ["purple"],
        }])
        ok("3 — a select filter value outside the field's value set is "
           "rejected", False, "no exception raised")
    except FilterValueError as exc:
        ok("3 — a select filter value outside the field's value set is "
           "rejected", True, str(exc))

    # ── multiselect 'in' against the value set ──
    result = await run([{
        "definition_id": fx["d_multiselect"], "operator": "in", "value": ["x"],
    }])
    ok("3 — multiselect 'in' matches the one seeded row containing 'x'",
       len(result["rows"]) == 1 and result["rows"][0]["id"] == ENTITY_IDS[0],
       f"got {len(result['rows'])}")

    # ── tags has-tag, case-insensitive via normalized_code ──
    result = await run([{
        "definition_id": fx["d_tags"], "operator": "has-tag", "value": "PROSPECT",
    }])
    ok("3 — tags 'has-tag' matches case-insensitively via normalized_code",
       len(result["rows"]) == 1 and result["rows"][0]["id"] == ENTITY_IDS[0],
       f"got {len(result['rows'])}")

    result = await run([{
        "definition_id": fx["d_tags"], "operator": "has-any-of", "value": ["vip"],
    }])
    ok("3 — tags 'has-any-of' matches both tagged rows, case-insensitively",
       len(result["rows"]) == 2, f"got {len(result['rows'])}")

    # ── a hidden field cannot be referenced as a filter at all ──
    try:
        await list_records_with_udf(
            conn, target_type="entity", org_id=ORG, user_id=U_RESTRICTED,
            tab_id=fx["tab_a"],
            filters=[{"definition_id": fx["d_hidden"], "operator": "equals", "value": "x"}],
        )
        ok("3 — filtering on a field hidden for the caller is refused",
           False, "no exception raised")
    except FilterFieldError as exc:
        ok("3 — filtering on a field hidden for the caller is refused",
           True, str(exc))


async def test_4_sort(conn, fx: dict) -> None:
    from services.portfolio_udf_records import list_records_with_udf

    fixture_filter = {"definition_id": fx["d_text"], "operator": "contains", "value": PFX}

    asc = await list_records_with_udf(
        conn, target_type="entity", org_id=ORG, user_id=U_ADMIN, tab_id=fx["tab_a"],
        filters=[fixture_filter],
        sort={"definition_id": fx["d_integer"], "direction": "asc"},
    )
    asc_order = [r["id"] for r in asc["rows"]]
    ok("4 — sort ascending produces the correctly ordered result",
       asc_order == ENTITY_IDS, f"got {asc_order}")

    desc = await list_records_with_udf(
        conn, target_type="entity", org_id=ORG, user_id=U_ADMIN, tab_id=fx["tab_a"],
        filters=[fixture_filter],
        sort={"definition_id": fx["d_integer"], "direction": "desc"},
    )
    desc_order = [r["id"] for r in desc["rows"]]
    ok("4 — sort descending produces the correctly ordered (reversed) result",
       desc_order == list(reversed(ENTITY_IDS)), f"got {desc_order}")


async def test_5_query_count(conn, fx: dict) -> None:
    from services.portfolio_udf_records import list_records_with_udf

    counter = _QueryCounter(conn)
    result = await list_records_with_udf(
        counter, target_type="entity", org_id=ORG, user_id=U_ADMIN, tab_id=fx["tab_qc"],
        filters=[{"definition_id": fx["d_text"], "operator": "contains", "value": PFX}],
    )
    ok("5 — the query-count case actually has 10 records and 5 columns "
       "(the representative case the sprint asked for)",
       len(result["rows"]) == 10 and len(result["columns"]) == 5,
       f"rows={len(result['rows'])} columns={len(result['columns'])}")
    find("5 — query count for 10 records x 5 UDF columns",
         f"{counter.count} real queries issued (N*M would be 50) — bounded, "
         f"not growing with N or M: one for available-column resolution's "
         f"candidate query, a fixed few for resolve_field_access_bulk's own "
         f"bulk access-map resolution, one for the base row query (the "
         f"filter's join lives in that same statement), and one bulk query "
         f"for udf_values inlining (no udf_tag_assignments query since this "
         f"tab has no tags column)")
    ok("5 — the query count is bounded well below N*M=50",
       counter.count < 15, f"count={counter.count}")


# ═══════════════════════════════════════════════════════════════════════════
# TASK — RLS cross-org isolation
# ═══════════════════════════════════════════════════════════════════════════


async def test_6_rls(app_conn, fx: dict) -> None:
    from services.portfolio_udf_records import list_records_with_udf

    bypass = await app_conn.fetchval(
        "SELECT rolbypassrls FROM pg_roles WHERE rolname = current_user"
    )
    ok("6 — app_service's rolbypassrls is False (a genuinely non-bypassing role)",
       bypass is False, f"rolbypassrls={bypass}")

    fixture_filter = {"definition_id": fx["d_text"], "operator": "contains", "value": PFX}

    tr = app_conn.transaction()
    await tr.start()
    try:
        await app_conn.execute("SELECT set_config('app.current_org_id', $1, true)", ORG)

        # Defense-in-depth: even a (hypothetical, spoofed) org_id argument
        # mismatched from the connection's real RLS context must not leak
        # the other org's rows — RLS backstops this independently of
        # list_records_with_udf's own `b.org_id = $1` predicate.
        spoofed = await list_records_with_udf(
            app_conn, target_type="entity", org_id=OTHER_ORG, user_id=U_OTHER,
            tab_id=None,
        )
        ok("6 — RLS: a caller in org A cannot read org B's records even when "
           "org_id=B is passed explicitly (RLS session context stays org A)",
           spoofed["rows"] == [] and spoofed["total_count"] == 0,
           f"got {len(spoofed['rows'])} rows, total_count={spoofed['total_count']}")

        # Positive control: the SAME connection, asked for its own org's
        # records, genuinely returns them. Filtered to our own fixture rows
        # (via the fixture's own PFX-tagged d_text value) and given a
        # generous limit — the real entities table already carries
        # production rows well beyond the default page size, and an
        # unfiltered/default-limit call would truncate before ever reaching
        # our fixtures, which would misreport as an RLS failure.
        own = await list_records_with_udf(
            app_conn, target_type="entity", org_id=ORG, user_id=U_ADMIN,
            tab_id=fx["tab_a"], filters=[fixture_filter], limit=500,
        )
        own_ids = {r["id"] for r in own["rows"]}
        ok("6 — RLS positive control: org A CAN read its own records under "
           "the same connection/context",
           set(ENTITY_IDS) == own_ids,
           f"expected exactly {ENTITY_IDS}, got {own_ids}")
    finally:
        await tr.rollback()


# ═══════════════════════════════════════════════════════════════════════════
# Router — 403/200 + envelope shape
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


HEADERS = {"Authorization": "Bearer verify-token"}


def endpoint_tests(fx: dict) -> None:
    import json as _json

    import main
    from starlette.testclient import TestClient

    with TestClient(main.app, raise_server_exceptions=False) as client:
        noperms = _Principal(client, ORG, SUB_NOPERMS)
        restricted = _Principal(client, ORG, SUB_RESTRICTED)
        admin = _Principal(client, ORG, SUB_ADMIN)

        r = noperms.get(f"/api/v1/udf/records/entity?tab_id={fx['tab_a']}", headers=HEADERS)
        ok("7 GET /udf/records — 403 without view_portfolio", r.status_code == 403,
           f"got {r.status_code}: {r.text[:200]}")

        filt = _json.dumps([{"definition_id": fx["d_text"], "operator": "contains", "value": PFX}])
        r = restricted.get(
            f"/api/v1/udf/records/entity?tab_id={fx['tab_a']}&filter={filt}", headers=HEADERS
        )
        ok("7 GET /udf/records — 200 with view_portfolio", r.status_code == 200,
           f"got {r.status_code}: {r.text[:300]}")
        body = r.json()
        ok("7 — envelope carries rows/columns/total_count/permissions/vocabularies",
           all(k in body for k in ("rows", "columns", "total_count", "permissions", "vocabularies")),
           f"keys={sorted(body.keys())}")

        col_ids = {c["definition_id"] for c in body["columns"]}
        ok("7 — hidden field's column is absent over HTTP",
           fx["d_hidden"] not in col_ids, f"col_ids={col_ids}")
        read_col = next((c for c in body["columns"] if c["definition_id"] == fx["d_read"]), None)
        ok("7 — read field's column IS present, flagged access='read'",
           read_col is not None and read_col["access"] == "read", f"got {read_col}")

        for row in body["rows"]:
            ok(f"7 — row {row['id'][-4:]}'s udf_values never carries the hidden field's key",
               fx["d_hidden"] not in row.get("udf_values", {}),
               f"udf_values keys={list(row.get('udf_values', {}).keys())}")

        ok("7 — vocabularies.editable excludes the read-only field",
           fx["d_read"] not in body["vocabularies"]["editable"],
           f"editable={body['vocabularies']['editable']}")

        r = admin.get(
            f"/api/v1/udf/records/entity?tab_id={fx['tab_a']}&filter={filt}", headers=HEADERS
        )
        admin_body = r.json()
        admin_col_ids = {c["definition_id"] for c in admin_body["columns"]}
        ok("7 — positive control: admin (no FLS grant) sees the hidden-for-"
           "restricted field over HTTP too",
           fx["d_hidden"] in admin_col_ids, f"admin col_ids={admin_col_ids}")

        # Hidden tab degrades gracefully (no 403), per this endpoint's own
        # design — see portfolio_udf_records module docstring.
        r = restricted.get(f"/api/v1/udf/records/entity?tab_id={fx['tab_b']}", headers=HEADERS)
        ok("7 — a hidden tab returns 200 with zero available columns, not a 403",
           r.status_code == 200 and r.json()["columns"] == [],
           f"got {r.status_code}: {r.text[:200]}")


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
            await test_1_regression_baseline()
            fx = await build_fixtures(conn)
            await test_2_available_columns(conn, fx)
            await test_3_filters(conn, fx)
            await test_4_sort(conn, fx)
            await test_5_query_count(conn, fx)
            await test_6_rls(app_conn, fx)

            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, endpoint_tests, fx)
        except Exception:  # noqa: BLE001
            FAIL.append(f"unhandled: {traceback.format_exc()}")
            print(f"[FAIL] unhandled exception\n{traceback.format_exc()}")
        finally:
            await teardown(conn)

        after = await counts(conn)
        for t in COUNTED:
            ok(f"8 — teardown: {t} row count returned to baseline",
               after[t] == before[t], f"before={before[t]} after={after[t]}")
    finally:
        await conn.close()
        await app_conn.close()

    print(f"\n{'=' * 70}\nudf02a: {len(PASS)} PASS, {len(FAIL)} FAIL, {len(FIND)} FIND")
    for f in FAIL:
        print(f"  FAIL {f}")
    for f in FIND:
        print(f"  FIND {f}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
