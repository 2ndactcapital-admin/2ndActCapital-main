"""DeepEval custom metrics for 2nd Act evals (Sprint 25).

DeepEval (Apache-2.0) is our eval framework. This module holds the base config
notes and the sprint's one custom metric, DocumentTypeSortAccuracy.

────────────────────────────────────────────────────────────────────────────
JUDGE-MODEL TRAP — READ BEFORE ADDING ANY NEW METRIC
────────────────────────────────────────────────────────────────────────────
Most of DeepEval's built-in metrics (AnswerRelevancy, Faithfulness,
Hallucination, GEval, …) are *LLM-judge* metrics: they call a model to grade
the output, and DeepEval DEFAULTS THAT JUDGE TO OPENAI (it reads OPENAI_API_KEY
and silently picks a GPT model). That is wrong for this codebase on two counts:
  1. We route ALL model calls through org_settings (ai.model.*), never a
     hardcoded/defaulted provider — see services/extraction.resolve_model.
  2. We are moving to Anthropic commercial + ZDR (and possibly Bedrock/Vertex);
     a stray OpenAI judge call would leak eval data outside that boundary.

DocumentTypeSortAccuracy — the ONLY metric this sprint needs — is a NO-JUDGE
metric: it is an exact category-code match against a KNOWN expected label, pure
string comparison, zero model calls. So no judge is configured here, correctly.

When a future sprint adds a judge-NEEDED metric (e.g. retrieval faithfulness in
S26), it MUST pass an explicit judge model resolved from ai.model.* — e.g.
build a deepeval DeepEvalBaseLLM wrapper around services.extraction.call_claude_*
and hand it to the metric as `model=...`. NEVER let the judge default. There is
no OpenAI key in this project by design; a defaulted judge will either fail or,
worse, silently exfiltrate. Resolve the judge model the same way every other
call path does.
"""

from deepeval.metrics import BaseMetric
from deepeval.test_case import LLMTestCase


def _normalize(code) -> str:
    """Canonicalize a category code for comparison (case/space-insensitive)."""
    return (code or "").strip().lower()


class DocumentTypeSortAccuracy(BaseMetric):
    """Scores whether the classifier sorted a document into the right category.

    NO-JUDGE metric. Compares the classifier's returned category code
    (``test_case.actual_output``) against the known expected category code
    (``test_case.expected_output``). Score is 1.0 on an exact (normalized)
    match, else 0.0 — no model is called to grade anything.

    Usage:
        tc = LLMTestCase(input=doc_text,
                         actual_output=predicted_code,
                         expected_output=expected_code)
        metric = DocumentTypeSortAccuracy()
        metric.measure(tc)   # -> 1.0 or 0.0, sets metric.success

    A proposed-new category (the open-set case) never equals an existing
    expected code, so it scores 0.0 here by design: this metric measures
    match-accuracy against known labels, which is exactly what a synthetic
    ground-truth set can assert. Evaluating the *quality* of new-category
    proposals needs human review, not this metric.
    """

    def __init__(self, threshold: float = 1.0):
        # threshold 1.0: a case is "successful" only on an exact match. No LLM
        # judge, so no model/async-model wiring is required or wanted.
        self.threshold = threshold

    def measure(self, test_case: LLMTestCase) -> float:
        expected = _normalize(test_case.expected_output)
        actual = _normalize(test_case.actual_output)
        matched = bool(expected) and expected == actual
        self.score = 1.0 if matched else 0.0
        self.success = self.score >= self.threshold
        self.reason = (
            f"matched expected category '{expected}'"
            if matched
            else f"expected '{expected}', classifier returned '{actual}'"
        )
        return self.score

    async def a_measure(self, test_case: LLMTestCase, *args, **kwargs) -> float:
        # No async work (no judge call) — delegate to the sync path so DeepEval's
        # async evaluate() harness works without spinning up a model.
        return self.measure(test_case)

    def is_successful(self) -> bool:
        return bool(getattr(self, "success", False))

    @property
    def __name__(self):  # DeepEval reads this for report labelling.
        return "Document Type Sort Accuracy"
