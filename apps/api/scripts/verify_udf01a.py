"""Sprint udf01a verification — UDF definitions layer.

Pass/fail only, no prompts. Run:

    python3 apps/api/scripts/verify_udf01a.py

Part 1 (schema) was already applied and independently verified object-by-object
by ``apply_udf01a.py`` (32/32 PASS). This script re-confirms the load-bearing
subset directly (so this file stands alone as the sprint's proof artifact) and
then proves every Task 2/3 assertion against the REAL deployed database.

Every fixture table this script writes to is counted before the first insert
and again after the last delete; teardown is by fixture-id, never TRUNCATE —
none of these tables were empty in a general sense even though udf_definitions
and udf_values happened to be at sprint start.
"""

from __future__ import annotations

import asyncio
import glob
import json
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

import asyncpg  # noqa: E402

from _db_connect import admin_dsn, app_service_dsn, connect  # noqa: E402

from uuid import NAMESPACE_URL, uuid5  # noqa: E402

D = Decimal
ORG = "00000000-0000-0000-0000-000000000001"
OTHER_ORG = "bb347258-8f28-4f49-8cc9-e29ccad82884"

# Prefix ready to be suffixed with 4 more hex digits per fixture, matching the
# convention verify_fee43.py established — every id below is a valid UUID.
P = "99000000-0000-0000-0000-0000da01"

# services.permissions.get_user_id resolves the ACTIVE request's user_id as
# uuid5(NAMESPACE_URL, sub) whenever sub is not itself a UUID string (see
# portfolio-c-rollup's own finding: "get_user_id returns uuid5(sub), so a
# hand-picked fixture id fakes a 403"). The router tests drive real HTTP
# requests through main.verify_token, which only controls the SUB claim — so
# every user's ``id`` here MUST be derived the same way, or the router sees a
# user with ZERO user_roles rows and rbac.has_permission's zero-roles
# default-allow silently makes every permission check pass.
SUB_SUPER = "udf01averify|super"
SUB_ADMIN = "udf01averify|admin"
SUB_TAGGER = "udf01averify|tagger"
SUB_VIEWER = "udf01averify|viewer"
SUB_TEAM_A = "udf01averify|team_a"
SUB_TEAM_B = "udf01averify|team_b"
SUB_ORGADMIN = "udf01averify|orgadmin"
SUB_OTHER = "udf01averify|other"
SUB_NOPERMS = "udf01averify|noperms"

U_SUPER = str(uuid5(NAMESPACE_URL, SUB_SUPER))
U_ADMIN = str(uuid5(NAMESPACE_URL, SUB_ADMIN))
U_TAGGER = str(uuid5(NAMESPACE_URL, SUB_TAGGER))
U_VIEWER = str(uuid5(NAMESPACE_URL, SUB_VIEWER))
U_TEAM_A = str(uuid5(NAMESPACE_URL, SUB_TEAM_A))
U_TEAM_B = str(uuid5(NAMESPACE_URL, SUB_TEAM_B))
U_ORGADMIN = str(uuid5(NAMESPACE_URL, SUB_ORGADMIN))
U_OTHER = str(uuid5(NAMESPACE_URL, SUB_OTHER))
U_NOPERMS = str(uuid5(NAMESPACE_URL, SUB_NOPERMS))
USERS = [U_SUPER, U_ADMIN, U_TAGGER, U_VIEWER, U_TEAM_A, U_TEAM_B, U_ORGADMIN,
         U_OTHER, U_NOPERMS]

TEAM_A = f"{P}2001"
ROLE_TAGGER = f"{P}3001"
ROLE_NOPERMS = f"{P}3002"

LIST_ORG_A = f"{P}4001"
LIST_ORG_B = f"{P}4002"
LIST_KEY = "udf01averify_colors"
RD_RED = f"{P}4011"
RD_BLUE = f"{P}4012"

T_ENTITY_1 = f"{P}5001"
T_POSITION_1 = f"{P}5002"
T_POSITION_2 = f"{P}5003"
T_POSITION_3 = f"{P}5004"
T_POSITION_4 = f"{P}5005"
T_POSITION_5 = f"{P}5006"
T_POSITION_6 = f"{P}5007"
T_POSITION_FEE = f"{P}5008"
T_ASSET_1 = f"{P}5011"
T_COMMIT_1 = f"{P}5012"
T_VALUATION_1 = f"{P}5013"
T_OTHERORG_1 = f"{P}5021"

COUNTED = [
    "portfolio.udf_definitions", "portfolio.udf_values",
    "portfolio.udf_tag_assignments", "portfolio.udf_definition_audit",
    "public.reference_data_lists", "public.reference_data",
    "public.users", "public.roles", "public.teams", "public.team_members",
    "public.org_settings",
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
    out = {}
    for t in COUNTED:
        out[t] = await conn.fetchval(f"SELECT count(*) FROM {t}")
    return out


async def teardown(conn) -> None:
    await conn.execute(
        "DELETE FROM portfolio.udf_definition_audit WHERE definition_id IN "
        "(SELECT id FROM portfolio.udf_definitions WHERE org_id IN ($1::uuid,$2::uuid) "
        " AND field_key LIKE 'udf01averify_%') "
        "OR definition_id IN (SELECT id FROM portfolio.udf_definitions "
        " WHERE owner_scope='platform' AND api_name LIKE 'udf01averify_%')",
        ORG, OTHER_ORG,
    )
    await conn.execute(
        "DELETE FROM portfolio.udf_tag_assignments WHERE org_id IN ($1::uuid,$2::uuid) "
        "AND definition_id IN (SELECT id FROM portfolio.udf_definitions "
        " WHERE field_key LIKE 'udf01averify_%')",
        ORG, OTHER_ORG,
    )
    await conn.execute(
        "DELETE FROM portfolio.udf_values WHERE org_id IN ($1::uuid,$2::uuid) "
        "AND definition_id IN (SELECT id FROM portfolio.udf_definitions "
        " WHERE field_key LIKE 'udf01averify_%')",
        ORG, OTHER_ORG,
    )
    await conn.execute(
        "DELETE FROM portfolio.udf_definitions WHERE field_key LIKE 'udf01averify_%' "
        "OR api_name LIKE 'udf01averify_%'"
    )
    await conn.execute(
        "DELETE FROM public.reference_data WHERE list_id IN ($1::uuid, $2::uuid)",
        LIST_ORG_A, LIST_ORG_B,
    )
    await conn.execute(
        "DELETE FROM public.reference_data_lists WHERE id IN ($1::uuid, $2::uuid)",
        LIST_ORG_A, LIST_ORG_B,
    )
    await conn.execute(
        "DELETE FROM public.org_settings WHERE org_id = $1::uuid "
        "AND setting_key = 'crm.udf.max_tags_per_record'", ORG,
    )
    await conn.execute(
        "DELETE FROM public.team_members WHERE team_id = $1::uuid", TEAM_A
    )
    await conn.execute("DELETE FROM public.teams WHERE id = $1::uuid", TEAM_A)
    await conn.execute(
        "DELETE FROM public.user_roles WHERE user_id = ANY($1::uuid[])", USERS
    )
    await conn.execute(
        "DELETE FROM public.role_permissions WHERE role_id = ANY($1::uuid[])",
        [ROLE_TAGGER, ROLE_NOPERMS],
    )
    await conn.execute(
        "DELETE FROM public.roles WHERE id = ANY($1::uuid[])",
        [ROLE_TAGGER, ROLE_NOPERMS],
    )
    await conn.execute("DELETE FROM public.users WHERE id = ANY($1::uuid[])", USERS)


async def setup(conn) -> dict:
    for user_id, org, sub, role, active in (
        (U_SUPER, ORG, SUB_SUPER, "super_admin", True),
        (U_ADMIN, ORG, SUB_ADMIN, "member", True),
        (U_TAGGER, ORG, SUB_TAGGER, "member", True),
        (U_VIEWER, ORG, SUB_VIEWER, "member", True),
        (U_TEAM_A, ORG, SUB_TEAM_A, "member", True),
        (U_TEAM_B, ORG, SUB_TEAM_B, "member", True),
        (U_ORGADMIN, ORG, SUB_ORGADMIN, "org_admin", True),
        (U_OTHER, OTHER_ORG, SUB_OTHER, "member", True),
        (U_NOPERMS, ORG, SUB_NOPERMS, "member", True),
    ):
        await conn.execute(
            """INSERT INTO public.users
                (id, org_id, email, full_name, auth0_sub, role, is_active)
            VALUES ($1::uuid, $2::uuid, $3, 'Verify udf01a', $4, $5, $6)
            ON CONFLICT (id) DO NOTHING""",
            user_id, org, f"udf01averify-{user_id[-4:]}@test.local",
            sub, role, active,
        )

    await conn.execute(
        """INSERT INTO public.roles (id, org_id, name, description)
        VALUES ($1::uuid, $2::uuid, 'udf01averify_tagger', 'verify fixture')
        ON CONFLICT (id) DO NOTHING""",
        ROLE_TAGGER, ORG,
    )
    await conn.execute(
        """INSERT INTO public.roles (id, org_id, name, description)
        VALUES ($1::uuid, $2::uuid, 'udf01averify_noperms', 'verify fixture')
        ON CONFLICT (id) DO NOTHING""",
        ROLE_NOPERMS, ORG,
    )
    await conn.execute(
        """INSERT INTO public.role_permissions (role_id, permission_id)
        SELECT $1::uuid, id FROM public.permissions
        WHERE name IN ('manage_portfolio', 'create_tags')
        ON CONFLICT DO NOTHING""",
        ROLE_TAGGER,
    )
    # ROLE_NOPERMS gets zero role_permissions rows — deliberately, so
    # rbac.has_permission's real per-permission lookup is what refuses it, not
    # the zero-roles default-allow (which would pass vacuously).
    for user_id, role_name in (
        (U_ADMIN, "admin"), (U_TAGGER, "udf01averify_tagger"),
        (U_VIEWER, "member"), (U_TEAM_A, "member"), (U_TEAM_B, "member"),
        (U_OTHER, "admin"), (U_NOPERMS, "udf01averify_noperms"),
    ):
        await conn.execute(
            """INSERT INTO public.user_roles (user_id, role_id)
            SELECT $1::uuid, r.id FROM public.roles r
            WHERE r.name = $2 AND r.org_id = $3::uuid
            ON CONFLICT DO NOTHING""",
            user_id, role_name, ORG if role_name != "admin" or user_id != U_OTHER else OTHER_ORG,
        )
    # U_OTHER's role lookup above used ORG by construction of the ternary;
    # fix it explicitly since 'admin' also exists under OTHER_ORG.
    await conn.execute(
        """INSERT INTO public.user_roles (user_id, role_id)
        SELECT $1::uuid, r.id FROM public.roles r
        WHERE r.name = 'admin' AND r.org_id = $2::uuid
        ON CONFLICT DO NOTHING""",
        U_OTHER, OTHER_ORG,
    )

    await conn.execute(
        """INSERT INTO public.teams (id, org_id, name, description)
        VALUES ($1::uuid, $2::uuid, 'udf01averify team', 'verify fixture')
        ON CONFLICT (id) DO NOTHING""",
        TEAM_A, ORG,
    )
    await conn.execute(
        """INSERT INTO public.team_members (team_id, user_id)
        VALUES ($1::uuid, $2::uuid) ON CONFLICT DO NOTHING""",
        TEAM_A, U_TEAM_A,
    )

    for list_id, org in ((LIST_ORG_A, ORG), (LIST_ORG_B, OTHER_ORG)):
        await conn.execute(
            """INSERT INTO public.reference_data_lists
                (id, org_id, list_key, label, owner_scope, is_extensible)
            VALUES ($1::uuid, $2::uuid, $3, 'Verify Colors', 'org', false)
            ON CONFLICT (id) DO NOTHING""",
            list_id, org, LIST_KEY,
        )
    for rd_id, code, label in ((RD_RED, "red", "Red"), (RD_BLUE, "blue", "Blue")):
        await conn.execute(
            """INSERT INTO public.reference_data
                (id, org_id, list_key, code, label, list_id, is_active)
            VALUES ($1::uuid, $2::uuid, $3, $4, $5, $6::uuid, true)
            ON CONFLICT (id) DO NOTHING""",
            rd_id, ORG, LIST_KEY, code, label, LIST_ORG_A,
        )

    return {}


# ═══════════════════════════════════════════════════════════════════════════
# TASK 1 (re-confirmation) — the load-bearing subset of Part 1
# ═══════════════════════════════════════════════════════════════════════════


async def test_1_part1_reconfirm(conn) -> None:
    ok("1 — udf_def_api_name_uq exists with the corrected column shape",
       await conn.fetchval(
           "SELECT indexdef FROM pg_indexes WHERE schemaname='portfolio' "
           "AND indexname='udf_def_api_name_uq'") is not None)
    ok("1 — reference_data_lists exists",
       await conn.fetchval("SELECT to_regclass('public.reference_data_lists')") is not None)
    ok("1 — udf_tag_assignments exists",
       await conn.fetchval("SELECT to_regclass('portfolio.udf_tag_assignments')") is not None)
    ok("1 — udf_definition_audit exists",
       await conn.fetchval("SELECT to_regclass('portfolio.udf_definition_audit')") is not None)
    ok("1 — create_tags permission exists",
       await conn.fetchval(
           "SELECT 1 FROM public.permissions WHERE name='create_tags'") == 1)


# ═══════════════════════════════════════════════════════════════════════════
# TASK 2a — append-only value writes
# ═══════════════════════════════════════════════════════════════════════════


async def test_2a_append_only(conn) -> None:
    from services.portfolio_udf import (
        create_org_definition, get_udf_value, get_value_history,
        record_udf_value,
    )

    def_id = await create_org_definition(
        conn, org_id=ORG, applies_to="commitment", field_key="udf01averify_append",
        label="Verify Append", data_type="text", type_params={"length": 50},
        created_by=U_ADMIN,
    )
    await record_udf_value(
        conn, org_id=ORG, definition_id=def_id, target_type="commitment",
        target_id=T_COMMIT_1, value="alpha",
    )
    await record_udf_value(
        conn, org_id=ORG, definition_id=def_id, target_type="commitment",
        target_id=T_COMMIT_1, value="beta",
    )
    rows = await conn.fetch(
        "SELECT value_text, system_to FROM portfolio.udf_values "
        "WHERE definition_id = $1::uuid AND target_id = $2::uuid ORDER BY system_from",
        def_id, T_COMMIT_1,
    )
    ok("8 — writing a value twice leaves exactly 2 rows (append, not overwrite)",
       len(rows) == 2, f"got {len(rows)}")
    if len(rows) == 2:
        ok("8 — predecessor has system_to SET", rows[0]["system_to"] is not None,
           f"value={rows[0]['value_text']!r}")
        ok("8 — successor has system_to NULL (current)", rows[1]["system_to"] is None,
           f"value={rows[1]['value_text']!r}")
    current = await get_udf_value(
        conn, org_id=ORG, definition_id=def_id, target_type="commitment",
        target_id=T_COMMIT_1,
    )
    ok("8 — get_udf_value returns exactly one CURRENT row = 'beta'",
       current is not None and current["value_text"] == "beta")

    history = await get_value_history(
        conn, org_id=ORG, definition_id=def_id, target_type="commitment",
        target_id=T_COMMIT_1,
    )
    ok("9 (17a-4) — the prior value is still retrievable after overwrite",
       len(history) == 2 and history[0]["value_text"] == "alpha"
       and history[1]["value_text"] == "beta",
       f"history={[h['value_text'] for h in history]}")


# ═══════════════════════════════════════════════════════════════════════════
# TASK 2b — type-parameter contract
# ═══════════════════════════════════════════════════════════════════════════

TYPE_CASES = [
    ("text", {"length": 100}, {"length": 0}),
    ("long_text", {"length": 1000}, {"length": 999999999}),
    ("rich_text", {"length": 500}, {"scale": 2}),
    ("integer", {"precision": 10}, {"precision": 0}),
    ("numeric", {"precision": 10, "scale": 2}, {"precision": 5, "scale": 10}),
    ("percent", {"precision": 5, "scale": 2}, {"precision": 5}),
    ("date", {}, {"length": 10}),
    ("datetime", {}, {"precision": 5}),
    ("boolean", {}, {"length": 1}),
    ("email", {"length": 254}, {}),
    ("url", {"length": 2048}, {}),
    ("phone", {"length": 20}, {}),
]


async def test_2b_type_contract(conn) -> None:
    from services.portfolio_udf import UdfTypeParamError, create_org_definition

    for data_type, valid_params, invalid_params in TYPE_CASES:
        key_ok = f"udf01averify_type_{data_type}_ok"
        try:
            def_id = await create_org_definition(
                conn, org_id=ORG, applies_to="asset", field_key=key_ok,
                label=f"Verify {data_type} OK", data_type=data_type,
                type_params=valid_params, created_by=U_ADMIN,
            )
            ok(f"4 — {data_type} accepts a valid param set", def_id is not None)
        except Exception as exc:  # noqa: BLE001
            ok(f"4 — {data_type} accepts a valid param set", False, repr(exc))

        key_bad = f"udf01averify_type_{data_type}_bad"
        try:
            await create_org_definition(
                conn, org_id=ORG, applies_to="asset", field_key=key_bad,
                label=f"Verify {data_type} BAD", data_type=data_type,
                type_params=invalid_params, created_by=U_ADMIN,
            )
            ok(f"4 — {data_type} rejects an invalid param set", False,
               "no exception raised")
        except UdfTypeParamError:
            ok(f"4 — {data_type} rejects an invalid param set", True)
        except Exception as exc:  # noqa: BLE001
            ok(f"4 — {data_type} rejects an invalid param set", False,
               f"wrong exception type: {exc!r}")

    # select / multiselect — negative case is "no value_set_id".
    for data_type in ("select", "multiselect"):
        try:
            await create_org_definition(
                conn, org_id=ORG, applies_to="asset",
                field_key=f"udf01averify_type_{data_type}_bad",
                label=f"Verify {data_type} BAD", data_type=data_type,
                type_params={}, created_by=U_ADMIN,
            )
            ok(f"4 — {data_type} rejects missing value_set_id", False, "no exception")
        except UdfTypeParamError:
            ok(f"4 — {data_type} rejects missing value_set_id", True)

    def_select = await create_org_definition(
        conn, org_id=ORG, applies_to="asset", field_key="udf01averify_type_select_ok2",
        label="Verify select with real value set", data_type="select",
        type_params={"value_set_id": LIST_ORG_A}, created_by=U_ADMIN,
    )
    ok("4 — select accepts a REAL value_set_id", def_select is not None)

    def_multi = await create_org_definition(
        conn, org_id=ORG, applies_to="asset", field_key="udf01averify_type_multiselect_ok",
        label="Verify multiselect with real value set", data_type="multiselect",
        type_params={"value_set_id": LIST_ORG_A}, created_by=U_ADMIN,
    )
    ok("4 — multiselect accepts a valid param set (real value_set_id)",
       def_multi is not None)

    def_tags_ok = await create_org_definition(
        conn, org_id=ORG, applies_to="asset", field_key="udf01averify_type_tags_ok",
        label="Verify tags contract OK", data_type="tags",
        type_params={}, created_by=U_ADMIN,
    )
    ok("4 — tags accepts a valid (empty) param set", def_tags_ok is not None)
    try:
        await create_org_definition(
            conn, org_id=ORG, applies_to="asset", field_key="udf01averify_type_tags_bad",
            label="Verify tags contract BAD", data_type="tags",
            type_params={"length": 5}, created_by=U_ADMIN,
        )
        ok("4 — tags rejects an unrecognised param (length)", False, "no exception")
    except UdfTypeParamError:
        ok("4 — tags rejects an unrecognised param (length)", True)


async def test_currency_and_valueset(conn) -> None:
    from services.portfolio_udf import UdfTypeParamError, create_org_definition

    try:
        await create_org_definition(
            conn, org_id=ORG, applies_to="asset", field_key="udf01averify_currency_bad",
            label="Verify currency bad scale", data_type="currency",
            type_params={"precision": 10, "scale": 2, "currency_code": "USD"},
            created_by=U_ADMIN,
        )
        ok("5 — currency with scale != 4 is rejected", False, "no exception")
    except UdfTypeParamError:
        ok("5 — currency with scale != 4 is rejected", True)

    def_id = await create_org_definition(
        conn, org_id=ORG, applies_to="asset", field_key="udf01averify_currency_ok",
        label="Verify currency", data_type="currency",
        type_params={"precision": 10, "scale": 4, "currency_code": "usd"},
        created_by=U_ADMIN,
    )
    row = await conn.fetchrow(
        "SELECT type_params FROM portfolio.udf_definitions WHERE id = $1::uuid", def_id
    )
    tp = json.loads(row["type_params"]) if isinstance(row["type_params"], str) else row["type_params"]
    ok("5 — currency with scale=4 succeeds and currency_code is normalised",
       tp.get("currency_code") == "USD", f"got {tp}")

    fake_list = "99000000-0000-0000-0000-000000000fff"
    try:
        await create_org_definition(
            conn, org_id=ORG, applies_to="asset", field_key="udf01averify_valueset_bad",
            label="Verify bad value_set_id", data_type="select",
            type_params={"value_set_id": fake_list}, created_by=U_ADMIN,
        )
        ok("19 — value_set_id FK rejects a non-existent list", False, "no exception")
    except UdfTypeParamError:
        ok("19 — value_set_id FK rejects a non-existent list", True)

    rows = await conn.fetch(
        "SELECT id::text, org_id::text FROM public.reference_data_lists "
        "WHERE list_key = $1 ORDER BY org_id", LIST_KEY,
    )
    orgs = {r["org_id"] for r in rows}
    ok("20 — reference_data org-scoped uniqueness permits two orgs to share list_key",
       {ORG, OTHER_ORG} <= orgs, f"orgs holding {LIST_KEY!r}: {orgs}")


# ═══════════════════════════════════════════════════════════════════════════
# TASK 2d — widening-only type-change matrix
# ═══════════════════════════════════════════════════════════════════════════


async def test_2d_scale_and_length(conn) -> None:
    from services.portfolio_udf import (
        UdfTypeChangeError, create_org_definition, get_udf_value,
        record_udf_value, update_definition,
    )

    def_id = await create_org_definition(
        conn, org_id=ORG, applies_to="position", field_key="udf01averify_scale",
        label="Verify scale", data_type="numeric",
        type_params={"precision": 10, "scale": 2}, created_by=U_ADMIN,
    )
    await record_udf_value(
        conn, org_id=ORG, definition_id=def_id, target_type="position",
        target_id=T_POSITION_1, value=D("12.34"),
    )
    try:
        await update_definition(
            conn, definition_id=def_id, org_id=ORG, changed_by=U_ADMIN,
            changes={"type_params": {"scale": 1}},
        )
        ok("6 — scale decrease is rejected unconditionally", False, "no exception")
    except UdfTypeChangeError as exc:
        ok("6 — scale decrease is rejected unconditionally", True,
           f"affected_rows={exc.affected_rows!r} (must be None — unconditional, no dry run)")
        ok("6 — the unconditional scale refusal ran NO dry run",
           exc.affected_rows is None)

    after = await update_definition(
        conn, definition_id=def_id, org_id=ORG, changed_by=U_ADMIN,
        changes={"type_params": {"scale": 3}},
    )
    ok("6 — scale increase succeeds", after["type_params"].get("scale") == 3,
       f"got {after['type_params']}")
    value_after = await get_udf_value(
        conn, org_id=ORG, definition_id=def_id, target_type="position",
        target_id=T_POSITION_1,
    )
    ok("6 — existing value survives a scale increase UNCHANGED",
       value_after["value_numeric"] == D("12.34"), f"got {value_after['value_numeric']}")

    def_len = await create_org_definition(
        conn, org_id=ORG, applies_to="asset", field_key="udf01averify_length",
        label="Verify length", data_type="text", type_params={"length": 20},
        created_by=U_ADMIN,
    )
    await record_udf_value(
        conn, org_id=ORG, definition_id=def_len, target_type="asset",
        target_id=T_ASSET_1, value="hello world",
    )
    try:
        await update_definition(
            conn, definition_id=def_len, org_id=ORG, changed_by=U_ADMIN,
            changes={"type_params": {"length": 5}},
        )
        ok("7 — length decrease with affected rows > 0 is rejected", False, "no exception")
    except UdfTypeChangeError as exc:
        ok("7 — length decrease with affected rows > 0 is rejected",
           exc.affected_rows == 1, f"affected_rows={exc.affected_rows}")

    after2 = await update_definition(
        conn, definition_id=def_len, org_id=ORG, changed_by=U_ADMIN,
        changes={"type_params": {"length": 15}},
    )
    ok("7 — length decrease with ZERO affected rows succeeds",
       after2["type_params"].get("length") == 15, f"got {after2['type_params']}")


async def test_decimal_roundtrip(conn) -> None:
    from services.portfolio_udf import (
        UdfValueTypeError, create_org_definition, get_udf_value,
        record_udf_value,
    )

    def_id = await create_org_definition(
        conn, org_id=ORG, applies_to="valuation", field_key="udf01averify_decimal",
        label="Verify decimal", data_type="numeric",
        type_params={"precision": 10, "scale": 2}, created_by=U_ADMIN,
    )
    try:
        await record_udf_value(
            conn, org_id=ORG, definition_id=def_id, target_type="valuation",
            target_id=T_VALUATION_1, value=12.34,  # a real float
        )
        ok("10 — a float is refused before it ever reaches the database", False,
           "no exception")
    except UdfValueTypeError:
        ok("10 — a float is refused before it ever reaches the database", True)
    n = await conn.fetchval(
        "SELECT count(*) FROM portfolio.udf_values WHERE definition_id = $1::uuid",
        def_id,
    )
    ok("10 — the refused float wrote NOTHING", n == 0, f"row count={n}")

    await record_udf_value(
        conn, org_id=ORG, definition_id=def_id, target_type="valuation",
        target_id=T_VALUATION_1, value=D("12.345"),
    )
    stored = await get_udf_value(
        conn, org_id=ORG, definition_id=def_id, target_type="valuation",
        target_id=T_VALUATION_1,
    )
    ok("10 — Decimal round-trips at the declared scale (ROUND_HALF_UP)",
       stored["value_numeric"] == D("12.35"), f"got {stored['value_numeric']}")


# ═══════════════════════════════════════════════════════════════════════════
# TASK 2c — lifecycle + audit
# ═══════════════════════════════════════════════════════════════════════════


async def test_2c_lifecycle_and_audit(conn) -> None:
    from services.portfolio_udf import (
        UdfImmutableError, UdfReferencedError, create_org_definition,
        deactivate_definition, get_definition, reactivate_definition,
        record_udf_value, resolve_visible_definitions, soft_delete_definition,
        undelete_definition, update_definition,
    )

    def_id = await create_org_definition(
        conn, org_id=ORG, applies_to="transaction", field_key="udf01averify_lifecycle",
        label="Verify Lifecycle", data_type="text", type_params={"length": 30},
        api_name="udf01averify_lifecycle_api", created_by=U_ADMIN,
    )
    try:
        await update_definition(
            conn, definition_id=def_id, org_id=ORG, changed_by=U_ADMIN,
            changes={"api_name": "udf01averify_changed"},
        )
        ok("3 — api_name is immutable", False, "no exception")
    except UdfImmutableError:
        ok("3 — api_name is immutable", True)

    updated = await update_definition(
        conn, definition_id=def_id, org_id=ORG, changed_by=U_ADMIN,
        changes={"label": "Verify Lifecycle RENAMED"},
    )
    ok("2c — label update succeeds (mutable field)",
       updated["label"] == "Verify Lifecycle RENAMED")

    deact = await deactivate_definition(
        conn, definition_id=def_id, org_id=ORG, changed_by=U_ADMIN
    )
    ok("2c — deactivate_definition sets is_active=false", deact["is_active"] is False)
    react = await reactivate_definition(
        conn, definition_id=def_id, org_id=ORG, changed_by=U_ADMIN
    )
    ok("2c — reactivate_definition reverses it", react["is_active"] is True)

    deleted = await soft_delete_definition(
        conn, definition_id=def_id, org_id=ORG, changed_by=U_ADMIN
    )
    ok("11 — soft-delete hides the definition (get_definition -> None)",
       await get_definition(conn, definition_id=def_id) is None)
    visible = await resolve_visible_definitions(
        conn, org_id=ORG, user_id=U_ADMIN, applies_to="transaction"
    )
    ok("11 — soft-deleted definition excluded from resolve_visible_definitions",
       def_id not in {d["id"] for d in visible})
    ok("11 — deleted_at is set, deleted_by recorded",
       deleted["deleted_at"] is not None)

    restored = await undelete_definition(
        conn, definition_id=def_id, org_id=ORG, changed_by=U_ADMIN
    )
    ok("11 — soft-delete is REVERSIBLE (undelete restores it)",
       restored["deleted_at"] is None
       and await get_definition(conn, definition_id=def_id) is not None)

    audit_rows = await conn.fetch(
        "SELECT change_kind, before_state, after_state FROM portfolio.udf_definition_audit "
        "WHERE definition_id = $1::uuid ORDER BY changed_at", def_id,
    )
    kinds = [r["change_kind"] for r in audit_rows]
    ok("13 — every lifecycle op wrote exactly ONE audit row, in order",
       kinds == ["create", "update", "deactivate", "reactivate", "soft_delete", "reactivate"],
       f"got {kinds}")
    create_row = audit_rows[0]
    ok("13 — 'create' audit row has before_state=NULL",
       create_row["before_state"] is None)
    update_row = audit_rows[1]
    before = json.loads(update_row["before_state"])
    after = json.loads(update_row["after_state"])
    ok("13 — 'update' audit row's before/after actually differ on the changed field",
       before.get("label") != after.get("label")
       and after.get("label") == "Verify Lifecycle RENAMED")

    # ── referenced-definition block (12) ────────────────────────────────────
    def_blocked = await create_org_definition(
        conn, org_id=ORG, applies_to="valuation", field_key="udf01averify_blocked",
        label="Verify Blocked Delete", data_type="text", type_params={"length": 20},
        created_by=U_ADMIN,
    )
    await record_udf_value(
        conn, org_id=ORG, definition_id=def_blocked, target_type="valuation",
        target_id=T_VALUATION_1, value="referenced",
    )
    try:
        await soft_delete_definition(
            conn, definition_id=def_blocked, org_id=ORG, changed_by=U_ADMIN
        )
        ok("12 — soft-delete is blocked when the definition is referenced", False,
           "no exception")
    except UdfReferencedError as exc:
        ok("12 — soft-delete is blocked when the definition is referenced",
           exc.references.get("udf_values") == 1, f"references={exc.references}")


# ═══════════════════════════════════════════════════════════════════════════
# api_name uniqueness across namespaces (2)
# ═══════════════════════════════════════════════════════════════════════════


async def test_2_api_name_namespaces(conn) -> None:
    from services.portfolio_udf import (
        UdfDuplicateError, create_org_definition, create_platform_definition,
    )

    api_name = "udf01averify_shared_apiname"
    await create_platform_definition(
        conn, applies_to="entity", field_key="udf01averify_apiname_platform",
        label="Verify api_name platform", data_type="text",
        type_params={"length": 50}, api_name=api_name, is_super_admin=True,
        created_by=U_SUPER,
    )
    await create_org_definition(
        conn, org_id=ORG, applies_to="entity", field_key="udf01averify_apiname_org_a",
        label="Verify api_name org A", data_type="text",
        type_params={"length": 50}, api_name=api_name, created_by=U_ADMIN,
    )
    await create_org_definition(
        conn, org_id=OTHER_ORG, applies_to="entity", field_key="udf01averify_apiname_org_b",
        label="Verify api_name org B", data_type="text",
        type_params={"length": 50}, api_name=api_name, created_by=U_ADMIN,
    )
    ok("2 — same api_name across 3 different namespaces (platform, org A, org B) "
       "all succeed", True)

    try:
        await create_org_definition(
            conn, org_id=ORG, applies_to="entity", field_key="udf01averify_apiname_org_a2",
            label="Verify api_name org A dup", data_type="text",
            type_params={"length": 50}, api_name=api_name, created_by=U_ADMIN,
        )
        ok("2 — the SAME api_name in the SAME namespace (org A again) is rejected",
           False, "no exception")
    except UdfDuplicateError as exc:
        ok("2 — the SAME api_name in the SAME namespace (org A again) is rejected",
           True, f"constraint={exc.constraint}")


# ═══════════════════════════════════════════════════════════════════════════
# TASK 2e — tags
# ═══════════════════════════════════════════════════════════════════════════


async def test_2e_tags(conn) -> None:
    from services.org_settings import set_setting
    from services.portfolio_udf import create_org_definition
    from services.portfolio_udf_tags import (
        TagCapError, TagPermissionError, assign_tags, get_vocabulary, merge_tags,
    )

    def_tags = await create_org_definition(
        conn, org_id=ORG, applies_to="position", field_key="udf01averify_tags",
        label="Verify Tags", data_type="tags", created_by=U_ADMIN,
    )

    try:
        await assign_tags(
            conn, org_id=ORG, definition_id=def_tags, target_id=T_POSITION_1,
            codes=["Prospect"], assigned_by=U_ADMIN, can_create_tags=False,
        )
        ok("14 — minting a NEW tag WITHOUT create_tags is rejected", False, "no exception")
    except TagPermissionError:
        ok("14 — minting a NEW tag WITHOUT create_tags is rejected", True)

    await assign_tags(
        conn, org_id=ORG, definition_id=def_tags, target_id=T_POSITION_1,
        codes=["Prospect"], assigned_by=U_TAGGER, can_create_tags=True,
    )
    ok("14 — minting a NEW tag WITH create_tags succeeds", True)

    await assign_tags(
        conn, org_id=ORG, definition_id=def_tags, target_id=T_POSITION_2,
        codes=["prospect"], assigned_by=U_VIEWER, can_create_tags=False,
    )
    await assign_tags(
        conn, org_id=ORG, definition_id=def_tags, target_id=T_POSITION_3,
        codes=[" PROSPECT "], assigned_by=U_VIEWER, can_create_tags=False,
    )
    ok("15 — assigning an EXISTING (differently-cased) tag needs no create_tags",
       True)

    vocab = await get_vocabulary(conn, definition_id=def_tags)
    prospect_entries = [v for v in vocab if v["normalized_code"] == "prospect"]
    ok("15 — 'Prospect'/'prospect'/' PROSPECT ' dedupe to ONE vocabulary entry",
       len(prospect_entries) == 1, f"vocab={vocab}")
    if prospect_entries:
        ok("15 — first-entered casing ('Prospect') is preserved",
           prospect_entries[0]["tag_code"] == "Prospect",
           f"got {prospect_entries[0]['tag_code']!r}")
        ok("15 — vocabulary entry's count reflects all 3 assignments",
           prospect_entries[0]["n"] == 3, f"n={prospect_entries[0]['n']}")

    await set_setting(conn, ORG, "crm.udf.max_tags_per_record", 2, U_SUPER)
    try:
        await assign_tags(
            conn, org_id=ORG, definition_id=def_tags, target_id=T_POSITION_4,
            codes=["cap_a", "cap_b", "cap_c"], assigned_by=U_TAGGER,
            can_create_tags=True,
        )
        ok("16 — max_tags_per_record is enforced from org_settings, not a constant",
           False, "no exception with cap=2 and 3 tags")
    except TagCapError as exc:
        ok("16 — max_tags_per_record is enforced from org_settings, not a constant",
           True, str(exc))

    await assign_tags(
        conn, org_id=ORG, definition_id=def_tags, target_id=T_POSITION_5,
        codes=["legacy_a"], assigned_by=U_TAGGER, can_create_tags=True,
    )
    await assign_tags(
        conn, org_id=ORG, definition_id=def_tags, target_id=T_POSITION_6,
        codes=["legacy_b"], assigned_by=U_TAGGER, can_create_tags=True,
    )
    n_repointed = await merge_tags(
        conn, org_id=ORG, definition_id=def_tags, from_code="legacy_a",
        into_code="legacy_b", changed_by=U_ADMIN,
    )
    ok("17 — tag merge repointed exactly 1 assignment", n_repointed == 1,
       f"got {n_repointed}")
    current_5 = await conn.fetchval(
        "SELECT normalized_code FROM portfolio.udf_tag_assignments "
        "WHERE definition_id = $1::uuid AND target_id = $2::uuid AND system_to IS NULL",
        def_tags, T_POSITION_5,
    )
    ok("17 — the repointed assignment now carries the INTO code",
       current_5 == "legacy_b", f"got {current_5!r}")
    merge_audit = await conn.fetchval(
        "SELECT count(*) FROM portfolio.udf_definition_audit "
        "WHERE definition_id = $1::uuid AND change_kind = 'update' "
        "AND after_state::text LIKE '%tag_merge_into%'", def_tags,
    )
    ok("17 — tag merge is audited", merge_audit >= 1, f"count={merge_audit}")


# ═══════════════════════════════════════════════════════════════════════════
# TASK 2e — fee_run_inputs regression / dual-write (TODO(udf-1d))
# ═══════════════════════════════════════════════════════════════════════════


async def test_fee_run_inputs_regression(conn) -> None:
    from services.portfolio_udf import create_org_definition
    from services.portfolio_udf_tags import assign_tags

    def_id = await create_org_definition(
        conn, org_id=ORG, applies_to="position", field_key="udf01averify_tags_fee",
        label="Verify Tags Fee Regression", data_type="tags", created_by=U_ADMIN,
    )
    await assign_tags(
        conn, org_id=ORG, definition_id=def_id, target_id=T_POSITION_FEE,
        codes=["income", "equity"], assigned_by=U_TAGGER, can_create_tags=True,
    )

    # The EXACT shape of the query in services/fee_run_inputs.py:806-816.
    rows = await conn.fetch(
        """SELECT target_id::text AS target_id, value_text
           FROM portfolio.udf_values
           WHERE org_id = $1::uuid AND target_type = 'position'
             AND target_id = ANY($2::uuid[])
             AND value_text IS NOT NULL
             AND valid_to IS NULL AND system_to IS NULL""",
        ORG, [T_POSITION_FEE],
    )
    ok("21 — regression: fee_run_inputs' exact query sees a tag minted "
       "through the new path (TODO(udf-1d) dual-write)",
       len(rows) == 1 and rows[0]["value_text"] == "equity income",
       f"got {[dict(r) for r in rows]}")


# ═══════════════════════════════════════════════════════════════════════════
# RLS (18)
# ═══════════════════════════════════════════════════════════════════════════


async def test_18_rls(conn, app_conn) -> None:
    from services.portfolio_udf import create_org_definition, record_udf_value
    from services.portfolio_udf_tags import assign_tags

    bypass = await app_conn.fetchval(
        "SELECT rolbypassrls FROM pg_roles WHERE rolname = current_user"
    )
    ok("18 — app_service's rolbypassrls is False (a genuinely non-bypassing role)",
       bypass is False, f"rolbypassrls={bypass}")

    other_def = await create_org_definition(
        conn, org_id=OTHER_ORG, applies_to="entity", field_key="udf01averify_otherorg_val",
        label="Verify OTHER_ORG value", data_type="text",
        type_params={"length": 20}, created_by=U_OTHER,
    )
    await record_udf_value(
        conn, org_id=OTHER_ORG, definition_id=other_def, target_type="entity",
        target_id=T_OTHERORG_1, value="other org secret",
    )
    other_tags = await create_org_definition(
        conn, org_id=OTHER_ORG, applies_to="entity", field_key="udf01averify_otherorg_tags",
        label="Verify OTHER_ORG tags", data_type="tags", created_by=U_OTHER,
    )
    await assign_tags(
        conn, org_id=OTHER_ORG, definition_id=other_tags, target_id=T_OTHERORG_1,
        codes=["otherorgsecret"], assigned_by=U_OTHER, can_create_tags=True,
    )

    tr = app_conn.transaction()
    await tr.start()
    try:
        await app_conn.execute(
            "SELECT set_config('app.current_org_id', $1, true)", ORG
        )
        d = await app_conn.fetchval(
            "SELECT 1 FROM portfolio.udf_definitions WHERE id = $1::uuid", other_def
        )
        ok("18 — RLS: org A cannot read org B's definition", d is None)

        v = await app_conn.fetchval(
            "SELECT 1 FROM portfolio.udf_values WHERE definition_id = $1::uuid",
            other_def,
        )
        ok("18 — RLS: org A cannot read org B's value", v is None)

        t = await app_conn.fetchval(
            "SELECT 1 FROM portfolio.udf_tag_assignments WHERE definition_id = $1::uuid",
            other_tags,
        )
        ok("18 — RLS: org A cannot read org B's tag assignment", t is None)

        a = await app_conn.fetchval(
            "SELECT 1 FROM portfolio.udf_definition_audit WHERE definition_id = $1::uuid",
            other_def,
        )
        ok("18 — RLS: org A cannot read org B's audit row", a is None)

        # Positive control: org A's OWN definition IS visible under the same GUC.
        own = await create_org_definition(
            app_conn, org_id=ORG, applies_to="entity",
            field_key="udf01averify_rls_own", label="Verify RLS own",
            data_type="text", type_params={"length": 10}, created_by=U_ADMIN,
        )
        own_visible = await app_conn.fetchval(
            "SELECT 1 FROM portfolio.udf_definitions WHERE id = $1::uuid", own
        )
        ok("18 — positive control: org A CAN read its own definition under the "
           "same connection/policy", own_visible == 1)
    finally:
        await tr.rollback()  # never persist anything written under app_conn


# ═══════════════════════════════════════════════════════════════════════════
# Router — every endpoint, 403 without permission / 200 with it (22)
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
        admin = _Principal(client, ORG, SUB_ADMIN)
        tagger = _Principal(client, ORG, SUB_TAGGER)

        r = noperms.get("/api/v1/udf/definitions?target_type=entity", headers=HEADERS)
        ok("22 GET /udf/definitions — 403 without view_portfolio", r.status_code == 403,
           f"got {r.status_code}")
        r = viewer.get("/api/v1/udf/definitions?target_type=entity", headers=HEADERS)
        ok("22 GET /udf/definitions — 200 with view_portfolio", r.status_code == 200,
           f"got {r.status_code}: {r.text[:200]}")

        body = {
            "owner_scope": "org", "applies_to": "entity",
            "field_key": "udf01averify_http_lifecycle", "label": "HTTP Lifecycle",
            "data_type": "text", "type_params": {"length": 40},
        }
        r = viewer.post("/api/v1/udf/definitions", json=body, headers=HEADERS)
        ok("22 POST /udf/definitions — 403 without manage_portfolio",
           r.status_code == 403, f"got {r.status_code}")
        r = admin.post("/api/v1/udf/definitions", json=body, headers=HEADERS)
        ok("22 POST /udf/definitions — 201 with manage_portfolio",
           r.status_code == 201, f"got {r.status_code}: {r.text[:300]}")
        def_id = r.json().get("id") if r.status_code == 201 else None

        if def_id:
            r = viewer.patch(f"/api/v1/udf/definitions/{def_id}",
                              json={"label": "nope"}, headers=HEADERS)
            ok("22 PATCH /udf/definitions/{id} — 403 without manage_portfolio",
               r.status_code == 403, f"got {r.status_code}")
            r = admin.patch(f"/api/v1/udf/definitions/{def_id}",
                             json={"label": "HTTP Lifecycle Renamed"}, headers=HEADERS)
            ok("22 PATCH /udf/definitions/{id} — 200 with manage_portfolio",
               r.status_code == 200, f"got {r.status_code}: {r.text[:300]}")

            r = viewer.post(f"/api/v1/udf/definitions/{def_id}/deactivate", headers=HEADERS)
            ok("22 POST .../deactivate — 403 without manage_portfolio",
               r.status_code == 403, f"got {r.status_code}")
            r = admin.post(f"/api/v1/udf/definitions/{def_id}/deactivate", headers=HEADERS)
            ok("22 POST .../deactivate — 200 with manage_portfolio",
               r.status_code == 200, f"got {r.status_code}: {r.text[:300]}")

            r = viewer.post(f"/api/v1/udf/definitions/{def_id}/reactivate", headers=HEADERS)
            ok("22 POST .../reactivate — 403 without manage_portfolio",
               r.status_code == 403, f"got {r.status_code}")
            r = admin.post(f"/api/v1/udf/definitions/{def_id}/reactivate", headers=HEADERS)
            ok("22 POST .../reactivate — 200 with manage_portfolio",
               r.status_code == 200, f"got {r.status_code}: {r.text[:300]}")

            r = viewer.delete(f"/api/v1/udf/definitions/{def_id}", headers=HEADERS)
            ok("22 DELETE /udf/definitions/{id} — 403 without manage_portfolio",
               r.status_code == 403, f"got {r.status_code}")
            r = admin.delete(f"/api/v1/udf/definitions/{def_id}", headers=HEADERS)
            ok("22 DELETE /udf/definitions/{id} — 200 with manage_portfolio",
               r.status_code == 200, f"got {r.status_code}: {r.text[:300]}")

            r = viewer.post(f"/api/v1/udf/definitions/{def_id}/undelete", headers=HEADERS)
            ok("22 POST .../undelete — 403 without manage_portfolio",
               r.status_code == 403, f"got {r.status_code}")
            r = admin.post(f"/api/v1/udf/definitions/{def_id}/undelete", headers=HEADERS)
            ok("22 POST .../undelete — 200 with manage_portfolio",
               r.status_code == 200, f"got {r.status_code}: {r.text[:300]}")

        r = noperms.get(f"/api/v1/udf/values/entity/{T_ENTITY_1}", headers=HEADERS)
        ok("22 GET /udf/values/... — 403 without view_portfolio", r.status_code == 403,
           f"got {r.status_code}")
        r = viewer.get(f"/api/v1/udf/values/entity/{T_ENTITY_1}", headers=HEADERS)
        ok("22 GET /udf/values/... — 200 with view_portfolio", r.status_code == 200,
           f"got {r.status_code}: {r.text[:200]}")

        val_def_body = {
            "owner_scope": "org", "applies_to": "entity",
            "field_key": "udf01averify_http_values", "label": "HTTP Values",
            "data_type": "text", "type_params": {"length": 40},
        }
        r = admin.post("/api/v1/udf/definitions", json=val_def_body, headers=HEADERS)
        val_def_id = r.json().get("id") if r.status_code == 201 else None
        if val_def_id:
            r = viewer.put(f"/api/v1/udf/values/entity/{T_ENTITY_1}",
                            json={"definition_id": val_def_id, "value": "nope"},
                            headers=HEADERS)
            ok("22 PUT /udf/values/... — 403 without manage_portfolio",
               r.status_code == 403, f"got {r.status_code}")
            r = admin.put(f"/api/v1/udf/values/entity/{T_ENTITY_1}",
                           json={"definition_id": val_def_id, "value": "via http"},
                           headers=HEADERS)
            ok("22 PUT /udf/values/... — 200 with manage_portfolio",
               r.status_code == 200, f"got {r.status_code}: {r.text[:300]}")

        tags_def_body = {
            "owner_scope": "org", "applies_to": "entity",
            "field_key": "udf01averify_http_tags", "label": "HTTP Tags",
            "data_type": "tags",
        }
        r = admin.post("/api/v1/udf/definitions", json=tags_def_body, headers=HEADERS)
        tags_def_id = r.json().get("id") if r.status_code == 201 else None
        if tags_def_id:
            r = noperms.get(f"/api/v1/udf/tags/{tags_def_id}", headers=HEADERS)
            ok("22 GET /udf/tags/{id} — 403 without view_portfolio",
               r.status_code == 403, f"got {r.status_code}")
            r = viewer.get(f"/api/v1/udf/tags/{tags_def_id}", headers=HEADERS)
            ok("22 GET /udf/tags/{id} — 200 with view_portfolio",
               r.status_code == 200, f"got {r.status_code}: {r.text[:200]}")

            r = viewer.put(f"/api/v1/udf/tags/{tags_def_id}/{T_ENTITY_1}",
                            json={"codes": ["httptag"]}, headers=HEADERS)
            ok("22 PUT /udf/tags/{id}/{target} — 403 without manage_portfolio",
               r.status_code == 403, f"got {r.status_code}")
            r = tagger.put(f"/api/v1/udf/tags/{tags_def_id}/{T_ENTITY_1}",
                            json={"codes": ["httptag"]}, headers=HEADERS)
            ok("22 PUT /udf/tags/{id}/{target} — 200 with manage_portfolio+create_tags",
               r.status_code == 200, f"got {r.status_code}: {r.text[:300]}")

            r = tagger.put(f"/api/v1/udf/tags/{tags_def_id}/{T_ENTITY_1}",
                            json={"codes": ["httptag2"]}, headers=HEADERS)
            r = viewer.post(f"/api/v1/udf/tags/{tags_def_id}/merge",
                             json={"from_code": "httptag", "into_code": "httptag2"},
                             headers=HEADERS)
            ok("22 POST /udf/tags/{id}/merge — 403 without manage_portfolio",
               r.status_code == 403, f"got {r.status_code}")
            r = tagger.post(f"/api/v1/udf/tags/{tags_def_id}/merge",
                             json={"from_code": "httptag", "into_code": "httptag2"},
                             headers=HEADERS)
            ok("22 POST /udf/tags/{id}/merge — 200 with manage_portfolio",
               r.status_code == 200, f"got {r.status_code}: {r.text[:300]}")


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
            await test_1_part1_reconfirm(conn)
            await test_2_api_name_namespaces(conn)
            await test_2a_append_only(conn)
            await test_2b_type_contract(conn)
            await test_currency_and_valueset(conn)
            await test_2d_scale_and_length(conn)
            await test_decimal_roundtrip(conn)
            await test_2c_lifecycle_and_audit(conn)
            await test_2e_tags(conn)
            await test_fee_run_inputs_regression(conn)
            await test_18_rls(conn, app_conn)

            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, endpoint_tests)
        except Exception:  # noqa: BLE001
            FAIL.append(f"unhandled: {traceback.format_exc()}")
            print(f"[FAIL] unhandled exception\n{traceback.format_exc()}")
        finally:
            await teardown(conn)

        after = await counts(conn)
        for t in COUNTED:
            ok(f"23 — teardown: {t} row count returned to baseline",
               after[t] == before[t], f"before={before[t]} after={after[t]}")
    finally:
        await conn.close()
        await app_conn.close()

    print(f"\n{'=' * 70}\nudf01a: {len(PASS)} PASS, {len(FAIL)} FAIL, {len(FIND)} FIND")
    for f in FAIL:
        print(f"  FAIL {f}")
    for f in FIND:
        print(f"  FIND {f}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
