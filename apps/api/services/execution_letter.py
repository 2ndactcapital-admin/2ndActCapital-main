"""Letter of instruction (rep2) — a PDF drawn on demand from an execution instruction.

For an instruction a member or another adviser carries out on a held-away
account. NEVER STORED and never the source of truth: the instruction row is;
this is a rendering of it at the moment it is asked for.

The account number is the stored mask (``****1234``). The full number appears
only when ``account_number`` (an :class:`AccountNumber`, already checked
against the account's salted hash by services.execution.letter) is passed —
and this is the ONE place in the letter path that calls ``reveal()``, because
printing the digits is the whole point of that explicit choice.

reportlab 4.4.4 (pinned for dealroom2) — standard fonts, WinAnsi-safe text.
"""

from __future__ import annotations

import io
from datetime import datetime, timezone

from services.deal_room_pdf import MARGIN, MUTED, NAVY, PAGE_H, PAGE_W, _safe

_LABEL_W = 150


def is_fixed_income(d: dict) -> bool:
    """CASH04 — a bill / note is ordered by PAR (USD face) with a limit per 100 of par."""
    return ((d.get("security") or {}).get("security_type") == "fixed_income")


def _amount(d: dict) -> str:
    if d.get("quantity") is not None:
        if is_fixed_income(d):
            return f"{d['quantity']} par (USD face value)"
        return f"{d['quantity']} shares"
    return f"{d['notional']} {(d.get('account') or {}).get('base_currency') or 'USD'} notional"


def _price(d: dict) -> str:
    kind = d.get("price_kind")
    if kind == "limit":
        return f"Limit {d['limit_price']}" + (" per 100 of par" if is_fixed_income(d) else "")
    if kind == "range":
        return (f"Between {d['range_low']} and {d['range_high']}, against a reference price of "
                f"{d['reference_price']} at {d['reference_at']}")
    return "At market"


def letter_rows(d: dict, *, account_display: str) -> list[tuple[str, str]]:
    """The (label, value) lines of the letter — also what the verify reads back."""
    from services.execution import EXECUTOR_LABELS, IF_NOT_FILLED_LABELS, SIDE_LABELS

    sec = d.get("security") or {}
    ident = ", ".join(x for x in (sec.get("ticker") and f"ticker {sec['ticker']}",
                                  sec.get("cusip") and f"CUSIP {sec['cusip']}") if x)
    rows = [
        ("Account", account_display),
        ("Instruction", f"{SIDE_LABELS[d['side']]} {_amount(d)}"),
        ("Security", f"{sec.get('name') or ''}" + (f" ({ident})" if ident else "")),
        ("Price", _price(d)),
        ("Execute by", d["execute_by"]),
        ("If not filled", IF_NOT_FILLED_LABELS[d["if_not_filled"]]),
        ("Carried out by", f"{EXECUTOR_LABELS[d['executor_type']]}"
                           + (f" — {d['executor_name']}" if d.get("executor_name") else "")),
        ("Reference", d["id"]),
    ]
    if d.get("wash_sale_notes"):
        rows.append(("Wash-sale notes", d["wash_sale_notes"]))
    if d.get("notes"):
        rows.append(("Notes", d["notes"]))
    return rows


def build_letter(d: dict, *, org_name: str, account_number=None, prepared_by: str = "") -> bytes:
    from reportlab.lib.utils import simpleSplit
    from reportlab.pdfgen import canvas

    masked = (d.get("account") or {}).get("account_number_masked") or ""
    # The single sanctioned reveal() in the letter path — only when the person
    # chose to include the full number and it matched the account's hash.
    account_display = account_number.reveal() if account_number is not None else masked

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(PAGE_W, PAGE_H), pageCompression=1)
    c.setTitle(_safe(f"Letter of instruction {d['id']}"))
    width = PAGE_W - 2 * MARGIN
    y = PAGE_H - MARGIN

    def line(text, font="Helvetica", size=10, color=(0, 0, 0), x=MARGIN, w=width, gap=13):
        nonlocal y
        c.setFillColorRGB(*color)
        c.setFont(font, size)
        for ln in simpleSplit(_safe(text), font, size, w) or [""]:
            if y < MARGIN + 40:
                c.showPage()
                y = PAGE_H - MARGIN
                c.setFillColorRGB(*color)
                c.setFont(font, size)
            c.drawString(x, y, ln)
            y -= gap

    line(org_name, "Times-Bold", 13, NAVY, gap=18)
    line("Letter of instruction", "Times-Roman", 17, NAVY, gap=24)
    line(f"Date: {datetime.now(timezone.utc).date().isoformat()}", color=MUTED)
    to = d.get("executor_name") or "the person carrying out this instruction"
    line(f"To: {to}" + (f" ({d['executor_contact']})" if d.get("executor_contact") else ""), color=MUTED, gap=22)
    line("Please carry out the following instruction for the account below. Reply to confirm when it has been "
         "placed, and send the execution details (quantity, price, time and any specific lots) once filled.",
         gap=14)
    y -= 8
    for label, value in letter_rows(d, account_display=account_display):
        top = y
        c.setFillColorRGB(*MUTED)
        c.setFont("Helvetica", 9)
        c.drawString(MARGIN, y, _safe(label))
        line(str(value), x=MARGIN + _LABEL_W, w=width - _LABEL_W)
        y = min(y, top - 15)
    if d.get("lots"):
        y -= 6
        line("Specific tax lots to sell", "Helvetica-Bold", 10, NAVY, gap=15)
        for lot in d["lots"]:
            line(f"Lot acquired {lot.get('acquired_date') or 'date not recorded'}: {lot['quantity']} shares"
                 + (f" ({lot['term']}-term)" if lot.get("term") else ""), x=MARGIN + 12, w=width - 12)
    y -= 18
    line("This letter is generated from the instruction record on request and is not itself a record.",
         size=8, color=MUTED, gap=11)
    if account_number is None:
        line("The account number is shown masked.", size=8, color=MUTED, gap=11)
    y -= 24
    line("Signature ______________________________", gap=16)
    if prepared_by:
        line(f"Prepared by {prepared_by}", size=9, color=MUTED)
    c.showPage()
    c.save()
    return buf.getvalue()


# ── CASH07 — the PAYMENT SHEET: the execution-letter variant the manual payment adapter "sends" ─────────────────────
#: The sheet shows the two accounts by their stored MASK only (``****1234``). Unlike the letter of instruction there is
#: NO full-number option: a payment sheet never carries a full account number (asserted by verify_cash07).
RAIL_LABELS = {"ach": "ACH", "wire": "Wire", "journal": "Journal between accounts", "check": "Check"}


def payment_sheet_rows(d: dict) -> list[tuple[str, str]]:
    """The (label, value) lines of the payment sheet — also what the verify reads back. ``d`` is
    services.payments.detail's instruction (masked accounts, names, Decimal amount as fixed-point text)."""
    src, dst = d.get("source") or {}, d.get("destination") or {}

    def acct(a: dict) -> str:
        parts = [a.get("account_number_masked") or "", a.get("owner_name") or "", a.get("custodian_label") or ""]
        return " — ".join(x for x in parts if x)

    rows = [
        ("From account", acct(src)),
        ("To account", acct(dst)),
        ("Amount", f"{d['amount']} {d.get('currency_code') or 'USD'}"),
        ("Rail", RAIL_LABELS.get(d["rail"], d["rail"])),
        ("Value date", d["value_date"]),
        ("Purpose", d.get("purpose") or ""),
        ("Approved by", f"{d.get('approved_by_name') or ''}" + (f" on {d['approved_at'][:10]}" if d.get("approved_at")
                                                               else "")),
        ("Reference", d["id"]),
    ]
    if d.get("purpose_ref_label"):
        rows.insert(6, ("Linked to", d["purpose_ref_label"]))
    return rows


def build_payment_sheet(d: dict, *, org_name: str, prepared_by: str = "", disclosure: str | None = None) -> bytes:
    """A printable PDF payment sheet the operator sends to the custodian (CASH07, ManualPaymentAdapter). Never stored:
    the instruction row is the record; this is a rendering of it on request."""
    from reportlab.lib.utils import simpleSplit
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(PAGE_W, PAGE_H), pageCompression=1)
    c.setTitle(_safe(f"Payment instruction {d['id']}"))
    width = PAGE_W - 2 * MARGIN
    y = PAGE_H - MARGIN

    def line(text, font="Helvetica", size=10, color=(0, 0, 0), x=MARGIN, w=width, gap=13):
        nonlocal y
        c.setFillColorRGB(*color)
        c.setFont(font, size)
        for ln in simpleSplit(_safe(text), font, size, w) or [""]:
            if y < MARGIN + 40:
                c.showPage()
                y = PAGE_H - MARGIN
                c.setFillColorRGB(*color)
                c.setFont(font, size)
            c.drawString(x, y, ln)
            y -= gap

    line(org_name, "Times-Bold", 13, NAVY, gap=18)
    line("Payment instruction — between the client's own accounts", "Times-Roman", 17, NAVY, gap=24)
    line(f"Date: {datetime.now(timezone.utc).date().isoformat()}", color=MUTED, gap=22)
    line("Please move the amount below from the first account to the second on the value date, by the rail shown. "
         "Both accounts have the same owner. Reply with your reference once it is accepted, and confirm when it "
         "settles.", gap=14)
    y -= 8
    for label, value in payment_sheet_rows(d):
        top = y
        c.setFillColorRGB(*MUTED)
        c.setFont("Helvetica", 9)
        c.drawString(MARGIN, y, _safe(label))
        line(str(value), x=MARGIN + _LABEL_W, w=width - _LABEL_W)
        y = min(y, top - 15)
    y -= 12
    if disclosure:
        line("Disclosure", "Helvetica-Bold", 9, NAVY, gap=12)
        line(disclosure, size=8, color=MUTED, gap=11)
        y -= 6
    line("This sheet is generated from the instruction record on request and is not itself a record. Account numbers "
         "are shown masked (last four only).", size=8, color=MUTED, gap=11)
    y -= 24
    line("Authorised signature ______________________________", gap=16)
    if prepared_by:
        line(f"Prepared by {prepared_by}", size=9, color=MUTED)
    c.showPage()
    c.save()
    return buf.getvalue()
