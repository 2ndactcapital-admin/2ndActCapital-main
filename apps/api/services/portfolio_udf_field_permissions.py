"""Field-level security (FLS) for UDFs — Sprint udf01c.

THREE-STATE ACCESS, SAME COMBINATION LOGIC AS TAB VISIBILITY
──────────────────────────────────────────────────────────────────────────────
``portfolio.udf_field_permissions`` binds to the same two grantee paths
``udf_tab_permissions`` does (``profile_id`` xor ``permission_set_id``, one
CHECK enforcing exactly one) and is resolved with the identical
most-restrictive-wins combination :func:`~services.portfolio_udf_tabs.
resolve_tab_visibility` already uses — generalized from a boolean
(visible/hidden) to three ranked states (hidden < read < edit). No grant row
at all defaults to ``'edit'`` on a visible tab, mirroring 1b's "no grant ->
visible" default exactly, per this sprint's design note.

PRECEDENCE: TAB-HIDDEN WINS OUTRIGHT, BUT ONLY WHEN A TAB IS IN SCOPE
──────────────────────────────────────────────────────────────────────────────
:func:`resolve_tab_visibility` is called FIRST and, if the tab is hidden,
short-circuits to ``'hidden'`` without a single field-grant query — reused
verbatim, never reimplemented, per this sprint's explicit instruction.

``tab_id`` is nullable here on purpose. Only ``GET /udf/layouts/{tab_id}`` has
an unambiguous tab in view. ``GET /udf/definitions`` and
``GET``/``PUT /udf/values/{target_type}/{target_id}`` resolve/write a
definition directly, with no tab in the URL at all — and a definition is not
1:1 with a tab: ``udf_layout_items.definition_id`` can place the same field on
zero, one, or several tabs' layouts, so guessing "the" tab for those three
endpoints would be arbitrary. ``udf_field_permissions`` itself keys only on
``definition_id`` (no ``tab_id`` column), so the field-level grant is tab
-independent by construction. The design decision made here: when no tab is
in scope, the tab-hidden precedence step is skipped entirely (as if the tab
were visible) and only the two field-grant paths are evaluated. Only the
layout endpoint, which has a real tab_id, runs the full two-step precedence.

REAL RLS GAP — FOUND, NOT FIXED (Part 1 DDL, out of scope)
──────────────────────────────────────────────────────────────────────────────
``udf_field_permissions_org_isolation`` (introspected in Task 1) ties every
row's visibility to ``definition_id IN (SELECT id FROM udf_definitions WHERE
org_id = current_org_id)``, OR super-admin — the same shape
``udf_tab_permissions_org_isolation`` uses. For tabs that is airtight: a tab's
``org_id`` is NOT NULL, always. For fields it is not: a PLATFORM-scope
definition (``udf_definitions.org_id IS NULL``) can never satisfy
``org_id = current_org_id`` for any org, so a normal (non-super-admin)
``_OrgWrite`` connection can NEVER see or write a platform-scope field's
permission grants — ``NULLIF(...) = ...`` on a NULL org_id is never true.
Practically: FLS on a platform-scope field can only be configured by a
super-admin, and any resolution attempted under a normal org connection sees
zero grant rows and therefore always defaults to ``'edit'`` for everyone,
regardless of what a super-admin may have configured elsewhere (which IS
visible to a super-admin connection, since ``app.is_super_admin`` satisfies
the OR). This is a real, reportable asymmetry versus org/team/user-scope
fields, not a bug introduced here — Part 1's DDL is already applied and out
of scope for this sprint to alter.
"""

from __future__ import annotations

from typing import Any

from services.portfolio_assets import PortfolioError, _require_org
from services.portfolio_udf import UdfError, _lifecycle_write_scope, get_definition
from services.portfolio_udf_tabs import (
    TABLE_PERMISSION_SETS,
    TABLE_PROFILES,
    TABLE_USER_PERMISSION_SETS,
    TABLE_USERS,
    resolve_tab_visibility,
)

TABLE_UDF_FIELD_PERMISSIONS = "portfolio.udf_field_permissions"

ACCESS_HIDDEN = "hidden"
ACCESS_READ = "read"
ACCESS_EDIT = "edit"

#: Mirrors the deployed ``udf_field_permissions_access_check`` verbatim.
ACCESS_LEVELS = frozenset({ACCESS_HIDDEN, ACCESS_READ, ACCESS_EDIT})

#: Most-restrictive-first ranking — the 3-state generalization of
#: ``resolve_tab_visibility``'s ``all(r['is_visible'] for r in rows)``.
_ACCESS_RANK = {ACCESS_HIDDEN: 0, ACCESS_READ: 1, ACCESS_EDIT: 2}

#: The one-grantee CHECK, mirrored from the deployed constraint.
FIELD_PERM_ONE_GRANTEE_CHK = "udf_field_perm_one_grantee_chk"


class FieldPermissionError(UdfError):
    """A field-permission write was refused for a reason the caller can fix."""


class FieldGranteeError(FieldPermissionError):
    """``set_field_access`` was called with zero or two grantees, or a
    grantee that does not belong to the relevant org."""


class FieldAccessDeniedError(PortfolioError):
    """A write was attempted against a field the caller does not have
    ``'edit'`` access to.

    Deliberately NOT a :class:`~services.portfolio_udf.UdfError` subclass —
    the router's ``_VALIDATION_ERRORS`` tuple (422 mapping) does not catch
    this; it is caught separately and mapped to 403, the same 403-not-422
    treatment ``TagPermissionError`` already gets for the identical reason:
    this is an authorization refusal, not a malformed-input refusal.
    """


def _check_access(access: Any) -> str:
    if access not in ACCESS_LEVELS:
        raise FieldPermissionError(
            f"access={access!r} is not one of {sorted(ACCESS_LEVELS)} — this "
            f"vocabulary is mirrored from udf_field_permissions_access_check"
        )
    return access


async def _validate_grantee(conn, *, org_id: str, profile_id, permission_set_id) -> None:
    has_profile = bool(profile_id)
    has_set = bool(permission_set_id)
    if has_profile == has_set:
        raise FieldGranteeError(
            "exactly one of profile_id or permission_set_id is required — "
            f"matching {FIELD_PERM_ONE_GRANTEE_CHK}"
        )
    if has_profile:
        owns = await conn.fetchval(
            f"SELECT 1 FROM {TABLE_PROFILES} WHERE id = $1::uuid AND org_id = $2::uuid",
            str(profile_id), org_id,
        )
        if not owns:
            raise FieldGranteeError(f"profile {profile_id} does not belong to org {org_id}")
    else:
        owns = await conn.fetchval(
            f"SELECT 1 FROM {TABLE_PERMISSION_SETS} WHERE id = $1::uuid AND org_id = $2::uuid",
            str(permission_set_id), org_id,
        )
        if not owns:
            raise FieldGranteeError(
                f"permission_set {permission_set_id} does not belong to org {org_id}"
            )


# ── Task 2a: write ───────────────────────────────────────────────────────────


async def set_field_access(
    conn, *, definition_id: str, access: str, org_id: str,
    profile_id: str | None = None, permission_set_id: str | None = None,
    is_super_admin: bool = False, created_by: str | None = None,
) -> dict:
    """Upsert one grant row. Exactly one of ``profile_id``/``permission_set_id``.

    The write CONTEXT (``_OrgWrite`` for the definition's own org, or
    ``_SuperAdminWrite`` for a platform-scope definition) is resolved by
    ``portfolio_udf._lifecycle_write_scope`` — reused verbatim rather than
    reimplemented, so a caller cannot aim a field-permission write at a
    definition it does not own by supplying a different ``org_id`` any more
    than a lifecycle write (rename/retire) could.

    For a platform-scope definition the grantee still belongs to ONE
    specific org (the org whose admin is narrowing visibility of a platform
    field for their own profiles/permission-sets) — that org is ``org_id``
    as supplied by the caller, validated by :func:`_validate_grantee`
    exactly as for any org-scope field.
    """
    access = _check_access(access)
    definition = await get_definition(conn, definition_id=definition_id)
    if definition is None:
        raise FieldPermissionError(
            f"definition {definition_id} does not exist or is deleted"
        )
    write_ctx = await _lifecycle_write_scope(
        conn, definition, org_id=org_id, is_super_admin=is_super_admin
    )
    grantee_org = definition["org_id"] or _require_org(org_id)
    await _validate_grantee(
        conn, org_id=grantee_org, profile_id=profile_id, permission_set_id=permission_set_id
    )
    async with write_ctx as c:
        if profile_id:
            row = await c.fetchrow(
                f"""INSERT INTO {TABLE_UDF_FIELD_PERMISSIONS}
                        (definition_id, profile_id, access, created_by)
                    VALUES ($1::uuid, $2::uuid, $3, $4::uuid)
                    ON CONFLICT (definition_id, profile_id) WHERE profile_id IS NOT NULL
                    DO UPDATE SET access = EXCLUDED.access
                    RETURNING id::text, definition_id::text, profile_id::text,
                              permission_set_id::text, access, created_at,
                              created_by::text AS created_by""",
                str(definition_id), str(profile_id), access,
                str(created_by) if created_by else None,
            )
        else:
            row = await c.fetchrow(
                f"""INSERT INTO {TABLE_UDF_FIELD_PERMISSIONS}
                        (definition_id, permission_set_id, access, created_by)
                    VALUES ($1::uuid, $2::uuid, $3, $4::uuid)
                    ON CONFLICT (definition_id, permission_set_id)
                        WHERE permission_set_id IS NOT NULL
                    DO UPDATE SET access = EXCLUDED.access
                    RETURNING id::text, definition_id::text, profile_id::text,
                              permission_set_id::text, access, created_at,
                              created_by::text AS created_by""",
                str(definition_id), str(permission_set_id), access,
                str(created_by) if created_by else None,
            )
        return dict(row)


async def list_field_grants(conn, *, definition_id: str) -> list[dict]:
    """Every current grant row for one definition — admin visibility, Task 3a."""
    rows = await conn.fetch(
        f"""SELECT id::text AS id, definition_id::text AS definition_id,
                   profile_id::text AS profile_id,
                   permission_set_id::text AS permission_set_id,
                   access, created_at, created_by::text AS created_by
            FROM {TABLE_UDF_FIELD_PERMISSIONS}
            WHERE definition_id = $1::uuid
            ORDER BY created_at""",
        str(definition_id),
    )
    return [dict(r) for r in rows]


# ── Task 2b/2c: resolve ─────────────────────────────────────────────────────


async def _caller_grantee_ids(conn, *, org_id: str, user_id: str) -> tuple[str | None, list[str]]:
    profile_id = await conn.fetchval(
        f"SELECT profile_id FROM {TABLE_USERS} WHERE id = $1::uuid AND org_id = $2::uuid",
        str(user_id), org_id,
    )
    set_ids = [
        str(r["permission_set_id"]) for r in await conn.fetch(
            f"SELECT permission_set_id FROM {TABLE_USER_PERMISSION_SETS} WHERE user_id = $1::uuid",
            str(user_id),
        )
    ]
    return profile_id, set_ids


async def resolve_field_access(
    conn, *, definition_id: str, tab_id: str | None, org_id: str, user_id: str,
) -> str:
    """Is this field hidden/read/edit for this caller?

    Calls ``resolve_tab_visibility`` first — ONLY when ``tab_id`` is given —
    and returns ``'hidden'`` immediately if the tab is hidden, without
    touching ``udf_field_permissions`` at all. On a visible tab (or when no
    tab is in scope, see module docstring), most-restrictive wins across the
    profile-level and permission-set-level grants; no grant row at all
    defaults to ``'edit'``.
    """
    org_id = _require_org(org_id)
    if tab_id is not None:
        visible = await resolve_tab_visibility(
            conn, tab_id=tab_id, org_id=org_id, user_id=user_id
        )
        if not visible:
            return ACCESS_HIDDEN
    profile_id, set_ids = await _caller_grantee_ids(conn, org_id=org_id, user_id=user_id)
    rows = await conn.fetch(
        f"""SELECT access FROM {TABLE_UDF_FIELD_PERMISSIONS}
            WHERE definition_id = $1::uuid
              AND ((profile_id IS NOT NULL AND profile_id = $2::uuid)
                OR (permission_set_id IS NOT NULL AND permission_set_id = ANY($3::uuid[])))""",
        str(definition_id), profile_id, set_ids,
    )
    if not rows:
        return ACCESS_EDIT
    return min((r["access"] for r in rows), key=lambda a: _ACCESS_RANK[a])


async def resolve_field_access_bulk(
    conn, *, definition_ids: list[str], tab_id: str | None, org_id: str, user_id: str,
) -> dict[str, str]:
    """``{definition_id: access}`` for every id in ``definition_ids``, in a
    query count that does NOT grow with the number of fields.

    Tab visibility (if ``tab_id`` is given) is resolved ONCE, not once per
    field — a hidden tab short-circuits every field to ``'hidden'`` without
    ever touching ``udf_field_permissions``. On a visible tab (or no tab in
    scope), the caller's grantee ids are resolved once and the per-field
    grants are read in a SINGLE query keyed by ``= ANY($1::uuid[])`` rather
    than one query per definition_id. Fixed query count regardless of N:
    3 (tab check) + 2 (grantee ids) + 1 (bulk grants) = 6 when ``tab_id`` is
    given; 2 + 1 = 3 when it is not — reported explicitly by the caller.
    """
    org_id = _require_org(org_id)
    def_ids = [str(d) for d in definition_ids]
    if not def_ids:
        return {}
    if tab_id is not None:
        visible = await resolve_tab_visibility(
            conn, tab_id=tab_id, org_id=org_id, user_id=user_id
        )
        if not visible:
            return {d: ACCESS_HIDDEN for d in def_ids}
    profile_id, set_ids = await _caller_grantee_ids(conn, org_id=org_id, user_id=user_id)
    rows = await conn.fetch(
        f"""SELECT definition_id::text AS definition_id, access
            FROM {TABLE_UDF_FIELD_PERMISSIONS}
            WHERE definition_id = ANY($1::uuid[])
              AND ((profile_id IS NOT NULL AND profile_id = $2::uuid)
                OR (permission_set_id IS NOT NULL AND permission_set_id = ANY($3::uuid[])))""",
        def_ids, profile_id, set_ids,
    )
    by_def: dict[str, list[str]] = {}
    for r in rows:
        by_def.setdefault(r["definition_id"], []).append(r["access"])
    return {
        d: (ACCESS_EDIT if d not in by_def else min(by_def[d], key=lambda a: _ACCESS_RANK[a]))
        for d in def_ids
    }
