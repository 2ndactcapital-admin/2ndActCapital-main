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
from services.ta_config import (
    DEFAULT_CALIBRATION_MIN_YEARS,
    DEFAULT_PERIODS_PER_YEAR,
    DEFAULT_PROJECTION_HORIZON_YEARS,
    DEFAULT_TA_STRATEGY_PARAMS,
    TA_CALIBRATION_MIN_YEARS_KEY,
    TA_DEFAULT_PERIODS_PER_YEAR_KEY,
    TA_PROJECTION_HORIZON_YEARS_KEY,
    TA_STRATEGY_DEFAULTS_KEY,
)

# ── Keys owned by user management ─────────────────────────────────────────
# Named constants because two other modules resolve them (services.invites for
# the expiry, routers.admin for the inactivity window) and a typo'd key string
# would silently resolve to None and then to a hardcoded fallback — a config
# that looks configured and is not.
INVITE_EXPIRY_DAYS_KEY = "invite.expiry_days"
USER_INACTIVITY_TIMEOUT_DAYS_KEY = "user.inactivity_timeout_days"

# Keys whose value MUST be a positive whole number of days. The settings editor
# posts every field as a string, so "14" has to become 14 before it is stored —
# otherwise the jsonb holds '"14"' and `int(value)` at read time works by
# accident until someone stores "two weeks". Coerced and validated on write.
POSITIVE_INT_DAY_KEYS = (
    INVITE_EXPIRY_DAYS_KEY,
    USER_INACTIVITY_TIMEOUT_DAYS_KEY,
)

# Upper bound, applied to both. Not arbitrary caution: an invite token is a
# bearer credential that grants an account in the org, and an unbounded expiry
# turns it into a permanent one.
MAX_SETTING_DAYS = 3650

# ── Secrets that live in this table but must never leave it ───────────────
# org_settings is a general-purpose per-org config store, and the fee module's
# account-number hash salt (Sprint fee31) has to live somewhere per-org. It
# landed here — but it is NOT configuration, it is a credential.
#
# THE HOLE THIS CLOSES. `is_public = false` keeps the salt out of
# get_public_settings, which is what /theme/public serves unauthenticated. That
# is necessary and NOT sufficient: this router's own read is documented as
# "open to any authenticated user of the org (the app cannot render its theme
# otherwise)". So every ordinary member could GET /orgs/{org_id}/settings and
# read the salt — and the salt is the only thing standing between a leaked
# `accounts.account_number_hash` column and brute-forcing the account numbers
# back out, since an account number is short enough to enumerate unsalted.
#
# Filtered on the READ side rather than by moving the value to another table:
# the read paths are the two functions below and both are covered here, whereas
# a second storage location would be a second set of RLS policies and grants to
# get right. Consumers that legitimately need a secret read it by key through
# get_setting, which is deliberately NOT filtered — a caller naming the key
# explicitly is asking for it, and services/custody/registry.py is the only one
# that does.
SECRET_SETTING_KEYS = frozenset({"custody.account_hash_salt"})

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
    # ai — which model each call path uses (mini-bedrock sprint). Resolved by
    # services/extraction.resolve_model so switching a client (or the whole
    # platform, e.g. a future AWS Bedrock move) to another model or provider is
    # a settings change, not a code change. These string literals are the ONE
    # allowed home for a model name in application code.
    "ai.model.default": "claude-haiku-4-5-20251001",
    "ai.model.provider": "anthropic",
    "ai.model.fallback": "claude-haiku-4-5-20251001",
    # Sprint 27 (TaskRouter) — the ORDERED fallback CHAIN the central resolver
    # actually walks (services/extraction.resolve_fallback_chain). Replaces the
    # single, never-consumed ai.model.fallback above. A one-item array here
    # preserves mini-bedrock behaviour exactly: primary (haiku) + [haiku]
    # dedupes to a single haiku call. An org_admin may configure a longer,
    # per-org chain (e.g. [primary, cheaper-backup]) without any code change.
    "ai.model.fallback_chain": ["claude-haiku-4-5-20251001"],
    "ai.model.assistant": "claude-sonnet-4-6",
    # Task-specific override for the S25 document-type classifier. Defaults to
    # the same Haiku model as ai.model.default; an org_admin may raise it to a
    # stronger model per-org. Resolved via extraction.resolve_model with
    # key=DOCUMENT_CLASSIFIER_MODEL_KEY (falls back to ai.model.default).
    "ai.model.document_classifier": "claude-haiku-4-5-20251001",
    # Chancery Phase 11b — the org's semantic-embedding provider + model. Same
    # dotted ai.* namespace / auto-categorization as ai.model.* above. Every org
    # defaults to Voyage, which is the ONLY functionally-enabled provider right
    # now (see the write-time validation below and services/document_embedding).
    # An org_admin may see OpenAI/Google/Cohere in the settings dropdown, but the
    # backend REJECTS setting any of them until they are actually enabled.
    "ai.embedding.provider": "voyage",
    "ai.embedding.model": "voyage-3.5",
    "ai.embedding.dimensions": 1024,
    # Portfolio Phase B — the ORDERED list of `positions.source_system` values,
    # most-trusted first, deciding which of several sources reporting the same
    # holding is the portfolio's answer (design V6 §1.1). Same shape as
    # ai.model.fallback_chain above: a JSON array that is meaningful as data.
    # A firm that trusts its custodian over its reporting tool re-orders this
    # and deploys nothing.
    #
    # The literal lives HERE and not in services/portfolio_precedence, per this
    # module's own docstring: DEFAULT_SETTINGS *is* the default data. It also
    # keeps the dependency one-directional — portfolio_precedence imports this,
    # never the reverse — which is what lets `_validate_setting` reach back into
    # the precedence validator with a lazy import instead of a cycle.
    #
    # Ordering rationale: a reporting tool sits DOWNSTREAM of the custodian it
    # was fed, having already reconciled that feed, so it outranks `altruist`
    # rather than competing with it. `manual` is last — a human typing a number
    # exists so an asset with no feed still has a position, not so that it can
    # overrule one that does.
    "portfolio.precedence.source_order": [
        "reporting_tool_bd",
        "reporting_tool_addepar",
        "reporting_tool_orion",
        "reporting_tool_apx",
        "reporting_tool_import",
        "altruist",
        "spv_subscriptions",
        "chancery",
        "manual",
    ],
    # ── User management (this sprint) ──────────────────────────────────────
    # How long an admin-minted invite token stays redeemable. Was the module
    # constant services.invites.INVITE_TTL_DAYS; that constant now READS this
    # value, so an org that never configures the key behaves exactly as before.
    # A firm that runs slower onboarding raises it and deploys nothing.
    INVITE_EXPIRY_DAYS_KEY: 7,
    # How long a member may go without signing in before the account is
    # considered dormant. Surfaced on the user-management screen and resolved by
    # routers.admin alongside users.last_login_at. Deliberately REPORTING ONLY:
    # nothing auto-deactivates on this threshold, because an automatic lockout
    # that no admin initiated is not something to ship without a decision. The
    # column and the setting are what a later sprint would need to enforce it.
    USER_INACTIVITY_TIMEOUT_DAYS_KEY: 90,
    # ── UDF definitions layer (udf01a) ─────────────────────────────────────
    # The caps the UDF layer enforces. They live HERE, as DEFAULT_SETTINGS, and
    # not as module constants in services/portfolio_udf, for the reason this
    # module's own docstring gives: DEFAULT_SETTINGS *is* the default data.
    #
    # This is also the only workable home for a "platform default". org_settings
    # .org_id is NOT NULL with an FK to organizations and the table has no
    # owner_scope column, so there is no platform-scope ROW to seed — the
    # platform default is the fallback in this dict, and an org_settings row is
    # an org's OVERRIDE of it. get_setting already resolves in that order, so an
    # org that configures nothing gets these values without any row existing.
    "crm.udf.max_custom_tabs": 3,
    "crm.udf.max_fields_per_target": 100,
    "crm.udf.max_value_set_values": 500,
    "crm.udf.max_tags_per_record": 25,
    "crm.udf.max_tag_vocabulary": 500,
    # 131072 = 128 KiB. Bounds long_text/rich_text `length` in the type contract.
    "crm.udf.max_rich_text_chars": 131072,
    # udf01b — layout shape caps, same "crm.udf." prefix as the six above.
    "crm.udf.max_sections_per_layout": 10,
    "crm.udf.max_items_per_section": 20,
    # ── TA Model Sprint 1 ────────────────────────────────────────────────────
    # Sourced from services.ta_config (the single owner of these literals) so
    # an org that has never called PUT /admin/modeling/ta/defaults still gets
    # usable TA parameters from GET /modeling/ta/defaults, exactly like every
    # other category here — a key present in org_settings with no per-org row
    # must resolve to SOMETHING, never to None. Task 2's 4 seed rows write the
    # SAME values into the default org's own rows; this is what every OTHER
    # org (never explicitly seeded) falls back to.
    TA_STRATEGY_DEFAULTS_KEY: DEFAULT_TA_STRATEGY_PARAMS,
    TA_PROJECTION_HORIZON_YEARS_KEY: DEFAULT_PROJECTION_HORIZON_YEARS,
    TA_DEFAULT_PERIODS_PER_YEAR_KEY: DEFAULT_PERIODS_PER_YEAR,
    TA_CALIBRATION_MIN_YEARS_KEY: DEFAULT_CALIBRATION_MIN_YEARS,
}

# Category per key, used when a key is written for the first time and when
# grouping the admin editors. Derived from the key namespace.
CATEGORY_BY_PREFIX = {
    "brand.": "branding",
    "footer.": "footer",
    "locale.": "locale",
    "naming.": "naming",
    "ai.": "ai",
    "portfolio.": "portfolio",
    # Both new user-management keys group under one heading on the settings
    # screen — an admin configuring invite lifetime and dormancy is doing one
    # job, not two.
    "invite.": "membership",
    "user.": "membership",
    # udf01a — the UDF caps group under one heading; an admin raising a field
    # limit and a tag limit is doing one job.
    "crm.udf.": "crm",
    # TA Model Sprint 1 — modeling.ta.* (services.ta_config.TA_SETTINGS_KEYS).
    "modeling.": "modeling",
}

DEFAULT_CATEGORY = "general"


def category_for(key: str) -> str:
    for prefix, category in CATEGORY_BY_PREFIX.items():
        if key.startswith(prefix):
            return category
    return DEFAULT_CATEGORY


class SettingsPermissionError(Exception):
    """Raised when a caller may not write the requested org's settings."""


class SettingsValidationError(Exception):
    """Raised when a setting's VALUE is not allowed (router maps this to 400).

    Distinct from SettingsPermissionError (403): the caller MAY write settings,
    but the specific value is rejected — e.g. selecting an embedding provider
    that is not yet functionally enabled.
    """


def _coerce_setting(key: str, value):
    """Normalise a value before validation/storage. Returns the value to store.

    Only the positive-integer day keys need this today. The settings editor is a
    text field, so it posts ``"14"``; storing that verbatim gives a jsonb string
    where every reader expects a number. Coercing on the WRITE side means the
    read side (``resolve_invite_ttl_days``) never has to guess which shape it
    got. A non-numeric string is left alone so ``_validate_setting`` can reject
    it with a message about the actual key, rather than this function raising a
    bare ValueError.
    """
    if key in POSITIVE_INT_DAY_KEYS and isinstance(value, str):
        stripped = value.strip()
        if stripped.isdigit():
            return int(stripped)
    return value


def _validate_setting(key: str, value) -> None:
    """Reject values that are not allowed for a given key.

    This is the SOURCE-OF-TRUTH backend enforcement (Chancery Phase 11b): an org
    may SEE the full embedding-provider landscape in the UI, but only Voyage is
    functionally enabled, so any attempt to SET a non-Voyage provider is rejected
    here — not merely hidden in the client, which could be bypassed by calling
    the API directly. Clearing the key (value None) resets it to the default
    (Voyage) and is allowed.
    """
    if key == "ai.embedding.provider" and value is not None:
        # Lazy import avoids a module-load cycle (document_embedding imports
        # get_setting from this module).
        from services.document_embedding import (
            EMBEDDING_PROVIDER_DISABLED_MSG,
            ENABLED_EMBEDDING_PROVIDERS,
        )
        if str(value).lower() not in ENABLED_EMBEDDING_PROVIDERS:
            raise SettingsValidationError(EMBEDDING_PROVIDER_DISABLED_MSG)

    if key in POSITIVE_INT_DAY_KEYS and value is not None:
        # Clearing the key (None) resets it to the DEFAULT_SETTINGS value and is
        # allowed — that is how an org un-configures a setting everywhere else
        # in this module, and the same must hold here.
        if isinstance(value, bool) or not isinstance(value, int):
            raise SettingsValidationError(
                f"{key} must be a whole number of days, got {value!r}"
            )
        if value < 1 or value > MAX_SETTING_DAYS:
            raise SettingsValidationError(
                f"{key} must be between 1 and {MAX_SETTING_DAYS} days, got {value}"
            )

    if key == "portfolio.precedence.source_order" and value is not None:
        # Same lazy-import shape as above, same reason: portfolio_precedence
        # imports DEFAULT_SETTINGS from this module, so a top-level import here
        # would be a cycle.
        #
        # Validating at WRITE time is the point. A precedence order naming a
        # source_system no position can carry ranks nothing — the source it was
        # meant to promote just silently stays where the unranked-tail rule puts
        # it. Caught here, it is a 400 on the save; caught at resolve time it is
        # every ingestion run since the save having quietly mis-ranked.
        from services.portfolio_precedence import (
            PrecedenceConfigError,
            validate_source_order,
        )
        try:
            validate_source_order(value)
        except PrecedenceConfigError as exc:
            raise SettingsValidationError(str(exc)) from exc


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


async def get_setting_with_origin(conn, org_id, key: str) -> tuple[object, bool]:
    """``(value, is_default)`` for one key — the single-key form of the flag
    ``get_settings_detail`` already exposes as ``is_default``.

    ``get_setting`` alone cannot answer this. It folds "the org stored nothing"
    and "the org stored exactly the default" into the same return value, and a
    caller that has to report WHERE a number came from — which is every
    reconciliation and provenance surface — needs them apart. An org that
    deliberately saves an order identical to the platform default has still
    configured it, and saying "using the platform default" about a deliberate
    choice misreports the provenance.
    """
    row = await conn.fetchrow(
        "SELECT setting_value FROM org_settings "
        "WHERE org_id = $1 AND setting_key = $2",
        org_id, key,
    )
    if row is None:
        return DEFAULT_SETTINGS.get(key), True
    return _decode(row["setting_value"]), False


async def get_all_settings(conn, org_id) -> dict:
    """Return every setting for the org, defaults filled in for unset keys.

    This is the bulk fetch that hydrates the frontend theme provider on page
    load — one round trip for the whole brand.

    SECRET_SETTING_KEYS are omitted. This value reaches the browser of any
    authenticated member, so a credential stored in this table would be
    published to every user of the org by a function whose job is to fetch
    colours. A caller that genuinely needs a secret asks for it by key through
    ``get_setting``.
    """
    rows = await conn.fetch(
        "SELECT setting_key, setting_value FROM org_settings WHERE org_id = $1",
        org_id,
    )
    resolved = dict(DEFAULT_SETTINGS)
    for row in rows:
        if row["setting_key"] in SECRET_SETTING_KEYS:
            continue
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

    SECRET_SETTING_KEYS are omitted entirely rather than shown with the value
    blanked. A settings editor that rendered the salt as an editable field would
    invite an admin to "fix" a random hex string that must never change — every
    account number in the org rehashes if it does, silently detaching every
    existing account from its own next import.
    """
    rows = await conn.fetch(
        "SELECT setting_key, setting_value, category, is_public, updated_at "
        "FROM org_settings WHERE org_id = $1",
        org_id,
    )
    stored = {
        r["setting_key"]: r
        for r in rows
        if r["setting_key"] not in SECRET_SETTING_KEYS
    }

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

    # Normalise first (the settings editor posts every field as a string), then
    # validate the normalised value — otherwise a perfectly good "14" would be
    # rejected for not being an int. Value-level validation may raise
    # SettingsValidationError → HTTP 400, and runs AFTER the permission check so
    # a forbidden caller learns 403, not 400.
    value = _coerce_setting(key, value)
    _validate_setting(key, value)

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
