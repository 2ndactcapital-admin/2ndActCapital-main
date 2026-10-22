"""DEALROOM2 — the signed copy as a PDF, with a certificate page.

Generated ON DEMAND from the signature row — never stored, never the source of
truth (the append-only ``deal_signatures`` row is). Pure Python only (Render has
no system PDF libraries): reportlab 4.4.4 draws the text pages and the
certificate; pypdf 6.1.1 appends an uploaded agreement's own pages (and its
Exhibit A) and reads PDFs back. Both are pinned in apps/api/requirements.txt.

Document pages:
  template signature  the exact ``rendered_text`` the signer saw
  upload signature    the uploaded PDF itself (its bytes must still hash to
                      the signature's ``document_sha256`` — else refused)
then Exhibit A when the version has one, then ONE certificate page listing:
signer, investing entity, typed name, consent statement, signed time (UTC and
America/New_York), IP, user agent, agreement and version, document SHA-256,
record SHA-256, chain position, and the verify reference.
"""

from __future__ import annotations

import io
from datetime import timezone
from zoneinfo import ZoneInfo

CONSENT_STATEMENT = "I agree to sign electronically"
ET = ZoneInfo("America/New_York")
NAVY = (0x1B / 255, 0x2B / 255, 0x4B / 255)
MUTED = (0x64 / 255, 0x74 / 255, 0x8B / 255)
ERROR = (0x9B / 255, 0x23 / 255, 0x35 / 255)
PAGE_W, PAGE_H = 612, 792  # US letter, points
MARGIN = 60


def _safe(s) -> str:
    """reportlab's standard fonts are WinAnsi: keep what cp1252 can draw
    (em dashes and curly quotes included), replace anything else."""
    return str(s if s is not None else "").encode("cp1252", "replace").decode("cp1252")


def _canvas(buf):
    from reportlab.pdfgen import canvas

    c = canvas.Canvas(buf, pagesize=(PAGE_W, PAGE_H), pageCompression=1)
    return c


def text_pages(title: str, text: str, *, draft: bool) -> bytes:
    """The rendered agreement text, wrapped onto as many pages as it needs."""
    from reportlab.lib.utils import simpleSplit

    buf = io.BytesIO()
    c = _canvas(buf)
    c.setTitle(_safe(title))
    width = PAGE_W - 2 * MARGIN
    y = PAGE_H - MARGIN

    def newpage():
        nonlocal y
        c.showPage()
        y = PAGE_H - MARGIN

    c.setFillColorRGB(*NAVY)
    c.setFont("Times-Bold", 15)
    for ln in simpleSplit(_safe(title), "Times-Bold", 15, width):
        c.drawString(MARGIN, y, ln)
        y -= 20
    if draft:
        c.setFillColorRGB(*ERROR)
        c.setFont("Helvetica", 9)
        c.drawString(MARGIN, y, "Template marked DRAFT — pending counsel review.")
        y -= 16
    y -= 6
    c.setFillColorRGB(0, 0, 0)
    for para in _safe(text).split("\n"):
        lines = simpleSplit(para, "Helvetica", 10, width) or [""]
        for ln in lines:
            if y < MARGIN:
                newpage()
            c.setFont("Helvetica", 10)
            c.drawString(MARGIN, y, ln)
            y -= 13
    c.showPage()
    c.save()
    return buf.getvalue()


def certificate_rows(sig: dict, ctx: dict) -> list[tuple[str, str]]:
    """The certificate's (label, value) lines — also what verify compares."""
    at = sig["signed_at"]
    utc = at.astimezone(timezone.utc)
    et = at.astimezone(ET)
    version = ctx.get("version")
    agreement = f"{ctx.get('title') or ''}" + (f" — version {version}" if version else "")
    if ctx.get("template_key"):
        agreement += f" (template {ctx['template_key']} v{sig.get('template_version')})"
    rows = [
        ("Signer", f"{ctx.get('signer_name') or ''}" + (f" <{ctx['signer_email']}>" if ctx.get("signer_email") else "")),
        ("Investing entity", ctx.get("entity_name") or "—"),
        ("Typed name", sig["typed_name"]),
        ("Consent", f"The signer ticked “{CONSENT_STATEMENT}” (consent = {str(bool(sig['consent'])).lower()})"),
        ("Signed (UTC)", utc.strftime("%Y-%m-%d %H:%M:%S UTC")),
        ("Signed (ET)", et.strftime("%Y-%m-%d %H:%M:%S %Z")),
        ("IP address", sig.get("ip") or "—"),
        ("User agent", sig.get("user_agent") or "—"),
        ("Deal", ctx.get("deal_name") or "—"),
        ("Agreement", agreement),
        ("Document SHA-256", sig.get("document_sha256") or sig["rendered_text_sha256"]),
        ("Rendered text SHA-256", sig["rendered_text_sha256"]),
        ("Record SHA-256", sig["record_sha256"]),
        ("Previous record SHA-256", sig.get("prev_signature_sha256") or "— (first in chain)"),
        ("Chain position", f"{sig['chain']} #{sig['chain_seq']} (record format v{int(sig.get('record_version') or 1)})"),
        ("Signature id", str(sig["id"])),
    ]
    if ctx.get("exhibit_sha256"):
        rows.insert(11, ("Exhibit A SHA-256", ctx["exhibit_sha256"]))
    rows.append(("Verify", ctx.get("verify_ref") or ""))
    return rows


HASH_LABELS = {"Document SHA-256", "Rendered text SHA-256", "Record SHA-256", "Previous record SHA-256",
               "Exhibit A SHA-256", "Signature id"}


def certificate_page(sig: dict, ctx: dict) -> bytes:
    from reportlab.lib.utils import simpleSplit

    buf = io.BytesIO()
    c = _canvas(buf)
    width = PAGE_W - 2 * MARGIN
    y = PAGE_H - MARGIN
    c.setFillColorRGB(*NAVY)
    c.setFont("Times-Bold", 16)
    c.drawString(MARGIN, y, "Certificate of electronic signature")
    y -= 18
    c.setFillColorRGB(*MUTED)
    c.setFont("Helvetica", 9)
    for ln in simpleSplit("Generated on demand from the signature record, which is the source of truth. The "
                          "record is append-only and hash-chained; the hashes below let anyone check this copy "
                          "against it.", "Helvetica", 9, width):
        c.drawString(MARGIN, y, ln)
        y -= 11
    y -= 10
    for label, value in certificate_rows(sig, ctx):
        c.setFillColorRGB(*MUTED)
        c.setFont("Helvetica-Bold", 8.5)
        c.drawString(MARGIN, y, _safe(label))
        y -= 11
        c.setFillColorRGB(0, 0, 0)
        mono = label in HASH_LABELS
        font, size = ("Courier", 8.5) if mono else ("Helvetica", 9.5)
        for ln in simpleSplit(_safe(value), font, size, width) or ["—"]:
            c.setFont(font, size)
            c.drawString(MARGIN, y, ln)
            y -= 12
        y -= 4
    c.showPage()
    c.save()
    return buf.getvalue()


def build(sig: dict, ctx: dict, *, document_pdf: bytes | None = None, exhibit_pdf: bytes | None = None) -> bytes:
    """The whole signed copy: document pages, Exhibit A, certificate."""
    from pypdf import PdfReader, PdfWriter

    writer = PdfWriter()
    body = document_pdf if document_pdf is not None else text_pages(
        ctx.get("title") or "Signed agreement", sig["rendered_text"], draft=ctx.get("template_status") == "draft")
    for part in (body, exhibit_pdf, certificate_page(sig, ctx)):
        if part is None:
            continue
        for page in PdfReader(io.BytesIO(part)).pages:
            writer.add_page(page)
    writer.add_metadata({"/Title": _safe(f"{ctx.get('title') or 'Agreement'} — signed copy"),
                         "/Subject": f"deal_signatures {sig['id']} record {sig['record_sha256']}"})
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def page_png(pdf: bytes, index: int, *, scale: float = 1.4) -> bytes:
    """One page of a PDF as PNG, for the in-app viewer (pypdfium2 — already
    deployed as pdfplumber's dependency; its wheel bundles pdfium)."""
    import pypdfium2 as pdfium

    doc = pdfium.PdfDocument(pdf)
    try:
        if not 0 <= index < len(doc):
            raise IndexError(index)
        img = doc[index].render(scale=scale).to_pil()
        buf = io.BytesIO()
        img.save(buf, format="PNG", optimize=True)
        return buf.getvalue()
    finally:
        doc.close()
