"""CASH08 — VENDOR INVOICES: an invoice uploaded to Chancery becomes a FIRM cash obligation (cash01's vendor_invoice).

    Chancery intake (S26) → the document is TAGGED as one of the household's vendor invoices
      (document_record_links 'household_vendor_invoice' → the household; Chancery has no invoice category —
      docs/cash/CASH08_NOTES.md §1)
    → Treasury → Invoices lists the household's tagged invoices not yet recorded (``awaiting``)
    → a staff member RECORDS it (``record``): an ACTIVE payee of the household, the paying entity (a household member),
      the invoice number, the amount and due date TYPED by the person (no model call, no extraction)
    → ``cash_obligations.create`` (source_type vendor_invoice, certainty firm, source_ref → the document) — in coverage
      at once — plus the 'cash_payee_invoice' (→ payee) and 'cash_obligation' (→ the obligation lineage) links, in ONE
      transaction.

DUPLICATES: the same payee + invoice number + amount is recorded once — refused by the service, and by
``cash_obligations_external_key_uq`` (``external_key = vendor_invoice:<payee>:<invoice number>:<amount>``) as the
backstop. The invoice number is normalised (trimmed, upper-cased, inner spaces collapsed) before it is compared.
Every record / refusal is audited (``cash.invoice.*``).
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from decimal import Decimal

from services import cash_obligations as OB
from services import cash_payees as CP

log = logging.getLogger(__name__)


class InvoiceError(ValueError):
    """422."""


class InvoiceNotFound(LookupError):
    pass


class InvoiceDuplicate(Exception):
    """409 — already recorded (same payee, invoice number and amount)."""


DUPLICATE = "this invoice is already recorded (same payee, invoice number and amount)"

AWAITING_SQL = """
SELECT d.id, d.original_filename, d.created_at, d.status, d.storage_key IS NOT NULL AS has_file, l.created_by
  FROM public.document_record_links l
  JOIN public.documents d ON d.id = l.document_id AND d.org_id = l.org_id
 WHERE l.org_id = $1::uuid AND l.record_type = $2 AND l.record_id = $3::uuid
   AND NOT EXISTS (SELECT 1 FROM public.cash_obligations o
                    WHERE o.org_id = l.org_id AND o.source_type = 'vendor_invoice' AND o.source_ref_type = 'document'
                      AND o.source_ref_id = d.id AND o.valid_to IS NULL AND o.system_to IS NULL)
 ORDER BY d.created_at DESC, d.id
"""

RECORDED_SQL = """
SELECT o.id, o.lineage_id, o.name, o.amount, o.due_date, o.entity_id, o.source_ref_id AS document_id, o.external_key,
       d.original_filename
  FROM public.cash_obligations o
  JOIN public.documents d ON d.id = o.source_ref_id AND d.org_id = o.org_id
 WHERE o.org_id = $1::uuid AND o.source_type = 'vendor_invoice' AND o.source_ref_type = 'document'
   AND o.valid_to IS NULL AND o.system_to IS NULL AND o.entity_id = ANY($2::uuid[])
 ORDER BY o.due_date, o.id
"""


def normalise_number(v) -> str:
    s = re.sub(r"\s+", " ", str(v or "")).strip().upper()
    if not s:
        raise InvoiceError("type the invoice number as it appears on the invoice")
    if len(s) > 64:
        raise InvoiceError("the invoice number is longer than 64 characters")
    return s


def external_key(payee_id, invoice_number: str, amount: Decimal) -> str:
    return f"vendor_invoice:{payee_id}:{invoice_number}:{format(amount, 'f')}"


def payee_of_key(key: str | None) -> str | None:
    """The payee id inside an invoice obligation's external_key (None for any other key)."""
    if not key or not key.startswith("vendor_invoice:"):
        return None
    parts = key.split(":")
    return parts[1] if len(parts) >= 4 else None


def _dumps(v) -> str:
    return json.dumps(v, default=str)


async def _audit(conn, org_id, user_id, action: str, resource_id, payload: dict) -> str:
    return str(await conn.fetchval(
        "INSERT INTO audit_log (org_id, user_id, action, resource_type, resource_id, payload) "
        "VALUES ($1::uuid, $2::uuid, $3, 'document', $4::uuid, $5::jsonb) RETURNING id",
        str(org_id), str(user_id) if user_id else None, action, str(resource_id) if resource_id else None,
        _dumps(payload)))


async def _refused(org_id, actor_id, document_id, reasons: list[str]) -> None:
    try:
        from services.database import get_pool

        pool = await get_pool()
        async with pool.acquire() as c:
            await _audit(c, org_id, actor_id, "cash.invoice.refused", document_id, {"reasons": reasons})
    except Exception:  # noqa: BLE001
        log.exception("cash_invoices: could not audit a refusal")


async def _members(conn, org_id, household_id) -> set[str]:
    from services import household_total as HHT

    return set(await HHT.household_members(conn, org_id, household_id))


async def tag(pool, *, org_id, actor_id, household_id, document_id) -> dict:
    """Tag a Chancery document as one of the household's vendor invoices (the manual tag). Idempotent."""
    async with pool.acquire() as conn:
        actor = await CP._human(conn, actor_id, org_id)
        await CP.household_of(conn, org_id, household_id)
        if not await conn.fetchval("SELECT 1 FROM documents WHERE id = $1::uuid AND org_id = $2::uuid",
                                   CP._uuid(document_id, "document"), str(org_id)):
            raise InvoiceNotFound("document not found")
        async with conn.transaction():
            link = await conn.fetchval(
                "INSERT INTO document_record_links (document_id, org_id, record_type, record_id, created_by) VALUES "
                "($1::uuid, $2::uuid, $3, $4::uuid, $5::uuid) ON CONFLICT (document_id, record_type, record_id) "
                "DO NOTHING RETURNING id", CP._uuid(document_id), str(org_id), CP.LINK_INVOICE_TAG, str(household_id),
                str(actor["id"]))
            if link:
                await _audit(conn, org_id, actor["id"], "cash.invoice.tagged", document_id,
                             {"household_id": str(household_id), "link_id": str(link)})
    return {"document_id": CP._uuid(document_id), "link_id": str(link) if link else None, "created": bool(link)}


async def awaiting(conn, org_id, household_id) -> list[dict]:
    rows = await conn.fetch(AWAITING_SQL, str(org_id), CP.LINK_INVOICE_TAG, str(household_id))
    return [{"document_id": str(r["id"]), "original_filename": r["original_filename"],
             "uploaded_at": r["created_at"].isoformat() if r["created_at"] else None, "has_file": r["has_file"]}
            for r in rows]


async def recorded(conn, org_id, household_id) -> list[dict]:
    members = sorted(await _members(conn, org_id, household_id))
    if not members:
        return []
    rows = [dict(r) for r in await conn.fetch(RECORDED_SQL, str(org_id), members)]
    pids = sorted({p for p in (payee_of_key(r["external_key"]) for r in rows) if p})
    names = {}
    if pids:
        names = {str(r["id"]): (r["display_name"], r["last_four"]) for r in await conn.fetch(
            "SELECT id, display_name, last_four FROM public.cash_payees WHERE id = ANY($1::uuid[]) AND org_id = "
            "$2::uuid AND system_to IS NULL", pids, str(org_id))}
    out = []
    for r in rows:
        pid = payee_of_key(r["external_key"])
        nm = names.get(pid or "")
        out.append({"obligation_id": str(r["id"]), "lineage_id": str(r["lineage_id"]), "name": r["name"],
                    "amount": format(r["amount"], "f"), "due_date": r["due_date"].isoformat(),
                    "entity_id": str(r["entity_id"]), "document_id": str(r["document_id"]),
                    "original_filename": r["original_filename"], "payee_id": pid,
                    "payee_name": nm[0] if nm else None, "payee_last_four": nm[1] if nm else None})
    return out


async def record(pool, *, org_id, actor_id, household_id, document_id, payee_id, entity_id, invoice_number,
                 amount, due_date, notes: str | None = None) -> dict:
    """Record a tagged invoice as a FIRM vendor_invoice obligation (coverage reads it at once). Returns the obligation
    plus the link ids. Refuses a duplicate (same payee + invoice number + amount)."""
    inv = normalise_number(invoice_number)
    try:
        amt = OB.parse_money(amount, "Amount")
    except OB.ObligationError as exc:
        raise InvoiceError(str(exc)) from None
    if amt <= 0:
        raise InvoiceError("an invoice amount is more than zero")
    doc = CP._uuid(document_id, "document")
    async with pool.acquire() as conn:
        actor = await CP._human(conn, actor_id, org_id)
        aid = uuid.UUID(str(actor["id"]))
        await CP.household_of(conn, org_id, household_id)
        tagged = await conn.fetchval(
            "SELECT 1 FROM document_record_links WHERE document_id = $1::uuid AND org_id = $2::uuid AND record_type = $3 "
            "AND record_id = $4::uuid", doc, str(org_id), CP.LINK_INVOICE_TAG, str(household_id))
        if not tagged:
            raise InvoiceNotFound("this document is not one of the household's invoices (tag it first)")
        try:
            payee = await CP.load(conn, org_id, payee_id)
        except CP.PayeeNotFound:
            raise InvoiceError("choose a payee of this household") from None
        if str(payee["household_id"]) != str(household_id):
            raise InvoiceError("choose a payee of this household")
        if payee["status"] != "active":
            raise InvoiceError(f"the payee is {CP.STATUS_LABELS[payee['status']].lower()} — an invoice is recorded "
                               "against an approved, active payee")
        if str(entity_id) not in await _members(conn, org_id, household_id):
            raise InvoiceError("the paying entity is a member of this household")
        key = external_key(payee["id"], inv, amt)
        if await conn.fetchval("SELECT 1 FROM public.cash_obligations WHERE org_id = $1::uuid AND external_key = $2 "
                               "AND valid_to IS NULL AND system_to IS NULL", str(org_id), key):
            await _refused(org_id, aid, doc, [DUPLICATE])
            raise InvoiceDuplicate(DUPLICATE)
        if await conn.fetchval("SELECT 1 FROM public.cash_obligations WHERE org_id = $1::uuid AND source_type = "
                               "'vendor_invoice' AND source_ref_type = 'document' AND source_ref_id = $2::uuid AND "
                               "valid_to IS NULL AND system_to IS NULL", str(org_id), doc):
            await _refused(org_id, aid, doc, ["this document is already recorded as an invoice"])
            raise InvoiceDuplicate("this document is already recorded as an invoice")
        from services import payments as PS

        t = PS.today()
        try:
            async with conn.transaction():
                ob = await OB.create(conn, org_id=org_id, actor_id=aid, data={
                    "entity_id": str(entity_id), "source_type": "vendor_invoice",
                    "name": f"{payee['display_name']} — invoice {inv}", "amount": format(amt, "f"),
                    "due_date": due_date, "certainty": "firm", "source_ref_type": "document", "source_ref_id": doc,
                    "notes": notes}, today=t, external_key=key, reason="vendor invoice recorded (CASH08)")
                links = []
                for rtype, rid in ((CP.LINK_PAYEE_INVOICE, str(payee["id"])), (CP.LINK_OBLIGATION, ob["lineage_id"])):
                    links.append(str(await conn.fetchval(
                        "INSERT INTO document_record_links (document_id, org_id, record_type, record_id, created_by) "
                        "VALUES ($1::uuid, $2::uuid, $3, $4::uuid, $5::uuid) ON CONFLICT (document_id, record_type, "
                        "record_id) DO UPDATE SET created_by = document_record_links.created_by RETURNING id",
                        doc, str(org_id), rtype, rid, str(aid))))
                await _audit(conn, org_id, aid, "cash.invoice.recorded", doc,
                             {"household_id": str(household_id), "payee_id": str(payee["id"]),
                              "payee_last_four": payee["last_four"], "obligation_id": ob["id"],
                              "lineage_id": ob["lineage_id"], "invoice_number": inv, "amount": format(amt, "f"),
                              "due_date": str(due_date)})
        except OB.ObligationError as exc:
            raise InvoiceError(str(exc)) from None
        except Exception as exc:  # the unique index: a concurrent record of the same invoice
            import asyncpg

            if isinstance(exc, asyncpg.UniqueViolationError):
                await _refused(org_id, aid, doc, [DUPLICATE])
                raise InvoiceDuplicate(DUPLICATE) from None
            raise
    return {"obligation": ob, "link_ids": links, "payee_id": str(payee["id"]), "document_id": doc}
