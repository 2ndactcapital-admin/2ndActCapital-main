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
    UploadFile,
)
from starlette.concurrency import run_in_threadpool

from routers.entities import get_org_id
from schemas.marketplace import (
    ConfigResponse,
    DealCreate,
    DealDetail,
    DealDocumentResponse,
    DealResponse,
    DealScoreCreate,
    DealScoreResponse,
    DealUpdate,
    InterestOverrideRequest,
    InterestRequest,
    InterestResponse,
    InterestUserResponse,
    StatusUpdate,
    VoteRequest,
)
from services.audit import write_audit_log
from services.database import get_pool
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
    "id, org_id, slug, name, description, deal_status, asset_super_class, "
    "asset_class, asset_sub_category, sponsor_entity_id, sponsor_name_override, "
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
    "processing_status, extracted_data, created_at"
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


def _deal_response(row, *, composite=None, votes=None, user_vote=None,
                   interested=False, doc_count=0) -> DealResponse:
    data = dict(row)
    data["target_raise"] = _f(data.get("target_raise"))
    data["minimum_investment"] = _f(data.get("minimum_investment"))
    data["expected_return_pct"] = _f(data.get("expected_return_pct"))
    data["highlights"] = data.get("highlights") or []
    data["tags"] = data.get("tags") or []
    votes = votes or {}
    return DealResponse(
        **data,
        composite_score=composite,
        vote_count=votes.get("vote_count", 0),
        upvotes=votes.get("upvotes", 0),
        downvotes=votes.get("downvotes", 0),
        user_vote=user_vote,
        has_indicated_interest=interested,
        document_count=doc_count,
    )


# ---------------------------------------------------------------------------
# Deals — collection
# ---------------------------------------------------------------------------
@router.get("/deals", response_model=list[DealResponse])
async def list_deals(
    request: Request,
    status: str | None = None,
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

    return [
        _deal_response(
            r,
            composite=composite.get(r["id"]),
            votes=votes.get(r["id"]),
            user_vote=user_votes.get(r["id"]),
            interested=r["id"] in interest,
            doc_count=docs.get(r["id"], 0),
        )
        for r in rows
    ]


@router.post("/deals", response_model=DealResponse, status_code=201)
async def create_deal(request: Request, body: DealCreate):
    require_permission(request, "manage_deals")
    org_id = get_org_id(request)
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
        doc_rows = await conn.fetch(
            f"SELECT {DOC_SELECT} FROM deal_documents WHERE deal_id = $1 "
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

    deal = _deal_response(
        row,
        composite=composite,
        votes=votes,
        user_vote=user_vote,
        interested=interest,
        doc_count=doc_count,
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
    pool = await get_pool()

    async with pool.acquire() as conn:
        async with conn.transaction():
            current = await _fetch_deal(conn, org_id, deal_id)
            if current is None:
                raise HTTPException(status_code=404, detail="Deal not found")

            updates = body.model_dump(exclude_unset=True)

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
                "name", "description", "deal_status", "asset_super_class",
                "asset_class", "asset_sub_category", "sponsor_entity_id",
                "sponsor_name_override", "target_raise", "minimum_investment",
                "expected_return_pct", "term_months", "deal_date", "close_date",
                "location", "highlights", "tags", "is_featured",
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
