"""eval_correction_loop.py — Chancery Phase 8 DeepEval before/after harness.

Measures whether retrieval-augmented CLASSIFICATION (correction_retrieval →
classify_document few-shot) actually improves accuracy — a real BEFORE/AFTER, not
"it ran". Reuses the S25 DeepEval discipline: the NO-JUDGE
``DocumentTypeSortAccuracy`` metric (exact category-code match, zero model calls
to grade), never an LLM-judge, never a defaulted OpenAI judge (see
services/eval_metrics.py).

Method (honest by construction):
  * Each case is a genuinely DUAL-PURPOSE document whose "correct" label is an
    org FILING PREFERENCE (e.g. a subscription doc this org files under
    accreditation). The base model's natural reading differs from the org's
    preference — exactly the situation a past correction should fix.
  * A prior correction for a SIMILAR (not identical) document is seeded into
    ``document_field_corrections`` (reserved field_name 'doc_category').
  * WITHOUT: classify_document(..., use_corrections=False) — the pre-Phase-8 path.
  * WITH:    classify_document(..., use_corrections=True)  — few-shot injected.
  * Both are scored against the org-preferred expected code. We report the two
    accuracies and every per-case prediction. Whatever it shows, it is printed
    verbatim — the cases are NOT tuned to force a favourable result.

All seeding happens inside a transaction that is ROLLED BACK, so a run never
persists rows. Requires DATABASE_URL + ANTHROPIC_API_KEY; skips gracefully
without either.

Run from apps/api/:  python scripts/eval_correction_loop.py
"""

import asyncio
import glob
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_API_ROOT = os.path.dirname(_HERE)
_REPO_ROOT = os.path.dirname(os.path.dirname(_API_ROOT))
if _API_ROOT not in sys.path:
    sys.path.insert(0, _API_ROOT)
for _venv in (os.path.join(_REPO_ROOT, "venv"), os.path.join(_API_ROOT, "venv")):
    for _sp in glob.glob(os.path.join(_venv, "lib/python*/site-packages")):
        if _sp not in sys.path:
            sys.path.insert(0, _sp)

from deepeval.test_case import LLMTestCase  # noqa: E402

from services.correction_retrieval import CLASSIFICATION_FIELD  # noqa: E402
from services.document_classifier import classify_document  # noqa: E402
from services.eval_metrics import DocumentTypeSortAccuracy  # noqa: E402

DATABASE_URL = os.environ.get("DATABASE_URL")
DEFAULT_ORG = "00000000-0000-0000-0000-000000000001"

# ── Dual-purpose cases: base reading ≠ org preference ────────────────────────
# `text`        — the NEW document to classify.
# `expected`    — the org's preferred (corrected) category code.
# `natural`     — the category the base model tends to pick WITHOUT the hint
#                 (documentation only; not asserted).
# `prior_text`  — a SIMILAR earlier document the org previously corrected (its
#                 extracted_text, used as the few-shot excerpt).
AMBIGUOUS_CASES = [
    {
        "text": (
            "SUBSCRIPTION AGREEMENT — GRANITE FUND III, L.P.\n"
            "The undersigned subscriber hereby subscribes for limited partnership "
            "interests and commits $500,000 per Schedule A. The subscriber "
            "represents and warrants that it is an ACCREDITED INVESTOR with net "
            "worth exceeding $1,000,000, and attaches accreditation representations."
        ),
        "expected": "accreditation",
        "natural": "subscription_doc",
        "prior_text": (
            "SUBSCRIPTION AGREEMENT — SUMMIT FUND II, L.P. Subscriber affirms "
            "accredited-investor status with net worth over one million dollars "
            "and subscribes for LP interests."
        ),
    },
    {
        "text": (
            "OPERATING AGREEMENT OF MERIDIAN HOLDINGS LLC\n"
            "Executed in connection with the FORMATION and organization of the "
            "limited liability company. Recites the Articles of Organization filed "
            "with the Secretary of State, the initial members, and their capital "
            "contributions at organization."
        ),
        "expected": "llc_formation",
        "natural": "operating_agreement",
        "prior_text": (
            "OPERATING AGREEMENT OF CEDAR POINT LLC, executed upon formation of "
            "the company, reciting the filed articles of organization and the "
            "initial members admitted at organization."
        ),
    },
    {
        "text": (
            "ESTATE PLANNING MEMORANDUM AND POUR-OVER WILL\n"
            "This memorandum summarizes the client's overall ESTATE PLAN: a "
            "pour-over will pouring the residuary estate into the revocable living "
            "trust, together with durable powers of attorney and an advance "
            "healthcare directive."
        ),
        "expected": "estate_plan",
        "natural": "will",
        "prior_text": (
            "ESTATE PLANNING SUMMARY: overview of the client's plan including a "
            "pour-over will, revocable living trust, power of attorney and "
            "healthcare directive."
        ),
    },
]


async def _seed_correction(conn, org_id, case, idx):
    """Insert a document + extraction (for the excerpt) + a doc_category
    correction that a human previously made on a SIMILAR document. Caller owns
    the surrounding transaction (rolled back)."""
    doc_id = await conn.fetchval(
        """
        INSERT INTO documents (org_id, original_filename, source, status)
        VALUES ($1, $2, 'upload', 'confirmed')
        RETURNING id
        """,
        org_id, f"eval_correction_loop_prior_{idx}.pdf",
    )
    await conn.execute(
        """
        INSERT INTO document_extractions
            (document_id, org_id, extraction_method, has_native_text_layer,
             extracted_text, page_count)
        VALUES ($1, $2, 'native_pdfplumber', true, $3, 1)
        """,
        doc_id, org_id, case["prior_text"],
    )
    await conn.execute(
        """
        INSERT INTO document_field_corrections
            (document_id, org_id, template_extraction_id, field_name,
             original_value, corrected_value)
        VALUES ($1, $2, NULL, $3, $4, $5)
        """,
        doc_id, org_id, CLASSIFICATION_FIELD, case["natural"], case["expected"],
    )
    return doc_id


async def evaluate(conn, org_id, cases=None) -> dict:
    """Seed each case's prior correction, then classify WITH and WITHOUT the
    retrieval loop and score both. Caller must run this inside a transaction it
    ROLLS BACK. Returns a report dict."""
    cases = cases or AMBIGUOUS_CASES
    metric = DocumentTypeSortAccuracy()
    per_case = []
    with_pass = without_pass = 0

    for idx, case in enumerate(cases):
        await _seed_correction(conn, org_id, case, idx)

        without = await classify_document(
            conn, org_id, case["text"], use_corrections=False)
        with_ = await classify_document(
            conn, org_id, case["text"], use_corrections=True)
        wo_pred = without.get("category_code")
        w_pred = with_.get("category_code")

        tc_wo = LLMTestCase(input=case["text"], actual_output=wo_pred or "",
                            expected_output=case["expected"])
        tc_w = LLMTestCase(input=case["text"], actual_output=w_pred or "",
                           expected_output=case["expected"])
        wo_score = metric.measure(tc_wo)
        w_score = metric.measure(tc_w)
        without_pass += 1 if wo_score >= 1.0 else 0
        with_pass += 1 if w_score >= 1.0 else 0

        per_case.append({
            "expected": case["expected"],
            "without_pred": wo_pred,
            "with_pred": w_pred,
            "flipped_to_correct": wo_score < 1.0 <= w_score,
            "regressed": w_score < 1.0 <= wo_score,
        })

    n = len(cases)
    return {
        "n": n,
        "without_accuracy": (without_pass / n) if n else 0.0,
        "with_accuracy": (with_pass / n) if n else 0.0,
        "without_pass": without_pass,
        "with_pass": with_pass,
        "cases": per_case,
    }


def print_report(report: dict):
    print("\nChancery Phase 8 — correction loop before/after (DeepEval, no-judge)")
    print("-" * 74)
    for c in report["cases"]:
        wo = "MATCH" if c["without_pred"] == c["expected"] else "miss "
        w = "MATCH" if c["with_pred"] == c["expected"] else "miss "
        flag = ""
        if c["flipped_to_correct"]:
            flag = "  <= FLIPPED wrong→right by correction"
        elif c["regressed"]:
            flag = "  <= REGRESSED right→wrong (reported honestly)"
        print(f"  expected={c['expected']:<18} "
              f"WITHOUT[{wo}]={str(c['without_pred']):<18} "
              f"WITH[{w}]={str(c['with_pred']):<18}{flag}")
    print("-" * 74)
    wo_pct = report["without_accuracy"] * 100
    w_pct = report["with_accuracy"] * 100
    print(f"WITHOUT correction retrieval: {report['without_pass']}/{report['n']} "
          f"= {wo_pct:.1f}%")
    print(f"WITH    correction retrieval: {report['with_pass']}/{report['n']} "
          f"= {w_pct:.1f}%")
    delta = w_pct - wo_pct
    verdict = ("improvement" if delta > 0 else
               "no change" if delta == 0 else "REGRESSION")
    print(f"Δ accuracy = {delta:+.1f} pts  ({verdict})")


async def main():
    if not DATABASE_URL:
        print("[eval] SKIP — DATABASE_URL not set")
        return
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("[eval] SKIP — ANTHROPIC_API_KEY not set (classifier cannot run)")
        return

    import asyncpg

    conn = await asyncpg.connect(DATABASE_URL, statement_cache_size=0)
    tr = conn.transaction()
    await tr.start()
    try:
        report = await evaluate(conn, DEFAULT_ORG)
        print_report(report)
    finally:
        await tr.rollback()  # never persist eval rows
        await conn.close()
    sys.exit(0)


if __name__ == "__main__":
    asyncio.run(main())
