"""AWS Textract OCR service — the single, shared Textract choke point.

Phase 3 (TABULAR / K-1) was BLOCKED at its gate (no AWS creds), so it never
produced a reusable Textract *service* — only ``verify_textractgate.py``, a
standalone gate check that proved a live ``DetectDocumentText`` call works once
credentials were provisioned. This module promotes that gate-proven call into
the canonical, reusable integration so Chancery Phase 4 (standalone-image OCR)
and any future OCR/TABULAR phase route through ONE place — no duplicated boto3
Textract clients scattered across the codebase.

Credentials + region come from the standard AWS env chain
(``AWS_ACCESS_KEY_ID`` / ``AWS_SECRET_ACCESS_KEY`` / ``AWS_DEFAULT_REGION``),
exactly as ``verify_textractgate.py`` proved. This module NEVER prints, logs, or
hardcodes secrets. boto3 is synchronous — callers in async code MUST invoke via
``run_in_threadpool``.
"""

import os

# Textract DetectDocumentText hard-caps synchronous byte payloads at 10 MB and
# rasterizable single-page images. Bigger inputs (multi-page async) are a future
# phase; we surface a clear error rather than blindly calling the API.
_MAX_SYNC_BYTES = 10 * 1024 * 1024

# ClientError codes that mean "Textract is not usable here" (bad/placeholder
# creds, expired token, denied permission, malformed signature) as opposed to a
# genuine problem with the submitted document. These degrade to needs_ocr.
_UNAVAILABLE_CLIENT_CODES = frozenset({
    "IncompleteSignatureException", "InvalidSignatureException",
    "SignatureDoesNotMatch", "UnrecognizedClientException", "InvalidClientTokenId",
    "AccessDeniedException", "AuthFailure", "ExpiredTokenException",
    "ExpiredToken", "InvalidAccessKeyId", "MissingAuthenticationToken",
    "UnauthorizedException", "ThrottlingException",
    "ProvisionedThroughputExceededException",
})


class TextractUnavailable(RuntimeError):
    """Textract is not usable (no credentials / no region / client init failed).

    Raised so callers can degrade gracefully (mark a document ``needs_ocr`` /
    leave it un-extracted) instead of crashing a batch — mirrors how the rest of
    the pipeline treats a missing external dependency.
    """


def _usable_secret(name: str) -> bool:
    """A credential value is usable only if present and whitespace-free.

    Whitespace is NEVER valid in an AWS access key id or secret, so a value
    containing any (a placeholder like ``"changeme\\n"``) is treated as absent —
    this cheaply skips a doomed API call that would only raise
    IncompleteSignatureException.
    """
    v = os.environ.get(name) or ""
    return bool(v) and not any(c.isspace() for c in v)


def textract_configured() -> bool:
    """True when usable AWS creds + a region are present in the environment.

    A cheap pre-check so callers can skip Textract cleanly in unattended / CI
    runs (no or placeholder creds) rather than eating a boto3 exception per
    document.
    """
    have_creds = _usable_secret("AWS_ACCESS_KEY_ID") and _usable_secret(
        "AWS_SECRET_ACCESS_KEY"
    )
    have_region = bool(
        os.environ.get("AWS_DEFAULT_REGION") or os.environ.get("AWS_REGION")
    )
    return have_creds and have_region


def _client():
    """Construct a boto3 Textract client (region/creds from the env chain)."""
    import boto3  # local import: boto3 is only needed on the OCR path

    region = os.environ.get("AWS_DEFAULT_REGION") or os.environ.get("AWS_REGION")
    return boto3.client("textract", region_name=region)


def _run_textract(op_name: str, document_bytes: bytes, **extra) -> dict:
    """Invoke a synchronous Textract operation on a single-page document's bytes.

    Shared choke point for ``detect_document_text`` (DetectDocumentText) and
    ``analyze_document`` (AnalyzeDocument). Applies the same size / config
    pre-checks and the same error taxonomy for BOTH operations: config / auth /
    credential faults degrade to :class:`TextractUnavailable` (retryable —
    callers park the doc as needs_ocr); a genuine document/parameter ``ClientError``
    (e.g. UnsupportedDocumentException) is re-raised as a real fault. Returns the
    raw boto3 response dict.

    Synchronous (boto3) — call via ``run_in_threadpool`` from async handlers.
    """
    if not document_bytes:
        raise TextractUnavailable("empty document bytes")
    if len(document_bytes) > _MAX_SYNC_BYTES:
        raise TextractUnavailable(
            f"document is {len(document_bytes)} bytes; Textract sync limit "
            f"is {_MAX_SYNC_BYTES} bytes"
        )
    if not textract_configured():
        raise TextractUnavailable(
            "AWS Textract not configured (missing AWS_ACCESS_KEY_ID / "
            "AWS_SECRET_ACCESS_KEY / AWS_DEFAULT_REGION)"
        )

    try:
        from botocore.exceptions import (
            BotoCoreError,
            ClientError,
            NoCredentialsError,
            NoRegionError,
        )
    except Exception as exc:  # pragma: no cover - boto3/botocore always present with boto3
        raise TextractUnavailable(f"botocore import failed: {exc}") from exc

    try:
        client = _client()
    except Exception as exc:  # noqa: BLE001 - client construction failure = unavailable
        raise TextractUnavailable(f"textract client init failed: {exc}") from exc

    try:
        resp = getattr(client, op_name)(Document={"Bytes": document_bytes}, **extra)
    except (NoCredentialsError, NoRegionError) as exc:
        raise TextractUnavailable(f"{type(exc).__name__}: {exc}") from exc
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")
        if code in _UNAVAILABLE_CLIENT_CODES:
            raise TextractUnavailable(f"ClientError [{code}]: {exc}") from exc
        raise
    except BotoCoreError as exc:
        # Network/endpoint faults — unavailable, not "no result".
        raise TextractUnavailable(f"{type(exc).__name__}: {exc}") from exc
    return resp


def detect_document_text(image_bytes: bytes) -> dict:
    """Run Textract ``DetectDocumentText`` on a single-page image's bytes.

    Returns ``{"lines": [str, ...], "text": str, "block_count": int}`` where
    ``text`` is the LINE blocks joined with newlines (reading order as Textract
    returns them). Raises :class:`TextractUnavailable` when Textract is not
    configured or the client cannot be built; lets a genuine API ``ClientError``
    propagate so a real service fault is never silently swallowed as "no text".

    Synchronous (boto3) — call via ``run_in_threadpool`` from async handlers.
    """
    resp = _run_textract("detect_document_text", image_bytes)
    blocks = resp.get("Blocks", [])
    lines = [b["Text"] for b in blocks if b.get("BlockType") == "LINE" and b.get("Text")]
    return {
        "lines": lines,
        "text": "\n".join(lines),
        "block_count": len(blocks),
    }


def _block_text(block: dict, by_id: dict) -> str:
    """Reconstruct a block's text from its CHILD WORD / SELECTION_ELEMENT blocks."""
    parts: list[str] = []
    for rel in block.get("Relationships") or []:
        if rel.get("Type") != "CHILD":
            continue
        for cid in rel.get("Ids", []):
            child = by_id.get(cid)
            if not child:
                continue
            btype = child.get("BlockType")
            if btype == "WORD" and child.get("Text"):
                parts.append(child["Text"])
            elif btype == "SELECTION_ELEMENT" and child.get("SelectionStatus") == "SELECTED":
                parts.append("[X]")
    return " ".join(parts).strip()


def parse_analyze_blocks(blocks: list) -> dict:
    """Parse AnalyzeDocument ``Blocks`` into forms (key→value), tables, lines.

    * ``forms``  — a dict of KEY text → VALUE text (from KEY_VALUE_SET pairs).
    * ``tables`` — a list of 2-D string grids (row-major, 1-based cell indices
      flattened to a dense grid).
    * ``lines``  — LINE block texts in reading order.
    """
    by_id = {b["Id"]: b for b in blocks if b.get("Id")}

    forms: dict[str, str] = {}
    for kb in blocks:
        if kb.get("BlockType") != "KEY_VALUE_SET":
            continue
        if "KEY" not in (kb.get("EntityTypes") or []):
            continue
        key_text = _block_text(kb, by_id)
        value_text = ""
        for rel in kb.get("Relationships") or []:
            if rel.get("Type") == "VALUE":
                for vid in rel.get("Ids", []):
                    vb = by_id.get(vid)
                    if vb:
                        value_text = _block_text(vb, by_id)
        if key_text:
            forms[key_text] = value_text

    tables: list[list[list[str]]] = []
    for tb in blocks:
        if tb.get("BlockType") != "TABLE":
            continue
        cells: dict[tuple[int, int], str] = {}
        max_r = max_c = 0
        for rel in tb.get("Relationships") or []:
            if rel.get("Type") != "CHILD":
                continue
            for cid in rel.get("Ids", []):
                cb = by_id.get(cid)
                if not cb or cb.get("BlockType") != "CELL":
                    continue
                r, c = cb.get("RowIndex", 0), cb.get("ColumnIndex", 0)
                cells[(r, c)] = _block_text(cb, by_id)
                max_r, max_c = max(max_r, r), max(max_c, c)
        grid = [[cells.get((r, c), "") for c in range(1, max_c + 1)]
                for r in range(1, max_r + 1)]
        if grid:
            tables.append(grid)

    lines = [b["Text"] for b in blocks if b.get("BlockType") == "LINE" and b.get("Text")]
    return {"forms": forms, "tables": tables, "lines": lines}


def analyze_document(
    document_bytes: bytes, feature_types: tuple = ("TABLES", "FORMS")
) -> dict:
    """Run Textract ``AnalyzeDocument`` (TABLES + FORMS by default) on one page.

    Used for TABULAR documents (e.g. K-1s) where the value lives in form fields
    and tables, not free prose. Returns::

        {"lines": [str], "text": str, "forms": {key: value},
         "tables": [[[cell, ...], ...], ...], "block_count": int,
         "feature_types": [str, ...]}

    Same :class:`TextractUnavailable` degradation contract as
    ``detect_document_text``. Synchronous (boto3) — call via ``run_in_threadpool``.
    """
    resp = _run_textract(
        "analyze_document", document_bytes, FeatureTypes=list(feature_types)
    )
    blocks = resp.get("Blocks", [])
    parsed = parse_analyze_blocks(blocks)
    return {
        "lines": parsed["lines"],
        "text": "\n".join(parsed["lines"]),
        "forms": parsed["forms"],
        "tables": parsed["tables"],
        "block_count": len(blocks),
        "feature_types": list(feature_types),
    }
