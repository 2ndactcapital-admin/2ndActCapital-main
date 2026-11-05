"""The ONE write path for an entities row edit (system-axis archive, stable id).

Extracted from ``routers.entities.update_entity`` (CRMDETAIL1) so the record's Details form, the typed detail fields
(trust_type / charity_type set ``sub_type``) and the people fields (date_of_death) share it.

Bi-temporal, FK-safe (Rule 3, SYSTEM axis — entities.id is referenced by ~90 FKs): the prior version is archived as
a NEW row with ``system_to = now()``, then the live row (stable id) is updated in place. Every column of the live
row — the CRMDETAIL1 columns maiden_name / date_of_death included — is copied into the archived version.

PEOPLE RULE (Task 4): setting ``date_of_death`` on a person sets ``status = 'deceased'`` and ``is_active = false``
in the same write; the record and its relationships stay (nothing is deleted). Clearing it does NOT silently
re-activate the record — the caller sets status / is_active explicitly.
"""

from __future__ import annotations

from services.crm_vocab import PERSON, STATUS_DECEASED

#: columns the archive copies (every live column but id) and the editable set
ARCHIVED_COLUMNS = (
    "org_id, entity_type, display_name, name_prefix, first_name, middle_name, surname, name_suffix, legal_name, "
    "legal_name_overridden, tax_id, date_of_birth, inception_date, end_date, country_of_formation, notes, is_active, "
    "url, country_code, region_code, sub_type, status, lead_source, relationship_manager_id, tags, linkedin_url, "
    "primary_email, primary_phone, profile_mode, is_incomplete, created_via, marital_status, maiden_name, date_of_death")
# NOT copied, exactly as before this sprint: primary_household_id (households.primary_household_networth selects
# members WITHOUT a live-row filter — an archived copy carrying the pointer would count as a member),
# access_restricted, citizenship_status, annual_income, liquid_net_worth.

EDITABLE = (
    "entity_type", "display_name", "name_prefix", "first_name", "middle_name", "surname", "name_suffix",
    "legal_name", "legal_name_overridden", "tax_id", "inception_date", "end_date", "country_of_formation", "notes",
    "is_active", "url", "country_code", "region_code", "sub_type", "status", "lead_source", "relationship_manager_id",
    "tags", "linkedin_url", "primary_email", "primary_phone", "profile_mode", "is_incomplete",
    "date_of_birth", "marital_status", "maiden_name", "date_of_death",
)


class EntityWriteError(ValueError):
    pass


def apply_people_rule(entity_type: str, updates: dict) -> dict:
    """date_of_death on a person → deceased + inactive (returns the amended updates)."""
    if updates.get("date_of_death") is not None:
        if entity_type not in PERSON:
            raise EntityWriteError("date of death applies to a person record only")
        updates = {**updates, "status": STATUS_DECEASED, "is_active": False}
    return updates


async def update_entity_row(conn, org_id, entity_id, updates: dict, *, returning: str):
    """Archive the live version and apply ``updates`` (keys from EDITABLE only) to the live row. Returns the
    updated row (``returning`` columns) or None when there is no live row in the org. The caller owns the
    transaction."""
    unknown = sorted(set(updates) - set(EDITABLE))
    if unknown:
        raise EntityWriteError(f"not editable: {', '.join(unknown)}")
    await conn.execute(
        f"""
        INSERT INTO entities ({ARCHIVED_COLUMNS}, valid_from, valid_to, system_from, system_to,
                              created_by, created_at, updated_at)
        SELECT {ARCHIVED_COLUMNS}, valid_from, valid_to, system_from, now(), created_by, created_at, updated_at
        FROM entities
        WHERE id = $1 AND org_id = $2 AND valid_to IS NULL AND system_to IS NULL
        """,
        entity_id, org_id,
    )
    set_clauses = ["system_from = now()", "updated_at = now()"]
    params: list = [entity_id, org_id]
    for f in EDITABLE:
        if f in updates:
            params.append(updates[f])
            set_clauses.append(f"{f} = ${len(params)}")
    return await conn.fetchrow(
        f"UPDATE entities SET {', '.join(set_clauses)} WHERE id = $1 AND org_id = $2 "
        f"AND valid_to IS NULL AND system_to IS NULL RETURNING {returning}",
        *params,
    )
