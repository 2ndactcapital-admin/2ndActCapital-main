"""Order execution sheet (rep9) — a PDF drawn on demand from a trade block, or
from one account instruction carried out by a broker / desk / trader.

The MANUAL route, always offered: the adviser prints this sheet and gives it
to a broker of their choice, then enters the fills by hand. NEVER STORED and
never the source of truth (the block / instruction rows are); a rendering at
the moment it is asked for, like rep2's letter of instruction and rep7b's DVP
settlement instruction, with the same rendering helpers.

WHAT IT CARRIES: firm, executing account (masked), security (name, ticker,
CUSIP), side, total quantity, price guidance with its source and as-of,
execute-by, what to do if not filled, the allocation table (account
reference MASKED, quantity, receiving custodian, the DVP / receive
instructions per allocation), the firm contact and the block id.
WHAT IT NEVER CARRIES: a member's name, a full account number (only the
stored masks — this module has no reveal()).

reportlab 4.4.4 — standard fonts, WinAnsi-safe text.
"""

from __future__ import annotations

import io
from datetime import datetime, timezone

from services.deal_room_pdf import MARGIN, MUTED, NAVY, PAGE_H, PAGE_W, _safe

_LABEL_W = 150


def price_text(d: dict) -> str:
    kind = d.get("price_kind")
    if kind == "limit":
        head = f"Limit {d['limit_price']}"
    elif kind == "range":
        head = f"Between {d['range_low']} and {d['range_high']}"
    else:
        head = "At market"
    ref = d.get("reference") or {}
    if ref.get("price"):
        src = ("security-data layer, provider " + str(ref.get("provider")) if ref.get("source") == "security_data"
               else "typed by the adviser")
        head += f"; reference price {ref['price']} as of {ref.get('as_of')} ({src})"
    else:
        head += "; no reference price on record"
    return head


def settlement_text(a: dict) -> str:
    """One allocation's settlement instruction, in words."""
    if a.get("settlement_kind") == "dvp":
        s = a.get("settlement") or a.get("receiving") or {}
        parts = [f"DVP to {s.get('receiving_custodian_name') or s.get('custodian_name') or a.get('receiving_custodian')}",
                 f"DTC / participant {s.get('receiving_participant_number') or s.get('participant_number') or 'not recorded'}"]
        bank = s.get("receiving_agent_bank") or s.get("agent_bank")
        if bank:
            parts.append(f"agent bank {bank}")
        acct = s.get("receiving_account_masked") or s.get("account_masked")
        if acct:
            parts.append(f"account there {acct}")
        if s.get("settlement_date"):
            parts.append(f"settles {s['settlement_date']}")
        return "; ".join(parts)
    return (f"Receive versus payment at {a.get('receiving_custodian')} (the integrated custodian) — its trading desk "
            "is instructed to receive")


def sheet_rows(d: dict) -> list[tuple[str, str]]:
    """The header (label, value) lines — also what the verify reads back."""
    sec = d.get("security") or {}
    ident = ", ".join(x for x in (sec.get("ticker") and f"ticker {sec['ticker']}",
                                  sec.get("cusip") and f"CUSIP {sec['cusip']}") if x)
    ea = d.get("execution_account") or {}
    executing = d.get("executing_text") or (
        f"{ea.get('broker_name')} — account {ea.get('account_reference_masked')}" if ea else
        "Custodian trading desk (no executing account)")
    rows = [
        ("Reference", f"{d.get('kind_label') or 'Block'} {d['id']}"),
        ("Executing account", executing),
        ("Security", f"{sec.get('name') or ''}" + (f" ({ident})" if ident else "")),
        ("Side", d.get("side_label") or d.get("side")),
        ("Total quantity", f"{d['total_quantity']} shares"),
        ("Price guidance", price_text(d)),
        ("Execute by", d.get("execute_by")),
        ("If not filled", d.get("if_not_filled_label") or d.get("if_not_filled")),
        ("Executed by", (d.get("executor_label") or d.get("executor_type") or "")
         + (f" — {d['executor_name']}" if d.get("executor_name") else "")),
        ("Firm contact", d.get("firm_contact") or "not recorded"),
    ]
    if d.get("policy_version"):
        rows.append(("Allocation policy", f"version {d['policy_version']} (fixed before execution)"))
    return rows


def allocation_rows(d: dict) -> list[tuple[str, str, str, str]]:
    """(account masked, quantity, receiving custodian, settlement) per allocation."""
    out = []
    for a in d.get("allocations") or []:
        if a.get("status") == "cancelled":
            continue
        q = a.get("allocated_quantity") if a.get("final_quantity") in (None, "") else a.get("final_quantity")
        out.append((a.get("account_number_masked") or "", str(q), a.get("receiving_custodian") or "",
                    settlement_text(a)))
    return out


def build_sheet(d: dict, *, org_name: str, prepared_by: str = "") -> bytes:
    from reportlab.lib.utils import simpleSplit
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(PAGE_W, PAGE_H), pageCompression=1)
    c.setTitle(_safe(f"Order execution sheet {d['id']}"))
    width = PAGE_W - 2 * MARGIN
    y = PAGE_H - MARGIN

    def line(text, font="Helvetica", size=10, color=(0, 0, 0), x=MARGIN, w=width, gap=13):
        nonlocal y
        c.setFillColorRGB(*color)
        c.setFont(font, size)
        for ln in simpleSplit(_safe(str(text)), font, size, w) or [""]:
            if y < MARGIN + 40:
                c.showPage()
                y = PAGE_H - MARGIN
                c.setFillColorRGB(*color)
                c.setFont(font, size)
            c.drawString(x, y, ln)
            y -= gap

    line(org_name, "Times-Bold", 13, NAVY, gap=18)
    line("Order execution sheet", "Times-Roman", 17, NAVY, gap=24)
    line(f"Date: {datetime.now(timezone.utc).date().isoformat()}", color=MUTED)
    to = d.get("executor_name") or "the executing broker"
    line(f"To: {to}" + (f" ({d['executor_contact']})" if d.get("executor_contact") else ""), color=MUTED, gap=22)
    line("Please execute the order below as one order in the executing account named here, allocate it as set out in "
         "the allocation table (fixed before execution) and settle each allocation as instructed. Send the execution "
         "details (each fill's quantity, price, time and venue) once filled.", gap=14)
    y -= 8
    for label, value in sheet_rows(d):
        top = y
        c.setFillColorRGB(*MUTED)
        c.setFont("Helvetica", 9)
        c.drawString(MARGIN, y, _safe(label))
        line(str(value), x=MARGIN + _LABEL_W, w=width - _LABEL_W)
        y = min(y, top - 15)
    y -= 6
    line("Allocation", "Helvetica-Bold", 11, NAVY, gap=16)
    cols = (MARGIN, MARGIN + 85, MARGIN + 165, MARGIN + 270)
    c.setFont("Helvetica", 8)
    c.setFillColorRGB(*MUTED)
    for x, h in zip(cols, ("Account", "Quantity", "Receiving custodian", "Settlement")):
        c.drawString(x, y, h)
    y -= 13
    for acct, qty, rc, st in allocation_rows(d):
        top = y
        c.setFillColorRGB(0, 0, 0)
        c.setFont("Helvetica", 9)
        c.drawString(cols[0], y, _safe(acct))
        c.drawString(cols[1], y, _safe(qty))
        for i, ln in enumerate(simpleSplit(_safe(rc), "Helvetica", 9, cols[3] - cols[2] - 6)[:2]):
            c.drawString(cols[2], y - 11 * i, ln)
        line(st, size=9, x=cols[3], w=PAGE_W - MARGIN - cols[3], gap=11)
        y = min(y, top - 24) - 4
    for lot_line in d.get("lot_lines") or []:
        line(lot_line, size=9, x=MARGIN + 12, w=width - 12, gap=12)
    y -= 14
    line("Generated from the platform's record on request; not itself a record. Nothing is sent electronically.",
         size=8, color=MUTED, gap=11)
    line("Account numbers are shown masked. No member names appear on this sheet.", size=8, color=MUTED, gap=11)
    y -= 20
    line("Signature ______________________________", gap=16)
    if prepared_by:
        line(f"Prepared by {prepared_by}", size=9, color=MUTED)
    c.showPage()
    c.save()
    return buf.getvalue()
