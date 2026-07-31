"""Chancery Phase 10 — aggregate VDR analysis → deal-creation proposal.

This is the FIRST aggregate / cross-document AI capability in the platform.
Every prior Chancery AI call has been PER-DOCUMENT (one document → one AI call,
see ``services.document_classifier`` and ``services.chancery_intake``). Here we
concatenate the extracted text of EVERY document in a ``document_drops`` batch
and reason across the whole VDR at once to identify a single deal.

Discipline (mirrors every other propose-not-create path on this platform):
  * We NEVER auto-create a ``deals`` row here. The output is a
    ``vdr_deal_proposals`` row (``status='pending'``) — a human reviews it and,
    on approval, the REAL createDeal mechanism (``routers.marketplace``) runs.
  * If the aggregated content does not clearly describe a single coherent deal,
    we DO NOT force a low-confidence, mostly-empty proposal. We report that
    honestly and insert nothing (see ``_meets_confidence_bar``).

``proposed_fields`` (jsonb) is shaped to match the REAL ``deals`` table (Task 1a
discovery): only ``name`` is required by createDeal; everything else is
optional. Taxonomy columns (asset_super_class / asset_class /
asset_sub_category) are taxonomy KEYS validated server-side — the AI cannot know
an org's keys, so we DO NOT populate them. Instead the AI's free-text asset read
goes into ``asset_class_hint`` for the human reviewer to map at approval time.

org_id is always supplied by the caller (never from a request body). Monetary
figures are handled as ``Decimal`` and stored as strings in jsonb to preserve
precision.
"""

from __future__ import annotations

import json
from decimal import Decimal, InvalidOperation

from services.extraction import call_claude_json

# Cap the aggregated prompt so a large VDR does not blow the model context /
# budget. Per-document slice keeps one huge file from crowding out the others.
_MAX_TOTAL_CHARS = 60_000
_MAX_PER_DOC_CHARS = 20_000

# createDeal-bound scalar fields the AI may propose (name handled separately).
_TEXT_FIELDS = ("description", "sponsor_name_override", "location")
_MONEY_FIELDS = ("target_raise", "minimum_investment")
_NUMERIC_FIELDS = ("expected_return_pct",)  # percent, numeric
_INT_FIELDS = ("term_months",)
_LIST_FIELDS = ("highlights", "tags")

_SYSTEM = (
    "You are a diligence analyst reviewing a Virtual Data Room (VDR): a batch of "
    "documents uploaded together for ONE prospective investment deal. You are "
    "given the concatenated text of EVERY document in the batch. Reason across "
    "ALL of them together to identify the single deal they collectively "
    "describe. Do not invent facts that are not supported by the documents. If "
    "the documents do not clearly describe one coherent deal (e.g. they are "
    "unrelated, or none names a deal/sponsor/asset), say so honestly via "
    "is_coherent_deal=false rather than guessing.\n\n"
    "Respond with ONLY a JSON object (no prose, no code fences) with keys:\n"
    "  is_coherent_deal (boolean): true only if the batch clearly describes one "
    "investment deal.\n"
    "  confidence (\"high\"|\"medium\"|\"low\").\n"
    "  name (string): concise deal name, or \"\" if unknown.\n"
    "  description (string): 2-4 sentence investment thesis / summary, or \"\".\n"
    "  sponsor_name_override (string): sponsor / GP / manager name, or \"\".\n"
    "  asset_class_hint (string): plain-English asset class / strategy "
    "(e.g. \"multifamily real estate\", \"private credit\"), or \"\".\n"
    "  location (string): primary geography, or \"\".\n"
    "  target_raise (string): total raise as a bare number, no currency symbols "
    "or commas, or \"\".\n"
    "  minimum_investment (string): minimum check as a bare number, or \"\".\n"
    "  expected_return_pct (string): target/expected annual return as a bare "
    "number (e.g. \"15\" for 15%), or \"\".\n"
    "  term_months (string): investment term in months as a bare integer, or "
    "\"\".\n"
    "  highlights (array of short strings): 0-5 key selling points.\n"
    "  tags (array of short strings): 0-5 topical tags.\n"
    "  rationale (string): one sentence on why this is / isn't a coherent deal."
)


class VDRAnalysisError(Exception):
    def __init__(self, status_code: int, detail: str):
        self.status_code = status_code
        self.detail = detail
        super().__init__(detail)


# ── aggregation ──────────────────────────────────────────────────────────────
async def _drop_belongs_to_org(conn, org_id, document_drop_id) -> bool:
    return bool(await conn.fetchval(
        "SELECT 1 FROM document_drops WHERE id = $1 AND org_id = $2",
        document_drop_id, org_id,
    ))


async def aggregate_drop_text(conn, org_id, document_drop_id) -> list[dict]:
    """Every document in the drop with its extracted text, in drop order.

    Returns ``[{document_id, filename, text}]``. Documents with no extraction row
    (or empty text) are included with ``text=''`` so the caller can see the full
    batch shape and count.
    """
    rows = await conn.fetch(
        """
        SELECT d.id AS document_id,
               d.original_filename AS filename,
               d.sequence_in_drop,
               x.extracted_text
        FROM documents d
        LEFT JOIN document_extractions x
          ON x.document_id = d.id AND x.org_id = d.org_id
        WHERE d.drop_id = $1 AND d.org_id = $2
        ORDER BY d.sequence_in_drop NULLS LAST, d.created_at
        """,
        document_drop_id, org_id,
    )
    return [
        {
            "document_id": r["document_id"],
            "filename": r["filename"],
            "text": (r["extracted_text"] or "").strip(),
        }
        for r in rows
    ]


def _build_prompt(docs: list[dict]) -> str:
    parts: list[str] = []
    used = 0
    for i, doc in enumerate(docs, start=1):
        text = doc["text"][:_MAX_PER_DOC_CHARS]
        header = f"===== DOCUMENT {i}: {doc['filename']} =====\n"
        block = header + (text or "(no extractable text)") + "\n"
        if used + len(block) > _MAX_TOTAL_CHARS:
            block = block[: max(0, _MAX_TOTAL_CHARS - used)]
        parts.append(block)
        used += len(block)
        if used >= _MAX_TOTAL_CHARS:
            parts.append("\n[...additional document text truncated for length...]")
            break
    return "\n".join(parts)


# ── confidence gate ──────────────────────────────────────────────────────────
def _clean_money(value) -> str | None:
    """Parse a monetary string to a canonical Decimal string, or None."""
    if value in (None, ""):
        return None
    raw = str(value).replace(",", "").replace("$", "").strip()
    if not raw:
        return None
    try:
        return str(Decimal(raw))
    except (InvalidOperation, ValueError):
        return None


def _clean_number(value) -> str | None:
    if value in (None, ""):
        return None
    raw = str(value).replace("%", "").replace(",", "").strip()
    if not raw:
        return None
    try:
        return str(Decimal(raw))
    except (InvalidOperation, ValueError):
        return None


def _clean_int(value) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(Decimal(str(value).replace(",", "").strip()))
    except (InvalidOperation, ValueError):
        return None


def _clean_list(value) -> list[str]:
    if not isinstance(value, list):
        return []
    out = []
    for item in value:
        s = str(item).strip()
        if s:
            out.append(s[:200])
    return out[:5]


def normalize_proposed_fields(ai: dict) -> dict:
    """Coerce the raw AI JSON into the stored ``proposed_fields`` shape."""
    name = str(ai.get("name") or "").strip()
    fields: dict = {"name": name}
    for f in _TEXT_FIELDS:
        v = str(ai.get(f) or "").strip()
        fields[f] = v or None
    fields["asset_class_hint"] = (str(ai.get("asset_class_hint") or "").strip()
                                  or None)
    for f in _MONEY_FIELDS:
        fields[f] = _clean_money(ai.get(f))
    for f in _NUMERIC_FIELDS:
        fields[f] = _clean_number(ai.get(f))
    for f in _INT_FIELDS:
        fields[f] = _clean_int(ai.get(f))
    for f in _LIST_FIELDS:
        fields[f] = _clean_list(ai.get(f))
    fields["confidence"] = str(ai.get("confidence") or "").strip().lower() or "low"
    fields["rationale"] = str(ai.get("rationale") or "").strip()
    fields["asset_class"] = None  # taxonomy KEY — set by human reviewer at approve
    fields["asset_super_class"] = None
    fields["asset_sub_category"] = None
    return fields


def _substantive_count(fields: dict) -> int:
    """How many non-name substantive fields the AI actually filled."""
    count = 0
    for f in ("description", "sponsor_name_override", "asset_class_hint",
              "location", "target_raise", "minimum_investment",
              "expected_return_pct", "term_months"):
        if fields.get(f):
            count += 1
    for f in _LIST_FIELDS:
        if fields.get(f):
            count += 1
    return count


def _meets_confidence_bar(ai: dict, fields: dict) -> tuple[bool, str]:
    """Our judgment on "confident enough to propose". Honest gate: refuse weak
    signal rather than emit a mostly-empty record.

    Requires: the model asserts a coherent deal, confidence not "low", a real
    deal name, and at least TWO other substantive fields.
    """
    if not ai.get("is_coherent_deal"):
        return False, "AI did not identify a single coherent deal in the batch."
    if fields["confidence"] == "low":
        return False, "AI confidence was low."
    if not fields["name"]:
        return False, "No deal name could be identified."
    sub = _substantive_count(fields)
    if sub < 2:
        return False, (f"Only {sub} substantive field(s) identified — too thin "
                       "to propose a deal.")
    return True, "Confident enough to propose."


# ── main entry point ─────────────────────────────────────────────────────────
async def analyze_drop(pool, org_id, document_drop_id, *, created_by=None) -> dict:
    """Aggregate a drop, ask the AI to identify a deal, and — only if confident —
    insert a pending ``vdr_deal_proposals`` row.

    Returns a report dict:
      {proposal_created: bool, proposal_id: str|None, reason: str,
       confidence: str|None, document_count: int, fields: dict|None}
    Never raises for "weak signal" — that is a normal, honestly-reported outcome.
    Returns proposal_created=False with a reason when the AI is unavailable.
    """
    async with pool.acquire() as conn:
        if not await _drop_belongs_to_org(conn, org_id, document_drop_id):
            raise VDRAnalysisError(404, "Document drop not found")
        docs = await aggregate_drop_text(conn, org_id, document_drop_id)

    doc_count = len(docs)
    with_text = [d for d in docs if d["text"]]
    if not with_text:
        return {
            "proposal_created": False,
            "proposal_id": None,
            "reason": "No documents in the drop had extractable text to analyze.",
            "confidence": None,
            "document_count": doc_count,
            "fields": None,
        }

    prompt = _build_prompt(docs)
    ai = await call_claude_json(
        _SYSTEM, prompt, max_tokens=900,
        org_id=org_id, task_type="vdr_analysis",
    )
    if ai is None:
        return {
            "proposal_created": False,
            "proposal_id": None,
            "reason": "AI unavailable or returned an unparseable response.",
            "confidence": None,
            "document_count": doc_count,
            "fields": None,
        }

    fields = normalize_proposed_fields(ai)
    ok, reason = _meets_confidence_bar(ai, fields)
    if not ok:
        return {
            "proposal_created": False,
            "proposal_id": None,
            "reason": reason,
            "confidence": fields["confidence"],
            "document_count": doc_count,
            "fields": fields,
        }

    fields["source_document_count"] = doc_count
    async with pool.acquire() as conn:
        proposal_id = await conn.fetchval(
            """
            INSERT INTO vdr_deal_proposals
                (org_id, document_drop_id, proposed_fields, status)
            VALUES ($1, $2, $3::jsonb, 'pending')
            RETURNING id
            """,
            org_id, document_drop_id, json.dumps(fields),
        )
    return {
        "proposal_created": True,
        "proposal_id": str(proposal_id),
        "reason": reason,
        "confidence": fields["confidence"],
        "document_count": doc_count,
        "fields": fields,
    }


# ── review helpers (used by routers.vdr) ─────────────────────────────────────
def _proposal_row(row) -> dict:
    fields = row["proposed_fields"]
    if isinstance(fields, str):
        fields = json.loads(fields)
    return {
        "id": str(row["id"]),
        "document_drop_id": str(row["document_drop_id"]),
        "proposed_fields": fields,
        "status": row["status"],
        "reviewed_by": str(row["reviewed_by"]) if row["reviewed_by"] else None,
        "reviewed_at": row["reviewed_at"].isoformat() if row["reviewed_at"] else None,
        "created_deal_id": (str(row["created_deal_id"])
                            if row["created_deal_id"] else None),
        "created_at": row["created_at"].isoformat() if row["created_at"] else None,
    }


async def list_pending_proposals(conn, org_id) -> list[dict]:
    rows = await conn.fetch(
        """
        SELECT id, document_drop_id, proposed_fields, status, reviewed_by,
               reviewed_at, created_deal_id, created_at
        FROM vdr_deal_proposals
        WHERE org_id = $1 AND status = 'pending'
        ORDER BY created_at DESC
        """,
        org_id,
    )
    return [_proposal_row(r) for r in rows]


async def get_proposal(conn, org_id, proposal_id) -> dict | None:
    row = await conn.fetchrow(
        """
        SELECT id, document_drop_id, proposed_fields, status, reviewed_by,
               reviewed_at, created_deal_id, created_at
        FROM vdr_deal_proposals
        WHERE id = $1 AND org_id = $2
        """,
        proposal_id, org_id,
    )
    return _proposal_row(row) if row else None


async def mark_approved(conn, org_id, proposal_id, *, created_deal_id,
                        reviewed_by) -> None:
    await conn.execute(
        """
        UPDATE vdr_deal_proposals
        SET status = 'approved', created_deal_id = $3,
            reviewed_by = $4, reviewed_at = now()
        WHERE id = $1 AND org_id = $2
        """,
        proposal_id, org_id, created_deal_id, reviewed_by,
    )


async def mark_rejected(conn, org_id, proposal_id, *, reviewed_by) -> None:
    await conn.execute(
        """
        UPDATE vdr_deal_proposals
        SET status = 'rejected', reviewed_by = $3, reviewed_at = now()
        WHERE id = $1 AND org_id = $2
        """,
        proposal_id, org_id, reviewed_by,
    )


async def link_drop_documents_to_deal(conn, org_id, document_drop_id, deal_id, *,
                                      created_by) -> list[dict]:
    """Link EVERY document in the drop to the new deal via Phase-9's proven
    ``document_record_links`` mechanism (record_type='deal'). Idempotent."""
    from services.document_linkage import link_document_to_record

    doc_ids = await conn.fetch(
        "SELECT id FROM documents WHERE drop_id = $1 AND org_id = $2",
        document_drop_id, org_id,
    )
    results = []
    for r in doc_ids:
        results.append(await link_document_to_record(
            conn, org_id, r["id"], "deal", deal_id, created_by=created_by,
        ))
    return results


# ── approval: build the deal body + drive the REAL createDeal core ───────────
# createDeal-bound keys we forward from proposed_fields/overrides. Note:
# asset_class_hint / confidence / rationale / source_document_count are metadata
# for the reviewer and are NOT ``deals`` columns — they are deliberately dropped.
_DEAL_BODY_KEYS = (
    "name", "description", "deal_status", "deal_stage",
    "asset_super_class", "asset_class", "asset_sub_category",
    "sponsor_entity_id", "sponsor_name_override",
    "target_raise", "minimum_investment", "expected_return_pct", "term_months",
    "deal_date", "close_date", "location", "highlights", "tags", "is_featured",
)
_DEAL_MONEY_KEYS = ("target_raise", "minimum_investment", "expected_return_pct")


def build_deal_body(final_fields: dict):
    """Map merged (proposed + human-edited) fields onto a ``DealCreate``.

    Monetary/percent figures are parsed as ``Decimal`` (never float text) and
    handed to the model as-is; pydantic coerces at the createDeal boundary.
    """
    from schemas.marketplace import DealCreate

    kwargs = {k: final_fields[k] for k in _DEAL_BODY_KEYS if k in final_fields}
    for k in _DEAL_MONEY_KEYS:
        v = kwargs.get(k)
        if isinstance(v, str):
            v = v.strip()
            try:
                kwargs[k] = Decimal(v) if v else None
            except (InvalidOperation, ValueError):
                kwargs[k] = None
    name = str(kwargs.get("name") or "").strip()
    if not name:
        raise VDRAnalysisError(422, "A deal name is required to create the deal.")
    kwargs["name"] = name
    return DealCreate(**kwargs)


async def approve_proposal(conn, org_id, proposal_id, *, reviewed_by,
                           overrides: dict | None = None) -> dict:
    """Approve a pending proposal: create the REAL deal via the shared
    ``deal_creation.insert_deal`` core (same path as POST /api/v1/deals), record
    ``created_deal_id``, then link EVERY document in the drop to the deal.

    ``overrides`` (optional) are human edits merged OVER the proposed fields —
    e.g. the reviewer supplying real taxonomy KEYS the AI could not know.
    Caller is responsible for the ``manage_deals`` permission and the enclosing
    transaction. Taxonomy keys, if supplied, are validated with the SAME
    validator the marketplace endpoint uses.
    """
    from services.deal_creation import insert_deal
    from services.taxonomy import validate_taxonomy_fields

    proposal = await get_proposal(conn, org_id, proposal_id)
    if proposal is None:
        raise VDRAnalysisError(404, "Proposal not found")
    if proposal["status"] != "pending":
        raise VDRAnalysisError(409, f"Proposal already {proposal['status']}")

    final_fields = {**proposal["proposed_fields"], **(overrides or {})}
    body = build_deal_body(final_fields)

    tax_errors = await validate_taxonomy_fields(
        str(org_id), body.asset_super_class, body.asset_class,
        body.asset_sub_category,
    )
    if tax_errors:
        raise VDRAnalysisError(422, tax_errors)

    row = await insert_deal(conn, org_id, body, returning="id, name, slug")
    deal_id = row["id"]

    await mark_approved(conn, org_id, proposal_id,
                        created_deal_id=deal_id, reviewed_by=reviewed_by)
    links = await link_drop_documents_to_deal(
        conn, org_id, proposal["document_drop_id"], deal_id,
        created_by=reviewed_by,
    )
    return {
        "proposal_id": str(proposal_id),
        "status": "approved",
        "created_deal_id": str(deal_id),
        "deal_name": row["name"],
        "deal_slug": row["slug"],
        "linked_documents": len(links),
        "links": links,
    }


async def reject_proposal(conn, org_id, proposal_id, *, reviewed_by) -> dict:
    """Reject a pending proposal: NO deal, NO links. Idempotent-ish (409 if
    already decided)."""
    proposal = await get_proposal(conn, org_id, proposal_id)
    if proposal is None:
        raise VDRAnalysisError(404, "Proposal not found")
    if proposal["status"] != "pending":
        raise VDRAnalysisError(409, f"Proposal already {proposal['status']}")
    await mark_rejected(conn, org_id, proposal_id, reviewed_by=reviewed_by)
    return {"proposal_id": str(proposal_id), "status": "rejected"}
