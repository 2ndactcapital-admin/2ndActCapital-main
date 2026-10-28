"""DVP settlement instruction (rep7b) — a PDF drawn on demand from an execution
instruction on a MANAGED account held at ANOTHER custodian.

Separate from rep2's letter of instruction (services/execution_letter.py):
that letter asks a member or another adviser to PLACE a trade; this document
tells the executing broker / trader how the trade SETTLES — delivery versus
payment to the receiving custodian (its name, DTC / participant number, agent
bank, the masked account there) on the settlement date. Different audience,
different content; the same rendering helpers and the same masking rule.

NEVER STORED and never the source of truth: the instruction row and its
public.instruction_settlements row are. The account number is the stored mask
unless the caller chose to include the full number AND it matched the
account's salted hash (rep2's path, services.execution.settlement_letter) — this
module's one ``reveal()``. The receiving custodian's account is only ever the
stored mask (the platform never holds that number).

Manual until rep9: nothing here, or anywhere, sends it — a person does.
"""

from __future__ import annotations

import io
from datetime import datetime, timezone

from services.deal_room_pdf import MARGIN, MUTED, NAVY, PAGE_H, PAGE_W, _safe
from services.execution_letter import _amount, _price

_LABEL_W = 170


def settlement_rows(d: dict, s: dict, *, account_display: str) -> list[tuple[str, str]]:
    """The (label, value) lines — also what the verify reads back."""
    from services.execution import EXECUTOR_LABELS, SIDE_LABELS

    sec = d.get("security") or {}
    ident = ", ".join(x for x in (sec.get("ticker") and f"ticker {sec['ticker']}",
                                  sec.get("cusip") and f"CUSIP {sec['cusip']}") if x)
    rows = [
        ("Settlement", "Delivery versus payment (DVP)"),
        ("Account", account_display),
        ("Trade", f"{SIDE_LABELS[d['side']]} {_amount(d)}"),
        ("Security", f"{sec.get('name') or ''}" + (f" ({ident})" if ident else "")),
        ("Price", _price(d)),
        ("Trade date", s["trade_date"]),
        ("Settlement date", f"{s['settlement_date']} (T+1; weekends only — no holiday calendar)"),
        ("Receiving custodian", s["receiving_custodian_name"]),
        ("DTC / participant number", s["receiving_participant_number"]),
        ("Agent bank", s.get("receiving_agent_bank") or "not recorded"),
        ("Account at the receiving custodian", s.get("receiving_account_masked") or "not recorded"),
        ("Contra / executing broker", s.get("contra_broker") or d.get("executor_name") or "not recorded"),
        ("Executed by", EXECUTOR_LABELS[d["executor_type"]]
         + (f" — {d['executor_name']}" if d.get("executor_name") else "")),
        ("Reference", d["id"]),
    ]
    if d.get("wash_sale_notes"):
        rows.append(("Wash-sale notes", d["wash_sale_notes"]))
    return rows


def build_settlement(d: dict, s: dict, *, org_name: str, account_number=None, prepared_by: str = "") -> bytes:
    from reportlab.lib.utils import simpleSplit
    from reportlab.pdfgen import canvas

    masked = (d.get("account") or {}).get("account_number_masked") or ""
    # The single sanctioned reveal() in this path — only when the person chose to include the full number and it
    # matched the account's hash.
    account_display = account_number.reveal() if account_number is not None else masked

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(PAGE_W, PAGE_H), pageCompression=1)
    c.setTitle(_safe(f"DVP settlement instruction {d['id']}"))
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
    line("DVP settlement instruction", "Times-Roman", 17, NAVY, gap=24)
    line(f"Date: {datetime.now(timezone.utc).date().isoformat()}", color=MUTED)
    to = d.get("executor_name") or "the executing broker"
    line(f"To: {to}" + (f" ({d['executor_contact']})" if d.get("executor_contact") else ""), color=MUTED, gap=22)
    line("Please execute the trade below and settle it delivery versus payment with the receiving custodian named "
         "here, on the settlement date. Send the execution details (quantity, price, time and any specific lots) "
         "once filled.", gap=14)
    y -= 8
    for label, value in settlement_rows(d, s, account_display=account_display):
        top = y
        c.setFillColorRGB(*MUTED)
        c.setFont("Helvetica", 9)
        c.drawString(MARGIN, y, _safe(label))
        line(str(value), x=MARGIN + _LABEL_W, w=width - _LABEL_W)
        y = min(y, top - 15)
    if d.get("lots"):
        y -= 6
        line("Specific tax lots to deliver", "Helvetica-Bold", 10, NAVY, gap=15)
        for lot in d["lots"]:
            line(f"Lot acquired {lot.get('acquired_date') or 'date not recorded'}: {lot['quantity']} shares"
                 + (f" ({lot['term']}-term)" if lot.get("term") else ""), x=MARGIN + 12, w=width - 12)
    y -= 18
    line("Generated from the instruction record on request; not itself a record. Settlement is arranged manually.",
         size=8, color=MUTED, gap=11)
    if account_number is None:
        line("Account numbers are shown masked.", size=8, color=MUTED, gap=11)
    y -= 24
    line("Signature ______________________________", gap=16)
    if prepared_by:
        line(f"Prepared by {prepared_by}", size=9, color=MUTED)
    c.showPage()
    c.save()
    return buf.getvalue()
