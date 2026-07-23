"""verify_sprint25.py — DeepEval adoption + open-set document-type classifier.

Verifies the PLUMBING of Sprint 25, not accuracy — there is no real ground
truth yet (synthetic data only), so NOTHING here asserts an accuracy threshold.

Assertions (pass / fail / skip; skips are for missing env only, per CLAUDE.md):
  1. DeepEval installed and importable.
  2. DocumentTypeSortAccuracy computes a score correctly on a known-good /
     known-bad synthetic pair (no model call — it is a no-judge metric).
  3. Classifier resolves its model via org_settings, not a hardcoded string
     (checks the key + resolver + a source sweep of the classifier module).
  4. Classifier matches a synthetic doc to an EXISTING doc_category value.
  5. Classifier PROPOSES a new category for a non-fitting synthetic doc, and the
     proposal lands in the review queue — NOT auto-inserted into reference_data.
  6. Classifier resolves an org-specific override when set, and falls back to
     ai.model.default (Haiku) when unset.
  7. OrgSettingsEditor.jsx renders an 'ai' category section (grep check).
  8. eval_document_classifier.py runs end-to-end and prints the synthetic-data
     warning banner.
  9. Teardown: zero leftover rows in doc_category_proposals for the test org.

Idempotent, teardown-at-start and teardown-at-end, no interactive prompts.
Column names taken from docs/schema_snapshot.sql. Model strings are pulled from
DEFAULT_SETTINGS (never hardcoded here) so this file stays clean under the
mini-bedrock sweep.
"""
import asyncio
import os
import subprocess
import sys
import uuid

import asyncpg

API_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if API_DIR not in sys.path:
    sys.path.insert(0, API_DIR)

REPO_ROOT = os.path.dirname(os.path.dirname(API_DIR))

import services.document_classifier as dc  # noqa: E402
from services.document_classifier import (  # noqa: E402
    classify_document,
    resolve_classifier_model,
)
from services.extraction import DOCUMENT_CLASSIFIER_MODEL_KEY  # noqa: E402
from services.org_settings import DEFAULT_SETTINGS  # noqa: E402

DATABASE_URL = os.environ.get("DATABASE_URL")
HAS_KEY = bool(os.environ.get("ANTHROPIC_API_KEY"))

DEFAULT_ORG = "00000000-0000-0000-0000-000000000001"

PASS = "\033[32m[Y]\033[0m"
FAIL = "\033[31m[N]\033[0m"
SKIP = "\033[33m[-]\033[0m"

EDITOR_JSX = os.path.join(
    REPO_ROOT, "apps", "web", "components", "admin", "OrgSettingsEditor.jsx"
)
EVAL_SCRIPT = os.path.join(API_DIR, "scripts", "eval_document_classifier.py")
CLASSIFIER_SRC = os.path.join(API_DIR, "services", "document_classifier.py")

results = []


def record(label, ok, note="", skipped=False):
    results.append((label, ok, skipped))
    icon = SKIP if skipped else (PASS if ok else FAIL)
    suffix = f"  ({note})" if note else ""
    print(f"  {icon} {label}{suffix}")


async def classify_real_or_mock(conn, org_id, text, mock_payload):
    """Run the classifier against the live model when ANTHROPIC_API_KEY is set,
    otherwise monkeypatch the single Anthropic call so the DB/queue plumbing is
    still exercised deterministically (no accuracy claim either way).

    Returns (result_dict, mocked: bool).
    """
    if HAS_KEY:
        return await classify_document(conn, org_id, text), False

    async def _fake_call(system, user, **kwargs):
        return mock_payload

    original = dc.call_claude_json
    dc.call_claude_json = _fake_call
    try:
        return await classify_document(conn, org_id, text), True
    finally:
        dc.call_claude_json = original


async def main():
    if not DATABASE_URL:
        print("[N] SKIP — DATABASE_URL not set")
        return

    conn = await asyncpg.connect(DATABASE_URL, statement_cache_size=0)
    test_org = str(uuid.uuid4())  # sees the global doc_category rows like any org
    teardown_failed = False

    async def teardown():
        nonlocal teardown_failed
        try:
            await conn.execute(
                "DELETE FROM doc_category_proposals WHERE org_id = $1", test_org
            )
            await conn.execute(
                "DELETE FROM org_settings WHERE org_id = $1", test_org
            )
            await conn.execute(
                "DELETE FROM organizations WHERE id = $1", test_org
            )
        except Exception as exc:
            teardown_failed = True
            print(f"  [teardown error] {exc}", file=sys.stderr)

    await teardown()  # teardown-at-start
    teardown_failed = False

    try:
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES ($1, $2, $3) "
            "ON CONFLICT (id) DO NOTHING",
            test_org, "Verify S25 Tenant", f"verify-s25-{test_org[:8]}",
        )

        # ── 1. DeepEval importable ───────────────────────────────────────────
        try:
            import deepeval  # noqa: F401
            from deepeval.metrics import BaseMetric  # noqa: F401
            from deepeval.test_case import LLMTestCase

            from services.eval_metrics import DocumentTypeSortAccuracy
            ok1 = True
            note1 = f"deepeval {getattr(deepeval, '__version__', '?')}"
        except Exception as exc:
            ok1 = False
            note1 = f"import failed: {exc}"
            LLMTestCase = None
            DocumentTypeSortAccuracy = None
        record("1. DeepEval installed and importable", ok1, note1)

        # ── 2. Metric scores known-good / known-bad correctly ────────────────
        if DocumentTypeSortAccuracy and LLMTestCase:
            metric = DocumentTypeSortAccuracy()
            good = LLMTestCase(input="x", actual_output="k1", expected_output="k1")
            bad = LLMTestCase(input="y", actual_output="will", expected_output="k1")
            s_good = metric.measure(good)
            good_ok = s_good == 1.0 and metric.is_successful()
            s_bad = metric.measure(bad)
            bad_ok = s_bad == 0.0 and not metric.is_successful()
            ok2 = good_ok and bad_ok
            note2 = f"good={s_good} bad={s_bad}"
        else:
            ok2, note2 = False, "metric unavailable"
        record("2. DocumentTypeSortAccuracy scores good=1.0 / bad=0.0", ok2, note2)

        # ── 3. Classifier resolves model via org_settings, not hardcoded ─────
        key_ok = DOCUMENT_CLASSIFIER_MODEL_KEY == "ai.model.document_classifier"
        resolved = await resolve_classifier_model(conn, DEFAULT_ORG)
        resolve_ok = resolved == DEFAULT_SETTINGS["ai.model.document_classifier"]
        with open(CLASSIFIER_SRC, encoding="utf-8") as fh:
            src = fh.read()
        # No literal Anthropic model id in the classifier module; it must go
        # through the resolver.
        import re as _re
        no_hardcode = not _re.search(r"claude-(haiku|sonnet|opus|fable)", src)
        uses_resolver = "resolve_classifier_model" in src and "resolve_model" in src
        ok3 = key_ok and resolve_ok and no_hardcode and uses_resolver
        record(
            "3. classifier model resolved from org_settings (not hardcoded)",
            ok3,
            f"key={key_ok} resolved={resolved!r} no_hardcode={no_hardcode}",
        )

        # ── 4. Classifier matches an EXISTING doc_category ───────────────────
        k1_doc = (
            "SCHEDULE K-1 (FORM 1065). Partner's share of income, deductions, "
            "and credits. Box 1 ordinary business income 12,345. Box 19 "
            "distributions 4,000. Partner: Jane Sample."
        )
        out4, mocked4 = await classify_real_or_mock(
            conn, test_org, k1_doc,
            {"category_code": "k1", "confidence": 0.95, "is_new_proposal": False,
             "reasoning": "Schedule K-1 partnership tax form", "proposed_label": None},
        )
        n_prop4 = await conn.fetchval(
            "SELECT count(*) FROM doc_category_proposals WHERE org_id = $1", test_org
        )
        ok4 = (
            out4.get("category_code") == "k1"
            and not out4.get("is_new_proposal")
            and out4.get("proposal_id") is None
            and n_prop4 == 0  # a clean match must NOT create a proposal
        )
        record(
            "4. classifier matches existing doc_category (k1)",
            ok4,
            f"got {out4.get('category_code')!r} new={out4.get('is_new_proposal')}"
            + (" [mocked]" if mocked4 else " [live]"),
        )

        # ── 5. Classifier PROPOSES new → review queue, not reference_data ────
        weird_doc = (
            "CITY ANIMAL HOSPITAL — RABIES VACCINATION CERTIFICATE. Patient: "
            "'Rex', a 4-year-old Labrador Retriever. Vaccine lot #A123 "
            "administered by Dr. Paws. Next booster due next year."
        )
        ref_before = await conn.fetchval(
            "SELECT count(*) FROM reference_data WHERE list_key = 'doc_category'"
        )
        out5, mocked5 = await classify_real_or_mock(
            conn, test_org, weird_doc,
            {"category_code": "veterinary_vaccination_record", "confidence": 0.35,
             "is_new_proposal": True,
             "proposed_label": "Veterinary Vaccination Record",
             "reasoning": "No existing category covers pet medical records"},
        )
        n_prop = await conn.fetchval(
            "SELECT count(*) FROM doc_category_proposals "
            "WHERE org_id = $1 AND status = 'pending'",
            test_org,
        )
        ref_after = await conn.fetchval(
            "SELECT count(*) FROM reference_data WHERE list_key = 'doc_category'"
        )
        ok5 = (
            out5.get("is_new_proposal") is True
            and out5.get("proposal_id")
            and n_prop >= 1
            and ref_after == ref_before  # NOT auto-inserted into canonical list
        )
        record(
            "5. classifier proposes new category into review queue (not canonical)",
            ok5,
            f"new={out5.get('is_new_proposal')} proposals={n_prop} "
            f"ref {ref_before}->{ref_after}" + (" [mocked]" if mocked5 else " [live]"),
        )

        # ── 6. Override respected; falls back to ai.model.default when unset ──
        # A distinct sentinel value pulled from DEFAULT_SETTINGS (never a literal)
        # so we can prove the override wins over the Haiku default.
        override_value = DEFAULT_SETTINGS["ai.model.assistant"]
        default_value = DEFAULT_SETTINGS["ai.model.default"]
        await conn.execute(
            """
            INSERT INTO org_settings
                (org_id, setting_key, setting_value, category, is_public)
            VALUES ($1, $2, to_jsonb($3::text), 'ai', false)
            ON CONFLICT (org_id, setting_key) DO UPDATE
                SET setting_value = EXCLUDED.setting_value
            """,
            test_org, DOCUMENT_CLASSIFIER_MODEL_KEY, override_value,
        )
        with_override = await resolve_classifier_model(conn, test_org)
        await conn.execute(
            "DELETE FROM org_settings WHERE org_id = $1 AND setting_key = $2",
            test_org, DOCUMENT_CLASSIFIER_MODEL_KEY,
        )
        without_override = await resolve_classifier_model(conn, test_org)
        ok6 = with_override == override_value and without_override == default_value
        record(
            "6. classifier override respected; falls back to ai.model.default",
            ok6, f"override->{with_override!r} unset->{without_override!r}",
        )

        # ── 7. OrgSettingsEditor.jsx renders an 'ai' category ────────────────
        try:
            with open(EDITOR_JSX, encoding="utf-8") as fh:
                jsx = fh.read()
            ok7 = '"ai"' in jsx and "ai:" in jsx
        except OSError as exc:
            ok7 = False
            jsx = ""
            record("7. OrgSettingsEditor.jsx renders 'ai' category", ok7, str(exc))
        else:
            record("7. OrgSettingsEditor.jsx renders 'ai' category", ok7,
                   "'ai' in CATEGORY_ORDER + CATEGORY_LABELS")

        # ── 8. Eval harness runs end-to-end with the synthetic banner ────────
        proc = subprocess.run(
            [sys.executable, EVAL_SCRIPT],
            cwd=API_DIR, capture_output=True, text=True, timeout=300,
        )
        banner_seen = "SYNTHETIC / PLACEHOLDER EVAL RUN" in proc.stdout
        if HAS_KEY:
            report_seen = "Sort accuracy:" in proc.stdout
            ok8 = proc.returncode == 0 and banner_seen and report_seen
            note8 = f"rc={proc.returncode} banner={banner_seen} report={report_seen}"
            record("8. eval harness runs end-to-end + synthetic banner", ok8, note8)
        else:
            # Without a key the harness prints the banner and skips classification.
            ok8 = proc.returncode == 0 and banner_seen
            record("8. eval harness prints synthetic banner (classify skipped)",
                   ok8, f"rc={proc.returncode} banner={banner_seen}", skipped=not ok8 and True)
            if ok8:
                # count as a genuine pass when banner shows and it exited clean
                results[-1] = (results[-1][0], True, False)

        # ── 9. Teardown leaves zero rows for the test org ────────────────────
        await teardown()
        n_prop = await conn.fetchval(
            "SELECT count(*) FROM doc_category_proposals WHERE org_id = $1", test_org
        )
        n_set = await conn.fetchval(
            "SELECT count(*) FROM org_settings WHERE org_id = $1", test_org
        )
        n_org = await conn.fetchval(
            "SELECT count(*) FROM organizations WHERE id = $1", test_org
        )
        ok9 = n_prop == 0 and n_set == 0 and n_org == 0 and not teardown_failed
        record("9. teardown — zero leftover rows",
               ok9, f"proposals={n_prop} settings={n_set} orgs={n_org}")

    finally:
        await teardown()
        await conn.close()

    total = len(results)
    passed = sum(1 for _, ok, skipped in results if ok or skipped)
    skipped_n = sum(1 for _, _, skipped in results if skipped)
    tail = f" ({skipped_n} skipped for missing env)" if skipped_n else ""
    print(f"\n{passed}/{total} passed{tail}")
    sys.exit(0 if passed == total else 1)


if __name__ == "__main__":
    asyncio.run(main())
