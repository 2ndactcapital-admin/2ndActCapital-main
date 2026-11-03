"""REP10 — the transfer-of-securities letter for an in-kind charitable gift.

A PDF drawn ON DEMAND from an approved gift proposal: one page per delivering
account, addressed to that account's custodian, naming the donee, its EIN,
the receiving broker, its DTC participant number, the donee's account (MASKED
— the full number is never stored, so it cannot be printed), and every lot
with its quantity. NEVER STORED and never the source of truth: the proposal
and its gift lots are. Delivery is manual, always: the member signs this and
the custodian acts on it; nothing here moves a security.

reportlab 4.4.4 (pinned for dealroom2) — standard fonts, WinAnsi-safe text.
"""

from __future__ import annotations

import io
from datetime import datetime, timezone

from services.deal_room_pdf import MARGIN, MUTED, NAVY, PAGE_H, PAGE_W, _safe

_LABEL_W = 170


def letter_rows(d: dict, acct: dict) -> list[tuple[str, str]]:
    """The (label, value) lines of one account's page — also what the verify reads back."""
    dn = d["donee"]
    return [
        ("Delivering account", acct["account_number_masked"]),
        ("Custodian", acct.get("custodian_code") or "—"),
        ("Deliver to", dn["name"]),
        ("Donee kind", dn["kind_label"]),
        ("Donee EIN", dn["ein"]),
        ("Receiving broker", dn["receiving_broker"]),
        ("DTC participant", dn["dtc_participant"]),
        ("Donee account", dn["account_number_masked"]),
        ("Delivery", "Free of payment (DFOP), in kind — do NOT sell; deliver the shares themselves"),
        ("Tax year of the gift", str(d["tax_year"])),
        ("Gift reference", d["id"]),
    ]


def build_letter(d: dict, *, org_name: str, prepared_by: str = "") -> bytes:
    from reportlab.lib.utils import simpleSplit
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(PAGE_W, PAGE_H), pageCompression=1)
    c.setTitle(_safe(f"Transfer of securities {d['id']}"))
    width = PAGE_W - 2 * MARGIN

    for acct in d["accounts"]:
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
        line("Letter of instruction — transfer of securities (charitable gift)", "Times-Roman", 15, NAVY, gap=22)
        line(f"Date: {datetime.now(timezone.utc).date().isoformat()}", color=MUTED)
        line(f"To: {acct.get('custodian_code') or 'the custodian'} — transfer department", color=MUTED, gap=20)
        line("Please deliver the securities below, in kind and free of payment, from the account shown to the "
             "receiving broker for the donee named. Do not sell them. Confirm to us when the delivery is made; the "
             "gift's date is the date the shares reach the donee's account.", gap=14)
        y -= 6
        for label, value in letter_rows(d, acct):
            top = y
            c.setFillColorRGB(*MUTED)
            c.setFont("Helvetica", 9)
            c.drawString(MARGIN, y, _safe(label))
            line(str(value), x=MARGIN + _LABEL_W, w=width - _LABEL_W)
            y = min(y, top - 15)
        y -= 6
        line("Securities and lots to deliver", "Helvetica-Bold", 10, NAVY, gap=15)
        for lot in acct["lots"]:
            line(f"{lot['security']}: {lot['quantity']} shares — lot acquired {lot['acquired_date']} "
                 f"(long-term)", x=MARGIN + 12, w=width - 12)
        y -= 16
        line("Generated from the gift record on request; not itself a record. Account numbers are shown masked; "
             "the full numbers are on file with the custodian and the receiving broker.", size=8, color=MUTED, gap=11)
        y -= 22
        line("Account holder signature ______________________________     Date __________", gap=16)
        if prepared_by:
            line(f"Prepared by {prepared_by}", size=9, color=MUTED)
        c.showPage()
    c.save()
    return buf.getvalue()
