"""Per-org (white-label) settings — Sprint 24.

Ripasso is the licensable software product; each client firm — 2nd Act Capital
is client #1 — is a tenant org whose branding, footer, locale and vocabulary
live in ``org_settings``.

Schema (from docs/schema_snapshot.sql — NOT bitemporal):

    org_settings(id, org_id, setting_key, setting_value jsonb NOT NULL,
                 category, is_public, updated_at, updated_by, created_at)
    UNIQUE org_settings_org_id_setting_key_key: (org_id, setting_key)

``setting_value`` is ``jsonb NOT NULL``, so scalars must be JSON-encoded on the
way in ('"USD"'::jsonb, not 'USD') and decoded on the way out. Writes are a
plain upsert on the natural key — Rule 3 (bi-temporal) does not apply here.

THIS FILE IS THE ONE PLACE IN APPLICATION CODE ALLOWED TO CONTAIN LITERAL
2nd Act BRAND VALUES. DEFAULT_SETTINGS *is* the default data — it is what a
newly-created org renders with before its Org Admin has configured anything,
which is what keeps client onboarding from landing on an unstyled app. Every
other module must resolve these through get_setting / get_all_settings.
"""

import json

from services.rbac import can_manage_org_settings, load_principal

# ── Defaults ──────────────────────────────────────────────────────────────
# Mirrors the values seeded for 2nd Act Capital. Any org that has not set a
# given key resolves to the value here. Categories must match the `category`
# column so the admin screens can group consistently.

DEFAULT_SETTINGS: dict[str, object] = {
    # branding — colours
    "brand.color.navy": "#1B2B4B",
    "brand.color.gold": "#C5A880",
    "brand.color.gold_light": "#E8D5A3",
    "brand.color.slate_blue": "#9AA6BF",
    "brand.color.bg_app": "#FAF9F6",
    "brand.color.bg_sidebar": "#F5F1EB",
    "brand.color.bg_card": "#FFFFFF",
    "brand.color.text_primary": "#0F172A",
    "brand.color.text_secondary": "#334155",
    "brand.color.text_muted": "#64748B",
    "brand.color.border": "#E2E8F0",
    # branding — identity
    "brand.name": "2nd Act Capital",
    "brand.short_name": "2nd Act",
    "brand.logo_url": None,
    "brand.favicon_url": None,
    # branding — type
    "brand.font.display": "Spectral",
    "brand.font.body": "Hanken Grotesk",
    # footer
    "footer.privacy_url": "/privacy",
    "footer.terms_url": "/terms",
    "footer.support_email": None,
    # locale
    "locale.base_currency": "USD",
    # naming
    "naming.member_label": "Member",
    "naming.deal_label": "Deal",
}

# Category per key, used when a key is written for the first time and when
# grouping the admin editors. Derived from the key namespace.
CATEGORY_BY_PREFIX = {
    "brand.": "branding",
    "footer.": "footer",
    "locale.": "locale",
    "naming.": "naming",
}

DEFAULT_CATEGORY = "general"


def category_for(key: str) -> str:
    for prefix, category in CATEGORY_BY_PREFIX.items():
        if key.startswith(prefix):
            return category
    return DEFAULT_CATEGORY


class SettingsPermissionError(Exception):
    """Raised when a caller may not write the requested org's settings."""


def _decode(value):
    """asyncpg returns jsonb as a str; decode to the Python value."""
    if isinstance(value, (str, bytes, bytearray)):
        try:
            return json.loads(value)
        except (ValueError, TypeError):
            return value
    return value


# ── Reads ─────────────────────────────────────────────────────────────────
# Open to any authenticated user of the org: the theme cannot render without
# them. No permission check here by design.


async def get_setting(conn, org_id, key: str):
    """Return the org's value for ``key``, falling back to DEFAULT_SETTINGS.

    Returns None for a key that is neither set nor defaulted.
    """
    row = await conn.fetchrow(
        "SELECT setting_value FROM org_settings "
        "WHERE org_id = $1 AND setting_key = $2",
        org_id, key,
    )
    if row is None:
        return DEFAULT_SETTINGS.get(key)
    return _decode(row["setting_value"])


async def get_all_settings(conn, org_id) -> dict:
    """Return every setting for the org, defaults filled in for unset keys.

    This is the bulk fetch that hydrates the frontend theme provider on page
    load — one round trip for the whole brand.
    """
    rows = await conn.fetch(
        "SELECT setting_key, setting_value FROM org_settings WHERE org_id = $1",
        org_id,
    )
    resolved = dict(DEFAULT_SETTINGS)
    for row in rows:
        resolved[row["setting_key"]] = _decode(row["setting_value"])
    return resolved


async def get_brand_name(pool_or_conn, org_id) -> str:
    """The tenant's display name, for prose that must name the firm.

    Used by the AI system prompts, which previously hardcoded one client's
    name. Accepts a pool or a connection so callers can use whichever they
    already hold. Never raises — falls back to the default brand name.
    """
    try:
        if hasattr(pool_or_conn, "acquire"):
            async with pool_or_conn.acquire() as conn:
                return await get_setting(conn, org_id, "brand.name")
        return await get_setting(pool_or_conn, org_id, "brand.name")
    except Exception:
        return DEFAULT_SETTINGS["brand.name"]


async def get_public_settings(conn, org_id) -> dict:
    """Only the is_public settings, defaults filled in.

    Safe to serve unauthenticated — this is what brands the login screen.
    """
    rows = await conn.fetch(
        "SELECT setting_key, setting_value FROM org_settings "
        "WHERE org_id = $1 AND is_public = true",
        org_id,
    )
    resolved = dict(DEFAULT_SETTINGS)
    for row in rows:
        resolved[row["setting_key"]] = _decode(row["setting_value"])
    return resolved


async def get_settings_detail(conn, org_id) -> list[dict]:
    """Like get_all_settings but annotated for the admin editors.

    Each entry carries its category and whether the value is the org's own or
    inherited from DEFAULT_SETTINGS, so the UI can show "not yet configured".
    """
    rows = await conn.fetch(
        "SELECT setting_key, setting_value, category, is_public, updated_at "
        "FROM org_settings WHERE org_id = $1",
        org_id,
    )
    stored = {r["setting_key"]: r for r in rows}

    detail = []
    for key in sorted(set(DEFAULT_SETTINGS) | set(stored)):
        row = stored.get(key)
        detail.append({
            "key": key,
            "value": _decode(row["setting_value"]) if row else DEFAULT_SETTINGS.get(key),
            "category": row["category"] if row else category_for(key),
            "is_public": row["is_public"] if row else True,
            "is_default": row is None,
            "updated_at": row["updated_at"] if row else None,
        })
    return detail


# ── Writes ────────────────────────────────────────────────────────────────


async def set_setting(conn, org_id, key: str, value, updated_by, *, principal=None):
    """Upsert one setting on (org_id, setting_key).

    Permission: super_admin (any org) or org_admin (own org only). ``principal``
    may be passed pre-loaded; otherwise it is read from ``updated_by``. Raises
    SettingsPermissionError when the caller is not allowed — the router maps
    that to HTTP 403.
    """
    if principal is None:
        principal = await load_principal(conn, updated_by)

    if not can_manage_org_settings(principal, org_id):
        role = (principal or {}).get("role") or "unknown"
        raise SettingsPermissionError(
            f"Role '{role}' may not manage settings for org {org_id}"
        )

    # json.dumps handles every scalar correctly: "USD" -> '"USD"', None ->
    # 'null', True -> 'true'. Passing the raw scalar would violate the jsonb
    # NOT NULL column.
    encoded = json.dumps(value)

    await conn.execute(
        """
        INSERT INTO org_settings
            (org_id, setting_key, setting_value, category, updated_by, updated_at)
        VALUES ($1, $2, $3::jsonb, $4, $5, now())
        ON CONFLICT (org_id, setting_key) DO UPDATE
            SET setting_value = EXCLUDED.setting_value,
                updated_by    = EXCLUDED.updated_by,
                updated_at    = now()
        """,
        org_id, key, encoded, category_for(key), updated_by,
    )
    return value


async def set_settings(conn, org_id, values: dict, updated_by, *, principal=None):
    """Upsert several settings under a single permission check."""
    if principal is None:
        principal = await load_principal(conn, updated_by)

    if not can_manage_org_settings(principal, org_id):
        role = (principal or {}).get("role") or "unknown"
        raise SettingsPermissionError(
            f"Role '{role}' may not manage settings for org {org_id}"
        )

    for key, value in values.items():
        await set_setting(
            conn, org_id, key, value, updated_by, principal=principal
        )
    return await get_all_settings(conn, org_id)
