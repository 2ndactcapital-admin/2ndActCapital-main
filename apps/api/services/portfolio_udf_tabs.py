"""UDF tabs and tab visibility — Sprint udf01b, Task 2b/2c.

TABS ARE ORG-SCOPED ONLY — NO PLATFORM/TEAM/USER NAMESPACE
──────────────────────────────────────────────────────────────────────────────
``portfolio.udf_tabs`` has a NOT NULL ``org_id`` and no ``owner_scope`` column
at all (introspected in Task 1 — the Part 1 DDL never gave tabs the
four-namespace shape ``udf_definitions`` has). Every tab write therefore goes
through :class:`~services.portfolio_assets._OrgWrite` for the CALLING org and
nothing else; there is no platform-scope tab and no super-admin write path to
route around here, unlike ``portfolio_udf.py``'s definitions.

NO BI-TEMPORAL COLUMNS
──────────────────────────────────────────────────────────────────────────────
``udf_tabs`` carries ``is_active``/``deleted_at``/``deleted_by`` but no
``valid_from``/``valid_to``/``system_from``/``system_to`` — introspected, not
assumed, and a real difference from ``udf_definitions``. A label update is
therefore a plain in-place ``UPDATE``, not a valid-time restatement; Rule 3
in CLAUDE.md applies to tables that carry the versioning columns; this one
doesn't have them, so there is nothing to restate. There is also no
``udf_tab_audit`` table (none of the five Part 1 objects is an audit table) —
tab lifecycle changes are not logged the way definition changes are. That is
a real, reportable gap, not an oversight in this module: there is no table to
write to, and inventing one is schema work outside this sprint's Part 1.

TAB PERMISSIONS ARE A THIRD, SEPARATE PERMISSION SYSTEM
──────────────────────────────────────────────────────────────────────────────
``profile_id``/``permission_set_id`` on ``udf_tab_permissions`` key into
``public.profiles``/``public.permission_sets`` — the profile-layer system in
``services.profiles`` (``users.profile_id`` + ``user_permission_sets`` +
``profile_permissions``/``permission_set_permissions``). This is NOT
``services.rbac``'s roles/user_roles/role_permissions system that
``require_permission``/``has_permission`` (the router's tenant gate) use.
Three permission systems now exist in this codebase and none of them is
reimplemented here — ``resolve_tab_visibility`` reads ``users.profile_id``
and ``user_permission_sets`` directly, the same tables ``services.profiles``
reads, but answers a different question (per-tab visibility, not a
capability-key grant) so it does not call into ``services.profiles`` itself.
"""

from __future__ import annotations

from typing import Any

import asyncpg

from services.portfolio_assets import PortfolioError, _OrgWrite, _require_org
from services.portfolio_udf import APPLIES_TO, UdfError, _check_choice, _check_label

TABLE_UDF_TABS = "portfolio.udf_tabs"
TABLE_UDF_TAB_PERMISSIONS = "portfolio.udf_tab_permissions"
TABLE_UDF_LAYOUTS = "portfolio.udf_layouts"
TABLE_UDF_LAYOUT_SECTIONS = "portfolio.udf_layout_sections"
TABLE_UDF_LAYOUT_ITEMS = "portfolio.udf_layout_items"
TABLE_PROFILES = "public.profiles"
TABLE_PERMISSION_SETS = "public.permission_sets"
TABLE_USERS = "public.users"
TABLE_USER_PERMISSION_SETS = "public.user_permission_sets"

#: ``udf_tab_api_name_uq`` — partial unique on (org_id, applies_to, api_name)
#: WHERE deleted_at IS NULL.
TAB_API_NAME_UNIQUE_INDEX = "udf_tab_api_name_uq"


class TabError(UdfError):
    """A tab write was refused for a reason the caller can fix."""


class TabDuplicateError(TabError):
    """An active tab already exists in this (org, applies_to, api_name)."""


class TabImmutableError(TabError):
    """An attempt to change ``api_name``, which is immutable once set."""


class TabCapError(TabError):
    """``crm.udf.max_custom_tabs`` would be exceeded."""


class TabReferencedError(TabError):
    """Soft delete refused: the tab has a non-empty layout.

    Carries ``references`` — counted per referencing table — the same shape
    ``UdfReferencedError`` uses for definitions.
    """

    def __init__(self, message: str, *, references: dict[str, int] | None = None):
        super().__init__(message)
        self.references = references or {}


class TabGranteeError(TabError):
    """``set_tab_visibility`` was called with zero or two grantees, or a
    grantee that does not belong to the calling org."""


def _check_api_name(api_name: Any) -> str:
    if not isinstance(api_name, str) or not api_name.strip():
        raise TabError("api_name is required and must be a non-empty string")
    return api_name.strip()


def _tab_row(row) -> dict:
    return dict(row) if row is not None else None


_TAB_SELECT = """
    id::text AS id, org_id::text AS org_id, applies_to, label, api_name,
    display_order, is_active, created_at, created_by::text AS created_by,
    deleted_at, deleted_by::text AS deleted_by
"""


async def get_tab(conn, *, tab_id: str, include_deleted: bool = False) -> dict | None:
    """One tab by id, or ``None``. RLS is the tenant boundary — no ``org_id``
    filter here, matching ``portfolio_udf.get_definition``'s reasoning."""
    deleted_clause = "" if include_deleted else "AND deleted_at IS NULL"
    row = await conn.fetchrow(
        f"SELECT {_TAB_SELECT} FROM {TABLE_UDF_TABS} "
        f"WHERE id = $1::uuid {deleted_clause}",
        str(tab_id),
    )
    return _tab_row(row)


async def _count_active_tabs(conn, *, org_id: str, applies_to: str) -> int:
    return await conn.fetchval(
        f"SELECT count(*) FROM {TABLE_UDF_TABS} "
        f"WHERE org_id = $1::uuid AND applies_to = $2 "
        f"AND is_active = true AND deleted_at IS NULL",
        org_id, applies_to,
    )


_TAB_INSERT = f"""
INSERT INTO {TABLE_UDF_TABS}
    (org_id, applies_to, label, api_name, display_order, created_by)
VALUES ($1::uuid, $2, $3, $4, $5, $6::uuid)
RETURNING id::text
"""


async def create_tab(
    conn, *, org_id: str, applies_to: str, label: str, api_name: str,
    display_order: int = 0, created_by: str | None = None,
) -> dict:
    """Create an org-scoped tab. Enforces ``crm.udf.max_custom_tabs`` from
    ``org_settings`` at creation, counting ONLY active, non-deleted tabs for
    this ``(org_id, applies_to)`` — a deactivated or deleted tab does not
    occupy a cap slot, so retiring one always frees room for a replacement.
    """
    from services.org_settings import get_setting

    org_id = _require_org(org_id)
    applies_to = _check_choice(applies_to, APPLIES_TO, "applies_to")
    label = _check_label(label)
    api_name = _check_api_name(api_name)

    async with _OrgWrite(conn, org_id) as c:
        cap = int(await get_setting(c, org_id, "crm.udf.max_custom_tabs"))
        count = await _count_active_tabs(c, org_id=org_id, applies_to=applies_to)
        if count >= cap:
            raise TabCapError(
                f"org {org_id} already has {count} active tab(s) for "
                f"applies_to={applies_to!r}; crm.udf.max_custom_tabs={cap}. "
                f"Deactivate or delete an existing tab first, or raise the "
                f"cap in org_settings."
            )
        try:
            row = await c.fetchrow(
                _TAB_INSERT, org_id, applies_to, label, api_name,
                int(display_order), str(created_by) if created_by else None,
            )
        except asyncpg.UniqueViolationError as exc:
            constraint = getattr(exc, "constraint_name", None)
            raise TabDuplicateError(
                f"an active tab already exists for (org={org_id}, "
                f"applies_to={applies_to!r}, api_name={api_name!r}). Refused "
                f"by {constraint or TAB_API_NAME_UNIQUE_INDEX} in the "
                f"database."
            ) from exc
        return await get_tab(c, tab_id=row["id"])


async def update_tab(
    conn, *, tab_id: str, org_id: str, changes: dict[str, Any],
) -> dict:
    """Sparse PATCH. Only ``label`` is mutable; ``api_name`` present in
    ``changes`` with a different value raises :class:`TabImmutableError`
    before anything else runs, mirroring ``portfolio_udf.update_definition``.
    """
    org_id = _require_org(org_id)
    current = await get_tab(conn, tab_id=tab_id)
    if current is None:
        raise TabError(f"tab {tab_id} does not exist or is deleted")
    if current["org_id"] != org_id:
        raise TabError(f"tab {tab_id} belongs to a different org")

    if "api_name" in changes and changes["api_name"] != current["api_name"]:
        raise TabImmutableError(
            f"api_name is immutable once set (current={current['api_name']!r}, "
            f"attempted={changes['api_name']!r}). label is free to change; "
            f"api_name is not."
        )
    if "label" not in changes:
        return current

    label = _check_label(changes["label"])
    async with _OrgWrite(conn, org_id) as c:
        await c.execute(
            f"UPDATE {TABLE_UDF_TABS} SET label = $2 WHERE id = $1::uuid",
            str(tab_id), label,
        )
        return await get_tab(c, tab_id=tab_id)


async def _set_tab_active(
    conn, *, tab_id: str, org_id: str, target_active: bool,
) -> dict:
    org_id = _require_org(org_id)
    current = await get_tab(conn, tab_id=tab_id)
    if current is None:
        raise TabError(f"tab {tab_id} does not exist or is deleted")
    if current["org_id"] != org_id:
        raise TabError(f"tab {tab_id} belongs to a different org")
    if current["is_active"] == target_active:
        return current
    async with _OrgWrite(conn, org_id) as c:
        await c.execute(
            f"UPDATE {TABLE_UDF_TABS} SET is_active = $2 WHERE id = $1::uuid",
            str(tab_id), target_active,
        )
        return await get_tab(c, tab_id=tab_id)


async def deactivate_tab(conn, *, tab_id: str, org_id: str) -> dict:
    return await _set_tab_active(conn, tab_id=tab_id, org_id=org_id, target_active=False)


async def reactivate_tab(conn, *, tab_id: str, org_id: str) -> dict:
    return await _set_tab_active(conn, tab_id=tab_id, org_id=org_id, target_active=True)


async def get_tab_references(conn, *, tab_id: str) -> dict[str, int]:
    """How many sections/items exist under this tab's layout(s).

    A tab may have a layout ROW (``udf_layouts.tab_id``) with zero sections —
    that is an EMPTY layout and does not block deletion, per the sprint's own
    wording ("non-empty layout"). Sections and items are counted separately
    so the caller sees exactly what would need clearing.
    """
    sections = await conn.fetchval(
        f"""SELECT count(*) FROM {TABLE_UDF_LAYOUT_SECTIONS} s
            JOIN {TABLE_UDF_LAYOUTS} l ON l.id = s.layout_id
            WHERE l.tab_id = $1::uuid""",
        str(tab_id),
    )
    items = await conn.fetchval(
        f"""SELECT count(*) FROM {TABLE_UDF_LAYOUT_ITEMS} i
            JOIN {TABLE_UDF_LAYOUT_SECTIONS} s ON s.id = i.section_id
            JOIN {TABLE_UDF_LAYOUTS} l ON l.id = s.layout_id
            WHERE l.tab_id = $1::uuid""",
        str(tab_id),
    )
    refs: dict[str, int] = {}
    if sections:
        refs["udf_layout_sections"] = sections
    if items:
        refs["udf_layout_items"] = items
    return refs


async def soft_delete_tab(conn, *, tab_id: str, org_id: str) -> dict:
    org_id = _require_org(org_id)
    current = await get_tab(conn, tab_id=tab_id)
    if current is None:
        raise TabError(f"tab {tab_id} does not exist or is already deleted")
    if current["org_id"] != org_id:
        raise TabError(f"tab {tab_id} belongs to a different org")
    refs = await get_tab_references(conn, tab_id=tab_id)
    if refs:
        raise TabReferencedError(
            f"tab {tab_id} has a non-empty layout and cannot be deleted: "
            f"{refs}. Remove the layout's sections/items first.",
            references=refs,
        )
    async with _OrgWrite(conn, org_id) as c:
        await c.execute(
            f"UPDATE {TABLE_UDF_TABS} SET deleted_at = now(), deleted_by = $2::uuid "
            f"WHERE id = $1::uuid",
            str(tab_id), None,
        )
        return await get_tab(c, tab_id=tab_id, include_deleted=True)


async def undelete_tab(conn, *, tab_id: str, org_id: str) -> dict:
    org_id = _require_org(org_id)
    current = await get_tab(conn, tab_id=tab_id, include_deleted=True)
    if current is None:
        raise TabError(f"tab {tab_id} does not exist")
    if current["org_id"] != org_id:
        raise TabError(f"tab {tab_id} belongs to a different org")
    if current["deleted_at"] is None:
        return current
    async with _OrgWrite(conn, org_id) as c:
        await c.execute(
            f"UPDATE {TABLE_UDF_TABS} SET deleted_at = NULL, deleted_by = NULL "
            f"WHERE id = $1::uuid",
            str(tab_id),
        )
        return await get_tab(c, tab_id=tab_id)


async def list_active_tabs(conn, *, org_id: str, applies_to: str) -> list[dict]:
    """Every active, non-deleted tab for ``(org_id, applies_to)`` — NOT yet
    filtered by visibility. Callers needing the visible subset should use
    :func:`list_visible_tabs`."""
    org_id = _require_org(org_id)
    applies_to = _check_choice(applies_to, APPLIES_TO, "applies_to")
    rows = await conn.fetch(
        f"SELECT {_TAB_SELECT} FROM {TABLE_UDF_TABS} "
        f"WHERE org_id = $1::uuid AND applies_to = $2 "
        f"AND is_active = true AND deleted_at IS NULL "
        f"ORDER BY display_order, label",
        org_id, applies_to,
    )
    return [_tab_row(r) for r in rows]


# ── Task 2c: tab permissions ────────────────────────────────────────────────


async def _validate_grantee(conn, *, org_id: str, profile_id, permission_set_id) -> None:
    has_profile = bool(profile_id)
    has_set = bool(permission_set_id)
    if has_profile == has_set:
        raise TabGranteeError(
            "exactly one of profile_id or permission_set_id is required — "
            "matching udf_tab_perm_one_grantee_chk"
        )
    if has_profile:
        owns = await conn.fetchval(
            f"SELECT 1 FROM {TABLE_PROFILES} WHERE id = $1::uuid AND org_id = $2::uuid",
            str(profile_id), org_id,
        )
        if not owns:
            raise TabGranteeError(f"profile {profile_id} does not belong to org {org_id}")
    else:
        owns = await conn.fetchval(
            f"SELECT 1 FROM {TABLE_PERMISSION_SETS} WHERE id = $1::uuid AND org_id = $2::uuid",
            str(permission_set_id), org_id,
        )
        if not owns:
            raise TabGranteeError(
                f"permission_set {permission_set_id} does not belong to org {org_id}"
            )


async def set_tab_visibility(
    conn, *, tab_id: str, org_id: str, is_visible: bool,
    profile_id: str | None = None, permission_set_id: str | None = None,
) -> dict:
    """Upsert one grant row. Exactly one of ``profile_id``/``permission_set_id``
    — enforced here AND by ``udf_tab_perm_one_grantee_chk`` in the database.

    The conflict target repeats the relevant PARTIAL unique index's column
    list and predicate — ``udf_tab_perm_profile_uq`` or
    ``udf_tab_perm_set_uq`` — the same reasoning ``portfolio_udf``'s
    ``_VALUE_CONFLICT_TARGET`` documents: there is no total unique constraint
    on this table, so ``ON CONFLICT ON CONSTRAINT`` cannot be used.
    """
    org_id = _require_org(org_id)
    tab = await get_tab(conn, tab_id=tab_id)
    if tab is None:
        raise TabError(f"tab {tab_id} does not exist or is deleted")
    if tab["org_id"] != org_id:
        raise TabError(f"tab {tab_id} belongs to a different org")
    await _validate_grantee(
        conn, org_id=org_id, profile_id=profile_id, permission_set_id=permission_set_id
    )
    async with _OrgWrite(conn, org_id) as c:
        if profile_id:
            row = await c.fetchrow(
                f"""INSERT INTO {TABLE_UDF_TAB_PERMISSIONS}
                        (tab_id, profile_id, is_visible)
                    VALUES ($1::uuid, $2::uuid, $3)
                    ON CONFLICT (tab_id, profile_id) WHERE profile_id IS NOT NULL
                    DO UPDATE SET is_visible = EXCLUDED.is_visible
                    RETURNING id::text, tab_id::text, profile_id::text,
                              permission_set_id::text, is_visible""",
                str(tab_id), str(profile_id), bool(is_visible),
            )
        else:
            row = await c.fetchrow(
                f"""INSERT INTO {TABLE_UDF_TAB_PERMISSIONS}
                        (tab_id, permission_set_id, is_visible)
                    VALUES ($1::uuid, $2::uuid, $3)
                    ON CONFLICT (tab_id, permission_set_id) WHERE permission_set_id IS NOT NULL
                    DO UPDATE SET is_visible = EXCLUDED.is_visible
                    RETURNING id::text, tab_id::text, profile_id::text,
                              permission_set_id::text, is_visible""",
                str(tab_id), str(permission_set_id), bool(is_visible),
            )
        return dict(row)


async def resolve_tab_visibility(conn, *, tab_id: str, org_id: str, user_id: str) -> bool:
    """Is this tab visible to this user? Most-restrictive-wins across both
    grantee paths; no grant row at all defaults to VISIBLE.

    This default is directionally consistent with ``rbac.has_permission``'s
    own "no roles assigned yet -> default allow" bootstrap posture (absence
    of a restriction does not manufacture one), but it is NOT the same
    mechanism: ``rbac``'s default-allow triggers on the USER having zero
    roles anywhere; this one triggers on the TAB having zero grant rows for
    this user specifically, and a user who is on the losing end of some
    OTHER tab's restriction is unaffected. ``services.profiles`` (the third
    permission system, profile/permission-set CAPABILITY grants) is the
    opposite polarity by design — an absent ``profile_permissions``/
    ``permission_set_permissions`` row there means NOT granted. Tab
    visibility is a display default, not a capability grant, and this sprint
    deliberately makes it open-by-default; that is a real, worth-recording
    asymmetry across the three systems, not a bug in any one of them.
    """
    org_id = _require_org(org_id)
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
    rows = await conn.fetch(
        f"""SELECT is_visible FROM {TABLE_UDF_TAB_PERMISSIONS}
            WHERE tab_id = $1::uuid
              AND ((profile_id IS NOT NULL AND profile_id = $2::uuid)
                OR (permission_set_id IS NOT NULL AND permission_set_id = ANY($3::uuid[])))""",
        str(tab_id), profile_id, set_ids,
    )
    if not rows:
        return True
    return all(r["is_visible"] for r in rows)


async def list_visible_tabs(conn, *, org_id: str, user_id: str, applies_to: str) -> list[dict]:
    """Every active tab for ``(org_id, applies_to)`` this user's grants do not
    hide. Filtered server-side, same non-bypassing principle as RLS — a
    caller cannot request the full list and filter client-side, because the
    hidden rows are never returned in the first place.
    """
    tabs = await list_active_tabs(conn, org_id=org_id, applies_to=applies_to)
    visible = []
    for tab in tabs:
        if await resolve_tab_visibility(conn, tab_id=tab["id"], org_id=org_id, user_id=user_id):
            visible.append(tab)
    return visible
