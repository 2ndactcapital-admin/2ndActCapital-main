"""PORTENTRY1 Task 2 — the CRM's per-org vocabularies from ``public.config`` (GAP-CRM-01 / 02 / 03).

Three categories, seeded by docs/portentry1_part1.sql for every real org and copied to any other org on first read
(``crm_seed_org_config`` — the deal room's pattern):

  crm_entity_statuses   crm_status_<key>                   prospect, client, contact, active, inactive, archived
                                                           (selectable) + deceased (system: set by a date of death)
  crm_marital_statuses  crm_marital_<key>                  single, married, divorced, widowed, separated,
                                                           domestic_partnership
  crm_sub_types         crm_subtype.<entity_type>.<key>    per entity type (LLC: holding, real_estate, operating,
                                                           collectibles, investment, other …)

The keys AND labels are data: nothing here lists them. ``value_type`` carries the role — 'selectable' rows are
offered on the forms, 'system' rows are shown but never offered.

STORED VALUES. ``entities.status`` and ``entities.marital_status`` store the KEY ('client', 'domestic_partnership').
``entities.sub_type`` stores the sub-type's LABEL ('Real estate') — unchanged from before this sprint: crmdetail1's
trust_type / charity_type already write their label there, every existing row holds a label, and a key would orphan
all of them. A write is checked against the org's vocabulary for that entity type (types with no vocabulary, e.g.
corp_other_intl, take free text); an unchanged legacy value is never refused.

EXISTING ROWS. Nothing is rewritten: an 'active' record may be a client or a contact and nothing on the row says
which, so 'active' stays a valid status (GAP-CRM-01 decision).
"""

from __future__ import annotations

from uuid import UUID

CAT_STATUS = "crm_entity_statuses"
CAT_MARITAL = "crm_marital_statuses"
CAT_SUBTYPE = "crm_sub_types"
CATEGORIES = (CAT_STATUS, CAT_MARITAL, CAT_SUBTYPE)
STATUS_PREFIX = "crm_status_"
MARITAL_PREFIX = "crm_marital_"
SUBTYPE_PREFIX = "crm_subtype."
SELECTABLE = "selectable"
#: The ONE status code assigns (services.entity_writes people rule) — its label still comes from config.
STATUS_DECEASED = "deceased"
#: Entity types whose sub_type is free text when the org has no vocabulary rows for them.
FREE_TEXT_SUBTYPE_TYPES = ("corp_other_intl",)


class VocabularyError(ValueError):
    """A value outside the org's vocabulary (422)."""


def _u(v) -> UUID:
    return v if isinstance(v, UUID) else UUID(str(v))


async def ensure_crm_config(conn, org_id) -> int:
    """Copy the CRM label rows from the default org when this org has none (crm_seed_org_config checks the org GUC
    or super admin itself)."""
    have = await conn.fetchval("SELECT count(*) FROM config WHERE org_id = $1 AND category = ANY($2::text[])",
                               _u(org_id), list(CATEGORIES))
    if have:
        return 0
    return int(await conn.fetchval("SELECT crm_seed_org_config($1)", _u(org_id)) or 0)


async def _rows(conn, org_id) -> list:
    await ensure_crm_config(conn, org_id)
    return await conn.fetch(
        "SELECT config_key, config_value, value_type, category, display_order FROM config "
        "WHERE org_id = $1 AND category = ANY($2::text[]) AND is_active ORDER BY category, display_order, config_key",
        _u(org_id), list(CATEGORIES))


def build_vocab(rows) -> dict:
    """Config rows → the envelope vocabularies (pure; the node render tests feed the same shape)."""
    statuses, marital, sub_types = [], [], {}
    for r in rows:
        key, label, vt, cat = r["config_key"], r["config_value"], r["value_type"], r["category"]
        if cat == CAT_STATUS and key.startswith(STATUS_PREFIX):
            statuses.append({"value": key[len(STATUS_PREFIX):], "label": label, "selectable": vt == SELECTABLE})
        elif cat == CAT_MARITAL and key.startswith(MARITAL_PREFIX):
            marital.append({"value": key[len(MARITAL_PREFIX):], "label": label, "selectable": vt == SELECTABLE})
        elif cat == CAT_SUBTYPE and key.startswith(SUBTYPE_PREFIX):
            parts = key[len(SUBTYPE_PREFIX):].split(".", 1)
            if len(parts) == 2:
                # the stored value is the label (see the module docstring); the key rides along for tests / exports
                sub_types.setdefault(parts[0], []).append({"value": label, "key": parts[1], "label": label})
    return {"entity_statuses": statuses, "marital_statuses": marital, "sub_types_by_type": sub_types,
            "free_text_sub_type_types": list(FREE_TEXT_SUBTYPE_TYPES),
            "status_filters": [{"value": "", "label": "All"}] + [{"value": s["value"], "label": s["label"]}
                                                                  for s in statuses]}


async def crm_vocab(conn, org_id) -> dict:
    return build_vocab(await _rows(conn, org_id))


def check_entity_values(vocab: dict, *, entity_type: str | None, status=None, marital_status=None, sub_type=None,
                        current: dict | None = None) -> None:
    """Refuse a status / marital status / sub-type outside the org's vocabulary — every error at once. A value equal
    to the record's current one is never refused (legacy rows keep what they hold)."""
    cur = current or {}
    errors = []
    if status is not None and status != cur.get("status"):
        allowed = {s["value"] for s in vocab["entity_statuses"] if s["selectable"]}
        if status not in allowed:
            errors.append(f"status '{status}' is not one of {', '.join(sorted(allowed))}")
    if marital_status not in (None, "") and marital_status != cur.get("marital_status"):
        allowed = {s["value"] for s in vocab["marital_statuses"]}
        if marital_status not in allowed:
            errors.append(f"marital status '{marital_status}' is not one of {', '.join(sorted(allowed))}")
    if sub_type not in (None, "") and sub_type != cur.get("sub_type") and entity_type:
        options = vocab["sub_types_by_type"].get(entity_type)
        if options:
            allowed = [o["value"] for o in options]
            if sub_type not in allowed:
                errors.append(f"sub-type '{sub_type}' is not one of {', '.join(allowed)} for this entity type")
        elif entity_type not in FREE_TEXT_SUBTYPE_TYPES:
            errors.append(f"this entity type has no sub-types (got '{sub_type}')")
    if errors:
        raise VocabularyError("; ".join(errors))
