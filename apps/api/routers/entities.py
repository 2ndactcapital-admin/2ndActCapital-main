"""Entity / CRM core endpoints.

All routes require a valid JWT (enforced by the global middleware in main.py)
and scope every query to the caller's org_id (read from JWT claims, falling
back to the default organization).
"""

import hashlib
from collections import deque
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, Request

from schemas.entities import (
    PERSON_ENTITY_TYPES,
    AddressCreate,
    AddressResponse,
    AddressUpdate,
    AttributeCreate,
    AttributeOut,
    ComplianceRecordResponse,
    ComplianceRecordUpdate,
    EmploymentCreate,
    EmploymentResponse,
    EmploymentUpdate,
    EntityCreate,
    EntityDetail,
    EntityFull,
    EntityOut,
    EntitySearchItem,
    EntitySearchResponse,
    EntityStubCreate,
    EntityType,
    EntityUpdate,
    GraphEdge,
    GraphNode,
    NoteCreate,
    NoteOut,
    ApplyNoteUpdatesIn,
    OwnershipCreate,
    OwnershipGraph,
    OwnershipOut,
    SocialProfileCreate,
    SocialProfileResponse,
    TaxIdCreate,
    TaxIdResponse,
    derive_legal_name,
)
from services.audit import write_audit_log
from services.database import get_pool

router = APIRouter(tags=["entities"])

DEFAULT_ORG_ID = "00000000-0000-0000-0000-000000000001"

ORG_ID_CLAIMS = (
    "org_id",
    "https://2ndactcapital.com/org_id",
    "https://api.2ndactcapital.com/org_id",
)

ENTITY_COLUMNS = (
    "id, org_id, entity_type, display_name, "
    "name_prefix, first_name, middle_name, surname, name_suffix, "
    "legal_name, legal_name_overridden, tax_id, "
    "inception_date, end_date, country_of_formation, notes, "
    "is_active, url, country_code, region_code, "
    "sub_type, status, lead_source, relationship_manager_id, tags, linkedin_url, "
    "primary_email, primary_phone, profile_mode, "
    "is_incomplete, created_via, "
    "valid_from, valid_to, system_from, system_to, created_at, updated_at"
)

# Entity types that can hold an investment / indicate interest in a deal. Used by
# the IOI and compliance-review selectors so members pick an investing vehicle
# (not a sponsor, fund, or foundation). Mirrors the frontend INVESTOR_ENTITY_TYPES.
INVESTOR_ENTITY_TYPES = (
    "individual", "trust", "llc", "lp", "household", "family_office",
)


def get_org_id(request: Request) -> str:
    """Resolve the caller's org_id from JWT claims, or the default org."""
    claims = getattr(request.state, "user", None) or {}
    for key in ORG_ID_CLAIMS:
        value = claims.get(key)
        if value:
            return value
    return DEFAULT_ORG_ID


def _mask_tax_id(last4: str) -> str:
    return f"•••• {last4}" if last4 else "••••"


def _parse_note_json(value):
    if value is None or isinstance(value, (dict, list)):
        return value
    import json

    try:
        return json.loads(value)
    except (ValueError, TypeError):
        return None


async def _ensure_entity_exists(conn, org_id, entity_id: UUID):
    found = await conn.fetchval(
        """
        SELECT 1 FROM entities
        WHERE id = $1 AND org_id = $2
          AND valid_to IS NULL AND system_to IS NULL
        """,
        entity_id, org_id,
    )
    if not found:
        raise HTTPException(status_code=404, detail="Entity not found")


# ---------------------------------------------------------------------------
# Collection
# ---------------------------------------------------------------------------
@router.get("/entities", response_model=list[EntityOut])
async def list_entities(
    request: Request,
    type: EntityType | None = None,
    status: str | None = None,
    search: str | None = None,
    investor_only: bool = False,
    include_inactive: bool = False,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    org_id = get_org_id(request)
    conditions = ["org_id = $1", "valid_to IS NULL", "system_to IS NULL"]
    if not include_inactive:
        conditions.append("is_active = true")
    params: list = [org_id]

    if type is not None:
        params.append(type.value)
        conditions.append(f"entity_type = ${len(params)}")
    if investor_only:
        # Return ALL org entities of investor-capable types so the IOI /
        # compliance selectors populate. The entities table has no user_id, so
        # this is org-scoped not user-scoped.
        # TODO(sprint-future): scope to the caller's entities via
        # relationship_manager_id or a users<->entities mapping once it exists.
        params.append(list(INVESTOR_ENTITY_TYPES))
        conditions.append(f"entity_type = ANY(${len(params)})")
    if status:
        params.append(status)
        conditions.append(f"status = ${len(params)}")
    if search:
        params.append(f"%{search}%")
        conditions.append(f"display_name ILIKE ${len(params)}")

    params.append(limit)
    limit_pos = len(params)
    params.append(offset)
    offset_pos = len(params)

    query = (
        f"SELECT {ENTITY_COLUMNS} FROM entities "
        f"WHERE {' AND '.join(conditions)} "
        f"ORDER BY display_name ASC "
        f"LIMIT ${limit_pos} OFFSET ${offset_pos}"
    )

    pool = await get_pool()
    rows = await pool.fetch(query, *params)
    return [EntityOut(**dict(r)) for r in rows]


@router.post("/entities", response_model=EntityOut, status_code=201)
async def create_entity(request: Request, body: EntityCreate):
    org_id = get_org_id(request)
    pool = await get_pool()

    legal_name = body.legal_name
    if body.entity_type.value in PERSON_ENTITY_TYPES and not body.legal_name_overridden:
        derived = derive_legal_name(
            body.name_prefix, body.first_name, body.middle_name,
            body.surname, body.name_suffix,
        )
        if derived:
            legal_name = derived

    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                f"""
                INSERT INTO entities (
                    org_id, entity_type, display_name,
                    name_prefix, first_name, middle_name, surname, name_suffix,
                    legal_name, legal_name_overridden, tax_id,
                    inception_date, end_date, country_of_formation, notes,
                    is_active, url, country_code, region_code,
                    sub_type, status, lead_source, relationship_manager_id,
                    tags, linkedin_url, primary_email, primary_phone,
                    is_incomplete, created_via
                ) VALUES (
                    $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11,
                    $12, $13, $14, $15, $16, $17, $18, $19,
                    $20, $21, $22, $23, $24, $25, $26, $27, $28, $29
                )
                RETURNING {ENTITY_COLUMNS}
                """,
                org_id,
                body.entity_type.value,
                body.display_name,
                body.name_prefix,
                body.first_name,
                body.middle_name,
                body.surname,
                body.name_suffix,
                legal_name,
                body.legal_name_overridden,
                body.tax_id,
                body.inception_date,
                body.end_date,
                body.country_of_formation,
                body.notes,
                body.is_active,
                body.url,
                body.country_code,
                body.region_code,
                body.sub_type,
                body.status or "prospect",
                body.lead_source,
                body.relationship_manager_id,
                body.tags or [],
                body.linkedin_url,
                body.primary_email,
                body.primary_phone,
                body.is_incomplete,
                body.created_via,
            )
            await write_audit_log(
                conn,
                org_id=org_id,
                action="create",
                table_name="entities",
                record_id=row["id"],
                new=dict(row),
            )
    return EntityOut(**dict(row))


# ---------------------------------------------------------------------------
# Sprint 17 — Entity search (debounced picker)
# ---------------------------------------------------------------------------
SEARCH_COLS = "id, display_name, legal_name, entity_type, is_incomplete, is_active"


@router.get("/entities/search", response_model=EntitySearchResponse)
async def search_entities(
    request: Request,
    q: str = Query(""),
    entity_type: list[str] = Query(default=[]),
    exclude_ids: list[str] = Query(default=[]),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    include_inactive: bool = False,
    include_incomplete: bool = True,
):
    org_id = get_org_id(request)
    pool = await get_pool()

    conditions = ["org_id = $1", "valid_to IS NULL", "system_to IS NULL"]
    params: list = [org_id]

    if not include_inactive:
        conditions.append("is_active = true")
    if not include_incomplete:
        conditions.append("is_incomplete = false")

    pattern = f"%{q.lower()}%" if q else "%"
    params.append(pattern)
    conditions.append(f"(LOWER(display_name) LIKE ${len(params)} OR LOWER(COALESCE(legal_name,'')) LIKE ${len(params)})")

    if entity_type:
        params.append(entity_type)
        conditions.append(f"entity_type = ANY(${len(params)}::text[])")
    if exclude_ids:
        params.append(exclude_ids)
        conditions.append(f"id != ALL(${len(params)}::uuid[])")

    where = " AND ".join(conditions)

    total = await pool.fetchval(
        f"SELECT COUNT(*) FROM entities WHERE {where}", *params
    )

    offset = (page - 1) * page_size
    # Ranking params: exact match and prefix match for the ORDER BY CASE
    params.append(q.lower() if q else "")
    exact_pos = len(params)
    params.append(f"{q.lower()}%" if q else "%")
    prefix_pos = len(params)
    params.append(page_size + 1)
    limit_pos = len(params)
    params.append(offset)
    offset_pos = len(params)

    rows = await pool.fetch(
        f"""
        SELECT {SEARCH_COLS}
        FROM entities
        WHERE {where}
        ORDER BY
          CASE WHEN LOWER(display_name) = ${exact_pos} THEN 0
               WHEN LOWER(display_name) LIKE ${prefix_pos} THEN 1
               ELSE 2 END,
          display_name ASC
        LIMIT ${limit_pos} OFFSET ${offset_pos}
        """,
        *params,
    )

    items = rows[:page_size]
    has_more = len(rows) > page_size

    return EntitySearchResponse(
        items=[EntitySearchItem(**dict(r)) for r in items],
        total=total or 0,
        has_more=has_more,
    )


# ---------------------------------------------------------------------------
# Sprint 17 — Entity stub (picker create with dupe check)
# ---------------------------------------------------------------------------
@router.post("/entities/stub", response_model=EntityOut, status_code=201)
async def create_entity_stub(request: Request, body: EntityStubCreate):
    org_id = get_org_id(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        if not body.force_create:
            dupes = await conn.fetch(
                f"""
                SELECT {SEARCH_COLS}
                FROM entities
                WHERE org_id = $1
                  AND LOWER(display_name) = LOWER($2)
                  AND valid_to IS NULL AND system_to IS NULL
                ORDER BY display_name
                LIMIT 5
                """,
                org_id, body.display_name,
            )
            if dupes:
                from fastapi.responses import JSONResponse
                return JSONResponse(
                    status_code=409,
                    content={
                        "message": "Possible duplicates found",
                        "possible_duplicates": [
                            {
                                "id": str(r["id"]),
                                "display_name": r["display_name"],
                                "entity_type": r["entity_type"],
                                "is_incomplete": r["is_incomplete"],
                                "is_active": r["is_active"],
                            }
                            for r in dupes
                        ],
                    },
                )

        from services.users import ensure_user
        creator = await ensure_user(conn, request)

        row = await conn.fetchrow(
            f"""
            INSERT INTO entities (
                org_id, entity_type, display_name,
                is_incomplete, created_via, status, tags
            ) VALUES ($1, $2, $3, true, 'picker_stub', 'prospect', '{{}}')
            RETURNING {ENTITY_COLUMNS}
            """,
            org_id, body.entity_type.value, body.display_name,
        )

        await conn.execute(
            """
            INSERT INTO member_todos (
                org_id, user_id, kind, category, source, title, detail, priority, status
            ) VALUES ($1, $2, 'actual', 'crm', 'entity_stub', $3, $4, 5, 'open')
            """,
            org_id, creator,
            f"Complete profile: {body.display_name}",
            f"Stub entity created via picker. Please complete the {body.entity_type.value} profile.",
        )

    return EntityOut(**dict(row))


# ---------------------------------------------------------------------------
# Single entity helpers
# ---------------------------------------------------------------------------
async def _fetch_active_entity(conn, org_id: str, entity_id: UUID):
    return await conn.fetchrow(
        f"""
        SELECT {ENTITY_COLUMNS} FROM entities
        WHERE id = $1 AND org_id = $2
          AND valid_to IS NULL AND system_to IS NULL
        """,
        entity_id,
        org_id,
    )


async def _fetch_owners(conn, org_id, entity_id):
    return await conn.fetch(
        """
        SELECT o.id, o.parent_id, o.child_id, o.ownership_pct,
               o.ownership_type, p.display_name AS parent_name,
               c.display_name AS child_name
        FROM entity_ownership o
        JOIN entities p ON p.id = o.parent_id
        JOIN entities c ON c.id = o.child_id
        WHERE o.child_id = $1 AND o.org_id = $2
          AND o.valid_to IS NULL AND o.system_to IS NULL
        ORDER BY o.ownership_pct DESC
        """,
        entity_id,
        org_id,
    )


async def _fetch_holdings(conn, org_id, entity_id):
    return await conn.fetch(
        """
        SELECT o.id, o.parent_id, o.child_id, o.ownership_pct,
               o.ownership_type, p.display_name AS parent_name,
               c.display_name AS child_name
        FROM entity_ownership o
        JOIN entities p ON p.id = o.parent_id
        JOIN entities c ON c.id = o.child_id
        WHERE o.parent_id = $1 AND o.org_id = $2
          AND o.valid_to IS NULL AND o.system_to IS NULL
        ORDER BY o.ownership_pct DESC
        """,
        entity_id,
        org_id,
    )


async def _fetch_attributes(conn, org_id, entity_id):
    return await conn.fetch(
        """
        SELECT id, entity_id, attribute_key, attribute_value, value_type,
               created_at
        FROM entity_attributes
        WHERE entity_id = $1 AND org_id = $2
          AND valid_to IS NULL AND system_to IS NULL
        ORDER BY attribute_key
        """,
        entity_id,
        org_id,
    )


@router.get("/entities/{entity_id}", response_model=EntityDetail)
async def get_entity(request: Request, entity_id: UUID):
    org_id = get_org_id(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        row = await _fetch_active_entity(conn, org_id, entity_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Entity not found")
        attributes = await _fetch_attributes(conn, org_id, entity_id)
        owners = await _fetch_owners(conn, org_id, entity_id)
        holdings = await _fetch_holdings(conn, org_id, entity_id)

    return EntityDetail(
        entity=EntityOut(**dict(row)),
        attributes=[AttributeOut(**dict(a)) for a in attributes],
        owners=[OwnershipOut(**dict(o)) for o in owners],
        holdings=[OwnershipOut(**dict(h)) for h in holdings],
    )


@router.get("/entities/{entity_id}/full", response_model=EntityFull)
async def get_entity_full(request: Request, entity_id: UUID):
    org_id = get_org_id(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        row = await _fetch_active_entity(conn, org_id, entity_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Entity not found")

        attributes = await _fetch_attributes(conn, org_id, entity_id)
        owners = await _fetch_owners(conn, org_id, entity_id)
        holdings = await _fetch_holdings(conn, org_id, entity_id)

        tax_ids = await conn.fetch(
            """
            SELECT id, entity_id, tax_id_type, tax_id_country, tax_id_last4,
                   is_primary, created_at
            FROM entity_tax_ids
            WHERE entity_id = $1 AND org_id = $2
              AND valid_to IS NULL AND system_to IS NULL
            ORDER BY is_primary DESC, created_at
            """,
            entity_id,
            org_id,
        )
        addresses = await conn.fetch(
            """
            SELECT id, entity_id, address_type, street1, street2, city, state,
                   postal_code, country, phone, country_code, region_code,
                   is_verified, is_primary, is_seasonal,
                   season_from_month, season_to_month, created_at
            FROM entity_addresses
            WHERE entity_id = $1 AND org_id = $2
              AND valid_to IS NULL AND system_to IS NULL
            ORDER BY is_primary DESC, created_at
            """,
            entity_id,
            org_id,
        )
        employment = await conn.fetch(
            """
            SELECT e.id, e.employee_id, e.employer_id, e.title, e.start_date,
                   e.end_date, e.is_current, e.notes, e.created_at,
                   emp.display_name AS employer_name
            FROM entity_employment e
            JOIN entities emp ON emp.id = e.employer_id
            WHERE e.employee_id = $1 AND e.org_id = $2
              AND e.valid_to IS NULL AND e.system_to IS NULL
            ORDER BY e.is_current DESC, e.start_date DESC NULLS LAST
            """,
            entity_id,
            org_id,
        )
        social = await conn.fetch(
            """
            SELECT id, entity_id, platform, url, is_primary,
                   linkedin_import_stub, created_at
            FROM entity_social_profiles
            WHERE entity_id = $1 AND org_id = $2
              AND valid_to IS NULL AND system_to IS NULL
            ORDER BY platform
            """,
            entity_id,
            org_id,
        )
        compliance = await conn.fetchrow(
            """
            SELECT id, entity_id, kyc_status, kyc_verified_date,
                   ofac_screen_status, ofac_screen_date, aml_risk_rating,
                   accreditation_status, accreditation_basis,
                   accreditation_verified_date, next_reverification_due,
                   pep_status, pep_details, notes, created_at, updated_at
            FROM compliance_records
            WHERE entity_id = $1 AND org_id = $2
              AND valid_to IS NULL AND system_to IS NULL
            """,
            entity_id,
            org_id,
        )

    return EntityFull(
        entity=EntityOut(**dict(row)),
        attributes=[AttributeOut(**dict(a)) for a in attributes],
        owners=[OwnershipOut(**dict(o)) for o in owners],
        holdings=[OwnershipOut(**dict(h)) for h in holdings],
        tax_ids=[
            TaxIdResponse(**dict(t), masked=_mask_tax_id(t["tax_id_last4"]))
            for t in tax_ids
        ],
        addresses=[AddressResponse(**dict(a)) for a in addresses],
        employment=[EmploymentResponse(**dict(e)) for e in employment],
        social_profiles=[SocialProfileResponse(**dict(s)) for s in social],
        compliance_record=(
            ComplianceRecordResponse(**dict(compliance)) if compliance else None
        ),
    )


@router.put("/entities/{entity_id}", response_model=EntityOut)
async def update_entity(request: Request, entity_id: UUID, body: EntityUpdate):
    org_id = get_org_id(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        async with conn.transaction():
            current = await _fetch_active_entity(conn, org_id, entity_id)
            if current is None:
                raise HTTPException(status_code=404, detail="Entity not found")

            updates = body.model_dump(exclude_unset=True)
            if isinstance(updates.get("entity_type"), EntityType):
                updates["entity_type"] = updates["entity_type"].value

            # Re-derive legal_name for person entities when name components change
            # and the caller has not explicitly overridden it.
            etype = updates.get("entity_type", current["entity_type"])
            overridden = updates.get(
                "legal_name_overridden", current["legal_name_overridden"]
            )
            name_fields = (
                "name_prefix", "first_name", "middle_name", "surname", "name_suffix"
            )
            if any(f in updates for f in name_fields) and etype in PERSON_ENTITY_TYPES and not overridden:
                derived = derive_legal_name(
                    updates.get("name_prefix", current["name_prefix"]),
                    updates.get("first_name", current["first_name"]),
                    updates.get("middle_name", current["middle_name"]),
                    updates.get("surname", current["surname"]),
                    updates.get("name_suffix", current["name_suffix"]),
                )
                if derived:
                    updates["legal_name"] = derived

            # Bi-temporal, FK-safe: archive the prior version as a new row with
            # system_to = now(), then update the live row (stable id) in place.
            await conn.execute(
                """
                INSERT INTO entities (
                    org_id, entity_type, display_name,
                    name_prefix, first_name, middle_name, surname, name_suffix,
                    legal_name, legal_name_overridden, tax_id,
                    inception_date, end_date, country_of_formation, notes,
                    is_active, url, country_code, region_code,
                    sub_type, status, lead_source, relationship_manager_id,
                    tags, linkedin_url, primary_email, primary_phone, profile_mode,
                    is_incomplete, created_via,
                    valid_from, valid_to, system_from, system_to,
                    created_by, created_at, updated_at
                )
                SELECT org_id, entity_type, display_name,
                       name_prefix, first_name, middle_name, surname, name_suffix,
                       legal_name, legal_name_overridden, tax_id,
                       inception_date, end_date, country_of_formation, notes,
                       is_active, url, country_code, region_code,
                       sub_type, status, lead_source, relationship_manager_id,
                       tags, linkedin_url, primary_email, primary_phone, profile_mode,
                       is_incomplete, created_via,
                       valid_from, valid_to, system_from, now(),
                       created_by, created_at, updated_at
                FROM entities
                WHERE id = $1 AND org_id = $2
                  AND valid_to IS NULL AND system_to IS NULL
                """,
                entity_id,
                org_id,
            )

            editable = (
                "entity_type",
                "display_name",
                "name_prefix",
                "first_name",
                "middle_name",
                "surname",
                "name_suffix",
                "legal_name",
                "legal_name_overridden",
                "tax_id",
                "inception_date",
                "end_date",
                "country_of_formation",
                "notes",
                "is_active",
                "url",
                "country_code",
                "region_code",
                "sub_type",
                "status",
                "lead_source",
                "relationship_manager_id",
                "tags",
                "linkedin_url",
                "primary_email",
                "primary_phone",
                "profile_mode",
                "is_incomplete",
            )
            set_clauses = ["system_from = now()", "updated_at = now()"]
            params: list = [entity_id, org_id]
            for field in editable:
                if field in updates:
                    params.append(updates[field])
                    set_clauses.append(f"{field} = ${len(params)}")

            updated = await conn.fetchrow(
                f"""
                UPDATE entities SET {', '.join(set_clauses)}
                WHERE id = $1 AND org_id = $2
                  AND valid_to IS NULL AND system_to IS NULL
                RETURNING {ENTITY_COLUMNS}
                """,
                *params,
            )

            await write_audit_log(
                conn,
                org_id=org_id,
                action="update",
                table_name="entities",
                record_id=entity_id,
                old=dict(current),
                new=dict(updated),
            )
    return EntityOut(**dict(updated))


# ---------------------------------------------------------------------------
# Attributes
# ---------------------------------------------------------------------------
@router.post(
    "/entities/{entity_id}/attributes", response_model=AttributeOut, status_code=201
)
async def add_attribute(request: Request, entity_id: UUID, body: AttributeCreate):
    org_id = get_org_id(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        entity = await _fetch_active_entity(conn, org_id, entity_id)
        if entity is None:
            raise HTTPException(status_code=404, detail="Entity not found")
        row = await conn.fetchrow(
            """
            INSERT INTO entity_attributes (
                org_id, entity_id, attribute_key, attribute_value, value_type
            ) VALUES ($1, $2, $3, $4, $5)
            RETURNING id, entity_id, attribute_key, attribute_value, value_type,
                      created_at
            """,
            org_id,
            entity_id,
            body.attribute_key,
            body.attribute_value,
            body.value_type,
        )
    return AttributeOut(**dict(row))


# ---------------------------------------------------------------------------
# Notes (Sprint 10 — natural-language CRM notes with AI extraction)
# ---------------------------------------------------------------------------
NOTE_COLS = (
    "id, entity_id, note_text, note_type, meeting_date, extracted_fields, "
    "extraction_status, created_at, updated_at"
)


async def _run_note_extraction(org_id, note_id, entity_id, note_text):
    """Background task: extract CRM updates from a note. Never raises."""
    try:
        from services.database import get_pool as _get_pool
        from services.extraction import extract_from_note

        pool = await _get_pool()
        await extract_from_note(pool, org_id, note_id, entity_id, note_text)
    except Exception as exc:  # pragma: no cover - defensive
        import traceback

        print(f"ERROR in note extraction: {exc}")
        print(traceback.format_exc())


@router.post("/entities/{entity_id}/notes", response_model=NoteOut, status_code=201)
async def create_note(
    request: Request,
    entity_id: UUID,
    body: NoteCreate,
    background_tasks: BackgroundTasks,
):
    org_id = get_org_id(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        await _ensure_entity_exists(conn, org_id, entity_id)
        from services.users import ensure_user

        creator = await ensure_user(conn, request)
        row = await conn.fetchrow(
            f"""
            INSERT INTO entity_notes
                (org_id, entity_id, note_text, note_type, meeting_date,
                 extraction_status, created_by)
            VALUES ($1, $2, $3, $4, $5, 'pending', $6)
            RETURNING {NOTE_COLS}
            """,
            org_id, entity_id, body.note_text, body.note_type,
            body.meeting_date, creator,
        )
    # Extract in the background so the response is not blocked.
    background_tasks.add_task(
        _run_note_extraction, org_id, row["id"], entity_id, body.note_text
    )
    return NoteOut(**{**dict(row), "extracted_fields": _parse_note_json(row["extracted_fields"])})


@router.get("/entities/{entity_id}/notes", response_model=list[NoteOut])
async def list_notes(request: Request, entity_id: UUID):
    org_id = get_org_id(request)
    pool = await get_pool()
    rows = await pool.fetch(
        f"""
        SELECT {NOTE_COLS} FROM entity_notes
        WHERE entity_id = $1 AND org_id = $2
        ORDER BY created_at DESC
        """,
        entity_id, org_id,
    )
    return [
        NoteOut(**{**dict(r), "extracted_fields": _parse_note_json(r["extracted_fields"])})
        for r in rows
    ]


@router.post("/entities/{entity_id}/notes/{note_id}/apply", status_code=200)
async def apply_note_updates(
    request: Request, entity_id: UUID, note_id: UUID, body: ApplyNoteUpdatesIn
):
    """Apply an advisor-confirmed note's suggested updates to the entity.

    entity_updates patch editable entity columns; new_attributes are added as
    entity_attributes rows. Only known-editable columns are applied.
    """
    org_id = get_org_id(request)
    pool = await get_pool()
    editable = {
        "primary_email", "primary_phone", "legal_name", "notes",
        "linkedin_url", "status", "lead_source",
    }
    entity_updates = {
        k: v for k, v in (body.entity_updates or {}).items() if k in editable
    }
    async with pool.acquire() as conn:
        async with conn.transaction():
            await _ensure_entity_exists(conn, org_id, entity_id)
            if entity_updates:
                set_clauses = ["updated_at = now()"]
                params: list = [entity_id, org_id]
                for field, value in entity_updates.items():
                    params.append(value)
                    set_clauses.append(f"{field} = ${len(params)}")
                await conn.execute(
                    f"""
                    UPDATE entities SET {', '.join(set_clauses)}
                    WHERE id = $1 AND org_id = $2
                      AND valid_to IS NULL AND system_to IS NULL
                    """,
                    *params,
                )
            for key, value in (body.new_attributes or {}).items():
                await conn.execute(
                    """
                    INSERT INTO entity_attributes
                        (org_id, entity_id, attribute_key, attribute_value, value_type)
                    VALUES ($1, $2, $3, $4, 'string')
                    """,
                    org_id, entity_id, key,
                    value if isinstance(value, str) else str(value),
                )
            await write_audit_log(
                conn,
                org_id=org_id,
                action="apply_note_updates",
                table_name="entities",
                record_id=entity_id,
                new={"note_id": str(note_id), "entity_updates": entity_updates,
                     "new_attributes": body.new_attributes or {}},
            )
    return {"ok": True, "applied": entity_updates,
            "attributes_added": list((body.new_attributes or {}).keys())}


# ---------------------------------------------------------------------------
# Tax IDs
# ---------------------------------------------------------------------------
@router.post(
    "/entities/{entity_id}/tax-ids", response_model=TaxIdResponse, status_code=201
)
async def add_tax_id(request: Request, entity_id: UUID, body: TaxIdCreate):
    org_id = get_org_id(request)
    pool = await get_pool()

    # Placeholder "encryption": hash the value (real encryption in a later
    # sprint). The clear value is never stored or returned.
    digits = "".join(ch for ch in body.value if ch.isalnum())
    last4 = digits[-4:] if digits else ""
    encrypted = hashlib.sha256(body.value.encode()).hexdigest()

    async with pool.acquire() as conn:
        entity = await _fetch_active_entity(conn, org_id, entity_id)
        if entity is None:
            raise HTTPException(status_code=404, detail="Entity not found")
        row = await conn.fetchrow(
            """
            INSERT INTO entity_tax_ids (
                org_id, entity_id, tax_id_type, tax_id_country,
                tax_id_encrypted, tax_id_last4, is_primary
            ) VALUES ($1, $2, $3, $4, $5, $6, $7)
            RETURNING id, entity_id, tax_id_type, tax_id_country, tax_id_last4,
                      is_primary, created_at
            """,
            org_id,
            entity_id,
            body.tax_id_type.value,
            body.tax_id_country,
            encrypted,
            last4,
            body.is_primary,
        )
    return TaxIdResponse(**dict(row), masked=_mask_tax_id(row["tax_id_last4"]))


# ---------------------------------------------------------------------------
# Addresses
# ---------------------------------------------------------------------------
@router.post(
    "/entities/{entity_id}/addresses", response_model=AddressResponse, status_code=201
)
async def add_address(request: Request, entity_id: UUID, body: AddressCreate):
    org_id = get_org_id(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        entity = await _fetch_active_entity(conn, org_id, entity_id)
        if entity is None:
            raise HTTPException(status_code=404, detail="Entity not found")
        row = await conn.fetchrow(
            """
            INSERT INTO entity_addresses (
                org_id, entity_id, address_type, street1, street2, city, state,
                postal_code, country, phone, country_code, region_code,
                is_primary, is_verified, is_seasonal,
                season_from_month, season_to_month
            ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17)
            RETURNING id, entity_id, address_type, street1, street2, city, state,
                      postal_code, country, phone, country_code, region_code,
                      is_verified, is_primary, is_seasonal,
                      season_from_month, season_to_month, created_at
            """,
            org_id,
            entity_id,
            body.address_type.value,
            body.street1,
            body.street2,
            body.city,
            body.state,
            body.postal_code,
            body.country,
            body.phone,
            body.country_code,
            body.region_code,
            body.is_primary,
            body.is_verified,
            body.is_seasonal,
            body.season_from_month,
            body.season_to_month,
        )
    return AddressResponse(**dict(row))


@router.put(
    "/entities/{entity_id}/addresses/{addr_id}",
    response_model=AddressResponse,
)
async def update_address(
    request: Request, entity_id: UUID, addr_id: UUID, body: AddressUpdate
):
    org_id = get_org_id(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            current = await conn.fetchrow(
                """
                SELECT id FROM entity_addresses
                WHERE id = $1 AND entity_id = $2 AND org_id = $3
                  AND valid_to IS NULL AND system_to IS NULL
                """,
                addr_id,
                entity_id,
                org_id,
            )
            if current is None:
                raise HTTPException(status_code=404, detail="Address not found")

            # Archive prior version, then update live row in place.
            await conn.execute(
                """
                INSERT INTO entity_addresses (
                    org_id, entity_id, address_type, street1, street2, city,
                    state, postal_code, country, phone, country_code, region_code,
                    is_verified, is_primary, is_seasonal,
                    season_from_month, season_to_month,
                    valid_from, valid_to, system_from, system_to,
                    created_by, created_at
                )
                SELECT org_id, entity_id, address_type, street1, street2, city,
                       state, postal_code, country, phone, country_code, region_code,
                       is_verified, is_primary, is_seasonal,
                       season_from_month, season_to_month,
                       valid_from, valid_to, system_from, now(),
                       created_by, created_at
                FROM entity_addresses WHERE id = $1
                """,
                addr_id,
            )

            updates = body.model_dump(exclude_unset=True)
            if hasattr(updates.get("address_type"), "value"):
                updates["address_type"] = updates["address_type"].value
            set_clauses = ["system_from = now()"]
            params: list = [addr_id]
            for field in (
                "address_type",
                "street1",
                "street2",
                "city",
                "state",
                "postal_code",
                "country",
                "phone",
                "country_code",
                "region_code",
                "is_primary",
                "is_verified",
                "is_seasonal",
                "season_from_month",
                "season_to_month",
            ):
                if field in updates:
                    params.append(updates[field])
                    set_clauses.append(f"{field} = ${len(params)}")

            row = await conn.fetchrow(
                f"""
                UPDATE entity_addresses SET {', '.join(set_clauses)}
                WHERE id = $1
                RETURNING id, entity_id, address_type, street1, street2, city,
                          state, postal_code, country, phone, country_code, region_code,
                          is_verified, is_primary, is_seasonal,
                          season_from_month, season_to_month, created_at
                """,
                *params,
            )
    return AddressResponse(**dict(row))


# ---------------------------------------------------------------------------
# Employment
# ---------------------------------------------------------------------------
@router.post(
    "/entities/{entity_id}/employment",
    response_model=EmploymentResponse,
    status_code=201,
)
async def add_employment(request: Request, entity_id: UUID, body: EmploymentCreate):
    org_id = get_org_id(request)
    if body.employer_id == entity_id:
        raise HTTPException(
            status_code=400, detail="Employer and employee must differ"
        )
    pool = await get_pool()
    async with pool.acquire() as conn:
        employee = await _fetch_active_entity(conn, org_id, entity_id)
        if employee is None:
            raise HTTPException(status_code=404, detail="Entity not found")
        employer = await _fetch_active_entity(conn, org_id, body.employer_id)
        if employer is None:
            raise HTTPException(
                status_code=400, detail="Employer is not a valid entity in this org"
            )
        row = await conn.fetchrow(
            """
            INSERT INTO entity_employment (
                org_id, employee_id, employer_id, title, start_date, end_date,
                is_current, notes
            ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8)
            RETURNING id, employee_id, employer_id, title, start_date, end_date,
                      is_current, notes, created_at
            """,
            org_id,
            entity_id,
            body.employer_id,
            body.title,
            body.start_date,
            body.end_date,
            body.is_current,
            body.notes,
        )
    return EmploymentResponse(**dict(row), employer_name=employer["display_name"])


@router.put(
    "/entities/{entity_id}/employment/{emp_id}",
    response_model=EmploymentResponse,
)
async def update_employment(
    request: Request, entity_id: UUID, emp_id: UUID, body: EmploymentUpdate
):
    org_id = get_org_id(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            current = await conn.fetchrow(
                """
                SELECT id FROM entity_employment
                WHERE id = $1 AND employee_id = $2 AND org_id = $3
                  AND valid_to IS NULL AND system_to IS NULL
                """,
                emp_id,
                entity_id,
                org_id,
            )
            if current is None:
                raise HTTPException(status_code=404, detail="Employment not found")

            await conn.execute(
                """
                INSERT INTO entity_employment (
                    org_id, employee_id, employer_id, title, start_date,
                    end_date, is_current, notes,
                    valid_from, valid_to, system_from, system_to,
                    created_by, created_at
                )
                SELECT org_id, employee_id, employer_id, title, start_date,
                       end_date, is_current, notes,
                       valid_from, valid_to, system_from, now(),
                       created_by, created_at
                FROM entity_employment WHERE id = $1
                """,
                emp_id,
            )

            updates = body.model_dump(exclude_unset=True)
            set_clauses = ["system_from = now()"]
            params: list = [emp_id]
            for field in ("title", "start_date", "end_date", "is_current", "notes"):
                if field in updates:
                    params.append(updates[field])
                    set_clauses.append(f"{field} = ${len(params)}")

            row = await conn.fetchrow(
                f"""
                UPDATE entity_employment SET {', '.join(set_clauses)}
                WHERE id = $1
                RETURNING id, employee_id, employer_id, title, start_date,
                          end_date, is_current, notes, created_at
                """,
                *params,
            )
            employer = await _fetch_active_entity(conn, org_id, row["employer_id"])
    return EmploymentResponse(
        **dict(row), employer_name=employer["display_name"] if employer else None
    )


# ---------------------------------------------------------------------------
# Social profiles
# ---------------------------------------------------------------------------
@router.post(
    "/entities/{entity_id}/social-profiles",
    response_model=SocialProfileResponse,
    status_code=201,
)
async def add_social_profile(
    request: Request, entity_id: UUID, body: SocialProfileCreate
):
    org_id = get_org_id(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        entity = await _fetch_active_entity(conn, org_id, entity_id)
        if entity is None:
            raise HTTPException(status_code=404, detail="Entity not found")
        stub = body.platform.value == "linkedin"
        row = await conn.fetchrow(
            """
            INSERT INTO entity_social_profiles (
                org_id, entity_id, platform, url, is_primary, linkedin_import_stub
            ) VALUES ($1,$2,$3,$4,$5,$6)
            ON CONFLICT (entity_id, platform) DO UPDATE SET
                url = EXCLUDED.url,
                is_primary = EXCLUDED.is_primary,
                linkedin_import_stub = EXCLUDED.linkedin_import_stub,
                valid_to = NULL, system_to = NULL
            RETURNING id, entity_id, platform, url, is_primary,
                      linkedin_import_stub, created_at
            """,
            org_id,
            entity_id,
            body.platform.value,
            body.url,
            body.is_primary,
            stub,
        )
    return SocialProfileResponse(**dict(row))


# ---------------------------------------------------------------------------
# Compliance (advisor access — stub check, always allow for now)
# ---------------------------------------------------------------------------
COMPLIANCE_COLUMNS = (
    "id, entity_id, kyc_status, kyc_verified_date, ofac_screen_status, "
    "ofac_screen_date, aml_risk_rating, accreditation_status, "
    "accreditation_basis, accreditation_verified_date, next_reverification_due, "
    "pep_status, pep_details, notes, created_at, updated_at"
)


@router.get(
    "/entities/{entity_id}/compliance", response_model=ComplianceRecordResponse
)
async def get_compliance(request: Request, entity_id: UUID):
    org_id = get_org_id(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"""
            SELECT {COMPLIANCE_COLUMNS} FROM compliance_records
            WHERE entity_id = $1 AND org_id = $2
              AND valid_to IS NULL AND system_to IS NULL
            """,
            entity_id,
            org_id,
        )
        if row is None:
            raise HTTPException(status_code=404, detail="Compliance record not found")
    return ComplianceRecordResponse(**dict(row))


@router.put(
    "/entities/{entity_id}/compliance", response_model=ComplianceRecordResponse
)
async def update_compliance(
    request: Request, entity_id: UUID, body: ComplianceRecordUpdate
):
    org_id = get_org_id(request)
    pool = await get_pool()
    updates = body.model_dump(exclude_unset=True)
    # Normalize enums to their values.
    for key, value in list(updates.items()):
        if hasattr(value, "value"):
            updates[key] = value.value

    async with pool.acquire() as conn:
        async with conn.transaction():
            existing = await conn.fetchrow(
                f"""
                SELECT {COMPLIANCE_COLUMNS} FROM compliance_records
                WHERE entity_id = $1 AND org_id = $2
                  AND valid_to IS NULL AND system_to IS NULL
                """,
                entity_id,
                org_id,
            )

            if existing is None:
                # Create a record then apply manual fields.
                cols = ["org_id", "entity_id"]
                vals: list = [org_id, entity_id]
                for field, value in updates.items():
                    cols.append(field)
                    vals.append(value)
                placeholders = ", ".join(f"${i + 1}" for i in range(len(vals)))
                row = await conn.fetchrow(
                    f"""
                    INSERT INTO compliance_records ({', '.join(cols)})
                    VALUES ({placeholders})
                    RETURNING {COMPLIANCE_COLUMNS}
                    """,
                    *vals,
                )
            else:
                set_clauses = ["updated_at = now()", "system_from = now()"]
                params: list = [entity_id, org_id]
                for field, value in updates.items():
                    params.append(value)
                    set_clauses.append(f"{field} = ${len(params)}")
                row = await conn.fetchrow(
                    f"""
                    UPDATE compliance_records SET {', '.join(set_clauses)}
                    WHERE entity_id = $1 AND org_id = $2
                      AND valid_to IS NULL AND system_to IS NULL
                    RETURNING {COMPLIANCE_COLUMNS}
                    """,
                    *params,
                )

            await write_audit_log(
                conn,
                org_id=org_id,
                action="update",
                table_name="compliance_records",
                record_id=row["id"],
                old=dict(existing) if existing else None,
                new=dict(row),
            )
    return ComplianceRecordResponse(**dict(row))


# ---------------------------------------------------------------------------
# Ownership graph
# ---------------------------------------------------------------------------
@router.get("/entities/{entity_id}/ownership-graph", response_model=OwnershipGraph)
async def ownership_graph(request: Request, entity_id: UUID):
    org_id = get_org_id(request)
    pool = await get_pool()
    max_depth = 5

    depths: dict[UUID, int] = {entity_id: 0}
    edges: dict[UUID, GraphEdge] = {}

    async with pool.acquire() as conn:
        root = await _fetch_active_entity(conn, org_id, entity_id)
        if root is None:
            raise HTTPException(status_code=404, detail="Entity not found")

        queue: deque[UUID] = deque([entity_id])
        while queue:
            node = queue.popleft()
            node_depth = depths[node]
            if node_depth >= max_depth:
                continue

            rows = await conn.fetch(
                """
                SELECT id, parent_id, child_id, ownership_pct, ownership_type
                FROM entity_ownership
                WHERE (parent_id = $1 OR child_id = $1) AND org_id = $2
                  AND valid_to IS NULL AND system_to IS NULL
                """,
                node,
                org_id,
            )
            for r in rows:
                edges.setdefault(
                    r["id"],
                    GraphEdge(
                        parent_id=r["parent_id"],
                        child_id=r["child_id"],
                        ownership_pct=float(r["ownership_pct"]),
                        ownership_type=r["ownership_type"],
                    ),
                )
                neighbor = r["child_id"] if r["parent_id"] == node else r["parent_id"]
                if neighbor not in depths:
                    depths[neighbor] = node_depth + 1
                    queue.append(neighbor)

        node_rows = await conn.fetch(
            """
            SELECT id, display_name, entity_type FROM entities
            WHERE id = ANY($1::uuid[]) AND org_id = $2
              AND valid_to IS NULL AND system_to IS NULL
            """,
            list(depths.keys()),
            org_id,
        )

    nodes = [
        GraphNode(
            id=r["id"],
            display_name=r["display_name"],
            entity_type=r["entity_type"],
            depth=depths[r["id"]],
        )
        for r in node_rows
    ]
    return OwnershipGraph(root_id=entity_id, nodes=nodes, edges=list(edges.values()))


# ---------------------------------------------------------------------------
# Ownership relationships
# ---------------------------------------------------------------------------
@router.post("/entity-ownership", response_model=OwnershipOut, status_code=201)
async def create_ownership(request: Request, body: OwnershipCreate):
    org_id = get_org_id(request)
    if body.parent_id == body.child_id:
        raise HTTPException(status_code=400, detail="An entity cannot own itself")

    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            existing = await conn.fetchval(
                """
                SELECT COALESCE(SUM(ownership_pct), 0)
                FROM entity_ownership
                WHERE child_id = $1 AND org_id = $2
                  AND valid_to IS NULL AND system_to IS NULL
                """,
                body.child_id,
                org_id,
            )
            if float(existing) + body.ownership_pct > 100:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"Ownership for this entity would total "
                        f"{float(existing) + body.ownership_pct:.4f}%, exceeding 100%"
                    ),
                )

            row = await conn.fetchrow(
                """
                INSERT INTO entity_ownership (
                    org_id, parent_id, child_id, ownership_pct, ownership_type
                ) VALUES ($1, $2, $3, $4, $5)
                RETURNING id, parent_id, child_id, ownership_pct, ownership_type
                """,
                org_id,
                body.parent_id,
                body.child_id,
                body.ownership_pct,
                body.ownership_type,
            )
    return OwnershipOut(**dict(row))
