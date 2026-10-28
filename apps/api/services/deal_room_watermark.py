"""DEALROOM3 — watermarked renditions and viewer pages (pure functions).

No database, no R2: callers pass bytes in and get bytes out. Pure Python only
(Render has no system PDF libraries): pypdf 6.1.1 merges a reportlab 4.4.4
overlay onto every page; pypdfium2 (pdfplumber's dependency, bundling pdfium)
renders one page to PNG for the view-only viewer.

What can be watermarked (DEALROOM3_DISCOVERY 1b):
  pdf    every page
  image  PNG / JPEG — wrapped into a one-page PDF first, then stamped
  other  (DOCX, XLSX, PPTX, CSV, ZIP …) — no pure-Python path; ``kind_of``
         returns None and the caller refuses with :data:`NOT_WATERMARKABLE`
         unless the manager marked the document "share unwatermarked".

Every stamped page carries, as extractable text: the investor's name, the
investing entity, the deal, the date and time (UTC) and the watermark id — the
``deal_room_watermarks`` row id, which resolves to the person it was made for.
"""

from __future__ import annotations

import io

NOT_WATERMARKABLE = "This file type cannot be watermarked — ask the publisher for a PDF."
NOT_VIEWABLE = "This file type cannot be shown in the viewer — ask the publisher for a PDF."
MAX_PAGES = 400
MAX_BYTES = 40 * 1024 * 1024
TOO_LARGE = (f"This file is too large to watermark here (over {MAX_PAGES} pages or {MAX_BYTES // (1024 * 1024)} MB) "
             "— ask the publisher to split it.")
IMAGE_EXT = (".png", ".jpg", ".jpeg")


def kind_of(file_name: str | None, file_type: str | None, head: bytes | None = None) -> str | None:
    """'pdf' | 'image' | None. Decided by the bytes when given (the magic
    number), else by the declared type and extension."""
    if head is not None:
        if head.startswith(b"%PDF-"):
            return "pdf"
        if head.startswith(b"\x89PNG\r\n\x1a\n") or head.startswith(b"\xff\xd8\xff"):
            return "image"
        return None
    name = (file_name or "").lower()
    ctype = (file_type or "").lower()
    if ctype == "application/pdf" or name.endswith(".pdf"):
        return "pdf"
    if ctype in ("image/png", "image/jpeg") or name.endswith(IMAGE_EXT):
        return "image"
    return None


def _safe(s) -> str:
    """reportlab's standard fonts are WinAnsi: keep what cp1252 can draw."""
    return str(s if s is not None else "").encode("cp1252", "replace").decode("cp1252")


def stamp_lines(*, investor: str, entity: str | None, deal: str, at_utc: str, watermark_id: str) -> list[str]:
    return [
        f"Confidential - prepared for {investor}" + (f" ({entity})" if entity else ""),
        f"{deal} - {at_utc} - Watermark {watermark_id}",
    ]


def image_to_pdf(data: bytes) -> bytes:
    """A PNG / JPEG as a one-page PDF (page sized to the image, max 1000 pt)."""
    from PIL import Image
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfgen import canvas

    img = Image.open(io.BytesIO(data))
    w, h = img.size
    scale = min(1.0, 1000.0 / max(w, h, 1))
    pw, ph = max(w * scale, 200), max(h * scale, 200)
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(pw, ph))
    c.drawImage(ImageReader(img), 0, 0, width=w * scale, height=h * scale, preserveAspectRatio=True)
    c.showPage()
    c.save()
    return buf.getvalue()


def as_pdf(data: bytes, kind: str) -> bytes:
    if kind == "pdf":
        return data
    if kind == "image":
        return image_to_pdf(data)
    raise ValueError(NOT_WATERMARKABLE)


def page_count(pdf: bytes) -> int:
    from pypdf import PdfReader

    return len(PdfReader(io.BytesIO(pdf)).pages)


def _overlay(width: float, height: float, lines: list[str]) -> bytes:
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(width, height))
    # a large, light diagonal name across the page …
    c.saveState()
    c.setFillColorRGB(0x9B / 255, 0x23 / 255, 0x35 / 255, alpha=0.16)
    c.setFont("Helvetica-Bold", max(14, min(width, height) / 18))
    c.translate(width / 2, height / 2)
    c.rotate(35)
    c.drawCentredString(0, 0, _safe(lines[0]))
    c.restoreState()
    # … and the full stamp, small, along the foot of the page
    c.setFillColorRGB(0x1B / 255, 0x2B / 255, 0x4B / 255, alpha=0.85)
    size = 6.5 if width < 500 else 7.5
    c.setFont("Helvetica", size)
    for i, ln in enumerate(lines):
        c.drawString(18, 8 + (len(lines) - 1 - i) * (size + 2), _safe(ln))
    c.showPage()
    c.save()
    return buf.getvalue()


def watermark_pdf(pdf: bytes, lines: list[str], *, only_page: int | None = None) -> bytes:
    """Every page (or only ``only_page``, 0-based, as a one-page PDF) with the
    stamp merged on top. Raises ValueError(TOO_LARGE) past the caps."""
    from pypdf import PdfReader, PdfWriter

    if len(pdf) > MAX_BYTES and only_page is None:
        raise ValueError(TOO_LARGE)
    reader = PdfReader(io.BytesIO(pdf))
    pages = list(reader.pages)
    if only_page is None and len(pages) > MAX_PAGES:
        raise ValueError(TOO_LARGE)
    if only_page is not None:
        if not 0 <= only_page < len(pages):
            raise IndexError(only_page)
        pages = [pages[only_page]]
    writer = PdfWriter()
    overlays: dict[tuple, object] = {}
    for p in pages:
        w, h = float(p.mediabox.width), float(p.mediabox.height)
        key = (round(w), round(h))
        if key not in overlays:
            overlays[key] = PdfReader(io.BytesIO(_overlay(w, h, lines))).pages[0]
        p.merge_page(overlays[key])
        writer.add_page(p)
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def single_page(pdf: bytes, index: int) -> bytes:
    """Page ``index`` (0-based) of a PDF as its own one-page PDF (unstamped)."""
    from pypdf import PdfReader, PdfWriter

    reader = PdfReader(io.BytesIO(pdf))
    if not 0 <= index < len(reader.pages):
        raise IndexError(index)
    writer = PdfWriter()
    writer.add_page(reader.pages[index])
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def page_png(pdf: bytes, index: int, *, scale: float = 1.4) -> bytes:
    from services.deal_room_pdf import page_png as _png

    return _png(pdf, index, scale=scale)


def page_texts(pdf: bytes) -> list[str]:
    from pypdf import PdfReader

    return [(p.extract_text() or "") for p in PdfReader(io.BytesIO(pdf)).pages]
