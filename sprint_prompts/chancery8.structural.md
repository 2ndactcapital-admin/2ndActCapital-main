CHANCERY — PHASE 8 (correction-learning loop). 4 tasks +
verification. NOT fine-tuning — Anthropic offers no mechanism
fitting "learn from one correction, apply it next quarter." The
real mechanism: a correction log (Phase 6's document_field_
corrections, already populated) + retrieval-augmented
classification — look up relevant PAST corrections before
calling the AI on a NEW document, include them as few-shot
examples. The correction takes effect on the very next matching
document, no batch retraining, no delay.

CONTEXT: document_field_corrections exists with real data from
Phase 6 (field_name, original_value, corrected_value, org_id,
document_id, corrected_by, corrected_at) but has NO direct
doc_category/template_type column — relevant context requires
joining back to documents/document_template_extractions. Confirm
this is sufficient before assuming a schema change is needed.

STANDING RULES: org_id never from request body; no interactive
prompts; light theme if any UI touched (none expected — backend/
eval work only).

=== TASK 1: Discover, don't assume ===
  (a) Re-read the REAL current document_field_corrections schema
      and confirm what real correction data exists after tonight's
      Phase 6/7 testing — confirm the join path to doc_category/
      template_type is genuinely sufficient for relevant retrieval,
      or if a schema addition is actually needed (report which).
  (b) Re-read the REAL current classify_document (services/
      document_classifier.py) — its exact prompt-construction
      logic, to know precisely where past-correction examples
      should be injected.
  (c) Re-read the REAL current K-1 template-mapping call site
      (from the Phase 3 completion sprint) — same question, for
      field-level corrections specifically.
Report all three findings before proceeding.

=== TASK 2: Correction-retrieval service ===
Build apps/api/services/correction_retrieval.py:
  - get_relevant_corrections(org_id, context) -> list — given an
    org and relevant context (doc_category for classification
    corrections, or template_type + field_name for extraction
    corrections), query document_field_corrections (joined per
    Task 1a's real findings) for the most relevant/recent past
    corrections. Cap at a reasonable number (e.g. 5) — this
    becomes few-shot prompt content, not a full history dump.

=== TASK 3: Wire retrieval into classification AND K-1
extraction ===
  - Modify classify_document (Task 1b's real hook) to call Task 2
    BEFORE constructing its AI prompt, and include relevant past
    corrections as few-shot examples (e.g. "a document with X
    characteristics was previously corrected from category A to
    category B — consider this pattern").
  - Modify the K-1 field-mapping logic (Task 1c's real hook)
    similarly for field-level corrections (e.g. "the field X was
    previously corrected from value pattern Y to Z for this
    org/template — consider this when mapping").
  - Both must gracefully handle ZERO relevant past corrections
    (the common case, especially early on) — no correction
    context available should never break or degrade the normal
    classification/extraction path.

=== TASK 4: DeepEval measurement — does this actually help ===
Build a DeepEval-based test (reuse the existing DeepEval adoption
from S25, the same "two-hour test" discipline — a custom no-judge
metric, not a vague LLM-judge comparison):
  - Construct a small set of test cases where a PRIOR correction
    genuinely should influence a NEW, similar document's
    classification/extraction.
  - Measure accuracy WITH correction-context retrieval enabled vs.
    WITHOUT (the same classify_document/mapping call, correction
    lookup disabled) — a real before/after comparison, not just
    "it ran."
  - Report the actual measured difference — if it shows no
    improvement or even regression, report that honestly, do not
    tune the test cases to force a favorable result.

=== VERIFICATION ===
Write verify_chancery8.py (apps/api/scripts/) — pass/fail only,
no interactive prompts, teardown-at-start and teardown-at-end.

Assertions to include:
  [Y] Report Task 1's three discovery findings explicitly
  [Y] get_relevant_corrections returns the correct, relevant past
      corrections for a real seeded scenario (and correctly
      returns EMPTY for an org/context with no relevant history)
  [Y] A classification call WITH a relevant past correction
      available produces a DIFFERENT (and correct, matching the
      pattern) result than the SAME call WITHOUT that correction
      available — proves the injection genuinely changes behavior,
      not just runs without erroring
  [Y] The same proof for K-1 field-mapping correction context
  [Y] Zero relevant corrections available does not break or alter
      normal classification/extraction behavior
  [Y] Report the DeepEval before/after accuracy measurement
      explicitly and honestly, whatever it shows
  [Y] A different org's corrections are NEVER used for another
      org's retrieval (test against the real app_service
      connection — this is a real data-isolation requirement, not
      just RLS on the table itself, since the retrieval QUERY
      logic itself must filter by org)
  [Y] Teardown: zero leftover rows

Report each assertion explicitly. Push when 100% pass — hold for
manual review regardless of tier.
