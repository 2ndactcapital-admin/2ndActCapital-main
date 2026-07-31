"""Shared deal-creation core — the ONE mechanism used to insert a ``deals`` row.

Extracted verbatim from ``routers.marketplace.create_deal`` (Sprint 5) so that
BOTH the ``POST /api/v1/deals`` endpoint AND Chancery Phase-10 VDR proposal
approval create deals through exactly the same code path — never a second,
parallel deal-creation path. The endpoint keeps its own permission check
(``manage_deals``) and taxonomy validation around this call; this function is the
deterministic slug → insert → audit core.

No transaction is opened here — the caller controls the transaction so the insert
and any surrounding writes (audit, proposal update, document links) commit
together. ``returning`` is the caller's column list (marketplace's ``DEAL_SELECT``)
so the returned Record is shaped for ``_deal_response``.
"""

from __future__ import annotations

import re

from services.audit import write_audit_log


def slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")
    return slug or "deal"


async def unique_slug(conn, org_id, name: str) -> str:
    base = slugify(name)
    slug = base
    suffix = 1
    while await conn.fetchval(
        "SELECT 1 FROM deals WHERE org_id = $1 AND slug = $2 LIMIT 1",
        org_id, slug,
    ):
        suffix += 1
        slug = f"{base}-{suffix}"
    return slug


async def insert_deal(conn, org_id, body, *, returning: str):
    """Insert one ``deals`` row from a ``DealCreate``-shaped body and write the
    audit log. Returns the inserted asyncpg Record (columns per ``returning``).

    ``deal_status`` is forced to 'draft' (same as the endpoint); ``created_by`` is
    intentionally not set on insert, matching the entities convention and avoiding
    a FK to users before the auth→users mapping is finalized.
    """
    slug = await unique_slug(conn, org_id, body.name)
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
        RETURNING {returning}
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
    return row
