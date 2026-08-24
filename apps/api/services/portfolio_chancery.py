"""Portfolio Phase E — turning a CONFIRMED Chancery document into a portfolio
asset + position, with the document linked to both.

────────────────────────────────────────────────────────────────────────────
TASK 1a — THE REAL HOOK POINT, TRACED RATHER THAN ASSUMED
────────────────────────────────────────────────────────────────────────────
``services.document_review.confirm_document(conn, org_id, document_id, *,
confirmed_by)`` (``document_review.py:356``) is the whole of it: one UPDATE
setting ``documents.status = 'confirmed'`` plus ``confirmed_by`` /
``confirmed_at``, returning those three values. It has **no extension point** —
no callback, no event table, no return of extracted fields.

The hook that DOES exist is one layer up, in the router:
``routers/document_review.py:104`` — ``POST /documents/{id}/confirm`` calls
``review.confirm_document`` and then, only if that succeeded, calls
``chancery_workflow_bridge.fire_document_confirmed_triggers(pool, org_id,
document_id, started_by=user_id)``. That is the established seam: an
after-the-fact call in the router, ordered AFTER the status write so a failed
confirm can never fire it, and written not to raise so it cannot break an
already-successful confirm.

**What is available at that point**, and nothing more: ``org_id`` (from JWT
claims via ``get_org_id``), ``document_id``, ``user_id``, and the pool. The
confirmed extraction fields are NOT passed — they must be read back from
``document_template_extractions`` / ``document_narrative_extractions`` by
``document_id``, which is what :func:`read_document_extractions` does.

**This module deliberately does not add a second bridge to that router.** A
"this document represents a position" decision is not one an auto-fire can make:
the same confirmed capital-account statement is a new position the first quarter
and a valuation on an existing one every quarter after, and nothing in the
document distinguishes them. :func:`create_position_from_chancery_document` is
therefore an explicitly-called function that VERIFIES the document reached the
confirm hook (``status='confirmed'``), rather than a trigger hanging off it.

────────────────────────────────────────────────────────────────────────────
TASK 1b — WHAT CHANCERY EXTRACTION ACTUALLY PRODUCES. HONEST ANSWER: NOT THIS.
────────────────────────────────────────────────────────────────────────────
Both deployed extractors were read, and the deployed ``reference_data``
``doc_category`` list was queried (12 codes, org_id NULL, all active).

**1. Narrative (Phase 11a, ``services/narrative_extraction.py``)** stores
``document_narrative_extractions`` with exactly four payload columns: ``summary``
(text), ``extracted_provisions`` / ``key_dates`` / ``key_parties`` (jsonb). Their
shapes are fixed by ``normalize_extraction``:

    key_provisions : [{"provision_type": str|None, "description": str|None}]
    key_dates      : [{"date": str|None, "description": str|None}]
    key_parties    : [{"name": str, "role": str|None}]

There is **no monetary field of any kind** — not one key in any of the three
lists holds a number. So nothing in narrative extraction maps to
``commitment_amount``, ``called_to_date`` or ``distributed_to_date``.

It also would not run on the document in question. ``run_narrative_extraction``
is gated by ``chancery_intake._NARRATIVE_CATEGORIES`` = ``{llc_formation,
trust_instrument, will, estate_plan, operating_agreement}``. **There is no
``capital_account_statement`` code in the deployed catalogue at all** — the
nearest are ``financial_statement`` and ``subscription_doc``, both of which
``doc_family_for_category`` places in the TABULAR family, so a capital-account
statement is routed to the K-1 extractor, not to this one.

**2. Tabular (Phase 3, ``services/textract_extraction.py``)** stores
``document_template_extractions`` with ``template_type`` — and ``'k1'`` is the
only template that exists (``K1_TEMPLATE_TYPE``, and ``run_k1_extraction`` is
gated on ``doc_category = 'k1'``). Its ``mapped_fields`` keys are fixed by
``_K1_BOXES`` and ``_FORM_TYPES``:

    ordinary_business_income · net_rental_real_estate_income · interest_income
    ordinary_dividends · net_long_term_capital_gain
    partner_name | shareholder_name | beneficiary_name

Income boxes and the RECIPIENT's name. No commitment, no called-to-date, no
distributed-to-date — and the party name is the **partner**, not the fund, so
using it as an asset name would label the holding with the holder.

**CONCLUSION, stated plainly: a capital-account statement's commitment / called /
distributed figures require a NEW extraction template that does not exist.** This
module does not pretend otherwise. :data:`COMMITMENT_FIELDS_NOT_EXTRACTED` names
the four fields no deployed extractor produces, and
:func:`commitment_fields_from_document` returns them as ``missing`` every time,
with the reason — so a caller that assumed extraction would supply them finds out
at the call and not from a zero in a report. Building that template is Chancery's
work, not the portfolio layer's, and guessing at its output shape now would mean
writing a mapper against fields whose names nobody has chosen yet.

**What DOES genuinely exist, and is therefore what gets mapped:**

  * ``documents.original_filename`` — always present (NOT NULL).
  * ``document_narrative_extractions.key_parties[].name`` — a real, extracted,
    document-stated NAME. For an ``llc_formation`` or ``operating_agreement``
    (the constituting document of a private holding), the instrument's own named
    entity is genuinely the asset's identifying name.
  * ``document_narrative_extractions.summary`` — real prose, carried through as
    provenance rather than parsed for numbers.

:func:`derive_asset_name` implements exactly that ladder and REPORTS which rung
it used, because "where did this name come from" is the first question asked of
an auto-created asset.

────────────────────────────────────────────────────────────────────────────
TASK 1c — THE COMPOSITION IS PHASE D'S, UNCHANGED
────────────────────────────────────────────────────────────────────────────
``services.portfolio_documents.create_asset_from_document`` and
``create_position_from_document`` already do "call A2's writer, then link the id
it returned" for ``record_type='portfolio_asset'`` and ``'portfolio_position'``.
This module calls those two functions and contains **no** ``INSERT INTO
portfolio.*`` and **no** direct ``document_record_links`` write — asserted by AST
in the verification, the same way Phase D asserted it of the cash module.

That matters beyond tidiness: ``portfolio_assets.create_position`` is the only
code in the codebase enforcing the ownership-basis contract (``positions`` has no
CHECK covering it), and ``link_portfolio_document`` is the only thing checking
the record-type vocabulary against a column that has no CHECK either. A
Chancery-specific shortcut past either would be the one write path nobody
validated.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from services.portfolio_assets import (
    PortfolioError,
    _OrgWrite,
    _require_org,
)
from services.portfolio_documents import (
    RECORD_TYPE_ASSET,
    RECORD_TYPE_POSITION,
    create_asset_from_document,
    create_position_from_document,
)

#: ``positions_authority_chk``. A statement or a deed STATES the holding; it is
#: not an aggregator feed (``aggregated``), not a custodian file (``custodial``),
#: not our own derivation (``internal``) and not a human typing into a form
#: (``manual``). ``stated`` is the honest one and the phase brief mandates it.
CHANCERY_AUTHORITY = "stated"

#: ``positions_source_chk``. Already in A2's deployed vocabulary — Phase B
#: reserved the slot and Phase B's own status note recorded that nothing wrote
#: through it yet. This is what writes through it.
CHANCERY_SOURCE_SYSTEM = "chancery"

#: ``documents.status`` after ``document_review.confirm_document``. Imported by
#: value rather than from ``document_review`` so this module stays importable
#: with the review subsystem absent, and asserted equal to it in verification.
CONFIRMED_STATUS = "confirmed"

#: The four commitment figures NO deployed Chancery extractor produces. See
#: Task 1b above. Named so the gap is machine-checkable, not prose-only.
COMMITMENT_FIELDS_NOT_EXTRACTED = frozenset({
    "commitment_amount", "called_to_date", "distributed_to_date",
    "recallable_amount",
})

#: Why. One sentence, returned to every caller that asks for those fields.
COMMITMENT_EXTRACTION_GAP = (
    "No deployed Chancery extractor produces commitment figures. Narrative "
    "extraction (document_narrative_extractions) has four payload columns — "
    "summary, extracted_provisions, key_dates, key_parties — and not one "
    "monetary key among them; template extraction has exactly one template "
    "('k1') whose mapped_fields are five income boxes and the recipient's name. "
    "A capital-account-statement template does not exist and inventing its "
    "field names here would be fabricating an interface."
)

#: Which rung of :func:`derive_asset_name`'s ladder supplied the name.
NAME_SOURCE_EXPLICIT = "caller_supplied"
NAME_SOURCE_NARRATIVE_PARTY = "narrative_key_parties"
NAME_SOURCE_FILENAME = "documents.original_filename"

TABLE_DOCUMENTS = "public.documents"
TABLE_NARRATIVE = "public.document_narrative_extractions"
TABLE_TEMPLATE = "public.document_template_extractions"


class ChanceryPortfolioError(PortfolioError):
    """A Chancery document could not be turned into a portfolio record."""


@dataclass(frozen=True)
class DocumentExtractions:
    """Everything the deployed extractors actually stored for one document."""

    document_id: str
    original_filename: str
    status: str
    doc_family: str | None
    #: ``document_narrative_extractions`` payload, or ``None`` if the document
    #: was never narrative-extracted (the case for every statement).
    narrative: dict | None = None
    #: ``document_template_extractions.mapped_fields``, or ``None``.
    template_mapped_fields: dict | None = None
    template_type: str | None = None

    @property
    def is_confirmed(self) -> bool:
        return self.status == CONFIRMED_STATUS

    @property
    def key_parties(self) -> list[dict]:
        return list((self.narrative or {}).get("key_parties") or [])


@dataclass(frozen=True)
class ChanceryPositionResult:
    """What :func:`create_position_from_chancery_document` created."""

    asset_id: str
    position_id: str
    asset_name: str
    name_source: str
    document_id: str
    asset_link: dict
    position_link: dict
    authority: str = CHANCERY_AUTHORITY
    source_system: str = CHANCERY_SOURCE_SYSTEM
    #: Fields a caller asked to be extracted that no extractor produces.
    unextractable_fields: tuple[str, ...] = field(default_factory=tuple)


# ── Reading what extraction really left behind ──────────────────────────────


def _decode_jsonb(value):
    """``document_*_extractions`` jsonb comes back as ``str`` under asyncpg with
    no codec registered — the same decode ``textract_extraction._decode_jsonb``
    does, and for the same reason."""
    if isinstance(value, (str, bytes)):
        try:
            return json.loads(value)
        except (ValueError, TypeError):
            return None
    return value


async def read_document_extractions(
    conn, *, org_id: str, document_id: str
) -> DocumentExtractions:
    """Load a document plus whatever the two deployed extractors stored for it.

    Org-scoped through :class:`_OrgWrite` so RLS on ``documents`` is the gate,
    not a Python ``if`` — this is the function a cross-org caller reaches first.
    """
    org_id = _require_org(org_id)
    async with _OrgWrite(conn, org_id) as c:
        doc = await c.fetchrow(
            f"SELECT id::text AS id, original_filename, status, doc_family "
            f"FROM {TABLE_DOCUMENTS} WHERE id = $1::uuid AND org_id = $2::uuid",
            str(document_id), org_id,
        )
        if doc is None:
            raise ChanceryPortfolioError(
                f"document {document_id} does not exist in org {org_id}"
            )
        narrative = await c.fetchrow(
            f"SELECT summary, extracted_provisions, key_dates, key_parties "
            f"FROM {TABLE_NARRATIVE} "
            f"WHERE document_id = $1::uuid AND org_id = $2::uuid "
            f"ORDER BY created_at DESC LIMIT 1",
            str(document_id), org_id,
        )
        template = await c.fetchrow(
            f"SELECT template_type, mapped_fields FROM {TABLE_TEMPLATE} "
            f"WHERE document_id = $1::uuid AND org_id = $2::uuid "
            f"ORDER BY created_at DESC LIMIT 1",
            str(document_id), org_id,
        )

    return DocumentExtractions(
        document_id=doc["id"],
        original_filename=doc["original_filename"],
        status=doc["status"],
        doc_family=doc["doc_family"],
        narrative=None if narrative is None else {
            "summary": narrative["summary"],
            "key_provisions": _decode_jsonb(narrative["extracted_provisions"]) or [],
            "key_dates": _decode_jsonb(narrative["key_dates"]) or [],
            "key_parties": _decode_jsonb(narrative["key_parties"]) or [],
        },
        template_mapped_fields=(
            None if template is None else _decode_jsonb(template["mapped_fields"])
        ),
        template_type=None if template is None else template["template_type"],
    )


def commitment_fields_from_document(extractions: DocumentExtractions) -> dict:
    """What a caller can and cannot get out of this document's extractions.

    Always returns every one of :data:`COMMITMENT_FIELDS_NOT_EXTRACTED` under
    ``missing``, with :data:`COMMITMENT_EXTRACTION_GAP` as the reason — because
    that is the truth today, on every document, for every category. Written as a
    function rather than a comment so a caller finds the gap by calling it, and
    so the day a capital-account template ships this is the one place that
    changes.
    """
    return {
        "document_id": extractions.document_id,
        "available": {},
        "missing": sorted(COMMITMENT_FIELDS_NOT_EXTRACTED),
        "reason": COMMITMENT_EXTRACTION_GAP,
        "narrative_extracted": extractions.narrative is not None,
        "template_type": extractions.template_type,
    }


def derive_asset_name(
    extractions: DocumentExtractions, explicit_name: str | None = None
) -> tuple[str, str]:
    """The asset's identifying name and WHICH real field supplied it.

    The ladder, all three rungs reading fields that genuinely exist:

      1. ``explicit_name`` — a human at the confirm screen typed it. Always wins;
         a person looking at the document beats any inference from it.
      2. the first ``key_parties[].name`` from narrative extraction — a real,
         document-stated name. Only reachable for the five narrative categories,
         which is exactly where it is the right answer: an ``llc_formation`` or
         ``operating_agreement`` names the entity it constitutes.
      3. ``documents.original_filename`` — NOT NULL, so this rung never fails.
         A filename is a poor asset name and is meant to be: it is visibly
         provisional, which is what makes somebody fix it.

    Deliberately NOT a rung: ``template_mapped_fields['partner_name']``. On a K-1
    that is the RECIPIENT of the K-1, not the partnership, and using it would
    name the asset after its holder — a mistake that looks correct in a list.
    """
    if explicit_name and explicit_name.strip():
        return explicit_name.strip(), NAME_SOURCE_EXPLICIT
    for party in extractions.key_parties:
        name = (party or {}).get("name")
        if isinstance(name, str) and name.strip():
            return name.strip(), NAME_SOURCE_NARRATIVE_PARTY
    stem = os.path.splitext(extractions.original_filename or "")[0].strip()
    return (stem or extractions.original_filename), NAME_SOURCE_FILENAME


def infer_ownership_basis(
    ownership_basis: str | None,
    *,
    quantity=None,
    ownership_pct=None,
    market_value=None,
) -> str:
    """Resolve the ownership basis ONCE, for both the asset and the position.

    A2's ``create_position`` defaults an omitted basis to the ASSET's declared
    one. That inheritance is right in general and is a trap here specifically:
    this function creates the asset and the position in the same breath, so an
    asset created with A2's ``'units'`` default while the caller supplied
    ``ownership_pct`` produces a position that inherits ``'units'`` and is
    refused by ``_validate_basis`` — an error about a basis nobody chose.

    So the basis is decided here from what the caller actually supplied, and the
    SAME value is passed to both writers. Explicit wins; otherwise a percentage
    means ``percent``, units mean ``units``, and everything else means ``value``
    — which is the honest default for a document-sourced holding, where the
    stated amount IS the fact and there is no unit count behind it.
    """
    if ownership_basis:
        return ownership_basis
    if ownership_pct is not None:
        return "percent"
    if quantity is not None:
        return "units"
    return "value"


# ── The creation itself ─────────────────────────────────────────────────────


async def create_position_from_chancery_document(
    conn,
    *,
    org_id: str,
    document_id: str,
    owner_entity_id: str,
    asset_type: str,
    as_of_date: date,
    name: str | None = None,
    asset_class: str = "financial",
    valuation_method: str = "nav",
    include_in_performance: bool = True,
    ownership_basis: str | None = None,
    quantity: Decimal | int | str | None = None,
    ownership_pct: Decimal | int | str | None = None,
    market_value: Decimal | int | str | None = None,
    cost_basis: Decimal | int | str | None = None,
    currency_code: str | None = None,
    default_taxonomy_key: str | None = None,
    taxonomy_key: str | None = None,
    inception_date: date | None = None,
    created_by: str | None = None,
    require_confirmed: bool = True,
) -> ChanceryPositionResult:
    """Create an asset + position FROM a confirmed Chancery document, and link
    the document to BOTH.

    ``authority='stated'`` and ``source_system='chancery'`` are not parameters.
    A caller who could pass ``authority='custodial'`` through this function would
    be asserting a custodian confirmed a holding that a PDF asserted, and the
    precedence engine (Phase B) ranks sources by exactly that field.

    ``require_confirmed=True`` is the Task-1a hook made explicit: this refuses to
    run against a document that has not been through
    ``document_review.confirm_document``. A dropped, sorted or extracted document
    is one nobody has vouched for, and a position created from it carries
    ``authority='stated'`` while nothing actually stated it. The flag exists
    because an operator backfilling historical documents may legitimately need to
    bypass it — but they have to say so.

    ``valuation_method`` defaults to ``'nav'``, not to A2's ``'market_price'``.
    A holding whose source of truth is a PDF has no listed price series by
    definition, and A2's ``record_transaction`` derives an asset's MARKET from
    this column: ``market_price`` would make ``call_investment`` and every other
    private-market type illegal against the position this function just created.
    A hard asset should pass ``'appraisal'``.

    ``include_in_performance`` and ``asset_class`` are passed through to
    ``create_asset`` explicitly on EVERY call, defaults included, so a hard asset
    overriding both is one call and not a follow-up UPDATE.

    Order is asset → link → position → link. The asset link is written before the
    position exists, so a failure between them leaves a linked asset with no
    position: a visible, fixable half-state. The reverse order would leave a
    position whose asset nobody can trace to a document.
    """
    org_id = _require_org(org_id)
    if not isinstance(as_of_date, date):
        raise ChanceryPortfolioError(
            f"as_of_date must be a datetime.date — got {type(as_of_date).__name__}"
        )

    extractions = await read_document_extractions(
        conn, org_id=org_id, document_id=document_id
    )
    if require_confirmed and not extractions.is_confirmed:
        raise ChanceryPortfolioError(
            f"document {document_id} has status={extractions.status!r}, not "
            f"{CONFIRMED_STATUS!r}. The Task-1a hook point is "
            f"document_review.confirm_document; a position sourced from an "
            f"unconfirmed document would carry authority={CHANCERY_AUTHORITY!r} "
            f"with nobody having stated anything. Pass require_confirmed=False "
            f"to backfill deliberately."
        )

    asset_name, name_source = derive_asset_name(extractions, name)
    basis = infer_ownership_basis(
        ownership_basis, quantity=quantity, ownership_pct=ownership_pct,
        market_value=market_value,
    )

    created_asset = await create_asset_from_document(
        conn,
        org_id=org_id,
        document_id=document_id,
        created_by=created_by,
        name=asset_name,
        asset_type=asset_type,
        asset_class=asset_class,
        valuation_method=valuation_method,
        include_in_performance=include_in_performance,
        ownership_basis=basis,
        currency_code=currency_code,
        default_taxonomy_key=default_taxonomy_key,
        inception_date=inception_date,
    )
    asset_id = created_asset["asset_id"]

    created_position = await create_position_from_document(
        conn,
        org_id=org_id,
        document_id=document_id,
        created_by=created_by,
        owner_entity_id=owner_entity_id,
        asset_id=asset_id,
        as_of_date=as_of_date,
        authority=CHANCERY_AUTHORITY,
        source_system=CHANCERY_SOURCE_SYSTEM,
        ownership_basis=basis,
        quantity=quantity,
        ownership_pct=ownership_pct,
        market_value=market_value,
        cost_basis=cost_basis,
        taxonomy_key=taxonomy_key,
    )

    return ChanceryPositionResult(
        asset_id=asset_id,
        position_id=created_position["position_id"],
        asset_name=asset_name,
        name_source=name_source,
        document_id=str(document_id),
        asset_link=created_asset["link"],
        position_link=created_position["link"],
        unextractable_fields=tuple(sorted(COMMITMENT_FIELDS_NOT_EXTRACTED)),
    )


# Named for what it is at the call site. The four record types Phase D defined
# are re-exported so a caller reading a link back does not have to know which
# module owns the vocabulary.
ASSET_RECORD_TYPE = RECORD_TYPE_ASSET
POSITION_RECORD_TYPE = RECORD_TYPE_POSITION
