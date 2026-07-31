"""Chancery Phase 5 — document ↔ entity / record linkage.

This module is the linkage engine behind the Phase-5 endpoints (``routers.
document_links``). It owns THREE tables (all created by Part-1 SQL, RLS enabled +
org-isolation policy):

  * ``document_entity_links``   — many-to-many document ↔ entity (UNIQUE per pair).
  * ``document_record_links``   — polymorphic document ↔ (record_type, record_id).
  * ``document_link_proposals`` — proposed links awaiting human review.

Reuse, not reinvention (Phase-5 Task 1a):
  Entity name-matching and entity creation go through the Sprint-17 entity-picker
  helpers ``find_entity_dupes`` / ``create_picker_stub_entity`` in
  ``routers.entities`` — the SAME code the ``POST /entities/stub`` picker uses.
  There is deliberately no second matching algorithm and no bare INSERT into
  ``entities`` here.

created_by convention:
  * A HUMAN manually linking → ``created_by`` = that user's id.
  * The SYSTEM auto-linking a K-1 (Task 3) → ``created_by`` = NULL.
  * Approving a proposal (Task 4) → ``created_by`` = the reviewer's id.

These tables are NOT bi-temporal (no ``valid_to``), so a link is a plain row:
insert to link, delete to unlink; ``ON CONFLICT DO NOTHING`` keeps writes
idempotent.
"""

from __future__ import annotations

from uuid import UUID

# Reuse the Sprint-17 entity-picker mechanism verbatim (Task 1a). Importing a
# router module from a service mirrors the existing ``services.users`` pattern
# (``from routers.entities import get_org_id``); no import cycle exists because
# ``routers.entities`` does not import this module.
from routers.entities import find_entity_dupes, create_picker_stub_entity


class LinkageError(Exception):
    """A known linkage failure the router should surface as an HTTP error."""

    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


# ---------------------------------------------------------------------------
# Task 1c — K-1 party-name contract.
#
# IMPORTANT (discovery finding): Phase 3's K-1 template extraction was BLOCKED
# (no AWS Textract access) and never built, so there is no *existing* mapped_fields
# schema to re-read. ``document_template_extractions.mapped_fields`` is the agreed
# home for those values. This is the forward contract Phase 3 will populate: the
# party (partner / shareholder / beneficiary) name lives under one of these keys,
# tried in order. Kept as a list so Phase 3 can settle on any one of them without
# changing Phase-5 linkage.
# ---------------------------------------------------------------------------
K1_PARTY_NAME_KEYS = (
    "partner_name",       # Form 1065 Schedule K-1, Part II line F ("Partner's name")
    "shareholder_name",   # Form 1120-S Schedule K-1
    "beneficiary_name",   # Form 1041 Schedule K-1
    "member_name",        # LLC-taxed-as-partnership phrasing
    "recipient_name",     # generic fallback
)

K1_TEMPLATE_TYPE = "k1"


def extract_party_name(mapped_fields) -> str | None:
    """Pull the party name out of a K-1 ``mapped_fields`` dict per the contract
    above. Returns a stripped non-empty string, or None if absent/blank."""
    if not isinstance(mapped_fields, dict):
        return None
    for key in K1_PARTY_NAME_KEYS:
        value = mapped_fields.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


# ---------------------------------------------------------------------------
# Existence guards (org-scoped)
# ---------------------------------------------------------------------------
async def _document_in_org(conn, org_id, document_id) -> bool:
    return bool(await conn.fetchval(
        "SELECT 1 FROM documents WHERE id = $1 AND org_id = $2",
        document_id, org_id,
    ))


async def _entity_in_org(conn, org_id, entity_id) -> bool:
    return bool(await conn.fetchval(
        """
        SELECT 1 FROM entities
        WHERE id = $1 AND org_id = $2
          AND valid_to IS NULL AND system_to IS NULL
        """,
        entity_id, org_id,
    ))


# ---------------------------------------------------------------------------
# Task 2 — manual linkage
# ---------------------------------------------------------------------------
async def link_document_to_entities(
    conn, org_id, document_id, entity_ids, *, created_by, link_role=None,
) -> list[dict]:
    """Link one document to one OR MORE entities (many-to-many). Idempotent:
    an existing pair is reported ``created=False`` rather than erroring. Raises
    ``LinkageError`` if the document or any entity is not in the org."""
    if not await _document_in_org(conn, org_id, document_id):
        raise LinkageError(404, "Document not found")

    results: list[dict] = []
    seen: set[str] = set()
    for entity_id in entity_ids:
        key = str(entity_id)
        if key in seen:
            continue
        seen.add(key)
        if not await _entity_in_org(conn, org_id, entity_id):
            raise LinkageError(404, f"Entity not found: {entity_id}")
        row = await conn.fetchrow(
            """
            INSERT INTO document_entity_links
                (document_id, entity_id, org_id, link_role, created_by)
            VALUES ($1, $2, $3, $4, $5)
            ON CONFLICT (document_id, entity_id) DO NOTHING
            RETURNING id
            """,
            document_id, entity_id, org_id, link_role, created_by,
        )
        results.append({
            "entity_id": key,
            "created": row is not None,
            "link_id": str(row["id"]) if row else None,
        })
    return results


async def unlink_document_from_entity(conn, org_id, document_id, entity_id) -> bool:
    """Remove a document↔entity link. Returns True if a row was deleted."""
    result = await conn.execute(
        "DELETE FROM document_entity_links "
        "WHERE document_id = $1 AND entity_id = $2 AND org_id = $3",
        document_id, entity_id, org_id,
    )
    # asyncpg returns e.g. "DELETE 1"
    return result.rsplit(" ", 1)[-1] != "0"


async def link_document_to_record(
    conn, org_id, document_id, record_type, record_id, *, created_by,
) -> dict:
    """Link a document to an arbitrary record (spv / transaction / deal / …).
    Polymorphic: (record_type text, record_id uuid). Idempotent."""
    if not await _document_in_org(conn, org_id, document_id):
        raise LinkageError(404, "Document not found")
    if not record_type or not str(record_type).strip():
        raise LinkageError(422, "record_type is required")
    row = await conn.fetchrow(
        """
        INSERT INTO document_record_links
            (document_id, org_id, record_type, record_id, created_by)
        VALUES ($1, $2, $3, $4, $5)
        ON CONFLICT (document_id, record_type, record_id) DO NOTHING
        RETURNING id
        """,
        document_id, org_id, record_type.strip(), record_id, created_by,
    )
    return {
        "record_type": record_type.strip(),
        "record_id": str(record_id),
        "created": row is not None,
        "link_id": str(row["id"]) if row else None,
    }


async def list_document_links(conn, org_id, document_id) -> dict:
    """All links (entity + record) for one document."""
    if not await _document_in_org(conn, org_id, document_id):
        raise LinkageError(404, "Document not found")
    entity_rows = await conn.fetch(
        """
        SELECT l.id, l.entity_id, l.link_role, l.created_by, l.created_at,
               e.display_name, e.entity_type
        FROM document_entity_links l
        JOIN entities e ON e.id = l.entity_id AND e.org_id = l.org_id
        WHERE l.document_id = $1 AND l.org_id = $2
        ORDER BY l.created_at
        """,
        document_id, org_id,
    )
    record_rows = await conn.fetch(
        """
        SELECT id, record_type, record_id, created_by, created_at
        FROM document_record_links
        WHERE document_id = $1 AND org_id = $2
        ORDER BY created_at
        """,
        document_id, org_id,
    )
    return {
        "document_id": str(document_id),
        "entity_links": [
            {
                "link_id": str(r["id"]),
                "entity_id": str(r["entity_id"]),
                "display_name": r["display_name"],
                "entity_type": r["entity_type"],
                "link_role": r["link_role"],
                "created_by": str(r["created_by"]) if r["created_by"] else None,
                "system_created": r["created_by"] is None,
                "created_at": r["created_at"].isoformat() if r["created_at"] else None,
            }
            for r in entity_rows
        ],
        "record_links": [
            {
                "link_id": str(r["id"]),
                "record_type": r["record_type"],
                "record_id": str(r["record_id"]),
                "created_by": str(r["created_by"]) if r["created_by"] else None,
                "created_at": r["created_at"].isoformat() if r["created_at"] else None,
            }
            for r in record_rows
        ],
    }


async def list_documents_for_entity(conn, org_id, entity_id) -> list[dict]:
    """Every document linked to an entity — the query Phase 9's contextual
    surfacing panel will use. Ordered most-recently-linked first."""
    if not await _entity_in_org(conn, org_id, entity_id):
        raise LinkageError(404, "Entity not found")
    rows = await conn.fetch(
        """
        SELECT d.id, d.original_filename, d.status, d.doc_family, d.mime_type,
               d.created_at AS document_created_at,
               l.id AS link_id, l.link_role, l.created_by, l.created_at AS linked_at
        FROM document_entity_links l
        JOIN documents d ON d.id = l.document_id AND d.org_id = l.org_id
        WHERE l.entity_id = $1 AND l.org_id = $2
        ORDER BY l.created_at DESC, d.original_filename
        """,
        entity_id, org_id,
    )
    return [
        {
            "document_id": str(r["id"]),
            "original_filename": r["original_filename"],
            "status": r["status"],
            "doc_family": r["doc_family"],
            "mime_type": r["mime_type"],
            "document_created_at": r["document_created_at"].isoformat()
            if r["document_created_at"] else None,
            "link_id": str(r["link_id"]),
            "link_role": r["link_role"],
            "system_created": r["created_by"] is None,
            "linked_at": r["linked_at"].isoformat() if r["linked_at"] else None,
        }
        for r in rows
    ]


async def list_documents_for_record(conn, org_id, record_type, record_id) -> list[dict]:
    """Every document linked to a GENERIC record (spv / deal / transaction / …)
    via ``document_record_links`` — the query Phase 9's contextual-surfacing panel
    uses for any non-entity record type. Deliberately shaped IDENTICALLY to
    ``list_documents_for_entity`` so the panel renders both uniformly (``link_role``
    is always None here — record links carry no role column). Ordered most-
    recently-linked first. Returns ``[]`` (never raises) for a record with zero
    links: the polymorphic ``record_id`` cannot be existence-checked, and a record
    with no documents is a clean empty state, not an error."""
    if not record_type or not str(record_type).strip():
        raise LinkageError(422, "record_type is required")
    rows = await conn.fetch(
        """
        SELECT d.id, d.original_filename, d.status, d.doc_family, d.mime_type,
               d.created_at AS document_created_at,
               l.id AS link_id, l.created_by, l.created_at AS linked_at
        FROM document_record_links l
        JOIN documents d ON d.id = l.document_id AND d.org_id = l.org_id
        WHERE l.record_type = $1 AND l.record_id = $2 AND l.org_id = $3
        ORDER BY l.created_at DESC, d.original_filename
        """,
        record_type.strip(), record_id, org_id,
    )
    return [
        {
            "document_id": str(r["id"]),
            "original_filename": r["original_filename"],
            "status": r["status"],
            "doc_family": r["doc_family"],
            "mime_type": r["mime_type"],
            "document_created_at": r["document_created_at"].isoformat()
            if r["document_created_at"] else None,
            "link_id": str(r["link_id"]),
            "link_role": None,
            "system_created": r["created_by"] is None,
            "linked_at": r["linked_at"].isoformat() if r["linked_at"] else None,
        }
        for r in rows
    ]


async def list_documents_for_panel(conn, org_id, record_type, record_id) -> list[dict]:
    """Single entry point behind the Phase-9 Documents panel: dispatch by
    ``record_type``. ``entity`` reuses Phase-5's entity-link query verbatim; any
    other type uses the generic ``document_record_links`` query. One function so
    the SAME panel component surfaces documents for every record type."""
    if record_type == "entity":
        return await list_documents_for_entity(conn, org_id, record_id)
    return await list_documents_for_record(conn, org_id, record_type, record_id)


# ---------------------------------------------------------------------------
# Phase 9 Task 4 — basic (NON-semantic) document search
# ---------------------------------------------------------------------------
async def search_documents(conn, org_id, query, *, limit=50) -> list[dict]:
    """BASIC metadata search — filename, ``doc_family`` (category), and a plain
    ILIKE substring match against ``document_extractions.extracted_text``. This is
    deliberately NOT semantic/vector search (that is Phase 11's INDEX/RETRIEVE
    work) — it is case-insensitive substring matching and nothing more. Empty /
    whitespace-only query returns ``[]``. ``matched_on`` reports which field(s)
    hit so the UI can be honest about why a result appeared."""
    q = (query or "").strip()
    if not q:
        return []
    like = f"%{q}%"
    rows = await conn.fetch(
        """
        SELECT d.id, d.original_filename, d.status, d.doc_family, d.mime_type,
               d.created_at,
               (d.original_filename ILIKE $2) AS match_filename,
               (d.doc_family ILIKE $2)        AS match_category,
               EXISTS (
                   SELECT 1 FROM document_extractions x
                   WHERE x.document_id = d.id AND x.org_id = d.org_id
                     AND x.extracted_text ILIKE $2
               )                              AS match_text
        FROM documents d
        WHERE d.org_id = $1
          AND (
               d.original_filename ILIKE $2
               OR d.doc_family ILIKE $2
               OR EXISTS (
                   SELECT 1 FROM document_extractions x
                   WHERE x.document_id = d.id AND x.org_id = d.org_id
                     AND x.extracted_text ILIKE $2
               )
          )
        ORDER BY d.created_at DESC
        LIMIT $3
        """,
        org_id, like, limit,
    )
    results = []
    for r in rows:
        matched_on = [
            label for flag, label in (
                (r["match_filename"], "filename"),
                (r["match_category"], "category"),
                (r["match_text"], "extracted_text"),
            ) if flag
        ]
        results.append({
            "document_id": str(r["id"]),
            "original_filename": r["original_filename"],
            "status": r["status"],
            "doc_family": r["doc_family"],
            "mime_type": r["mime_type"],
            "created_at": r["created_at"].isoformat() if r["created_at"] else None,
            "matched_on": matched_on,
        })
    return results


# ---------------------------------------------------------------------------
# Task 3 — automatic entity linkage for a completed K-1
# ---------------------------------------------------------------------------
async def _latest_k1_extraction(conn, org_id, document_id):
    return await conn.fetchrow(
        """
        SELECT id, mapped_fields
        FROM document_template_extractions
        WHERE document_id = $1 AND org_id = $2 AND template_type = $3
        ORDER BY created_at DESC
        LIMIT 1
        """,
        document_id, org_id, K1_TEMPLATE_TYPE,
    )


async def auto_link_k1_document(conn, org_id, document_id) -> dict:
    """When a K-1 completes: extract the party name, match it against existing
    entities using the SAME picker mechanism, and either

      * auto-link on a confident (exact, case-insensitive) match — a
        ``document_entity_links`` row with ``created_by = NULL`` (system), OR
      * create a ``document_link_proposals`` row (proposed_link_type='entity',
        proposed_name=<extracted name>) when nothing matches.

    NEVER auto-creates an entity. Returns an outcome dict describing what happened.
    """
    if not await _document_in_org(conn, org_id, document_id):
        raise LinkageError(404, "Document not found")

    extraction = await _latest_k1_extraction(conn, org_id, document_id)
    if extraction is None:
        return {"outcome": "skipped", "reason": "no completed K-1 template extraction"}

    mapped_fields = extraction["mapped_fields"]
    if isinstance(mapped_fields, str):
        import json
        try:
            mapped_fields = json.loads(mapped_fields)
        except (ValueError, TypeError):
            mapped_fields = None

    party_name = extract_party_name(mapped_fields)
    if not party_name:
        return {"outcome": "skipped", "reason": "no party name in mapped_fields"}

    # Task 1a: reuse the picker's exact matcher — no second algorithm.
    matches = await find_entity_dupes(conn, org_id, party_name)
    if matches:
        entity_id = matches[0]["id"]
        row = await conn.fetchrow(
            """
            INSERT INTO document_entity_links
                (document_id, entity_id, org_id, link_role, created_by)
            VALUES ($1, $2, $3, 'k1_party', NULL)
            ON CONFLICT (document_id, entity_id) DO NOTHING
            RETURNING id
            """,
            document_id, entity_id, org_id,
        )
        return {
            "outcome": "linked",
            "party_name": party_name,
            "entity_id": str(entity_id),
            "link_created": row is not None,
            "system_created": True,
        }

    # No match → propose (never auto-create an entity).
    proposal_id = await conn.fetchval(
        """
        INSERT INTO document_link_proposals
            (document_id, org_id, proposed_link_type, proposed_name, status)
        VALUES ($1, $2, 'entity', $3, 'pending')
        RETURNING id
        """,
        document_id, org_id, party_name,
    )
    return {
        "outcome": "proposed",
        "party_name": party_name,
        "proposal_id": str(proposal_id),
    }


# ---------------------------------------------------------------------------
# Task 4 — proposal review (approve / reject)
# ---------------------------------------------------------------------------
async def list_pending_proposals(conn, org_id) -> list[dict]:
    rows = await conn.fetch(
        """
        SELECT p.id, p.document_id, p.proposed_link_type, p.proposed_name,
               p.proposed_record_type, p.status, p.created_at,
               d.original_filename
        FROM document_link_proposals p
        LEFT JOIN documents d ON d.id = p.document_id AND d.org_id = p.org_id
        WHERE p.org_id = $1 AND p.status = 'pending'
        ORDER BY p.created_at
        """,
        org_id,
    )
    return [
        {
            "proposal_id": str(r["id"]),
            "document_id": str(r["document_id"]),
            "original_filename": r["original_filename"],
            "proposed_link_type": r["proposed_link_type"],
            "proposed_name": r["proposed_name"],
            "proposed_record_type": r["proposed_record_type"],
            "status": r["status"],
            "created_at": r["created_at"].isoformat() if r["created_at"] else None,
        }
        for r in rows
    ]


async def _get_pending_proposal(conn, org_id, proposal_id):
    row = await conn.fetchrow(
        "SELECT * FROM document_link_proposals WHERE id = $1 AND org_id = $2",
        proposal_id, org_id,
    )
    if row is None:
        raise LinkageError(404, "Proposal not found")
    if row["status"] != "pending":
        raise LinkageError(409, f"Proposal already {row['status']}")
    return row


async def approve_proposal(
    conn, org_id, proposal_id, *, reviewed_by, entity_id=None, entity_type="individual",
) -> dict:
    """Approve a proposal.

    For an ``entity`` proposal, resolve a target entity through the REAL picker
    find-or-create path (Task 1a), then link the document to it:
      * ``entity_id`` given → link to that existing entity;
      * else an exact name match exists now → link to it (find-or-create);
      * else create the entity via ``create_picker_stub_entity`` (incomplete +
        member-todo), NEVER a bare INSERT — then link.

    Always stamps ``status='approved'``, ``reviewed_by``, ``reviewed_at=now()``.
    """
    proposal = await _get_pending_proposal(conn, org_id, proposal_id)
    document_id = proposal["document_id"]
    entity_created = False

    if proposal["proposed_link_type"] == "entity":
        if entity_id is not None:
            if not await _entity_in_org(conn, org_id, entity_id):
                raise LinkageError(404, "Entity not found")
            target_entity_id = entity_id
        else:
            name = proposal["proposed_name"]
            if not name or not name.strip():
                raise LinkageError(
                    422, "Proposal has no proposed_name; supply entity_id to link")
            # Find-or-create through the picker mechanism (same as /entities/stub).
            matches = await find_entity_dupes(conn, org_id, name)
            if matches:
                target_entity_id = matches[0]["id"]
            else:
                new_entity = await create_picker_stub_entity(
                    conn, org_id, entity_type, name.strip(), reviewed_by)
                target_entity_id = new_entity["id"]
                entity_created = True

        link_row = await conn.fetchrow(
            """
            INSERT INTO document_entity_links
                (document_id, entity_id, org_id, link_role, created_by)
            VALUES ($1, $2, $3, 'proposal_approved', $4)
            ON CONFLICT (document_id, entity_id) DO NOTHING
            RETURNING id
            """,
            document_id, target_entity_id, org_id, reviewed_by,
        )
        outcome_extra = {
            "entity_id": str(target_entity_id),
            "entity_created": entity_created,
            "link_created": link_row is not None,
        }
    else:
        # Non-entity proposal: no id in the table to link to, so approval simply
        # records the decision. A record link is created explicitly via the
        # record-link endpoint. (Phase-5 scope centres on entity proposals.)
        outcome_extra = {"note": "non-entity proposal approved; no auto-link"}

    await conn.execute(
        """
        UPDATE document_link_proposals
        SET status = 'approved', reviewed_by = $2, reviewed_at = now()
        WHERE id = $1 AND org_id = $3
        """,
        proposal_id, reviewed_by, org_id,
    )
    return {"outcome": "approved", "proposal_id": str(proposal_id), **outcome_extra}


async def reject_proposal(conn, org_id, proposal_id, *, reviewed_by) -> dict:
    """Reject a proposal. No entity is created and no link is made."""
    await _get_pending_proposal(conn, org_id, proposal_id)
    await conn.execute(
        """
        UPDATE document_link_proposals
        SET status = 'rejected', reviewed_by = $2, reviewed_at = now()
        WHERE id = $1 AND org_id = $3
        """,
        proposal_id, reviewed_by, org_id,
    )
    return {"outcome": "rejected", "proposal_id": str(proposal_id)}
