"""Marketplace endpoints: deals, scoring, votes, interest, documents, config.

All routes require a valid JWT (enforced by the global middleware in main.py)
and scope every query to the caller's org_id. Fine-grained actions are gated by
permissions (see services.permissions). Configurable values — scoring
dimensions, asset classes, deal types — live in the ``config`` table and are
never hard-coded here.
"""

import json
import os
import re
import uuid
from uuid import UUID

from fastapi import (
    APIRouter,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
)
from starlette.concurrency import run_in_threadpool

from routers.entities import get_org_id
from schemas.marketplace import (
    AISummaryResponse,
    ComplianceReviewRequest,
    ComplianceReviewResponse,
    ComplianceReviewStatusUpdate,
    ConfigResponse,
    DealCreate,
    DealDetail,
    DealDocumentResponse,
    DealResponse,
    DealScoreCreate,
    DealScoreResponse,
    DealStageUpdate,
    DealUpdate,
    DocumentReviewRequest,
    InterestOverrideRequest,
    InterestRequest,
    InterestResponse,
    InterestUserResponse,
    MemberInvestmentResponse,
    MemberInvestmentStageUpdate,
    StatusUpdate,
    VoteRequest,
)
from services.audit import write_audit_log
from services.database import get_pool
from services.taxonomy import build_taxonomy, validate_taxonomy_fields
from services.permissions import get_user_id, is_staff, require_permission
from services.storage import upload_bytes

router = APIRouter(tags=["marketplace"])

# Statuses visible to ordinary members.
MEMBER_VISIBLE_STATUSES = ("active", "under_review")

# Allowed status transitions: from -> set(to).
STATUS_TRANSITIONS = {
    "draft": {"submitted"},
    "submitted": {"under_review"},
    "under_review": {"active"},
    "active": {"closed"},
}
# "any -> archived" is always permitted.

# Accreditation values that satisfy the interest compliance gate.
QUALIFIED_ACCREDITATION = ("self_certified", "third_party_verified")

DEAL_SELECT = (
    "id, org_id, slug, name, description, deal_status, deal_stage, "
    "asset_super_class, asset_class, asset_sub_category, "
    "sponsor_entity_id, sponsor_name_override, "
    "target_raise, minimum_investment, expected_return_pct, term_months, "
    "deal_date, close_date, location, highlights, tags, is_featured, "
    "submitted_by, published_at, created_at, updated_at"
)

SCORE_SELECT = (
    "id, deal_id, dimension, score, weight, notes, scored_by, scored_by_ai, "
    "ai_model, ai_confidence, created_at"
)

DOC_SELECT = (
    "id, deal_id, file_name, file_type, file_size_bytes, document_type, "
    "processing_status, extracted_data, created_at, "
    "status, reviewed_by, review_notes, reviewed_at, visible_to_members"
)


def _parse_json(value):
    if value is None or isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (ValueError, TypeError):
        return None


def _f(value):
    """Coerce Decimal/None to float/None."""
    return float(value) if value is not None else None


def _slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")
    return slug or "deal"


async def _unique_slug(conn, org_id, name: str) -> str:
    base = _slugify(name)
    slug = base
    suffix = 1
    while await conn.fetchval(
        "SELECT 1 FROM deals WHERE org_id = $1 AND slug = $2 LIMIT 1",
        org_id,
        slug,
    ):
        suffix += 1
        slug = f"{base}-{suffix}"
    return slug


async def _resolve_user_names(conn, ids) -> dict:
    """Best-effort id -> display name from an unknown ``users`` table.

    Degrades to an empty mapping if the table/columns differ, so the core read
    paths never break on the users schema.
    """
    ids = [i for i in ids if i]
    if not ids:
        return {}
    for name_expr in (
        "COALESCE(name, full_name, email)",
        "COALESCE(full_name, name)",
        "name",
        "email",
    ):
        try:
            rows = await conn.fetch(
                f"SELECT id, {name_expr} AS nm FROM users WHERE id = ANY($1::uuid[])",
                ids,
            )
            return {r["id"]: r["nm"] for r in rows}
        except Exception:
            continue
    return {}


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
@router.get("/config", response_model=list[ConfigResponse])
async def list_config(request: Request, category: str | None = None):
    org_id = get_org_id(request)
    conditions = ["org_id = $1"]
    params: list = [org_id]
    if category:
        params.append(category)
        conditions.append(f"category = ${len(params)}")

    query = (
        "SELECT id, config_key, config_value, value_type, category, "
        "display_order FROM config "
        f"WHERE {' AND '.join(conditions)} "
        "ORDER BY display_order NULLS LAST, config_key"
    )
    pool = await get_pool()
    rows = await pool.fetch(query, *params)
    return [
        ConfigResponse(**{**dict(r), "config_value": _coerce_config(r)})
        for r in rows
    ]


def _coerce_config(row):
    value = row["config_value"]
    vtype = row["value_type"]
    if value is None:
        return None
    if vtype in ("json", "jsonb", "array", "object"):
        return _parse_json(value)
    return value


@router.get("/taxonomy")
async def get_taxonomy(request: Request, response: Response):
    org_id = get_org_id(request)
    response.headers["Cache-Control"] = "max-age=3600"
    return await build_taxonomy(str(org_id))


# ---------------------------------------------------------------------------
# Deal aggregates
# ---------------------------------------------------------------------------
async def _composite_scores(conn, deal_ids) -> dict:
    rows = await conn.fetch(
        """
        SELECT deal_id,
               SUM(score * weight) AS sw,
               SUM(weight) AS w
        FROM deal_scores
        WHERE deal_id = ANY($1::uuid[])
        GROUP BY deal_id
        """,
        deal_ids,
    )
    out = {}
    for r in rows:
        w = _f(r["w"]) or 0
        out[r["deal_id"]] = round(_f(r["sw"]) / w, 2) if w else None
    return out


async def _vote_summary(conn, deal_ids) -> dict:
    rows = await conn.fetch(
        """
        SELECT deal_id,
               COUNT(*) AS total,
               COUNT(*) FILTER (WHERE vote = 1) AS up,
               COUNT(*) FILTER (WHERE vote = -1) AS down
        FROM deal_votes
        WHERE deal_id = ANY($1::uuid[])
        GROUP BY deal_id
        """,
        deal_ids,
    )
    return {
        r["deal_id"]: {
            "vote_count": r["total"],
            "upvotes": r["up"],
            "downvotes": r["down"],
        }
        for r in rows
    }


async def _user_votes(conn, deal_ids, user_id) -> dict:
    rows = await conn.fetch(
        """
        SELECT deal_id, vote FROM deal_votes
        WHERE deal_id = ANY($1::uuid[]) AND user_id = $2
        """,
        deal_ids,
        user_id,
    )
    return {r["deal_id"]: r["vote"] for r in rows}


async def _user_interest(conn, deal_ids, user_id) -> set:
    rows = await conn.fetch(
        """
        SELECT DISTINCT deal_id FROM deal_interest
        WHERE deal_id = ANY($1::uuid[]) AND user_id = $2
        """,
        deal_ids,
        user_id,
    )
    return {r["deal_id"] for r in rows}


async def _doc_counts(conn, deal_ids) -> dict:
    rows = await conn.fetch(
        """
        SELECT deal_id, COUNT(*) AS n FROM deal_documents
        WHERE deal_id = ANY($1::uuid[])
        GROUP BY deal_id
        """,
        deal_ids,
    )
    return {r["deal_id"]: r["n"] for r in rows}


async def _resolve_taxonomy_labels(conn, keys) -> dict:
    """Return {config_key: label} for the given taxonomy config keys."""
    keys = [k for k in keys if k]
    if not keys:
        return {}
    rows = await conn.fetch(
        "SELECT config_key, config_value FROM config WHERE config_key = ANY($1::text[])",
        keys,
    )
    return {r["config_key"]: r["config_value"] for r in rows}


def _deal_response(row, *, composite=None, votes=None, user_vote=None,
                   interested=False, doc_count=0, label_map=None) -> DealResponse:
    data = dict(row)
    data["target_raise"] = _f(data.get("target_raise"))
    data["minimum_investment"] = _f(data.get("minimum_investment"))
    data["expected_return_pct"] = _f(data.get("expected_return_pct"))
    data["highlights"] = data.get("highlights") or []
    data["tags"] = data.get("tags") or []
    votes = votes or {}
    label_map = label_map or {}
    return DealResponse(
        **data,
        composite_score=composite,
        vote_count=votes.get("vote_count", 0),
        upvotes=votes.get("upvotes", 0),
        downvotes=votes.get("downvotes", 0),
        user_vote=user_vote,
        has_indicated_interest=interested,
        document_count=doc_count,
        asset_super_class_label=label_map.get(data.get("asset_super_class")),
        asset_class_label=label_map.get(data.get("asset_class")),
        asset_sub_category_label=label_map.get(data.get("asset_sub_category")),
    )


# ---------------------------------------------------------------------------
# Deals — collection
# ---------------------------------------------------------------------------
@router.get("/deals/stage-summary")
async def get_deal_stage_summary(request: Request):
    org_id = get_org_id(request)
    staff = is_staff(request)
    conditions = ["org_id = $1", "valid_to IS NULL", "system_to IS NULL"]
    params: list = [org_id]
    if not staff:
        params.append(list(MEMBER_VISIBLE_STATUSES))
        conditions.append(f"deal_status = ANY(${len(params)})")
    query = (
        f"SELECT COALESCE(deal_stage, 'sourced') AS stage, COUNT(*) AS count "
        f"FROM deals WHERE {' AND '.join(conditions)} "
        f"GROUP BY COALESCE(deal_stage, 'sourced') ORDER BY stage"
    )
    pool = await get_pool()
    rows = await pool.fetch(query, *params)
    return [{"stage": r["stage"], "count": r["count"]} for r in rows]


@router.get("/deals", response_model=list[DealResponse])
async def list_deals(
    request: Request,
    status: str | None = None,
    deal_stage: str | None = None,
    asset_class: str | None = None,
    search: str | None = None,
    is_featured: bool | None = None,
    limit: int = Query(20, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    org_id = get_org_id(request)
    user_id = get_user_id(request)
    staff = is_staff(request)

    conditions = ["org_id = $1", "valid_to IS NULL", "system_to IS NULL"]
    params: list = [org_id]

    if status:
        params.append(status)
        conditions.append(f"deal_status = ${len(params)}")
    elif not staff:
        params.append(list(MEMBER_VISIBLE_STATUSES))
        conditions.append(f"deal_status = ANY(${len(params)})")

    if deal_stage:
        params.append(deal_stage)
        conditions.append(f"deal_stage = ${len(params)}")
    if asset_class:
        params.append(asset_class)
        conditions.append(f"asset_class = ${len(params)}")
    if search:
        params.append(f"%{search}%")
        conditions.append(f"name ILIKE ${len(params)}")
    if is_featured is not None:
        params.append(is_featured)
        conditions.append(f"is_featured = ${len(params)}")

    params.append(limit)
    limit_pos = len(params)
    params.append(offset)
    offset_pos = len(params)

    query = (
        f"SELECT {DEAL_SELECT} FROM deals "
        f"WHERE {' AND '.join(conditions)} "
        f"ORDER BY is_featured DESC, created_at DESC NULLS LAST "
        f"LIMIT ${limit_pos} OFFSET ${offset_pos}"
    )

    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(query, *params)
        ids = [r["id"] for r in rows]
        if not ids:
            return []
        composite = await _composite_scores(conn, ids)
        votes = await _vote_summary(conn, ids)
        user_votes = await _user_votes(conn, ids, user_id)
        interest = await _user_interest(conn, ids, user_id)
        docs = await _doc_counts(conn, ids)
        taxonomy_keys = list({
            k
            for r in rows
            for k in [r.get("asset_super_class"), r.get("asset_class"), r.get("asset_sub_category")]
            if k
        })
        label_map = await _resolve_taxonomy_labels(conn, taxonomy_keys)

    return [
        _deal_response(
            r,
            composite=composite.get(r["id"]),
            votes=votes.get(r["id"]),
            user_vote=user_votes.get(r["id"]),
            interested=r["id"] in interest,
            doc_count=docs.get(r["id"], 0),
            label_map=label_map,
        )
        for r in rows
    ]


@router.post("/deals", response_model=DealResponse, status_code=201)
async def create_deal(request: Request, body: DealCreate):
    require_permission(request, "manage_deals")
    org_id = get_org_id(request)
    tax_errors = await validate_taxonomy_fields(
        str(org_id),
        body.asset_super_class,
        body.asset_class,
        body.asset_sub_category,
    )
    if tax_errors:
        raise HTTPException(status_code=422, detail=tax_errors)
    pool = await get_pool()

    async with pool.acquire() as conn:
        async with conn.transaction():
            slug = await _unique_slug(conn, org_id, body.name)
            # Note: created_by is intentionally not set on insert, matching the
            # entities convention and avoiding a FK to users before the
            # auth->users mapping is finalized.
            row = await conn.fetchrow(
                f"""
                INSERT INTO deals (
                    org_id, slug, name, description, deal_status,
                    asset_super_class, asset_class, asset_sub_category,
                    sponsor_entity_id, sponsor_name_override, target_raise,
                    minimum_investment, expected_return_pct, term_months,
                    deal_date, close_date, location, highlights, tags,
                    is_featured
                ) VALUES (
                    $1, $2, $3, $4, 'draft', $5, $6, $7, $8, $9, $10, $11,
                    $12, $13, $14, $15, $16, $17, $18, $19
                )
                RETURNING {DEAL_SELECT}
                """,
                org_id,
                slug,
                body.name,
                body.description,
                body.asset_super_class,
                body.asset_class,
                body.asset_sub_category,
                body.sponsor_entity_id,
                body.sponsor_name_override,
                body.target_raise,
                body.minimum_investment,
                body.expected_return_pct,
                body.term_months,
                body.deal_date,
                body.close_date,
                body.location,
                body.highlights or [],
                body.tags or [],
                bool(body.is_featured),
            )
            await write_audit_log(
                conn,
                org_id=org_id,
                action="create",
                table_name="deals",
                record_id=row["id"],
                new=dict(row),
            )
    return _deal_response(row)


# ---------------------------------------------------------------------------
# Deals — single
# ---------------------------------------------------------------------------
async def _fetch_deal(conn, org_id, deal_id: UUID):
    return await conn.fetchrow(
        f"""
        SELECT {DEAL_SELECT} FROM deals
        WHERE id = $1 AND org_id = $2
          AND valid_to IS NULL AND system_to IS NULL
        """,
        deal_id,
        org_id,
    )


@router.get("/deals/{deal_id}", response_model=DealDetail)
async def get_deal(request: Request, deal_id: UUID):
    org_id = get_org_id(request)
    user_id = get_user_id(request)
    staff = is_staff(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        row = await _fetch_deal(conn, org_id, deal_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Deal not found")
        if not staff and row["deal_status"] not in MEMBER_VISIBLE_STATUSES:
            raise HTTPException(status_code=404, detail="Deal not found")

        ids = [deal_id]
        composite = (await _composite_scores(conn, ids)).get(deal_id)
        votes = (await _vote_summary(conn, ids)).get(deal_id)
        user_vote = (await _user_votes(conn, ids, user_id)).get(deal_id)
        interest = deal_id in (await _user_interest(conn, ids, user_id))
        doc_count = (await _doc_counts(conn, ids)).get(deal_id, 0)

        score_rows = await conn.fetch(
            f"SELECT {SCORE_SELECT} FROM deal_scores WHERE deal_id = $1 "
            f"ORDER BY dimension",
            deal_id,
        )
        if staff:
            doc_rows = await conn.fetch(
                f"SELECT {DOC_SELECT} FROM deal_documents WHERE deal_id = $1 "
                f"ORDER BY created_at DESC NULLS LAST",
                deal_id,
            )
        else:
            doc_rows = await conn.fetch(
                f"SELECT {DOC_SELECT} FROM deal_documents WHERE deal_id = $1 "
                f"AND status = 'approved' AND visible_to_members = true "
                f"ORDER BY created_at DESC NULLS LAST",
                deal_id,
            )

        interest_count = await conn.fetchval(
            "SELECT COUNT(DISTINCT user_id) FROM deal_interest WHERE deal_id = $1",
            deal_id,
        )

        # Names (best-effort).
        names = await _resolve_user_names(
            conn, [row.get("submitted_by")]
        )
        sponsor_name = row.get("sponsor_name_override")
        if row.get("sponsor_entity_id"):
            sponsor_name = await conn.fetchval(
                """
                SELECT display_name FROM entities
                WHERE id = $1 AND valid_to IS NULL AND system_to IS NULL
                """,
                row["sponsor_entity_id"],
            ) or sponsor_name

        label_map = await _resolve_taxonomy_labels(
            conn,
            [row.get("asset_super_class"), row.get("asset_class"), row.get("asset_sub_category")],
        )

    deal = _deal_response(
        row,
        composite=composite,
        votes=votes,
        user_vote=user_vote,
        interested=interest,
        doc_count=doc_count,
        label_map=label_map,
    )
    return DealDetail(
        deal=deal,
        scores=[
            DealScoreResponse(
                **{**dict(s), "score": _f(s["score"]), "weight": _f(s["weight"]),
                   "ai_confidence": _f(s["ai_confidence"])}
            )
            for s in score_rows
        ],
        documents=[
            DealDocumentResponse(
                **{**dict(d), "extracted_data": _parse_json(d["extracted_data"])}
            )
            for d in doc_rows
        ],
        submitted_by_name=names.get(row.get("submitted_by")),
        sponsor_name=sponsor_name,
        interest_count=interest_count or 0,
    )


@router.put("/deals/{deal_id}", response_model=DealResponse)
async def update_deal(request: Request, deal_id: UUID, body: DealUpdate):
    require_permission(request, "manage_deals")
    org_id = get_org_id(request)
    updates = body.model_dump(exclude_unset=True)
    if any(k in updates for k in ("asset_super_class", "asset_class", "asset_sub_category")):
        tax_errors = await validate_taxonomy_fields(
            str(org_id),
            updates.get("asset_super_class"),
            updates.get("asset_class"),
            updates.get("asset_sub_category"),
        )
        if tax_errors:
            raise HTTPException(status_code=422, detail=tax_errors)
    pool = await get_pool()

    async with pool.acquire() as conn:
        async with conn.transaction():
            current = await _fetch_deal(conn, org_id, deal_id)
            if current is None:
                raise HTTPException(status_code=404, detail="Deal not found")

            # Bi-temporal, FK-safe: archive prior version, then update live row.
            await conn.execute(
                """
                INSERT INTO deals (
                    org_id, slug, name, description, deal_status,
                    asset_super_class, asset_class, asset_sub_category,
                    sponsor_entity_id, sponsor_name_override, target_raise,
                    minimum_investment, expected_return_pct, term_months,
                    deal_date, close_date, location, highlights, tags,
                    is_featured, submitted_by, published_at,
                    valid_from, valid_to, system_from, system_to,
                    created_by, created_at, updated_at
                )
                SELECT org_id, slug, name, description, deal_status,
                       asset_super_class, asset_class, asset_sub_category,
                       sponsor_entity_id, sponsor_name_override, target_raise,
                       minimum_investment, expected_return_pct, term_months,
                       deal_date, close_date, location, highlights, tags,
                       is_featured, submitted_by, published_at,
                       valid_from, valid_to, system_from, now(),
                       created_by, created_at, updated_at
                FROM deals
                WHERE id = $1 AND org_id = $2
                  AND valid_to IS NULL AND system_to IS NULL
                """,
                deal_id,
                org_id,
            )

            editable = (
                "name", "description", "deal_status", "deal_stage",
                "asset_super_class", "asset_class", "asset_sub_category",
                "sponsor_entity_id", "sponsor_name_override", "target_raise",
                "minimum_investment", "expected_return_pct", "term_months",
                "deal_date", "close_date", "location", "highlights", "tags",
                "is_featured",
            )
            set_clauses = ["system_from = now()", "updated_at = now()"]
            params: list = [deal_id, org_id]
            for field in editable:
                if field in updates:
                    params.append(updates[field])
                    set_clauses.append(f"{field} = ${len(params)}")

            updated = await conn.fetchrow(
                f"""
                UPDATE deals SET {', '.join(set_clauses)}
                WHERE id = $1 AND org_id = $2
                  AND valid_to IS NULL AND system_to IS NULL
                RETURNING {DEAL_SELECT}
                """,
                *params,
            )
            await write_audit_log(
                conn,
                org_id=org_id,
                action="update",
                table_name="deals",
                record_id=deal_id,
                old=dict(current),
                new=dict(updated),
            )
    return _deal_response(updated)


@router.put("/deals/{deal_id}/status", response_model=DealResponse)
async def update_deal_status(request: Request, deal_id: UUID, body: StatusUpdate):
    org_id = get_org_id(request)
    target = body.status
    pool = await get_pool()

    async with pool.acquire() as conn:
        async with conn.transaction():
            current = await _fetch_deal(conn, org_id, deal_id)
            if current is None:
                raise HTTPException(status_code=404, detail="Deal not found")

            src = current["deal_status"]
            allowed = STATUS_TRANSITIONS.get(src, set()) | {"archived"}
            if target not in allowed:
                raise HTTPException(
                    status_code=400,
                    detail=f"Invalid transition: {src} -> {target}",
                )

            # Members may only submit a draft; everything else needs manage_deals.
            member_allowed = src == "draft" and target == "submitted"
            if not member_allowed:
                require_permission(request, "manage_deals")

            set_clauses = ["deal_status = $3", "system_from = now()",
                           "updated_at = now()"]
            params: list = [deal_id, org_id, target]
            if target == "active":
                set_clauses.append("published_at = now()")
            if target == "submitted":
                params.append(get_user_id(request))
                set_clauses.append(f"submitted_by = ${len(params)}")

            updated = await conn.fetchrow(
                f"""
                UPDATE deals SET {', '.join(set_clauses)}
                WHERE id = $1 AND org_id = $2
                  AND valid_to IS NULL AND system_to IS NULL
                RETURNING {DEAL_SELECT}
                """,
                *params,
            )
            await write_audit_log(
                conn,
                org_id=org_id,
                action="status_change",
                table_name="deals",
                record_id=deal_id,
                old={"deal_status": src},
                new={"deal_status": target},
            )
    return _deal_response(updated)


# ---------------------------------------------------------------------------
# Scores
# ---------------------------------------------------------------------------
@router.post("/deals/{deal_id}/scores", response_model=DealScoreResponse, status_code=201)
async def upsert_score(request: Request, deal_id: UUID, body: DealScoreCreate):
    require_permission(request, "score_deal")
    org_id = get_org_id(request)
    user_id = get_user_id(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        async with conn.transaction():
            deal = await _fetch_deal(conn, org_id, deal_id)
            if deal is None:
                raise HTTPException(status_code=404, detail="Deal not found")

            # Dimension must be configured (config category=deal_scoring).
            valid = await conn.fetchval(
                """
                SELECT 1 FROM config
                WHERE org_id = $1 AND category = 'deal_scoring'
                  AND config_key = $2 LIMIT 1
                """,
                org_id,
                body.dimension,
            )
            if not valid:
                raise HTTPException(
                    status_code=400,
                    detail=f"Unknown scoring dimension: {body.dimension}",
                )

            existing = await conn.fetchrow(
                "SELECT id FROM deal_scores WHERE deal_id = $1 AND dimension = $2",
                deal_id,
                body.dimension,
            )
            scored_by = None if body.scored_by_ai else user_id
            if existing:
                row = await conn.fetchrow(
                    f"""
                    UPDATE deal_scores SET
                        score = $2, weight = $3, notes = $4,
                        scored_by = $5, scored_by_ai = $6, ai_model = $7,
                        ai_confidence = $8
                    WHERE id = $1
                    RETURNING {SCORE_SELECT}
                    """,
                    existing["id"],
                    body.score,
                    body.weight,
                    body.notes,
                    scored_by,
                    body.scored_by_ai,
                    body.ai_model,
                    body.ai_confidence,
                )
            else:
                row = await conn.fetchrow(
                    f"""
                    INSERT INTO deal_scores (
                        org_id, deal_id, dimension, score, weight, notes,
                        scored_by, scored_by_ai, ai_model, ai_confidence
                    ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)
                    RETURNING {SCORE_SELECT}
                    """,
                    org_id,
                    deal_id,
                    body.dimension,
                    body.score,
                    body.weight,
                    body.notes,
                    scored_by,
                    body.scored_by_ai,
                    body.ai_model,
                    body.ai_confidence,
                )

            # Recalculate + persist composite score on the deal.
            # Use a nested transaction (SAVEPOINT) so a failure here — e.g. the
            # composite_score column not existing — rolls back only this UPDATE
            # and leaves the outer transaction (with the score row) intact.
            composite = (await _composite_scores(conn, [deal_id])).get(deal_id)
            try:
                async with conn.transaction():
                    await conn.execute(
                        """
                        UPDATE deals SET composite_score = $2, updated_at = now()
                        WHERE id = $1 AND valid_to IS NULL AND system_to IS NULL
                        """,
                        deal_id,
                        composite,
                    )
            except Exception:
                pass

    # Audit write uses its own fresh pool connection (see services/audit.py) so
    # it is never affected by the state of the connection used above.
    await write_audit_log(
        org_id=org_id,
        action="upsert",
        table_name="deal_scores",
        record_id=row["id"],
        new=dict(row),
    )
    return DealScoreResponse(
        **{**dict(row), "score": _f(row["score"]), "weight": _f(row["weight"]),
           "ai_confidence": _f(row["ai_confidence"])}
    )


# ---------------------------------------------------------------------------
# Votes
# ---------------------------------------------------------------------------
@router.post("/deals/{deal_id}/vote")
async def vote_deal(request: Request, deal_id: UUID, body: VoteRequest):
    require_permission(request, "vote_deal")
    if body.vote not in (1, -1):
        raise HTTPException(status_code=400, detail="vote must be 1 or -1")
    org_id = get_org_id(request)
    user_id = get_user_id(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        async with conn.transaction():
            deal = await _fetch_deal(conn, org_id, deal_id)
            if deal is None:
                raise HTTPException(status_code=404, detail="Deal not found")

            existing = await conn.fetchrow(
                "SELECT id, vote FROM deal_votes WHERE deal_id = $1 AND user_id = $2",
                deal_id,
                user_id,
            )
            user_vote = body.vote
            if existing and existing["vote"] == body.vote:
                # Same vote again -> toggle off.
                await conn.execute(
                    "DELETE FROM deal_votes WHERE id = $1", existing["id"]
                )
                user_vote = None
            elif existing:
                await conn.execute(
                    "UPDATE deal_votes SET vote = $2 WHERE id = $1",
                    existing["id"],
                    body.vote,
                )
            else:
                await conn.execute(
                    """
                    INSERT INTO deal_votes (org_id, deal_id, user_id, vote)
                    VALUES ($1, $2, $3, $4)
                    """,
                    org_id,
                    deal_id,
                    user_id,
                    body.vote,
                )

            summary = (await _vote_summary(conn, [deal_id])).get(deal_id) or {
                "vote_count": 0,
                "upvotes": 0,
                "downvotes": 0,
            }
    return {**summary, "user_vote": user_vote}


# ---------------------------------------------------------------------------
# Interest (+ compliance gate)
# ---------------------------------------------------------------------------
async def _is_compliant(conn, org_id, entity_id) -> bool:
    if not entity_id:
        return False
    rec = await conn.fetchrow(
        """
        SELECT kyc_status, accreditation_status FROM compliance_records
        WHERE entity_id = $1 AND org_id = $2
          AND valid_to IS NULL AND system_to IS NULL
        """,
        entity_id,
        org_id,
    )
    if not rec:
        return False
    return (
        rec["kyc_status"] == "approved"
        and rec["accreditation_status"] in QUALIFIED_ACCREDITATION
    )


@router.post("/deals/{deal_id}/interest", response_model=InterestResponse, status_code=201)
async def indicate_interest(request: Request, deal_id: UUID, body: InterestRequest):
    require_permission(request, "vote_deal")
    org_id = get_org_id(request)
    user_id = get_user_id(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        async with conn.transaction():
            deal = await _fetch_deal(conn, org_id, deal_id)
            if deal is None:
                raise HTTPException(status_code=404, detail="Deal not found")

            qualified = await _is_compliant(conn, org_id, body.entity_id)
            if not qualified:
                override = await conn.fetchval(
                    """
                    SELECT 1 FROM deal_interest
                    WHERE deal_id = $1 AND user_id = $2
                      AND compliance_override = true LIMIT 1
                    """,
                    deal_id,
                    user_id,
                )
                if not override:
                    raise HTTPException(
                        status_code=403,
                        detail={
                            "error": "compliance_required",
                            "message": (
                                "KYC approval and accreditation verification "
                                "required to indicate interest"
                            ),
                        },
                    )

            existing = await conn.fetchrow(
                "SELECT id FROM deal_interest WHERE deal_id = $1 AND user_id = $2",
                deal_id,
                user_id,
            )
            if existing:
                row = await conn.fetchrow(
                    """
                    UPDATE deal_interest SET
                        entity_id = $2, amount_interest = $3, notes = $4
                    WHERE id = $1
                    RETURNING id, deal_id, entity_id, user_id, amount_interest,
                              notes, compliance_override, created_at
                    """,
                    existing["id"],
                    body.entity_id,
                    body.amount_interest,
                    body.notes,
                )
            else:
                row = await conn.fetchrow(
                    """
                    INSERT INTO deal_interest (
                        org_id, deal_id, entity_id, user_id, amount_interest, notes
                    ) VALUES ($1,$2,$3,$4,$5,$6)
                    RETURNING id, deal_id, entity_id, user_id, amount_interest,
                              notes, compliance_override, created_at
                    """,
                    org_id,
                    deal_id,
                    body.entity_id,
                    user_id,
                    body.amount_interest,
                    body.notes,
                )
            await write_audit_log(
                conn,
                org_id=org_id,
                action="indicate_interest",
                table_name="deal_interest",
                record_id=row["id"],
                new=dict(row),
            )
            # Auto-create member_investment at the first configured stage.
            try:
                async with conn.transaction():
                    first_stage = await conn.fetchval(
                        """
                        SELECT config_key FROM config
                        WHERE org_id = $1 AND category = 'investment_stages'
                        ORDER BY display_order NULLS LAST, config_key LIMIT 1
                        """,
                        org_id,
                    )
                    if first_stage:
                        await conn.execute(
                            """
                            INSERT INTO member_investments (org_id, deal_id, user_id, investment_stage)
                            VALUES ($1, $2, $3, $4)
                            ON CONFLICT (deal_id, user_id) DO NOTHING
                            """,
                            org_id, deal_id, user_id, first_stage,
                        )
            except Exception:
                pass
    return InterestResponse(
        **{**dict(row), "amount_interest": _f(row["amount_interest"])}
    )


@router.post("/deals/{deal_id}/interest/override", response_model=InterestResponse)
async def override_interest(
    request: Request, deal_id: UUID, body: InterestOverrideRequest
):
    require_permission(request, "override_compliance")
    org_id = get_org_id(request)
    target_user = str(body.user_id) if body.user_id else get_user_id(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        async with conn.transaction():
            deal = await _fetch_deal(conn, org_id, deal_id)
            if deal is None:
                raise HTTPException(status_code=404, detail="Deal not found")

            existing = await conn.fetchrow(
                "SELECT id FROM deal_interest WHERE deal_id = $1 AND user_id = $2",
                deal_id,
                target_user,
            )
            if existing:
                row = await conn.fetchrow(
                    """
                    UPDATE deal_interest SET
                        compliance_override = true,
                        entity_id = COALESCE($2, entity_id),
                        notes = COALESCE($3, notes)
                    WHERE id = $1
                    RETURNING id, deal_id, entity_id, user_id, amount_interest,
                              notes, compliance_override, created_at
                    """,
                    existing["id"],
                    body.entity_id,
                    body.notes,
                )
            else:
                row = await conn.fetchrow(
                    """
                    INSERT INTO deal_interest (
                        org_id, deal_id, entity_id, user_id, notes,
                        compliance_override
                    ) VALUES ($1,$2,$3,$4,$5,true)
                    RETURNING id, deal_id, entity_id, user_id, amount_interest,
                              notes, compliance_override, created_at
                    """,
                    org_id,
                    deal_id,
                    body.entity_id,
                    target_user,
                    body.notes,
                )
            await write_audit_log(
                conn,
                org_id=org_id,
                action="compliance_override",
                table_name="deal_interest",
                record_id=row["id"],
                new={
                    "deal_id": str(deal_id),
                    "user_id": target_user,
                    "granted_by": get_user_id(request),
                    "notes": body.notes,
                },
            )
    return InterestResponse(
        **{**dict(row), "amount_interest": _f(row["amount_interest"])}
    )


@router.get("/deals/{deal_id}/interest", response_model=list[InterestUserResponse])
async def list_interest(request: Request, deal_id: UUID):
    require_permission(request, "manage_deals")
    org_id = get_org_id(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT i.id, i.deal_id, i.entity_id, i.user_id, i.amount_interest,
                   i.notes, i.compliance_override, i.created_at,
                   e.display_name AS entity_name
            FROM deal_interest i
            LEFT JOIN entities e ON e.id = i.entity_id
              AND e.valid_to IS NULL AND e.system_to IS NULL
            WHERE i.deal_id = $1 AND i.org_id = $2
            ORDER BY i.created_at DESC NULLS LAST
            """,
            deal_id,
            org_id,
        )
    return [
        InterestUserResponse(
            **{**dict(r), "amount_interest": _f(r["amount_interest"])}
        )
        for r in rows
    ]


# ---------------------------------------------------------------------------
# Documents
# ---------------------------------------------------------------------------
@router.post(
    "/deals/{deal_id}/documents",
    response_model=DealDocumentResponse,
    status_code=201,
)
async def upload_document(
    request: Request,
    deal_id: UUID,
    file: UploadFile = File(...),
    document_type: str | None = Form(None),
):
    require_permission(request, "manage_documents")
    org_id = get_org_id(request)
    pool = await get_pool()

    data = await file.read()
    key = f"deals/{deal_id}/{uuid.uuid4()}_{file.filename}"

    async with pool.acquire() as conn:
        deal = await _fetch_deal(conn, org_id, deal_id)
        if deal is None:
            raise HTTPException(status_code=404, detail="Deal not found")

        bucket = os.environ.get("R2_BUCKET_NAME", "2ndactcapital-docs")
        await run_in_threadpool(upload_bytes, key, data, file.content_type, bucket)

        async with conn.transaction():
            row = await conn.fetchrow(
                f"""
                INSERT INTO deal_documents (
                    org_id, deal_id, file_name, file_type, file_size_bytes,
                    document_type, r2_key, r2_bucket, processing_status
                ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,'pending')
                RETURNING {DOC_SELECT}
                """,
                org_id,
                deal_id,
                file.filename,
                file.content_type,
                len(data),
                document_type,
                key,
                bucket,
            )
            await write_audit_log(
                conn,
                org_id=org_id,
                action="upload",
                table_name="deal_documents",
                record_id=row["id"],
                new=dict(row),
            )
    return DealDocumentResponse(
        **{**dict(row), "extracted_data": _parse_json(row["extracted_data"])}
    )


# ---------------------------------------------------------------------------
# Compliance review requests
# ---------------------------------------------------------------------------
COMPLIANCE_SELECT = (
    "id, deal_id, user_id, entity_id, request_notes, status, "
    "reviewed_by, review_notes, reviewed_at, created_at, updated_at"
)


@router.post(
    "/deals/{deal_id}/compliance-requests",
    response_model=ComplianceReviewResponse,
    status_code=201,
)
async def create_compliance_request(
    request: Request, deal_id: UUID, body: ComplianceReviewRequest
):
    require_permission(request, "vote_deal")
    org_id = get_org_id(request)
    user_id = get_user_id(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        deal = await _fetch_deal(conn, org_id, deal_id)
        if deal is None:
            raise HTTPException(status_code=404, detail="Deal not found")

        row = await conn.fetchrow(
            f"""
            INSERT INTO compliance_override_requests
                (org_id, deal_id, user_id, entity_id, request_notes, status)
            VALUES ($1, $2, $3, $4, $5, 'pending')
            ON CONFLICT (deal_id, user_id)
            DO UPDATE SET entity_id = $4, request_notes = $5,
                          status = 'pending', updated_at = now()
            RETURNING {COMPLIANCE_SELECT}
            """,
            org_id,
            deal_id,
            user_id,
            body.entity_id,
            body.request_notes,
        )
    return ComplianceReviewResponse(**dict(row))


@router.get(
    "/deals/{deal_id}/compliance-requests",
    response_model=list[ComplianceReviewResponse],
)
async def list_compliance_requests(request: Request, deal_id: UUID):
    require_permission(request, "manage_deals")
    org_id = get_org_id(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        deal = await _fetch_deal(conn, org_id, deal_id)
        if deal is None:
            raise HTTPException(status_code=404, detail="Deal not found")

        rows = await conn.fetch(
            f"""
            SELECT {COMPLIANCE_SELECT}
            FROM compliance_override_requests
            WHERE deal_id = $1 AND org_id = $2
            ORDER BY created_at DESC NULLS LAST
            """,
            deal_id,
            org_id,
        )
    return [ComplianceReviewResponse(**dict(r)) for r in rows]


@router.put(
    "/deals/{deal_id}/compliance-requests/{req_id}",
    response_model=ComplianceReviewResponse,
)
async def update_compliance_request(
    request: Request,
    deal_id: UUID,
    req_id: UUID,
    body: ComplianceReviewStatusUpdate,
):
    require_permission(request, "override_compliance")
    org_id = get_org_id(request)
    reviewer_id = get_user_id(request)
    pool = await get_pool()

    if body.status not in ("approved", "denied"):
        raise HTTPException(
            status_code=400, detail="status must be 'approved' or 'denied'"
        )

    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                f"""
                UPDATE compliance_override_requests
                SET status = $3, reviewed_by = $4, review_notes = $5,
                    reviewed_at = now(), updated_at = now()
                WHERE id = $1 AND deal_id = $2 AND org_id = $6
                RETURNING {COMPLIANCE_SELECT}
                """,
                req_id,
                deal_id,
                body.status,
                reviewer_id,
                body.review_notes,
                org_id,
            )
            if row is None:
                raise HTTPException(status_code=404, detail="Request not found")

            if body.status == "approved":
                # Grant compliance override in deal_interest.
                existing = await conn.fetchrow(
                    "SELECT id FROM deal_interest WHERE deal_id = $1 AND user_id = $2",
                    deal_id,
                    row["user_id"],
                )
                if existing:
                    await conn.execute(
                        "UPDATE deal_interest SET compliance_override = true WHERE id = $1",
                        existing["id"],
                    )
                else:
                    await conn.execute(
                        """
                        INSERT INTO deal_interest
                            (org_id, deal_id, entity_id, user_id, compliance_override)
                        VALUES ($1, $2, $3, $4, true)
                        """,
                        org_id,
                        deal_id,
                        row["entity_id"],
                        row["user_id"],
                    )
                await write_audit_log(
                    conn,
                    org_id=org_id,
                    action="compliance_override",
                    table_name="deal_interest",
                    record_id=deal_id,
                    new={
                        "deal_id": str(deal_id),
                        "user_id": str(row["user_id"]),
                        "granted_by": str(reviewer_id),
                        "via_review_request": str(req_id),
                    },
                )
    return ComplianceReviewResponse(**dict(row))


# ---------------------------------------------------------------------------
# Document review (Sprint 7)
# ---------------------------------------------------------------------------
@router.put(
    "/deals/{deal_id}/documents/{doc_id}/review",
    response_model=DealDocumentResponse,
)
async def review_document(
    request: Request, deal_id: UUID, doc_id: UUID, body: DocumentReviewRequest
):
    require_permission(request, "manage_documents")
    org_id = get_org_id(request)
    reviewer_id = get_user_id(request)

    if body.status not in ("approved", "rejected"):
        raise HTTPException(
            status_code=400, detail="status must be 'approved' or 'rejected'"
        )

    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                f"""
                UPDATE deal_documents
                SET status = $3, reviewed_by = $4, review_notes = $5,
                    reviewed_at = now(), visible_to_members = $6
                WHERE id = $1 AND deal_id = $2
                RETURNING {DOC_SELECT}
                """,
                doc_id,
                deal_id,
                body.status,
                reviewer_id,
                body.review_notes,
                body.visible_to_members,
            )
            if row is None:
                raise HTTPException(status_code=404, detail="Document not found")
            await write_audit_log(
                conn,
                org_id=org_id,
                action="review_document",
                table_name="deal_documents",
                record_id=doc_id,
                new=dict(row),
            )
    return DealDocumentResponse(
        **{**dict(row), "extracted_data": _parse_json(row["extracted_data"])}
    )


# ---------------------------------------------------------------------------
# AI deal summary (Sprint 7)
# ---------------------------------------------------------------------------
AI_SUMMARY_SELECT = (
    "id, deal_id, model_used, generated_at, summary_text, "
    "key_strengths AS strengths, key_risks AS risks, market_context"
)

_AI_MODEL = "claude-haiku-4-5-20251001"


@router.post("/deals/{deal_id}/ai-summary", response_model=AISummaryResponse)
async def generate_ai_summary(request: Request, deal_id: UUID):
    require_permission(request, "manage_deals")
    org_id = get_org_id(request)

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise HTTPException(status_code=500, detail="ANTHROPIC_API_KEY not configured")

    pool = await get_pool()
    async with pool.acquire() as conn:
        deal = await _fetch_deal(conn, org_id, deal_id)
        if deal is None:
            raise HTTPException(status_code=404, detail="Deal not found")

    parts = [f"Deal Name: {deal['name']}"]
    if deal.get("description"):
        parts.append(f"Description: {deal['description']}")
    if deal.get("asset_class"):
        parts.append(f"Asset Class: {deal['asset_class']}")
    if deal.get("target_raise") is not None:
        parts.append(f"Target Raise: ${_f(deal['target_raise']):,.0f}")
    if deal.get("minimum_investment") is not None:
        parts.append(f"Minimum Investment: ${_f(deal['minimum_investment']):,.0f}")
    if deal.get("expected_return_pct") is not None:
        parts.append(f"Expected Return: {_f(deal['expected_return_pct'])}%")
    if deal.get("term_months"):
        parts.append(f"Term: {deal['term_months']} months")
    highlights = deal.get("highlights") or []
    if isinstance(highlights, str):
        try:
            highlights = json.loads(highlights)
        except Exception:
            highlights = []
    if highlights:
        parts.append(f"Highlights: {'; '.join(highlights)}")

    deal_context = "\n".join(parts)

    try:
        import anthropic as _anthropic
        client = _anthropic.AsyncAnthropic(api_key=api_key)
        message = await client.messages.create(
            model=_AI_MODEL,
            max_tokens=1024,
            system=(
                "You are a financial analyst for 2nd Act Capital, a private investment platform "
                "for accredited investors. Analyze the deal and respond ONLY with valid JSON "
                "(no markdown fences) containing exactly these keys: "
                "summary_text (string), strengths (array of strings), "
                "risks (array of strings), market_context (string)."
            ),
            messages=[
                {"role": "user", "content": f"Analyze this investment deal:\n\n{deal_context}"}
            ],
        )
        raw = message.content[0].text
        parsed = json.loads(raw)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"AI generation failed: {exc}")

    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                f"""
                INSERT INTO deal_ai_summaries
                    (deal_id, model_used, generated_at, summary_text, key_strengths, key_risks, market_context)
                VALUES ($1, $2, now(), $3, $4, $5, $6)
                ON CONFLICT (deal_id) DO UPDATE SET
                    model_used = $2, generated_at = now(), summary_text = $3,
                    key_strengths = $4, key_risks = $5, market_context = $6
                RETURNING {AI_SUMMARY_SELECT}
                """,
                deal_id,
                _AI_MODEL,
                parsed.get("summary_text"),
                list(parsed.get("strengths") or []),
                list(parsed.get("risks") or []),
                parsed.get("market_context"),
            )
    return AISummaryResponse(
        **{**dict(row), "strengths": list(row["strengths"] or []), "risks": list(row["risks"] or [])}
    )


@router.get("/deals/{deal_id}/ai-summary", response_model=AISummaryResponse)
async def get_ai_summary(request: Request, deal_id: UUID):
    org_id = get_org_id(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        deal = await _fetch_deal(conn, org_id, deal_id)
        if deal is None:
            raise HTTPException(status_code=404, detail="Deal not found")
        row = await conn.fetchrow(
            f"SELECT {AI_SUMMARY_SELECT} FROM deal_ai_summaries WHERE deal_id = $1",
            deal_id,
        )
    if row is None:
        raise HTTPException(status_code=404, detail="No AI summary generated yet")
    return AISummaryResponse(
        **{**dict(row), "strengths": list(row["strengths"] or []), "risks": list(row["risks"] or [])}
    )


# ---------------------------------------------------------------------------
# Deal stage pipeline (Sprint 7)
# ---------------------------------------------------------------------------
@router.put("/deals/{deal_id}/stage", response_model=DealResponse)
async def update_deal_stage(request: Request, deal_id: UUID, body: DealStageUpdate):
    require_permission(request, "manage_deals")
    org_id = get_org_id(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        valid = await conn.fetchval(
            """
            SELECT 1 FROM config
            WHERE org_id = $1 AND category = 'deal_stages' AND config_key = $2 LIMIT 1
            """,
            org_id,
            body.stage,
        )
        if not valid:
            raise HTTPException(status_code=400, detail=f"Unknown deal stage: {body.stage}")

        async with conn.transaction():
            current = await _fetch_deal(conn, org_id, deal_id)
            if current is None:
                raise HTTPException(status_code=404, detail="Deal not found")

            updated = await conn.fetchrow(
                f"""
                UPDATE deals SET deal_stage = $2, updated_at = now()
                WHERE id = $1 AND valid_to IS NULL AND system_to IS NULL
                RETURNING {DEAL_SELECT}
                """,
                deal_id,
                body.stage,
            )
            await write_audit_log(
                conn,
                org_id=org_id,
                action="stage_change",
                table_name="deals",
                record_id=deal_id,
                old={"deal_stage": current.get("deal_stage")},
                new={"deal_stage": body.stage},
            )
    return _deal_response(updated)


# ---------------------------------------------------------------------------
# Member investments (Sprint 7)
# ---------------------------------------------------------------------------
MEMBER_INVESTMENT_SELECT = (
    "id, deal_id, user_id, org_id, investment_stage AS stage, notes, "
    "invested_amount, created_at, updated_at"
)


@router.get(
    "/deals/{deal_id}/member-investments",
    response_model=list[MemberInvestmentResponse],
)
async def list_member_investments(request: Request, deal_id: UUID):
    require_permission(request, "manage_deals")
    org_id = get_org_id(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        deal = await _fetch_deal(conn, org_id, deal_id)
        if deal is None:
            raise HTTPException(status_code=404, detail="Deal not found")
        rows = await conn.fetch(
            f"""
            SELECT {MEMBER_INVESTMENT_SELECT} FROM member_investments
            WHERE deal_id = $1 AND org_id = $2
            ORDER BY updated_at DESC NULLS LAST
            """,
            deal_id,
            org_id,
        )
    return [
        MemberInvestmentResponse(
            **{**dict(r), "invested_amount": _f(r["invested_amount"])}
        )
        for r in rows
    ]


@router.post(
    "/deals/{deal_id}/member-investments/{member_user_id}/stage",
    response_model=MemberInvestmentResponse,
)
async def update_member_investment_stage(
    request: Request,
    deal_id: UUID,
    member_user_id: UUID,
    body: MemberInvestmentStageUpdate,
):
    require_permission(request, "manage_deals")
    org_id = get_org_id(request)
    actor_id = get_user_id(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        valid = await conn.fetchval(
            """
            SELECT 1 FROM config
            WHERE org_id = $1 AND category = 'investment_stages' AND config_key = $2 LIMIT 1
            """,
            org_id,
            body.stage,
        )
        if not valid:
            raise HTTPException(status_code=400, detail=f"Unknown investment stage: {body.stage}")

        async with conn.transaction():
            deal = await _fetch_deal(conn, org_id, deal_id)
            if deal is None:
                raise HTTPException(status_code=404, detail="Deal not found")

            existing = await conn.fetchrow(
                "SELECT id FROM member_investments WHERE deal_id = $1 AND user_id = $2",
                deal_id,
                member_user_id,
            )
            if existing:
                row = await conn.fetchrow(
                    f"""
                    UPDATE member_investments
                    SET investment_stage = $2, notes = COALESCE($3, notes), updated_at = now()
                    WHERE id = $1
                    RETURNING {MEMBER_INVESTMENT_SELECT}
                    """,
                    existing["id"],
                    body.stage,
                    body.notes,
                )
                await conn.execute(
                    """
                    INSERT INTO investment_stage_history
                        (member_investment_id, stage, changed_by, notes)
                    VALUES ($1, $2, $3, $4)
                    """,
                    existing["id"],
                    body.stage,
                    actor_id,
                    body.notes,
                )
            else:
                row = await conn.fetchrow(
                    f"""
                    INSERT INTO member_investments (org_id, deal_id, user_id, investment_stage, notes)
                    VALUES ($1, $2, $3, $4, $5)
                    RETURNING {MEMBER_INVESTMENT_SELECT}
                    """,
                    org_id,
                    deal_id,
                    member_user_id,
                    body.stage,
                    body.notes,
                )
                await conn.execute(
                    """
                    INSERT INTO investment_stage_history
                        (member_investment_id, stage, changed_by, notes)
                    VALUES ($1, $2, $3, $4)
                    """,
                    row["id"],
                    body.stage,
                    actor_id,
                    body.notes,
                )
    return MemberInvestmentResponse(
        **{**dict(row), "invested_amount": _f(row.get("invested_amount"))}
    )
