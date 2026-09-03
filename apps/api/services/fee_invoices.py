"""fee43 — invoices, omnibus receipts, and the reconciliation exception queue.

Three things, in the order a billing quarter actually goes through them:

  1. A POSTED fee run's household-scoped lines become ``fee_invoices`` rows,
     each carrying the disclosure language fee41 already knows how to produce.
  2. A custodian's omnibus statement arrives as a Chancery document upload, is
     allocated back to the fee_run_lines it paid, and becomes ``fee_receipts``
     rows with a computed variance.
  3. Anything that does not tie becomes a reviewable exception, and closing one
     takes a named reviewer.


THE DISCLOSURE IS FEE41'S, NOT A SECOND GENERATOR
──────────────────────────────────────────────────────────────────────────────
``render_narrative`` is called directly. This module contains no template, no
token substitution, no prose and no fallback string — if fee41 cannot render,
invoice generation raises rather than substituting language nobody approved.
Disclosure text on a fee invoice is a regulatory artefact; a second generator
would be a second thing to keep in step with Form ADV, and the day they
diverged nobody would know which one the client received.

``fee_invoices`` has no column to store the text in (measured: 15 columns, none
of them textual beyond ``invoice_number``/``status``/``delivery_method``). The
render is therefore persisted where fee41 persists renders — ``fee_narratives``,
via ``save_narrative`` — and returned on the payload for the PDF. Reported as
[F43-G]: an invoice and its disclosure are linked only by
(org_id, household_id, fee_schedule_id), because no column relates them.


THE TOLERANCE, AND WHY IT IS A CENT
──────────────────────────────────────────────────────────────────────────────
A fee is computed as a rate against a value and then rounded to the cent. The
custodian computes and rounds the same fee independently. Two correct
implementations of the same arithmetic can therefore differ by the last cent
and by nothing else — that is a rounding artefact, not a difference of opinion
about what was owed.

So the per-line tolerance is exactly one cent, and it is a CEILING on
artefacts rather than a materiality threshold. Two cents is not "nearly a
cent", it is a second cause. A percentage-based tolerance was rejected: it
would scale with the invoice, so a large household could absorb a real
several-hundred-dollar error inside a "0.01%" band, which is the opposite of
what a reconciliation is for.

The OMNIBUS tie-out tolerance is zero, on the same reasoning read the other
way. The statement's own line items and its own total come from one document
and one rounding; there is no independent computation between them for a cent
to appear in. A statement that does not add up to itself is a bad statement.


A STATEMENT THAT DOES NOT TIE NEVER POSTS
──────────────────────────────────────────────────────────────────────────────
When the allocations do not sum to the omnibus total, or an allocation cannot
be matched to a fee_run_line, EVERY receipt the statement produces is written
``EXCEPTION`` — including the individual lines whose own variance is within
tolerance. This is deliberate and it is the point: if the document is wrong
about its total, the line that looks right may be the wrong one. Marking the
agreeing lines MATCHED would quietly launder a broken statement into a
reconciled state, one line at a time.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable, Mapping, Optional, Sequence

from services.fee_narratives import (
    NarrativeError,
    render_narrative,
    save_narrative,
)
from services.portfolio_assets import _OrgWrite, _require_org

ZERO = Decimal("0")

T_INVOICES = "public.fee_invoices"
T_RECEIPTS = "public.fee_receipts"
T_RUNS = "public.fee_runs"
T_LINES = "public.fee_run_lines"
T_HOUSEHOLDS = "public.households"
T_ACCOUNTS = "public.accounts"
T_DOCS = "public.documents"
T_EXTRACTIONS = "public.document_extractions"

#: ``fee_invoices_status_check``.
INVOICE_STATUSES = ("DRAFT", "ISSUED", "DELIVERED", "PAID", "VOID")
#: ``fee_receipts_reconciliation_status_check``.
RECONCILIATION_STATUSES = ("UNRECONCILED", "MATCHED", "EXCEPTION")
#: ``fee_receipts_source_check``.
RECEIPT_SOURCES = ("CUSTODIAN_DEBIT", "OMNIBUS_ALLOCATION", "ACH", "CHECK", "MANUAL")

SOURCE_OMNIBUS = "OMNIBUS_ALLOCATION"

#: See the module docstring. One cent, per line, absolute.
LINE_TOLERANCE = Decimal("0.01")
#: Zero. A statement's parts must add to its own total exactly.
OMNIBUS_TIE_TOLERANCE = Decimal("0.00")

#: The template code an invoice's disclosure renders from. Not invented at call
#: time: an org publishes its house language under a code, and an invoice that
#: silently fell back to some other template would be delivering language
#: nobody approved for this purpose.
DEFAULT_DISCLOSURE_TEMPLATE_CODE = "FEE_DISCLOSURE"


class InvoiceError(ValueError):
    def __init__(self, message: str, **context: Any):
        super().__init__(message)
        self.context = context


class ReconciliationError(ValueError):
    def __init__(self, message: str, **context: Any):
        super().__init__(message)
        self.context = context


def _dec(value: Any) -> Decimal:
    return Decimal(str(value))


# ═══════════════════════════════════════════════════════════════════════════
# Invoices
# ═══════════════════════════════════════════════════════════════════════════


def invoice_number(run_id: str, period_end: date, sequence: int) -> str:
    """Deterministic and unique under ``fee_invoices_number_uq (org, number)``.

    Deterministic on purpose: re-running generation for the same run produces
    the same numbers, so the unique index — not a Python guard — is what stops
    a duplicate invoice, and it stops one raised by any path including raw SQL.
    """
    return f"INV-{period_end:%Y%m}-{str(run_id)[:8]}-{sequence:03d}"


@dataclass
class InvoiceDisclosure:
    """What fee41 produced, and which of its renders produced it."""

    text: str
    narrative_ids: list[str]
    fee_schedule_ids: list[str]
    template_code: str


async def _render_disclosure(
    conn,
    org_id: str,
    *,
    household_id: str,
    fee_schedule_ids: Sequence[str],
    template_code: str,
    persist: bool = True,
) -> InvoiceDisclosure:
    """fee41's ``render_narrative``, once per schedule the household is billed on.

    A household billed on two schedules gets both disclosures, in schedule-id
    order so the text is stable across regeneration. No text is composed here
    beyond the blank line that separates them.
    """
    parts: list[str] = []
    narrative_ids: list[str] = []
    ordered = sorted(str(s) for s in fee_schedule_ids)

    for schedule_id in ordered:
        try:
            rendered = await render_narrative(
                conn, org_id,
                fee_schedule_id=schedule_id,
                household_id=household_id,
                template_code=template_code,
            )
        except NarrativeError as exc:
            # Not caught-and-defaulted. An invoice without its disclosure is
            # not a lesser invoice, it is a compliance problem.
            raise InvoiceError(
                f"fee41 could not render the disclosure for fee_schedule "
                f"{schedule_id} / household {household_id}: {exc}",
                household_id=household_id, fee_schedule_id=schedule_id,
                template_code=template_code,
            ) from exc
        parts.append(rendered.rendered_text)
        if persist:
            narrative_ids.append(await save_narrative(conn, org_id, rendered))

    return InvoiceDisclosure(
        text="\n\n".join(parts),
        narrative_ids=narrative_ids,
        fee_schedule_ids=ordered,
        template_code=template_code,
    )


async def generate_invoices_for_run(
    conn,
    org_id: str,
    run_id: str,
    *,
    created_by: Optional[str] = None,
    template_code: str = DEFAULT_DISCLOSURE_TEMPLATE_CODE,
    persist_narratives: bool = True,
) -> dict[str, Any]:
    """One DRAFT invoice per household on a POSTED run.

    Refuses an unposted run. An invoice is a demand for payment of an amount
    that has been through both approval gates; billing a client from a run that
    can still be re-previewed would send a number that is allowed to change.

    Lines with no ``household_id`` produce no invoice and are reported. The
    household is the invoicing grain because it is the grain a client sees —
    per-account invoices would send one household four bills for one
    relationship.
    """
    org_id = _require_org(org_id)
    run_id = str(run_id)

    run = await conn.fetchrow(
        f"SELECT id::text AS id, status, period_start, period_end "
        f"FROM {T_RUNS} WHERE id = $1::uuid AND org_id = $2::uuid",
        run_id, org_id,
    )
    if not run:
        raise InvoiceError(f"fee_run {run_id} not found in org {org_id}",
                           run_id=run_id, org_id=org_id)
    if run["status"] != "POSTED":
        raise InvoiceError(
            f"fee_run {run_id} is {run['status']}; invoices are generated only "
            f"from a POSTED run. Billing from a run that can still be "
            f"re-previewed would send a client a number that may change",
            run_id=run_id, status=run["status"],
        )

    lines = await conn.fetch(
        f"SELECT id::text AS id, household_id::text AS household_id, "
        f"       entity_id::text AS entity_id, "
        f"       fee_schedule_id::text AS fee_schedule_id, net_fee, currency "
        f"FROM {T_LINES} WHERE fee_run_id = $1::uuid AND org_id = $2::uuid "
        f"ORDER BY household_id, id",
        run_id, org_id,
    )

    households: dict[str, dict[str, Any]] = {}
    unassigned: list[str] = []
    for line in lines:
        hh = line["household_id"]
        if not hh:
            unassigned.append(line["id"])
            continue
        bucket = households.setdefault(hh, {
            "total": ZERO, "schedules": set(), "line_ids": [],
            "currency": line["currency"], "entity_id": line["entity_id"],
        })
        bucket["total"] += _dec(line["net_fee"])
        bucket["schedules"].add(line["fee_schedule_id"])
        bucket["line_ids"].append(line["id"])
        if bucket["currency"] != line["currency"]:
            raise InvoiceError(
                f"household {hh} has fee_run_lines in more than one currency "
                f"({bucket['currency']} and {line['currency']}); one invoice "
                f"cannot carry two currencies",
                household_id=hh, run_id=run_id,
            )

    invoices: list[dict[str, Any]] = []
    for sequence, household_id in enumerate(sorted(households), start=1):
        bucket = households[household_id]
        disclosure = await _render_disclosure(
            conn, org_id,
            household_id=household_id,
            fee_schedule_ids=bucket["schedules"],
            template_code=template_code,
            persist=persist_narratives,
        )
        number = invoice_number(run_id, run["period_end"], sequence)

        async with _OrgWrite(conn, org_id) as c:
            row = await c.fetchrow(
                f"""INSERT INTO {T_INVOICES}
                      (org_id, fee_run_id, household_id, entity_id,
                       invoice_number, status, total_amount, currency, created_by)
                    VALUES ($1::uuid, $2::uuid, $3::uuid, $4::uuid, $5, 'DRAFT',
                            $6, $7, $8::uuid)
                    RETURNING id::text AS id, invoice_number, status,
                              total_amount, currency, created_at""",
                org_id, run_id, household_id, bucket["entity_id"], number,
                bucket["total"], bucket["currency"], created_by,
            )
        invoices.append({
            **dict(row),
            "household_id": household_id,
            "line_ids": bucket["line_ids"],
            "disclosure_text": disclosure.text,
            "disclosure_narrative_ids": disclosure.narrative_ids,
            "disclosure_fee_schedule_ids": disclosure.fee_schedule_ids,
            "disclosure_template_code": disclosure.template_code,
        })

    return {
        "run_id": run_id,
        "invoices": invoices,
        "invoice_count": len(invoices),
        "total_invoiced": sum((i["total_amount"] for i in invoices), ZERO),
        "lines_without_household": unassigned,
    }


async def issue_invoice(
    conn, org_id: str, invoice_id: str, *, delivery_method: Optional[str] = None
) -> dict[str, Any]:
    """DRAFT -> ISSUED, stamping ``issued_at``.

    Only from DRAFT. Re-issuing an already-issued invoice would move the date
    a client was billed on, which is the one date a fee dispute turns on.
    """
    org_id = _require_org(org_id)
    async with _OrgWrite(conn, org_id) as c:
        row = await c.fetchrow(
            f"""UPDATE {T_INVOICES}
                SET status = 'ISSUED', issued_at = now(),
                    delivery_method = COALESCE($3, delivery_method)
                WHERE id = $1::uuid AND org_id = $2::uuid AND status = 'DRAFT'
                RETURNING id::text AS id, invoice_number, status, issued_at,
                          delivery_method""",
            str(invoice_id), org_id, delivery_method,
        )
    if not row:
        raise InvoiceError(
            f"fee_invoice {invoice_id} is not a DRAFT in org {org_id}; only a "
            f"DRAFT can be issued",
            invoice_id=str(invoice_id),
        )
    return dict(row)


# ═══════════════════════════════════════════════════════════════════════════
# Omnibus statement — read through Chancery, never a second upload path
# ═══════════════════════════════════════════════════════════════════════════

_MONEY_RE = re.compile(r"^\(?\s*-?\$?\s*([\d,]+(?:\.\d+)?)\s*\)?$")
_TOTAL_TOKENS = ("total", "omnibus total", "grand total", "statement total")


def _parse_money(cell: Any) -> Optional[Decimal]:
    """A statement cell as Decimal, or None if it is not money.

    Handles ``$1,234.56``, ``1234.56`` and the accounting negative ``(123.45)``.
    Returns None rather than 0 for an unparseable cell — a cell that is not a
    number must not silently become a zero receipt.
    """
    if cell is None:
        return None
    if isinstance(cell, Decimal):
        return cell
    if isinstance(cell, (int, float)):
        return Decimal(str(cell))
    text = str(cell).strip()
    if not text:
        return None
    match = _MONEY_RE.match(text)
    if not match:
        return None
    try:
        value = Decimal(match.group(1).replace(",", ""))
    except InvalidOperation:
        return None
    if text.startswith("(") and text.endswith(")") or text.lstrip().startswith("-"):
        value = -value
    return value


@dataclass
class OmnibusAllocation:
    """One row of the custodian's statement: who paid, and how much."""

    key: str
    amount: Decimal
    row_index: int


@dataclass
class ParsedStatement:
    allocations: list[OmnibusAllocation]
    stated_total: Optional[Decimal]

    @property
    def allocated_total(self) -> Decimal:
        return sum((a.amount for a in self.allocations), ZERO)

    @property
    def ties(self) -> bool:
        """Do the parts add to the stated total?

        A statement with no stated total does NOT tie. Treating "no total
        given" as agreement would let a statement that omits its own total skip
        the only check that catches a dropped row.
        """
        if self.stated_total is None:
            return False
        return abs(self.allocated_total - self.stated_total) <= OMNIBUS_TIE_TOLERANCE


def parse_omnibus_tables(tables: Any) -> ParsedStatement:
    """Allocations and the stated total, out of Chancery's extracted tables.

    ``document_extractions.extracted_tables`` is whatever the intake extractor
    produced — a list of tables, each a list of rows, each row a list of cells.
    This reads it positionally: the first cell that is not money is the key, the
    last cell that IS money is the amount. Positional rather than
    header-driven because custodian statements name that column a dozen
    different ways and a header whitelist would silently drop a statement whose
    header we had not seen.

    A row whose key contains a total token supplies ``stated_total`` and is not
    itself an allocation.
    """
    if isinstance(tables, str):
        tables = json.loads(tables)
    if not tables:
        return ParsedStatement([], None)
    if isinstance(tables, Mapping):
        tables = tables.get("tables") or []

    allocations: list[OmnibusAllocation] = []
    stated_total: Optional[Decimal] = None
    row_index = 0

    for table in tables:
        rows = table.get("rows") if isinstance(table, Mapping) else table
        for row in rows or []:
            cells = list(row.values()) if isinstance(row, Mapping) else list(row)
            if not cells:
                continue
            row_index += 1

            amount = None
            for cell in reversed(cells):
                amount = _parse_money(cell)
                if amount is not None:
                    break
            if amount is None:
                continue  # a header or a divider, not a data row

            key = ""
            for cell in cells:
                if cell is None:
                    continue
                if _parse_money(cell) is None and str(cell).strip():
                    key = str(cell).strip()
                    break
            if not key:
                continue

            if any(token in key.lower() for token in _TOTAL_TOKENS):
                stated_total = amount
                continue
            allocations.append(OmnibusAllocation(key, amount, row_index))

    return ParsedStatement(allocations, stated_total)


async def load_statement(conn, org_id: str, document_id: str) -> ParsedStatement:
    """Read a Chancery-ingested document's extracted tables.

    Reuses the intake pipeline entirely: the statement gets to the platform
    through ``POST /api/v1/documents`` like every other document, and this reads
    what that pipeline already extracted. There is no second upload path, no
    custodian API client, and nothing here parses a PDF.
    """
    org_id = _require_org(org_id)
    row = await conn.fetchrow(
        f"""SELECT d.id::text AS id, d.status, d.original_filename,
                   e.extracted_tables, e.extraction_method
            FROM {T_DOCS} d
            LEFT JOIN {T_EXTRACTIONS} e
                   ON e.document_id = d.id AND e.org_id = d.org_id
            WHERE d.id = $1::uuid AND d.org_id = $2::uuid""",
        str(document_id), org_id,
    )
    if not row:
        raise ReconciliationError(
            f"document {document_id} not found in org {org_id}. The omnibus "
            f"statement is ingested through Chancery; upload it there first",
            document_id=str(document_id),
        )
    if row["extracted_tables"] is None:
        raise ReconciliationError(
            f"document {document_id} ({row['original_filename']!r}) has no "
            f"extracted tables — status {row['status']!r}. Chancery has not "
            f"extracted it, or it is not a tabular statement",
            document_id=str(document_id), status=row["status"],
        )
    return parse_omnibus_tables(row["extracted_tables"])


async def _match_allocations(
    conn, org_id: str, run_id: str, allocations: Iterable[OmnibusAllocation]
) -> tuple[dict[str, list[OmnibusAllocation]], list[OmnibusAllocation]]:
    """Map each allocation onto a fee_run_line, by masked account or household.

    Two keys because a custodian statement identifies the payer one of two
    ways, and which one depends on whether the fee was debited from an account
    or billed to the relationship.
    """
    rows = await conn.fetch(
        f"""SELECT l.id::text AS id,
                   a.account_number_masked,
                   h.name AS household_name
            FROM {T_LINES} l
            LEFT JOIN {T_ACCOUNTS} a
                   ON a.id = l.account_id AND a.system_to IS NULL
            LEFT JOIN {T_HOUSEHOLDS} h ON h.id = l.household_id
            WHERE l.fee_run_id = $1::uuid AND l.org_id = $2::uuid""",
        str(run_id), org_id,
    )
    index: dict[str, str] = {}
    for row in rows:
        for key in (row["account_number_masked"], row["household_name"]):
            if key:
                index.setdefault(str(key).strip().casefold(), row["id"])

    matched: dict[str, list[OmnibusAllocation]] = {}
    unmatched: list[OmnibusAllocation] = []
    for allocation in allocations:
        line_id = index.get(allocation.key.casefold())
        if line_id is None:
            unmatched.append(allocation)
        else:
            matched.setdefault(line_id, []).append(allocation)
    return matched, unmatched


async def reconcile_omnibus_statement(
    conn,
    org_id: str,
    *,
    run_id: str,
    document_id: str,
    received_on: date,
    source: str = SOURCE_OMNIBUS,
    tolerance: Decimal = LINE_TOLERANCE,
    record_missing: bool = True,
) -> dict[str, Any]:
    """Allocate a statement to a run's lines and write ``fee_receipts``.

    ``variance`` is billed minus received, per the column's own definition: a
    POSITIVE variance means the client paid less than they were billed.

    Every receipt is written ``EXCEPTION`` when the statement does not tie or
    an allocation could not be matched — see the module docstring. Otherwise a
    receipt is MATCHED when its variance is within ``tolerance`` and EXCEPTION
    when it is not. Nothing is ever left UNRECONCILED by this function; the
    column default exists for receipts recorded by hand before anyone has
    looked at them.
    """
    org_id = _require_org(org_id)
    run_id = str(run_id)
    if source not in RECEIPT_SOURCES:
        raise ReconciliationError(
            f"source {source!r} is not one of {RECEIPT_SOURCES}", source=source
        )

    run = await conn.fetchrow(
        f"SELECT status FROM {T_RUNS} WHERE id = $1::uuid AND org_id = $2::uuid",
        run_id, org_id,
    )
    if not run:
        raise ReconciliationError(f"fee_run {run_id} not found in org {org_id}",
                                  run_id=run_id)
    if run["status"] != "POSTED":
        raise ReconciliationError(
            f"fee_run {run_id} is {run['status']}; receipts reconcile against "
            f"a POSTED run's billed amounts. Reconciling a run whose numbers "
            f"can still change would compare a payment to a draft",
            run_id=run_id, status=run["status"],
        )

    statement = await load_statement(conn, org_id, document_id)
    matched, unmatched = await _match_allocations(
        conn, org_id, run_id, statement.allocations
    )

    billed_rows = await conn.fetch(
        f"SELECT id::text AS id, net_fee FROM {T_LINES} "
        f"WHERE fee_run_id = $1::uuid AND org_id = $2::uuid",
        run_id, org_id,
    )
    billed = {r["id"]: _dec(r["net_fee"]) for r in billed_rows}

    ties = statement.ties
    tie_break_reason = None
    if not ties:
        if statement.stated_total is None:
            tie_break_reason = (
                f"the statement states no total; "
                f"{len(statement.allocations)} allocation(s) summing to "
                f"{statement.allocated_total} cannot be checked against "
                f"anything"
            )
        else:
            tie_break_reason = (
                f"statement does not tie: allocations sum to "
                f"{statement.allocated_total}, stated omnibus total is "
                f"{statement.stated_total} "
                f"(difference {statement.allocated_total - statement.stated_total})"
            )
    unmatched_reason = None
    if unmatched:
        unmatched_reason = (
            f"{len(unmatched)} allocation(s) match no fee_run_line on this "
            f"run: {[a.key for a in unmatched][:5]}"
        )

    poisoned = tie_break_reason or unmatched_reason

    receipts: list[dict[str, Any]] = []
    for line_id in sorted(matched):
        received = sum((a.amount for a in matched[line_id]), ZERO)
        variance = billed[line_id] - received
        if poisoned:
            status = "EXCEPTION"
            reason = "; ".join(r for r in (tie_break_reason, unmatched_reason) if r)
        elif abs(variance) <= tolerance:
            status = "MATCHED"
            reason = None
        else:
            status = "EXCEPTION"
            reason = (
                f"variance {variance} exceeds the {tolerance} rounding "
                f"tolerance: billed {billed[line_id]}, received {received}"
            )
        receipts.append(await _write_receipt(
            conn, org_id, line_id, received, received_on, source,
            variance, status, reason,
        ))

    missing: list[str] = []
    if record_missing:
        for line_id in sorted(set(billed) - set(matched)):
            if billed[line_id] == ZERO:
                continue
            missing.append(line_id)
            receipts.append(await _write_receipt(
                conn, org_id, line_id, ZERO, received_on, source,
                billed[line_id], "EXCEPTION",
                (f"billed {billed[line_id]} and the statement allocates "
                 f"nothing to this line"),
            ))

    return {
        "run_id": run_id,
        "document_id": str(document_id),
        "receipts": receipts,
        "receipt_count": len(receipts),
        "allocated_total": statement.allocated_total,
        "stated_total": statement.stated_total,
        "ties": ties,
        "tie_break_reason": tie_break_reason,
        "unmatched": [{"key": a.key, "amount": a.amount} for a in unmatched],
        "unreceipted_line_ids": missing,
        "matched_count": sum(1 for r in receipts if r["reconciliation_status"] == "MATCHED"),
        "exception_count": sum(1 for r in receipts if r["reconciliation_status"] == "EXCEPTION"),
        "tolerance": tolerance,
    }


async def _write_receipt(
    conn, org_id: str, line_id: str, received: Decimal, received_on: date,
    source: str, variance: Decimal, status: str, reason: Optional[str],
) -> dict[str, Any]:
    async with _OrgWrite(conn, org_id) as c:
        row = await c.fetchrow(
            f"""INSERT INTO {T_RECEIPTS}
                  (org_id, fee_run_line_id, received_amount, received_on,
                   source, variance, reconciliation_status, exception_reason)
                VALUES ($1::uuid, $2::uuid, $3, $4::date, $5, $6, $7, $8)
                RETURNING id::text AS id, fee_run_line_id::text AS fee_run_line_id,
                          received_amount, received_on, source, variance,
                          reconciliation_status, exception_reason,
                          reviewed_by::text AS reviewed_by, reviewed_at""",
            org_id, line_id, received, received_on, source, variance,
            status, reason,
        )
    return dict(row)


# ═══════════════════════════════════════════════════════════════════════════
# The exception queue
# ═══════════════════════════════════════════════════════════════════════════


async def list_exceptions(
    conn, org_id: str, *, include_closed: bool = False, run_id: Optional[str] = None
) -> list[dict[str, Any]]:
    """Open reconciliation exceptions — the queue somebody works through.

    "Closed" is ``reviewed_at IS NOT NULL``, not a status. ``fee_receipts`` has
    no CLOSED status to move to (the deployed CHECK admits exactly
    UNRECONCILED / MATCHED / EXCEPTION), and inventing one in application code
    would put a state in the queue that the database does not know about.
    Closing an exception records WHO looked at it, which is the thing an audit
    asks for.
    """
    org_id = _require_org(org_id)
    clauses = ["r.org_id = $1::uuid", "r.reconciliation_status = 'EXCEPTION'"]
    args: list[Any] = [org_id]
    if not include_closed:
        clauses.append("r.reviewed_at IS NULL")
    if run_id is not None:
        args.append(str(run_id))
        clauses.append(f"l.fee_run_id = ${len(args)}::uuid")

    rows = await conn.fetch(
        f"""SELECT r.id::text AS id, r.fee_run_line_id::text AS fee_run_line_id,
                   r.received_amount, r.received_on, r.source, r.variance,
                   r.reconciliation_status, r.exception_reason,
                   r.reviewed_by::text AS reviewed_by, r.reviewed_at,
                   l.fee_run_id::text AS fee_run_id, l.net_fee AS billed_amount,
                   l.household_id::text AS household_id, l.product_type
            FROM {T_RECEIPTS} r
            JOIN {T_LINES} l ON l.id = r.fee_run_line_id
            WHERE {' AND '.join(clauses)}
            ORDER BY r.created_at, r.id""",
        *args,
    )
    return [dict(r) for r in rows]


async def close_exception(
    conn,
    org_id: str,
    receipt_id: str,
    *,
    reviewed_by: str,
    resolution: str,
    resolve_as: str = "EXCEPTION",
) -> dict[str, Any]:
    """Record that a human reviewed an exception.

    ``reviewed_by`` is required and there is no default. ``reviewed_at`` is
    stamped here, never accepted from the caller — the pair is what
    ``fee_receipts_reviewed_pair_check`` enforces, and a caller-supplied
    timestamp would let a review be backdated to before it happened.

    ``resolve_as='MATCHED'`` is a real reviewer decision: the variance was
    looked at and accepted (a fee waived after billing, a partial payment
    agreed). It is not the default, because a queue that empties itself by
    calling everything matched is not a queue.
    """
    org_id = _require_org(org_id)
    if not reviewed_by:
        raise ReconciliationError(
            "reviewed_by is required to close an exception; "
            "fee_receipts_reviewed_pair_check refuses a review with no reviewer",
            receipt_id=str(receipt_id),
        )
    if resolve_as not in ("EXCEPTION", "MATCHED"):
        raise ReconciliationError(
            f"resolve_as must be EXCEPTION or MATCHED, not {resolve_as!r}",
            resolve_as=resolve_as,
        )
    if not resolution:
        raise ReconciliationError(
            "a resolution note is required; an exception closed with no "
            "explanation tells the next reader nothing",
            receipt_id=str(receipt_id),
        )

    async with _OrgWrite(conn, org_id) as c:
        row = await c.fetchrow(
            f"""UPDATE {T_RECEIPTS}
                SET reviewed_by = $3::uuid,
                    reviewed_at = now(),
                    reconciliation_status = $4,
                    exception_reason = COALESCE(exception_reason || ' | ', '')
                                       || 'RESOLVED: ' || $5
                WHERE id = $1::uuid AND org_id = $2::uuid
                  AND reconciliation_status = 'EXCEPTION'
                  AND reviewed_at IS NULL
                RETURNING id::text AS id, reconciliation_status, exception_reason,
                          reviewed_by::text AS reviewed_by, reviewed_at,
                          variance, received_amount""",
            str(receipt_id), org_id, str(reviewed_by), resolve_as, resolution,
        )
    if not row:
        raise ReconciliationError(
            f"fee_receipt {receipt_id} is not an open EXCEPTION in org "
            f"{org_id}; it does not exist, is not an exception, or has already "
            f"been reviewed",
            receipt_id=str(receipt_id),
        )
    return dict(row)


__all__ = [
    "DEFAULT_DISCLOSURE_TEMPLATE_CODE",
    "INVOICE_STATUSES",
    "InvoiceDisclosure",
    "InvoiceError",
    "LINE_TOLERANCE",
    "OMNIBUS_TIE_TOLERANCE",
    "OmnibusAllocation",
    "ParsedStatement",
    "RECEIPT_SOURCES",
    "RECONCILIATION_STATUSES",
    "ReconciliationError",
    "SOURCE_OMNIBUS",
    "close_exception",
    "generate_invoices_for_run",
    "invoice_number",
    "issue_invoice",
    "list_exceptions",
    "load_statement",
    "parse_omnibus_tables",
    "reconcile_omnibus_statement",
]
