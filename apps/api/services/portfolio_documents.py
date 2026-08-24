"""Portfolio Phase D — document drill-through for portfolio records.

WHAT THIS IS, AND WHY IT IS SO SHORT
──────────────────────────────────────────────────────────────────────────────
"Show me the statement this valuation came from." Chancery already built every
part of that except the four record types.

Task 1d, introspected against the deployed database rather than assumed:
``document_record_links`` is ``(document_id, org_id, record_type text, record_id
uuid)`` with a UNIQUE on ``(document_id, record_type, record_id)`` and **NO
CHECK CONSTRAINT** on ``record_type``. There is no vocabulary in the service
(``link_document_to_record`` only checks non-emptiness), none in the router
(``record_type: str``), and none in the frontend (``DocumentsPanel`` passes it
straight through to the URL). It is genuinely polymorphic text.

**So adding the four Phase-D record types required no migration at all**, and
this module deliberately does not add one — a CHECK constraint introduced now
would break the polymorphism Chancery relies on for every record type it has not
thought of yet. The constants below are a Python-side vocabulary for THIS
subsystem, enforced by :func:`link_portfolio_document` so a typo'd
``'portfolio_positon'`` fails at the call instead of writing a link that nothing
will ever read back.

REUSE, NOT A SECOND LINKAGE ENGINE
──────────────────────────────────────────────────────────────────────────────
Every function here delegates to ``services.document_linkage`` — the Phase-5
engine behind the Phase-9 Documents panel. No second INSERT into
``document_record_links``, no second lookup query. That is what makes the
assertion "queryable via Chancery's real existing lookup path" true rather than
merely plausible: the read side is ``list_documents_for_panel``, byte-for-byte
the function ``GET /records/{record_type}/{record_id}/documents`` calls, so a
link written here renders in the existing panel with no UI work.

The ``created_by`` convention is Chancery's, unchanged: a human linking → that
user's id; the SYSTEM auto-linking from an extraction → ``NULL``.

WHAT IS NOT HERE
──────────────────────────────────────────────────────────────────────────────
No UI. Phase 9's ``DocumentsPanel`` already renders linked documents for any
``(record_type, record_id)`` and needs nothing from this phase but the rows.
No unlink endpoint — ``document_linkage`` owns that, and a portfolio-specific
one would be a second delete path against the same table.
"""

from __future__ import annotations

from typing import Any

from services.document_linkage import (
    link_document_to_record,
    list_documents_for_panel,
)
from services.portfolio_assets import (
    PortfolioError,
    _OrgWrite,
    _require_org,
    create_asset,
    create_position,
    record_transaction,
    record_valuation,
)

# ── The four Phase-D record types, per the design ───────────────────────────
RECORD_TYPE_POSITION = "portfolio_position"
RECORD_TYPE_VALUATION = "portfolio_valuation"
RECORD_TYPE_TRANSACTION = "portfolio_transaction"
RECORD_TYPE_ASSET = "portfolio_asset"

#: Prefixed, not bare. ``document_record_links.record_type`` is a GLOBAL
#: namespace shared with every Chancery record type — ``entity``, ``spv``,
#: ``deal``, ``transaction``. A bare ``'transaction'`` from this subsystem would
#: collide with the SPV-ledger ``transaction`` links that already use it, and
#: since ``record_id`` is an unconstrained uuid nothing would raise: the panel
#: would just show one record's documents against another's.
PORTFOLIO_RECORD_TYPES = frozenset({
    RECORD_TYPE_POSITION,
    RECORD_TYPE_VALUATION,
    RECORD_TYPE_TRANSACTION,
    RECORD_TYPE_ASSET,
})


def _check_record_type(record_type: str) -> str:
    if record_type not in PORTFOLIO_RECORD_TYPES:
        raise PortfolioError(
            f"record_type={record_type!r} is not a portfolio record type. "
            f"Expected one of {sorted(PORTFOLIO_RECORD_TYPES)}. "
            f"document_record_links has no CHECK constraint, so an unrecognised "
            f"value would be written happily and read back by nothing."
        )
    return record_type


async def link_portfolio_document(
    conn,
    *,
    org_id: str,
    document_id: str,
    record_type: str,
    record_id: str,
    created_by: str | None = None,
) -> dict:
    """Link a document to a portfolio record. Idempotent.

    ``org_id`` comes from the caller's JWT claims, never a request body — the
    standing rule, and here it is also the RLS context ``_OrgWrite`` raises, so
    a mismatch is refused by the database rather than by a Python ``if``.

    Idempotency is the UNIQUE on ``(document_id, record_type, record_id)`` plus
    Chancery's ``ON CONFLICT DO NOTHING``. Re-running an extraction re-links the
    same document to the same record and creates nothing.

    ``created_by=None`` means the SYSTEM made this link — Chancery's convention,
    used unchanged. The ``from_document`` wrappers below default to it, because
    that is what they are.
    """
    org_id = _require_org(org_id)
    _check_record_type(record_type)
    if not document_id:
        raise PortfolioError("document_id is required")
    if not record_id:
        raise PortfolioError("record_id is required")

    # `document_linkage` writes with a plain conn and no SET LOCAL of its own —
    # it runs inside the router's org-scoped pool wrapper. Portfolio services do
    # not have that wrapper, so the context is raised here, with the SAME
    # `_OrgWrite` every other portfolio write uses.
    async with _OrgWrite(conn, org_id) as c:
        return await link_document_to_record(
            c,
            org_id,
            document_id,
            record_type,
            record_id,
            created_by=created_by,
        )


async def list_portfolio_record_documents(
    conn, *, org_id: str, record_type: str, record_id: str
) -> list[dict]:
    """Documents linked to a portfolio record.

    Delegates to ``document_linkage.list_documents_for_panel`` — the exact
    function behind ``GET /records/{record_type}/{record_id}/documents`` and the
    Phase-9 ``DocumentsPanel``. Not a similar query: the same one. A link this
    module writes is therefore visible in the existing panel without any UI
    change, which is what Task 4 asked for.
    """
    org_id = _require_org(org_id)
    _check_record_type(record_type)
    async with _OrgWrite(conn, org_id) as c:
        return await list_documents_for_panel(c, org_id, record_type, str(record_id))


# ── The natural points: create the record, then link it ─────────────────────
#
# Thin on purpose. Each wrapper calls A2's writer unchanged and then links the
# id it returned. The alternative — an optional `document_id` kwarg threaded
# into every A2 signature — would put a Chancery dependency inside the module
# that must stay importable with no document subsystem present at all.


async def record_valuation_from_document(
    conn,
    *,
    org_id: str,
    document_id: str,
    created_by: str | None = None,
    **valuation_kwargs: Any,
) -> dict:
    """Record a valuation extracted from a document, and link the two.

    The canonical case: a capital-account statement or an appraisal lands in
    Chancery, an extraction reads a NAV off it, and the resulting mark must be
    traceable to the page it came from. A valuation whose provenance is a
    ``valuation_source`` STRING is not traceable — "Q2 statement" does not open.

    The link is written AFTER the valuation and outside its transaction. If the
    link fails the valuation still stands, which is the right way round: an
    unlinked mark is a documentation gap, a lost mark is a data loss.
    """
    org_id = _require_org(org_id)
    valuation_id = await record_valuation(conn, org_id=org_id, **valuation_kwargs)
    link = await link_portfolio_document(
        conn,
        org_id=org_id,
        document_id=document_id,
        record_type=RECORD_TYPE_VALUATION,
        record_id=valuation_id,
        created_by=created_by,
    )
    return {"valuation_id": valuation_id, "link": link}


async def record_transaction_from_document(
    conn,
    *,
    org_id: str,
    document_id: str,
    created_by: str | None = None,
    **transaction_kwargs: Any,
) -> dict:
    """Record a transaction extracted from a document, and link the two.

    A capital-call notice or a distribution notice is the source document for
    exactly one transaction, and "which notice was this call?" is the question
    asked every time a member disputes a wire.
    """
    org_id = _require_org(org_id)
    transaction_id = await record_transaction(conn, org_id=org_id, **transaction_kwargs)
    link = await link_portfolio_document(
        conn,
        org_id=org_id,
        document_id=document_id,
        record_type=RECORD_TYPE_TRANSACTION,
        record_id=transaction_id,
        created_by=created_by,
    )
    return {"transaction_id": transaction_id, "link": link}


async def create_position_from_document(
    conn,
    *,
    org_id: str,
    document_id: str,
    created_by: str | None = None,
    **position_kwargs: Any,
) -> dict:
    """Create a position from a document (a statement, a K-1), and link it."""
    org_id = _require_org(org_id)
    position_id = await create_position(conn, org_id=org_id, **position_kwargs)
    link = await link_portfolio_document(
        conn,
        org_id=org_id,
        document_id=document_id,
        record_type=RECORD_TYPE_POSITION,
        record_id=position_id,
        created_by=created_by,
    )
    return {"position_id": position_id, "link": link}


async def create_asset_from_document(
    conn,
    *,
    org_id: str,
    document_id: str,
    created_by: str | None = None,
    **asset_kwargs: Any,
) -> dict:
    """Create an asset from a document (a deed, an operating agreement), and
    link it. The document that CONSTITUTED the asset, not one that mentions it —
    which is why this links to the asset and not to a position on it."""
    org_id = _require_org(org_id)
    asset_id = await create_asset(conn, org_id=org_id, **asset_kwargs)
    link = await link_portfolio_document(
        conn,
        org_id=org_id,
        document_id=document_id,
        record_type=RECORD_TYPE_ASSET,
        record_id=asset_id,
        created_by=created_by,
    )
    return {"asset_id": asset_id, "link": link}


async def link_imported_positions(
    conn,
    *,
    org_id: str,
    document_id: str,
    position_ids: list[str],
    created_by: str | None = None,
) -> list[dict]:
    """Link every position an import produced to the file it came from.

    The Phase-B hook. ``import_positions_file`` returns ``ImportResult.positions``
    — the ids it wrote — and when that file is a Chancery document this is what
    makes each resulting holding drill back to the upload.

    One link per position rather than one per file: the question is asked from a
    position ("where did THIS holding come from?"), and a file-level link cannot
    answer it without a scan.
    """
    return [
        await link_portfolio_document(
            conn,
            org_id=org_id,
            document_id=document_id,
            record_type=RECORD_TYPE_POSITION,
            record_id=pid,
            created_by=created_by,
        )
        for pid in position_ids
    ]
