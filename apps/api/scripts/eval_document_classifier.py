"""eval_document_classifier.py — DeepEval harness for the S25 document classifier.

Runs the open-set document-type classifier over a set of
``{text, expected_category}`` cases and scores each with the no-judge
DocumentTypeSortAccuracy metric (DeepEval's LLMTestCase format).

FIXTURES — no code change needed to evaluate real documents later:
  Drop JSON files into  apps/api/scripts/eval_fixtures/document_classifier/
  each shaped:  {"text": "<document text>", "expected_category": "<doc_category code>"}
  (a file may hold one object or a list of them). When that directory has any
  fixtures, they are used and the run is treated as REAL data. When it is empty
  — the case today, since no real documents exist yet — the harness falls back
  to a small SYNTHETIC placeholder set embedded below and prints an unmissable
  warning that the numbers are NOT representative of real-world accuracy.

Side-effect safety: classification runs inside a DB transaction that is ROLLED
BACK at the end, so an eval run never persists rows into doc_category_proposals.

Run from apps/api/:  python scripts/eval_document_classifier.py
Requires DATABASE_URL and ANTHROPIC_API_KEY (skips gracefully without either).
"""

import asyncio
import glob
import json
import os
import sys

API_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if API_DIR not in sys.path:
    sys.path.insert(0, API_DIR)

from deepeval.test_case import LLMTestCase  # noqa: E402

from services.document_classifier import classify_document  # noqa: E402
from services.eval_metrics import DocumentTypeSortAccuracy  # noqa: E402

DATABASE_URL = os.environ.get("DATABASE_URL")
DEFAULT_ORG = "00000000-0000-0000-0000-000000000001"

FIXTURE_DIR = os.path.join(
    API_DIR, "scripts", "eval_fixtures", "document_classifier"
)

# ── Synthetic placeholder set ───────────────────────────────────────────────
# CLEARLY FAKE snippets, one per flavour, each with a KNOWN expected category
# code from the seeded reference_data doc_category list. These exist ONLY so the
# plumbing can run before any real document exists. They are short and obviously
# invented on purpose — do not read anything into the resulting score.
SYNTHETIC_PLACEHOLDERS = [
    {
        "expected_category": "k1",
        "text": (
            "SCHEDULE K-1 (FORM 1065) — TAX YEAR 20XX [SYNTHETIC PLACEHOLDER]\n"
            "Partner's share of income, deductions, credits. Partner: Jane Fake. "
            "Box 1 Ordinary business income: 12,345. Box 19 Distributions: 4,000."
        ),
    },
    {
        "expected_category": "will",
        "text": (
            "LAST WILL AND TESTAMENT OF JOHN DOE [SYNTHETIC PLACEHOLDER]\n"
            "I, John Doe, being of sound mind, declare this my last will. I hereby "
            "revoke all prior wills. I bequeath my residuary estate to my children."
        ),
    },
    {
        "expected_category": "trust_instrument",
        "text": (
            "THE DOE FAMILY REVOCABLE TRUST [SYNTHETIC PLACEHOLDER]\n"
            "This Trust Agreement is made between John Doe, as Grantor, and John "
            "Doe, as Trustee. The Trustee shall hold the trust property in trust."
        ),
    },
    {
        "expected_category": "operating_agreement",
        "text": (
            "OPERATING AGREEMENT OF ACME HOLDINGS LLC [SYNTHETIC PLACEHOLDER]\n"
            "This Operating Agreement governs the Members and management of the "
            "limited liability company. Section 4: Capital Contributions of Members."
        ),
    },
    {
        "expected_category": "tax_return",
        "text": (
            "FORM 1040 U.S. INDIVIDUAL INCOME TAX RETURN [SYNTHETIC PLACEHOLDER]\n"
            "Filing status: Married filing jointly. Line 11 Adjusted gross income: "
            "250,000. Line 24 Total tax: 51,230. Refund: 0."
        ),
    },
    {
        "expected_category": "financial_statement",
        "text": (
            "BALANCE SHEET AS OF DECEMBER 31 [SYNTHETIC PLACEHOLDER]\n"
            "Total assets: 1,200,000. Total liabilities: 400,000. Total equity: "
            "800,000. Statement of cash flows attached."
        ),
    },
    {
        "expected_category": "id_document",
        "text": (
            "UNITED STATES OF AMERICA — PASSPORT [SYNTHETIC PLACEHOLDER]\n"
            "Surname: DOE. Given names: JOHN. Passport No: X00000000. "
            "Nationality: USA. Date of birth: 01 JAN 1970."
        ),
    },
    {
        "expected_category": "accreditation",
        "text": (
            "ACCREDITED INVESTOR VERIFICATION LETTER [SYNTHETIC PLACEHOLDER]\n"
            "This letter confirms that the investor qualifies as an accredited "
            "investor under Rule 501, with net worth exceeding one million dollars."
        ),
    },
    {
        "expected_category": "subscription_doc",
        "text": (
            "SUBSCRIPTION AGREEMENT — FUND II, LP [SYNTHETIC PLACEHOLDER]\n"
            "The undersigned subscriber hereby subscribes for limited partnership "
            "interests and agrees to the capital commitment set forth in Schedule A."
        ),
    },
    {
        "expected_category": "estate_plan",
        "text": (
            "ESTATE PLANNING SUMMARY & MEMORANDUM [SYNTHETIC PLACEHOLDER]\n"
            "Overview of the client's estate plan: pour-over will, revocable living "
            "trust, durable power of attorney, and advance healthcare directive."
        ),
    },
]


def load_fixtures() -> tuple[list[dict], bool]:
    """Return (cases, is_synthetic).

    Reads every *.json in FIXTURE_DIR (each an object or list of objects). When
    none are found, returns the embedded synthetic set with is_synthetic=True.
    """
    cases: list[dict] = []
    for path in sorted(glob.glob(os.path.join(FIXTURE_DIR, "*.json"))):
        try:
            with open(path, encoding="utf-8") as fh:
                payload = json.load(fh)
        except (OSError, ValueError) as exc:
            print(f"  [skip fixture {os.path.basename(path)}] {exc}")
            continue
        items = payload if isinstance(payload, list) else [payload]
        for item in items:
            if item.get("text") and item.get("expected_category"):
                cases.append(item)
    if cases:
        return cases, False
    return list(SYNTHETIC_PLACEHOLDERS), True


def print_synthetic_banner():
    bar = "!" * 74
    print("\n" + bar)
    print("!!  SYNTHETIC / PLACEHOLDER EVAL RUN — NOT A REAL ACCURACY MEASUREMENT !!")
    print("!!" + " " * 70 + "!!")
    print("!!  These cases are obviously-fake snippets that exist only to exercise !!")
    print("!!  the classifier plumbing. The score below says NOTHING about real   !!")
    print("!!  world accuracy. To evaluate for real, drop real documents into:    !!")
    print("!!    apps/api/scripts/eval_fixtures/document_classifier/*.json         !!")
    print("!!  as {\"text\": ..., \"expected_category\": ...} and re-run.              !!")
    print(bar + "\n")


async def main():
    if not DATABASE_URL:
        print("[eval] SKIP — DATABASE_URL not set")
        return
    if not os.environ.get("ANTHROPIC_API_KEY"):
        # Banner still prints so the operator knows what this harness is.
        print_synthetic_banner()
        print("[eval] SKIP — ANTHROPIC_API_KEY not set (classifier cannot run)")
        return

    import asyncpg

    cases, is_synthetic = load_fixtures()

    if is_synthetic:
        print_synthetic_banner()
    else:
        print(f"\n[eval] Using {len(cases)} REAL fixture case(s) from {FIXTURE_DIR}\n")

    metric = DocumentTypeSortAccuracy()
    conn = await asyncpg.connect(DATABASE_URL, statement_cache_size=0)

    # Classify inside a transaction we ROLL BACK — an eval must never persist
    # proposal rows.
    tr = conn.transaction()
    await tr.start()
    results = []
    try:
        for case in cases:
            out = await classify_document(conn, DEFAULT_ORG, case["text"])
            predicted = out.get("category_code")
            tc = LLMTestCase(
                input=case["text"],
                actual_output=predicted or "",
                expected_output=case["expected_category"],
            )
            score = metric.measure(tc)
            results.append({
                "expected": case["expected_category"],
                "predicted": predicted,
                "is_new_proposal": out.get("is_new_proposal"),
                "score": score,
            })
    finally:
        await tr.rollback()  # discard any proposal inserts from this run
        await conn.close()

    passed = sum(1 for r in results if r["score"] >= 1.0)
    total = len(results)

    print("Document Type Sort Accuracy — per case")
    print("-" * 74)
    for r in results:
        icon = "MATCH " if r["score"] >= 1.0 else "MISS  "
        flag = "  (proposed new)" if r["is_new_proposal"] else ""
        print(
            f"  {icon} expected={r['expected']:<20} "
            f"predicted={str(r['predicted']):<20}{flag}"
        )
    print("-" * 74)
    pct = (passed / total * 100) if total else 0.0
    print(f"Sort accuracy: {passed}/{total} = {pct:.1f}%")

    if is_synthetic:
        print_synthetic_banner()

    # Non-zero exit only on a harness failure (no cases); the accuracy value
    # itself is NOT a gate on synthetic data.
    sys.exit(0 if total else 1)


if __name__ == "__main__":
    asyncio.run(main())
