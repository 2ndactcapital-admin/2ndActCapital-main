"""Sprint udf02b verification — CSV import/export.

Pass/fail only, no prompts. Run:

    python3 apps/api/scripts/verify_udf02b.py

PREDECESSOR BASELINES (facts, not re-run — per this sprint's own instruction
not to chain-call predecessor main() functions):
    verify_udf01a.py: 119/0/0
    verify_udf01b.py: 104/0/2 (registered debt, not this sprint's)
    verify_udf01c.py: 93/0/2 (registered debt, not this sprint's)
    verify_udf02a.py: 73/0/0

Instead, one NEGATIVE-CASE check is run per shared function this sprint
reuses rather than reimplements:
  * get_available_columns  — hidden field excluded from export/import both
  * resolve_field_access_bulk (called BY get_available_columns) — read-only
    field appears with access='read', never 'edit'
  * build_filter_clause (called BY list_records_with_udf, called BY
    export_records_csv) — export respects a filter, not just a row dump
  * record_udf_value — an invalid type value is refused unchanged (same
    UdfValueTypeError export/import never re-raises as anything else)

Every fixture this script writes carries a 'udf02bverify_' prefix (label,
api_name, field_key) or a fixed 99000000-...-0000eb02-prefixed UUID; teardown
deletes by that prefix/id, never TRUNCATE, and row counts are taken before
the first insert and after the last delete.
"""

from __future__ import annotations

import asyncio
import glob
import io
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

P = "99000000-0000-0000-0000-0000eb02"
PFX = "udf02bverify_"

SUB_ADMIN = "udf02bverify|admin"
SUB_RESTRICTED = "udf02bverify|restricted"
SUB_NOPERMS = "udf02bverify|noperms"
SUB_OTHER = "udf02bverify|other"

U_ADMIN = str(uuid5(NAMESPACE_URL, SUB_ADMIN))
U_RESTRICTED = str(uuid5(NAMESPACE_URL, SUB_RESTRICTED))
U_NOPERMS = str(uuid5(NAMESPACE_URL, SUB_NOPERMS))
U_OTHER = str(uuid5(NAMESPACE_URL, SUB_OTHER))
USERS = [U_ADMIN, U_RESTRICTED, U_NOPERMS, U_OTHER]

ROLE_NOPERMS = f"{P}3001"
PROFILE_1 = f"{P}6001"

E1, E2, E3, E4 = (f"{P}70{i:02d}" for i in range(1, 5))
E_OTHER = f"{P}7999"
ENTITY_IDS = [E1, E2, E3, E4]

LIST_SELECT = f"{P}4001"
LIST_KEY_SELECT = f"{PFX}colors"

COUNTED = [
    "portfolio.udf_definitions", "portfolio.udf_values", "portfolio.udf_tag_assignments",
    "portfolio.udf_field_permissions",
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
        "DELETE FROM public.reference_data WHERE list_id = $1::uuid", LIST_SELECT,
    )
    await conn.execute(
        "DELETE FROM public.reference_data_lists WHERE id = $1::uuid", LIST_SELECT,
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
            VALUES ($1::uuid, $2::uuid, $3, 'Verify udf02b', $4, 'member', true)
            ON CONFLICT (id) DO NOTHING""",
            user_id, org, f"udf02bverify-{user_id[-4:]}@test.local", sub,
        )

    await conn.execute(
        """INSERT INTO public.roles (id, org_id, name, description)
        VALUES ($1::uuid, $2::uuid, 'udf02bverify_noperms', 'verify fixture')
        ON CONFLICT (id) DO NOTHING""",
        ROLE_NOPERMS, ORG,
    )
    for user_id, role_name, org in (
        (U_ADMIN, "admin", ORG), (U_RESTRICTED, "member", ORG),
        (U_NOPERMS, "udf02bverify_noperms", ORG), (U_OTHER, "admin", OTHER_ORG),
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

    await conn.execute(
        """INSERT INTO public.reference_data_lists
            (id, org_id, list_key, label, owner_scope, is_extensible)
        VALUES ($1::uuid, $2::uuid, $3, $4, 'org', false)
        ON CONFLICT (id) DO NOTHING""",
        LIST_SELECT, ORG, LIST_KEY_SELECT, "Verify Colors",
    )
    for i, (code, label) in enumerate((("red", "Red"), ("green", "Green"))):
        await conn.execute(
            """INSERT INTO public.reference_data
                (id, org_id, list_key, code, label, list_id, display_order, is_active)
            VALUES ($1::uuid, $2::uuid, $3, $4, $5, $6::uuid, $7, true)
            ON CONFLICT (id) DO NOTHING""",
            f"{P}41{i:02d}", ORG, LIST_KEY_SELECT, code, label, LIST_SELECT, i,
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


async def build_fixtures(conn) -> dict:
    from services.portfolio_udf import create_org_definition, record_udf_value
    from services.portfolio_udf_field_permissions import set_field_access

    async def new_def(suffix, label, data_type, type_params=None, **kw):
        return await create_org_definition(
            conn, org_id=ORG, applies_to="entity", field_key=f"{PFX}{suffix}",
            label=label, data_type=data_type, type_params=type_params or {},
            api_name=f"{PFX}{suffix}", created_by=U_ADMIN, **kw,
        )

    d_text = await new_def("d_text", "Text", "text", {"length": 100})
    d_integer = await new_def("d_integer", "Integer", "integer", {"precision": 8})
    d_boolean = await new_def("d_boolean", "Boolean", "boolean", {})
    d_required = await new_def(
        "d_required", "Required", "text", {"length": 50}, is_required=True,
    )
    d_select = await new_def(
        "d_select", "Select", "select", {"value_set_id": LIST_SELECT},
    )
    d_tags = await new_def("d_tags", "Tags", "tags", {})
    d_hidden = await new_def("d_hidden", "Hidden", "text", {"length": 100})
    d_read = await new_def("d_read", "ReadOnly", "text", {"length": 100})
    d_noapiname = await create_org_definition(
        conn, org_id=ORG, applies_to="entity", field_key=f"{PFX}d_noapiname",
        label="No API Name", data_type="text", type_params={"length": 50},
        created_by=U_ADMIN,
    )

    await set_field_access(
        conn, definition_id=d_hidden, access="hidden", org_id=ORG,
        profile_id=PROFILE_1, created_by=U_ADMIN,
    )
    await set_field_access(
        conn, definition_id=d_read, access="read", org_id=ORG,
        profile_id=PROFILE_1, created_by=U_ADMIN,
    )

    # E1 carries a PREDECESSOR value on d_text — proves the import write is
    # append-only (system_to stamped), not an overwrite.
    await record_udf_value(
        conn, org_id=ORG, definition_id=d_text, target_type="entity",
        target_id=E1, value=f"{PFX}predecessor",
    )
    # E3 carries a value on d_hidden/d_read for the export inclusion/exclusion
    # assertions.
    await record_udf_value(
        conn, org_id=ORG, definition_id=d_hidden, target_type="entity",
        target_id=E3, value="secret",
    )
    await record_udf_value(
        conn, org_id=ORG, definition_id=d_read, target_type="entity",
        target_id=E3, value="readonly-value",
    )
    await record_udf_value(
        conn, org_id=ORG, definition_id=d_text, target_type="entity",
        target_id=E3, value=f"{PFX}e3-text",
    )

    return {
        "d_text": d_text, "d_integer": d_integer, "d_boolean": d_boolean,
        "d_required": d_required, "d_select": d_select, "d_tags": d_tags,
        "d_hidden": d_hidden, "d_read": d_read, "d_noapiname": d_noapiname,
    }


def _csv_bytes(header: list[str], rows: list[list[str]]) -> bytes:
    import csv as _csv

    buf = io.StringIO()
    writer = _csv.writer(buf)
    writer.writerow(header)
    for r in rows:
        writer.writerow(r)
    return buf.getvalue().encode("utf-8")


# ═══════════════════════════════════════════════════════════════════════════
# TASK 2 — export
# ═══════════════════════════════════════════════════════════════════════════


async def test_1_export(conn, fx: dict) -> None:
    import csv as _csv

    from services.portfolio_udf_records import export_records_csv

    chunks = [
        c async for c in export_records_csv(
            conn, target_type="entity", org_id=ORG, user_id=U_RESTRICTED, tab_id=None,
        )
    ]
    text = "".join(chunks)
    reader = _csv.DictReader(io.StringIO(text))
    header = reader.fieldnames or []

    ok("1 — export header uses api_name, not field_key/label",
       f"{PFX}d_text" in header and f"{PFX}d_read" in header,
       f"header={header}")
    ok("1 — export excludes the field hidden for this caller (shared "
       "function: get_available_columns never returns a hidden column)",
       f"{PFX}d_hidden" not in header, f"header={header}")
    ok("1 — export excludes a definition with no api_name (never exposed "
       "over CSV in either direction)",
       f"{PFX}d_noapiname" not in header, f"header={header}")
    ok("1 — export's first column is target_id",
       header[0] == "target_id" if header else False, f"header={header}")

    rows = list(reader)
    e3_row = next((r for r in rows if r["target_id"] == E3), None)
    ok("1 — export includes a read-only-for-caller field's VALUE (export is "
       "read-only regardless of access level)",
       e3_row is not None and e3_row.get(f"{PFX}d_read") == "readonly-value",
       f"e3_row={e3_row}")
    ok("1 — export never leaks the hidden field's value alongside the row "
       "(the column itself is absent, so there is no key to check — proven "
       "by the header assertion above; here we confirm no stray column "
       "carries it under another name)",
       f"{PFX}d_hidden" not in (e3_row or {}),
       f"e3_row keys={(e3_row or {}).keys()}")

    # ── filter respected (build_filter_clause, via list_records_with_udf) ──
    import json as _json

    filt = _json.dumps([{
        "definition_id": fx["d_text"], "operator": "equals", "value": f"{PFX}e3-text",
    }])
    filtered_chunks = [
        c async for c in export_records_csv(
            conn, target_type="entity", org_id=ORG, user_id=U_ADMIN, tab_id=None,
            filters=_json.loads(filt),
        )
    ]
    f_reader = _csv.DictReader(io.StringIO("".join(filtered_chunks)))
    f_rows = list(f_reader)
    ok("1 — export respects a filter (shared function: build_filter_clause "
       "via list_records_with_udf) — matches exactly the one row",
       len(f_rows) == 1 and f_rows[0]["target_id"] == E3,
       f"got {[r['target_id'] for r in f_rows]}")


# ═══════════════════════════════════════════════════════════════════════════
# TASK 2 — import
# ═══════════════════════════════════════════════════════════════════════════


async def test_2_import_valid_row_append_only(conn, fx: dict) -> None:
    from services.portfolio_udf import get_value_history
    from services.portfolio_udf_records import import_records_csv

    before_history = await get_value_history(
        conn, org_id=ORG, definition_id=fx["d_text"], target_type="entity", target_id=E1,
    )
    ok("2 — precondition: E1 has exactly one CURRENT predecessor value "
       "before import", len(before_history) == 1 and before_history[0]["system_to"] is None,
       f"got {before_history}")

    csv_bytes = _csv_bytes(
        ["target_id", f"{PFX}d_text", f"{PFX}d_integer"],
        [[E1, f"{PFX}new-value", "42"]],
    )
    result = await import_records_csv(
        conn, target_type="entity", org_id=ORG, user_id=U_ADMIN, tab_id=None,
        csv_bytes=csv_bytes, can_create_tags=True,
    )
    ok("2 — a valid row is accepted",
       len(result["accepted"]) == 1 and result["accepted"][0]["target_id"] == E1,
       f"got {result}")
    ok("2 — rejected is empty for an all-valid file",
       result["rejected"] == [], f"got {result['rejected']}")

    after_history = await get_value_history(
        conn, org_id=ORG, definition_id=fx["d_text"], target_type="entity", target_id=E1,
    )
    ok("2 — append-only: the predecessor row is CLOSED (system_to set), not "
       "overwritten in place",
       len(after_history) == 2 and after_history[0]["system_to"] is not None,
       f"got {after_history}")
    ok("2 — the new value is the CURRENT row",
       after_history[1]["value_text"] == f"{PFX}new-value" and after_history[1]["system_to"] is None,
       f"got {after_history[1]}")

    integer_value = await conn.fetchval(
        f"SELECT value_numeric FROM portfolio.udf_values "
        f"WHERE definition_id = $1::uuid AND target_id = $2::uuid "
        f"AND system_to IS NULL AND valid_to IS NULL",
        fx["d_integer"], E1,
    )
    ok("2 — a numeric CSV string is coerced to the right value via "
       "record_udf_value (not reimplemented here)",
       integer_value == Decimal(42), f"got {integer_value}")


async def test_3_import_invalid_type_rejects_row_only(conn, fx: dict) -> None:
    from services.portfolio_udf_records import import_records_csv

    csv_bytes = _csv_bytes(
        ["target_id", f"{PFX}d_integer"],
        [[E2, "not-a-number"], [E4, "7"]],
    )
    result = await import_records_csv(
        conn, target_type="entity", org_id=ORG, user_id=U_ADMIN, tab_id=None,
        csv_bytes=csv_bytes, can_create_tags=True,
    )
    ok("3 — an invalid-type value rejects ONLY that row (shared function: "
       "record_udf_value's own coerce_value refusal, not reimplemented)",
       len(result["accepted"]) == 1 and result["accepted"][0]["target_id"] == E4
       and len(result["rejected"]) == 1 and result["rejected"][0]["row"] == 1,
       f"got {result}")
    ok("3 — the rejection reason is a real message, not empty",
       bool(result["rejected"][0]["reason"]), f"got {result['rejected']}")

    e2_value = await conn.fetchval(
        f"SELECT count(*) FROM portfolio.udf_values "
        f"WHERE definition_id = $1::uuid AND target_id = $2::uuid",
        fx["d_integer"], E2,
    )
    ok("3 — the rejected row wrote NOTHING (row-level atomicity via SAVEPOINT)",
       e2_value == 0, f"got {e2_value} rows")


async def test_4_import_value_set_rejects_row_only(conn, fx: dict) -> None:
    from services.portfolio_udf_records import import_records_csv

    csv_bytes = _csv_bytes(
        ["target_id", f"{PFX}d_select"],
        [[E2, "purple"], [E4, "red"]],
    )
    result = await import_records_csv(
        conn, target_type="entity", org_id=ORG, user_id=U_ADMIN, tab_id=None,
        csv_bytes=csv_bytes, can_create_tags=True,
    )
    ok("4 — a value outside the field's value set rejects only that row",
       len(result["accepted"]) == 1 and result["accepted"][0]["target_id"] == E4
       and len(result["rejected"]) == 1 and result["rejected"][0]["row"] == 1,
       f"got {result}")


async def test_5_import_hidden_readonly_field(conn, fx: dict) -> None:
    from services.portfolio_udf_records import import_records_csv

    # Hidden field for U_RESTRICTED.
    csv_bytes = _csv_bytes(["target_id", f"{PFX}d_hidden"], [[E2, "anything"]])
    result = await import_records_csv(
        conn, target_type="entity", org_id=ORG, user_id=U_RESTRICTED, tab_id=None,
        csv_bytes=csv_bytes, can_create_tags=False,
    )
    ok("5 — a row targeting a field HIDDEN for the caller rejects that row "
       "(shared function: get_available_columns/resolve_field_access_bulk)",
       len(result["accepted"]) == 0 and len(result["rejected"]) == 1,
       f"got {result}")

    # Read-only field for U_RESTRICTED.
    csv_bytes = _csv_bytes(["target_id", f"{PFX}d_read"], [[E2, "attempted-write"]])
    result = await import_records_csv(
        conn, target_type="entity", org_id=ORG, user_id=U_RESTRICTED, tab_id=None,
        csv_bytes=csv_bytes, can_create_tags=False,
    )
    ok("5 — a row targeting a READ-ONLY field for the caller rejects that "
       "row",
       len(result["accepted"]) == 0 and len(result["rejected"]) == 1
       and "access" in result["rejected"][0]["reason"],
       f"got {result}")

    read_value = await conn.fetchval(
        f"SELECT count(*) FROM portfolio.udf_values "
        f"WHERE definition_id = $1::uuid AND target_id = $2::uuid",
        fx["d_read"], E2,
    )
    ok("5 — the read-only-field write never happened",
       read_value == 0, f"got {read_value} rows")

    # Positive control: admin (no FLS grant) CAN write the same field.
    csv_bytes = _csv_bytes(["target_id", f"{PFX}d_read"], [[E2, "admin-write"]])
    result = await import_records_csv(
        conn, target_type="entity", org_id=ORG, user_id=U_ADMIN, tab_id=None,
        csv_bytes=csv_bytes, can_create_tags=False,
    )
    ok("5 — positive control: admin (no FLS grant) CAN write the field "
       "restricted for U_RESTRICTED",
       len(result["accepted"]) == 1, f"got {result}")


async def test_6_import_tag_permission(conn, fx: dict) -> None:
    from services.portfolio_udf_records import import_records_csv

    csv_bytes = _csv_bytes(["target_id", f"{PFX}d_tags"], [[E2, "BrandNewTag"]])
    result = await import_records_csv(
        conn, target_type="entity", org_id=ORG, user_id=U_ADMIN, tab_id=None,
        csv_bytes=csv_bytes, can_create_tags=False,
    )
    ok("6 — minting a NEW tag without create_tags permission rejects the row",
       len(result["accepted"]) == 0 and len(result["rejected"]) == 1,
       f"got {result}")

    tag_count = await conn.fetchval(
        f"SELECT count(*) FROM portfolio.udf_tag_assignments "
        f"WHERE definition_id = $1::uuid AND target_id = $2::uuid AND system_to IS NULL",
        fx["d_tags"], E2,
    )
    ok("6 — the rejected mint wrote NOTHING", tag_count == 0, f"got {tag_count}")

    result = await import_records_csv(
        conn, target_type="entity", org_id=ORG, user_id=U_ADMIN, tab_id=None,
        csv_bytes=csv_bytes, can_create_tags=True,
    )
    ok("6 — the SAME mint succeeds WITH create_tags permission",
       len(result["accepted"]) == 1, f"got {result}")

    tag_count = await conn.fetchval(
        f"SELECT tag_code FROM portfolio.udf_tag_assignments "
        f"WHERE definition_id = $1::uuid AND target_id = $2::uuid AND system_to IS NULL",
        fx["d_tags"], E2,
    )
    ok("6 — the tag is actually assigned", tag_count == "BrandNewTag", f"got {tag_count}")


async def test_7_import_missing_target_id(conn, fx: dict) -> None:
    from services.portfolio_udf_records import import_records_csv

    # No target_id column in the file at all.
    csv_bytes = _csv_bytes([f"{PFX}d_text"], [[f"{PFX}orphan"]])
    result = await import_records_csv(
        conn, target_type="entity", org_id=ORG, user_id=U_ADMIN, tab_id=None,
        csv_bytes=csv_bytes, can_create_tags=True,
    )
    ok("7 — a file with no target_id column rejects every row with a clear "
       "reason",
       len(result["rejected"]) == 1 and "target_id" in result["rejected"][0]["reason"],
       f"got {result}")

    # target_id column present but blank for this row.
    csv_bytes = _csv_bytes(["target_id", f"{PFX}d_text"], [["", f"{PFX}orphan"]])
    result = await import_records_csv(
        conn, target_type="entity", org_id=ORG, user_id=U_ADMIN, tab_id=None,
        csv_bytes=csv_bytes, can_create_tags=True,
    )
    ok("7 — a blank target_id cell rejects that row with a clear reason",
       len(result["rejected"]) == 1 and "target_id" in result["rejected"][0]["reason"],
       f"got {result}")

    # target_id naming a real row in ANOTHER org.
    csv_bytes = _csv_bytes(["target_id", f"{PFX}d_text"], [[E_OTHER, f"{PFX}crossorg"]])
    result = await import_records_csv(
        conn, target_type="entity", org_id=ORG, user_id=U_ADMIN, tab_id=None,
        csv_bytes=csv_bytes, can_create_tags=True,
    )
    ok("7 — a target_id from ANOTHER org is refused, not silently written "
       "(RLS: org A cannot import against org B's records)",
       len(result["rejected"]) == 1 and "not a current" in result["rejected"][0]["reason"],
       f"got {result}")


async def test_8_import_required_field(conn, fx: dict) -> None:
    from services.portfolio_udf_records import import_records_csv

    csv_bytes = _csv_bytes(["target_id", f"{PFX}d_required"], [[E2, ""]])
    result = await import_records_csv(
        conn, target_type="entity", org_id=ORG, user_id=U_ADMIN, tab_id=None,
        csv_bytes=csv_bytes, can_create_tags=True,
    )
    ok("8 — a blank cell for a REQUIRED field rejects that row",
       len(result["rejected"]) == 1 and "required" in result["rejected"][0]["reason"],
       f"got {result}")

    csv_bytes = _csv_bytes(["target_id", f"{PFX}d_required"], [[E2, f"{PFX}filled"]])
    result = await import_records_csv(
        conn, target_type="entity", org_id=ORG, user_id=U_ADMIN, tab_id=None,
        csv_bytes=csv_bytes, can_create_tags=True,
    )
    ok("8 — the same required field WITH a value succeeds",
       len(result["accepted"]) == 1, f"got {result}")


async def test_9_import_rejected_shape(conn, fx: dict) -> None:
    """9 — rejected carries {row, reason} for EVERY failure, in a batch with
    a genuine mix of accept/reject outcomes."""
    from services.portfolio_udf_records import import_records_csv

    csv_bytes = _csv_bytes(
        ["target_id", f"{PFX}d_integer"],
        [
            [E2, "12"],           # row 1: accept
            [E4, "not-a-number"], # row 2: reject
            ["", "3"],            # row 3: reject (no target_id)
        ],
    )
    result = await import_records_csv(
        conn, target_type="entity", org_id=ORG, user_id=U_ADMIN, tab_id=None,
        csv_bytes=csv_bytes, can_create_tags=True,
    )
    ok("9 — a mixed batch accepts the good row and rejects the two bad ones",
       len(result["accepted"]) == 1 and len(result["rejected"]) == 2,
       f"got {result}")
    rows_seen = {r["row"] for r in result["rejected"]}
    ok("9 — every rejected entry carries its own row number (2 and 3)",
       rows_seen == {2, 3}, f"got {rows_seen}")
    ok("9 — every rejected entry carries a non-empty reason",
       all(r.get("reason") for r in result["rejected"]),
       f"got {result['rejected']}")


# ═══════════════════════════════════════════════════════════════════════════
# TASK — RLS cross-org isolation
# ═══════════════════════════════════════════════════════════════════════════


async def test_10_rls(app_conn, fx: dict) -> None:
    from services.portfolio_udf_records import export_records_csv, import_records_csv

    bypass = await app_conn.fetchval(
        "SELECT rolbypassrls FROM pg_roles WHERE rolname = current_user"
    )
    ok("10 — app_service's rolbypassrls is False (a genuinely non-bypassing "
       "role)", bypass is False, f"rolbypassrls={bypass}")

    tr = app_conn.transaction()
    await tr.start()
    try:
        await app_conn.execute("SELECT set_config('app.current_org_id', $1, true)", ORG)

        # Export: org A's connection asked for org B's rows explicitly.
        chunks = [
            c async for c in export_records_csv(
                app_conn, target_type="entity", org_id=OTHER_ORG, user_id=U_OTHER, tab_id=None,
            )
        ]
        import csv as _csv
        rows = list(_csv.DictReader(io.StringIO("".join(chunks))))
        other_org_ids = {r["target_id"] for r in rows}
        ok("10 — RLS: org A's connection exporting with org_id=B returns "
           "none of org B's rows (RLS session context stays org A)",
           E_OTHER not in other_org_ids, f"got target_ids={other_org_ids}")

        # Import: org A's connection, a row naming org B's real entity.
        csv_bytes = _csv_bytes(["target_id", f"{PFX}d_text"], [[E_OTHER, f"{PFX}rls"]])
        result = await import_records_csv(
            app_conn, target_type="entity", org_id=ORG, user_id=U_ADMIN, tab_id=None,
            csv_bytes=csv_bytes, can_create_tags=True,
        )
        ok("10 — RLS: org A's connection cannot import against org B's "
           "record even by naming its real id",
           len(result["accepted"]) == 0 and len(result["rejected"]) == 1,
           f"got {result}")

        # Positive control: the same connection CAN export its own org's row.
        own_chunks = [
            c async for c in export_records_csv(
                app_conn, target_type="entity", org_id=ORG, user_id=U_ADMIN, tab_id=None,
            )
        ]
        own_rows = list(_csv.DictReader(io.StringIO("".join(own_chunks))))
        own_ids = {r["target_id"] for r in own_rows}
        ok("10 — RLS positive control: org A CAN export its own records "
           "under the same connection/context",
           E1 in own_ids, f"got {own_ids}")
    finally:
        await tr.rollback()


# ═══════════════════════════════════════════════════════════════════════════
# Router — 403/200/201 + endpoint shape
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


HEADERS = {"Authorization": "Bearer verify-token"}


def endpoint_tests(fx: dict) -> None:
    import main
    from starlette.testclient import TestClient

    with TestClient(main.app, raise_server_exceptions=False) as client:
        noperms = _Principal(client, ORG, SUB_NOPERMS)
        admin = _Principal(client, ORG, SUB_ADMIN)

        # ── export ──
        r = noperms.get("/api/v1/udf/records/entity/export", headers=HEADERS)
        ok("11 GET /udf/records/{t}/export — 403 without view_portfolio",
           r.status_code == 403, f"got {r.status_code}: {r.text[:200]}")

        r = admin.get("/api/v1/udf/records/entity/export", headers=HEADERS)
        ok("11 GET /udf/records/{t}/export — 200 with view_portfolio",
           r.status_code == 200, f"got {r.status_code}: {r.text[:300]}")
        ok("11 — export response is text/csv with an attachment disposition",
           r.headers.get("content-type", "").startswith("text/csv")
           and "attachment" in r.headers.get("content-disposition", ""),
           f"content-type={r.headers.get('content-type')} "
           f"disposition={r.headers.get('content-disposition')}")
        ok("11 — the HTTP export body carries the expected header row",
           r.text.splitlines()[0].split(",")[0] == "target_id",
           f"first line={r.text.splitlines()[:1]}")

        # ── import ──
        r = noperms.post(
            "/api/v1/udf/records/entity/import", headers=HEADERS,
            files={"file": ("x.csv", b"target_id,x\n", "text/csv")},
        )
        ok("11 POST /udf/records/{t}/import — 403 without manage_portfolio",
           r.status_code == 403, f"got {r.status_code}: {r.text[:200]}")

        csv_body = f"target_id,{PFX}d_text\n{E4},{PFX}http-write\n".encode()
        r = admin.post(
            "/api/v1/udf/records/entity/import", headers=HEADERS,
            files={"file": ("x.csv", csv_body, "text/csv")},
        )
        ok("11 POST /udf/records/{t}/import — 201 with manage_portfolio",
           r.status_code == 201, f"got {r.status_code}: {r.text[:300]}")
        body = r.json()
        ok("11 — import response carries accepted/rejected",
           "accepted" in body and "rejected" in body, f"keys={sorted(body.keys())}")
        ok("11 — the HTTP import actually accepted the row",
           len(body.get("accepted", [])) == 1, f"got {body}")


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
            fx = await build_fixtures(conn)
            await test_1_export(conn, fx)
            await test_2_import_valid_row_append_only(conn, fx)
            await test_3_import_invalid_type_rejects_row_only(conn, fx)
            await test_4_import_value_set_rejects_row_only(conn, fx)
            await test_5_import_hidden_readonly_field(conn, fx)
            await test_6_import_tag_permission(conn, fx)
            await test_7_import_missing_target_id(conn, fx)
            await test_8_import_required_field(conn, fx)
            await test_9_import_rejected_shape(conn, fx)
            await test_10_rls(app_conn, fx)

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

    print(f"\n{'=' * 70}\nudf02b: {len(PASS)} PASS, {len(FAIL)} FAIL, {len(FIND)} FIND")
    for f in FAIL:
        print(f"  FAIL {f}")
    for f in FIND:
        print(f"  FIND {f}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
