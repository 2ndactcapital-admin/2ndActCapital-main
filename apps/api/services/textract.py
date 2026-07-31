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


def detect_document_text(image_bytes: bytes) -> dict:
    """Run Textract ``DetectDocumentText`` on a single-page image's bytes.

    Returns ``{"lines": [str, ...], "text": str, "block_count": int}`` where
    ``text`` is the LINE blocks joined with newlines (reading order as Textract
    returns them). Raises :class:`TextractUnavailable` when Textract is not
    configured or the client cannot be built; lets a genuine API ``ClientError``
    propagate so a real service fault is never silently swallowed as "no text".

    Synchronous (boto3) — call via ``run_in_threadpool`` from async handlers.
    """
    if not image_bytes:
        raise TextractUnavailable("empty image bytes")
    if len(image_bytes) > _MAX_SYNC_BYTES:
        raise TextractUnavailable(
            f"image is {len(image_bytes)} bytes; DetectDocumentText sync limit "
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
        resp = client.detect_document_text(Document={"Bytes": image_bytes})
    except (NoCredentialsError, NoRegionError) as exc:
        raise TextractUnavailable(f"{type(exc).__name__}: {exc}") from exc
    except ClientError as exc:
        # Auth / credential / access ClientErrors mean Textract is not usable in
        # this environment (bad or placeholder creds, expired token, denied
        # permission) — a CONFIG problem, not a property of the document. Surface
        # them as "unavailable" so callers park the doc as needs_ocr (retryable)
        # rather than permanently failing it. A genuine document/parameter error
        # (e.g. UnsupportedDocumentException) is re-raised as a real fault.
        code = exc.response.get("Error", {}).get("Code", "")
        if code in _UNAVAILABLE_CLIENT_CODES:
            raise TextractUnavailable(f"ClientError [{code}]: {exc}") from exc
        raise
    except BotoCoreError as exc:
        # Network/endpoint faults — unavailable, not "no text".
        raise TextractUnavailable(f"{type(exc).__name__}: {exc}") from exc

    blocks = resp.get("Blocks", [])
    lines = [b["Text"] for b in blocks if b.get("BlockType") == "LINE" and b.get("Text")]
    return {
        "lines": lines,
        "text": "\n".join(lines),
        "block_count": len(blocks),
    }
