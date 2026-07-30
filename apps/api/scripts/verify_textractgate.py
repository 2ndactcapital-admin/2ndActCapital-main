"""Textract gate re-check — REAL, live AWS Textract access confirmation.

Pass/fail only. No interactive prompts (runs UNATTENDED). Teardown at start AND
at end. This is a GATE CHECK, not Chancery Phase 3 extraction logic: it proves
only that a real DetectDocumentText call against a freshly generated, real
test document succeeds with the just-provisioned AWS credentials and returns
the text we embedded.

What it proves (each [Y] reported explicitly):
  [Y1] boto3 textract client initializes without a credentials error.
  [Y2] A real DetectDocumentText call against a generated test document
       succeeds (no NoCredentialsError / AccessDeniedException / region error).
  [Y3] The detected text matches the REAL text embedded in the test document.
  [Y4] The client's effective region matches AWS_DEFAULT_REGION's configured
       value.

Credentials discipline: AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY (and any
session token) are read only by boto3 from the environment. This script NEVER
prints, logs, or hardcodes them. Only the region name (non-secret) is shown.

Test document discipline (mirrors Chancery Phase 1's in-memory test-doc
generation): the document is built in memory as PNG bytes with real embedded
text and handed to Textract as Bytes. Textract's DetectDocumentText stores
nothing server-side for this API, so there is no AWS resource to clean up. Any
on-disk temp artifact (none is written in the happy path) is removed at start
and at end.
"""

import glob
import io
import os
import sys
import traceback

# ── Make this runnable via the allowlisted system `python3` OR venv python ──
# Add the venv's site-packages so boto3 / Pillow import even under the system
# python3 (which shares the venv's ABI). apps/api/venv is searched first.
_HERE = os.path.dirname(os.path.abspath(__file__))
_API_ROOT = os.path.dirname(_HERE)
_REPO_ROOT = os.path.dirname(os.path.dirname(_API_ROOT))
if _API_ROOT not in sys.path:
    sys.path.insert(0, _API_ROOT)
for _venv in (os.path.join(_REPO_ROOT, "venv"), os.path.join(_API_ROOT, "venv")):
    for _sp in glob.glob(os.path.join(_venv, "lib/python*/site-packages")):
        if _sp not in sys.path:
            sys.path.insert(0, _sp)

# Any stray on-disk artifact this script could ever leave (none in the happy
# path — the doc lives in memory). Removed at start AND end to honor teardown.
_ARTIFACT_GLOB = os.path.join(_HERE, "_textractgate_testdoc_*.png")

# The exact, real text embedded in the generated test document. Assertions
# check that Textract detected these tokens back out of it.
EMBED_LINE_1 = "TEXTRACT GATE 20260730"
EMBED_LINE_2 = "SECOND ACT CAPITAL OCR CHECK"
REQUIRED_TOKENS = ["TEXTRACT", "GATE", "20260730", "SECOND", "ACT", "CAPITAL", "OCR", "CHECK"]


def _teardown():
    for path in glob.glob(_ARTIFACT_GLOB):
        try:
            os.remove(path)
        except OSError:
            pass


def build_test_png() -> bytes:
    """Generate a real, in-memory PNG with known embedded text (OCR-friendly:
    large scalable default font, black on white, generous padding)."""
    from PIL import Image, ImageDraw, ImageFont

    font = ImageFont.load_default(size=52)
    img = Image.new("RGB", (1100, 300), "white")
    draw = ImageDraw.Draw(img)
    draw.text((40, 50), EMBED_LINE_1, font=font, fill="black")
    draw.text((40, 150), EMBED_LINE_2, font=font, fill="black")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def main() -> int:
    _teardown()  # teardown-at-start

    results = []  # (label, ok, detail)

    def record(label, ok, detail=""):
        results.append((label, ok, detail))
        mark = "PASS" if ok else "FAIL"
        line = f"[{mark}] {label}"
        if detail:
            line += f" — {detail}"
        print(line)

    configured_region = os.environ.get("AWS_DEFAULT_REGION") or os.environ.get("AWS_REGION")

    # Report (without leaking secrets) whether the required credential env vars
    # are even present, so a misconfig is diagnosable.
    have_key = bool(os.environ.get("AWS_ACCESS_KEY_ID"))
    have_secret = bool(os.environ.get("AWS_SECRET_ACCESS_KEY"))
    print(f"[info] AWS_ACCESS_KEY_ID present: {have_key}")
    print(f"[info] AWS_SECRET_ACCESS_KEY present: {have_secret}")
    print(f"[info] AWS_DEFAULT_REGION configured: {configured_region!r}")

    # Non-secret credential-shape diagnostic. NEVER prints the values — only
    # structural facts (length, whitespace) — so an IncompleteSignatureException
    # (malformed Authorization header) can be traced to a placeholder/whitespace
    # credential vs. a genuine AWS key. Valid AWS creds are 20 chars (access key
    # id, alnum, no spaces) and 40 chars (secret, no spaces).
    def _shape(name):
        v = os.environ.get(name) or ""
        return (f"{name}: length={len(v)} "
                f"contains_whitespace={any(c.isspace() for c in v)} "
                f"looks_like_valid_aws_shape="
                f"{len(v) in (20, 40) and not any(c.isspace() for c in v)}")
    print(f"[info] {_shape('AWS_ACCESS_KEY_ID')}")
    print(f"[info] {_shape('AWS_SECRET_ACCESS_KEY')}")

    client = None
    client_region = None

    # ── [Y1] boto3 textract client initializes without a credentials error ──
    try:
        import boto3
        from botocore.exceptions import (
            BotoCoreError,
            ClientError,
            NoCredentialsError,
            NoRegionError,
        )

        client = boto3.client("textract")  # region + creds from environment
        client_region = client.meta.region_name
        record("Y1 boto3 textract client initializes", True,
                f"client ready (region={client_region!r})")
    except Exception as e:  # noqa: BLE001 — gate check reports any failure honestly
        record("Y1 boto3 textract client initializes", False,
                f"{type(e).__name__}: {e}")
        # Cannot proceed without a client; report the rest as failed and stop.
        record("Y2 real DetectDocumentText call succeeds", False, "skipped — no client")
        record("Y3 detected text matches embedded text", False, "skipped — no client")
        record("Y4 client region matches AWS_DEFAULT_REGION", False, "skipped — no client")
        _teardown()
        return _summarize(results)

    # ── [Y2] a real DetectDocumentText call succeeds ──
    detected_lines = []
    detected_text_upper = ""
    call_ok = False
    try:
        png_bytes = build_test_png()
        resp = client.detect_document_text(Document={"Bytes": png_bytes})
        blocks = resp.get("Blocks", [])
        detected_lines = [b["Text"] for b in blocks if b.get("BlockType") == "LINE"]
        detected_text_upper = " ".join(detected_lines).upper()
        call_ok = True
        record("Y2 real DetectDocumentText call succeeds", True,
                f"{len(blocks)} blocks, {len(detected_lines)} LINE(s) returned")
    except NoCredentialsError as e:
        record("Y2 real DetectDocumentText call succeeds", False,
                f"NoCredentialsError: {e}")
    except NoRegionError as e:
        record("Y2 real DetectDocumentText call succeeds", False,
                f"NoRegionError: {e}")
    except ClientError as e:
        # AccessDeniedException, UnrecognizedClientException, etc. surface here.
        code = e.response.get("Error", {}).get("Code", "?")
        msg = e.response.get("Error", {}).get("Message", str(e))
        record("Y2 real DetectDocumentText call succeeds", False,
                f"ClientError [{code}]: {msg}")
    except BotoCoreError as e:
        record("Y2 real DetectDocumentText call succeeds", False,
                f"{type(e).__name__}: {e}")
    except Exception as e:  # noqa: BLE001
        record("Y2 real DetectDocumentText call succeeds", False,
                f"{type(e).__name__}: {e}")

    # ── [Y3] detected text matches the embedded text ──
    if call_ok:
        missing = [t for t in REQUIRED_TOKENS if t not in detected_text_upper]
        if not missing:
            record("Y3 detected text matches embedded text", True,
                    f"all {len(REQUIRED_TOKENS)} embedded tokens detected; "
                    f"lines={detected_lines!r}")
        else:
            record("Y3 detected text matches embedded text", False,
                    f"missing tokens {missing}; detected lines={detected_lines!r}")
    else:
        record("Y3 detected text matches embedded text", False,
                "skipped — call did not succeed")

    # ── [Y4] client region matches AWS_DEFAULT_REGION ──
    if not configured_region:
        record("Y4 client region matches AWS_DEFAULT_REGION", False,
                "AWS_DEFAULT_REGION / AWS_REGION not set in environment")
    elif client_region == configured_region:
        record("Y4 client region matches AWS_DEFAULT_REGION", True,
                f"both are {client_region!r}")
    else:
        record("Y4 client region matches AWS_DEFAULT_REGION", False,
                f"client region {client_region!r} != configured {configured_region!r}")

    _teardown()  # teardown-at-end
    return _summarize(results)


def _summarize(results) -> int:
    passed = sum(1 for _, ok, _ in results if ok)
    total = len(results)
    print("=" * 60)
    print(f"TEXTRACT GATE: {passed}/{total} assertions passed")
    if passed == total:
        print("RESULT: PASS — real live Textract access confirmed.")
        return 0
    print("RESULT: FAIL — see the exact error(s) above.")
    return 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:  # noqa: BLE001 — never leave the gate ambiguous
        traceback.print_exc()
        sys.exit(1)
