TERM EXTRACTION — reference_filings -> securities_global_note_terms,
with a six-field hazard ensemble as the cross-check against misreads.
6 tasks + verification.
DEPENDS ON (confirm all three merged to main before proceeding — this
is Task 1's job, not an assumption):
portfolio.reference_filings (EDGAR corpus sprint)
portfolio.securities_global_note_terms + note_terms_field_registry
(payoff DSL sprint)
document_field_corrections polymorphism (target_type='note_terms')
WHAT THIS SPRINT DOES: for filings with extraction_status='extracted'
and passed the keyword prefilter, extract structured terms into
securities_global_note_terms rows. Deterministic validators run first;
an LLM (Haiku, per house model tiering) does the extraction; a cheap
second-model pass checks the six HAZARD fields specifically, because
those are the fields that pass every arithmetic check while being
catastrophically wrong.
WHAT THIS IS NOT — DO NOT BUILD THESE:
Underlying resolution to securities_global_relationships. A term
sheet naming "the S&P 500 Index" gets stored as raw text in this
sprint's output; RESOLVING that string to a global_security_id is
the next sprint. Do not build a resolver here — it's a distinct,
harder problem (decrement indices, fuzzy matching) and conflating
it with extraction will make both worse.
Comparability scoring, percentiles, or the staff UI. That is the
sprint after resolution.
Template induction / clustering. Extraction here is per-document,
LLM-driven. The template system (deterministic extraction via
induced boilerplate matching) is future work, NOT this sprint —
do not build a template matcher.
Any change to securities_global_note_terms' schema. It's built.
If this sprint finds it inadequate, STOP and report why rather
than silently altering it.
STANDING RULES: org_id never from request body (N/A here — this
writes to global tables); Decimal for money everywhere; no bare
assert (use typed exceptions — stripped under python -O); verify
scripts pass/fail only.
THERE IS NO HUMAN AVAILABLE. Report discovery, then continue
immediately in the same response. Exceptions are the explicit
STOP/BLOCKED gates below.

=== TASK 1: DISCOVER — confirm the prerequisites are real ===
Read the real current state, report, THEN CONTINUE IMMEDIATELY.
1a. Confirm all three dependency sprints are on main (not just the
feature branch) with their tables present and RLS-enabled, per
the schema queries used in their own verify scripts. If any is
missing or only exists on a feature branch, STOP and report
BLOCKED — do not proceed against unmerged schema.
1b. Report the current count and a sample of 5 rows from
reference_filings WHERE extraction_status = 'extracted'. This is
the actual input population for this sprint. Report exact
numbers, not "some rows exist."
1c. Confirm which model alias resolves for Haiku per the existing
ai.model.* org_settings / TaskRouter mechanism, since this
writes to a global table with no org_id — report how model
resolution works for a task that has no org context, and if
nothing handles that case today, treat it as a discovery finding
to report, not something to silently patch around.
1d. Confirm the exact enum values already loaded into
note_terms_field_registry and reproduce the 6 hazard_field=true
keys verbatim. Do not re-derive the hazard list from memory of
this prompt — read it from the live registry, since that table
is the source of truth per its own design.

=== TASK 2: DETERMINISTIC VALIDATORS (run before and after the LLM) ===
New file: apps/api/services/note_terms_validators.py
Implement, each returning (bool, str reason):
cusip_checksum(cusip: str) -> bool         # standard mod-10 Luhn variant
cik_matches_filer(extracted_issuer, filing_cik) -> bool
cross-check against reference_filings.cik — free ground truth
barrier_price_consistent(barrier_pct, initial_level, barrier_price,
tolerance=Decimal('0.01')) -> bool
barrier_pct * initial_level ~= barrier_price
autocall_le_coupon_barrier(coupon_barrier_pct, autocall_barrier_pct) -> bool
in nearly all Phoenix structures; log a warning not a hard
failure if violated, since "nearly all" means real exceptions exist
tenor_consistent(initial_valuation_date, final_valuation_date,
tenor_years, tolerance_days=10) -> bool
These catch NUMERICAL errors only. They will NOT catch the hazard
fields (Task 3) — document this explicitly in the module docstring so
nobody later assumes these validators are sufficient on their own.

=== TASK 3: EXTRACTION SERVICE ===
New file: apps/api/services/note_terms_extraction.py
extract_terms(filing_id: uuid, pool) -> ExtractionResult
Pipeline per filing:
Load reference_filings.extracted_text for the given filing_id.
Primary extraction call (Haiku per 1c's resolved mechanism):
structured-output prompt against the field list from
note_terms_field_registry, constrained to the columns that exist
on securities_global_note_terms. Do NOT let the model invent
field names.
Populate field_status per the four-state model already defined
on the table (extracted | not_applicable | extraction_failed |
not_in_template) — every field in the registry must get a status,
never silently omitted.
Run Task 2's validators against the extracted numeric fields.
Any validator failure -> extraction_confidence = 'needs_review',
do not silently accept.
HAZARD ENSEMBLE (the core of this sprint): for the 6 hazard_field
keys from 1d, make a SECOND independent extraction call for those
fields ONLY, using a different model (Sonnet, or a distinct
provider if one is already configured — report which is used and
why). Compare the two answers per hazard field.
Agreement -> keep the primary answer, extraction_confidence
unaffected by this check
Disagreement on ANY hazard field -> extraction_confidence =
'needs_review' regardless of what Task 2's validators said,
and record BOTH answers in a structured note (not silently
picking one)
This is disagreement detection producing a boolean flag, not a
compared output — it does not violate the "never vary model
within a compared set" rule, because nothing here compares the
two models' answers to each other as a ranked result; one flags
for human review.
Write ONE securities_global_note_terms row with terms_status
derived from reference_filings.form_type ('FWP' -> 'preliminary',
'424B2' -> 'final'), reference_filing_id set, source_char_start/
source_char_end populated from wherever the primary extraction
located its answer in extracted_text (this is the traceability
property from the EDGAR sprint — a later UI must be able to
highlight the source span; do not skip this to save a step).
Update reference_filings.extraction_status appropriately (this
is a DIFFERENT status field than the corpus sprint's fetch-status
use of the same column — confirm in Task 1 whether this creates
a state collision; if the same column is being asked to track
both "was this fetched/prefiltered" and "were terms extracted
from it," STOP and report this as a design gap rather than
overloading the column silently).
extract_underlying_mentions(filing_id) -> list[str]
Extract raw underlying-reference strings (e.g. "the Common Stock
of NVIDIA Corporation") WITHOUT resolving them. Write each as a
securities_global_relationships row with link_state='unresolved',
raw_underlying_text set, to_global_security_id NULL, relationship_
type='underlying_of'. This IS in scope — creating the unresolved
edge is extraction; RESOLVING it is not (see WHAT THIS IS NOT).

=== TASK 4: CORRECTION LOGGING WIRING ===
When a human (or a later review step) corrects a note_terms field,
the correction MUST log via document_field_corrections with
target_type='note_terms', target_id = the note_terms row id, org_id
NULL — using the polymorphism from the prerequisite sprint. Write a
thin wrapper function log_note_terms_correction(...) rather than
inlining the insert at each call site, so there is one place this
logic lives. Do not build a review UI this sprint — just the logging
function and its own focused test.

=== TASK 5: BOUNDED RUN ===
Run extraction against the population from 1b, capped at 50 filings
(not the full corpus — this proves the pipeline; scaling it is a
volume decision made after reviewing accuracy, not a default).
Report real numbers: filings processed, rows created, field_status
distribution, hazard disagreement rate, validator failure rate,
extraction_confidence distribution.

=== TASK 6: UPDATE PROJECT STATUS ===
Update docs/PROJECT_STATUS.md: what was built, the Task 5 numbers,
explicit note that underlying resolution is NOT done (edges exist as
unresolved), and the model-resolution finding from 1c.

=== VERIFICATION: apps/api/scripts/verify_notetermsextraction.py ===
Pass/fail only. No prompts. Idempotent. Teardown at start AND end.
Use APP_SERVICE_DATABASE_URL and FAIL LOUDLY if it cannot connect —
do not fall back to another role silently.
[ ] All 5 validators in Task 2 return correct results on hand-built
known-good AND known-bad fixtures (not just known-good — a
validator that always returns True passes a positive-only test)
[ ] cusip_checksum specifically: assert it rejects a CUSIP with a
single transposed digit (the realistic error mode)
[ ] field_status is populated for EVERY registry field on every
created row — assert no row has a registry field missing from
its field_status dict
[ ] HAZARD ENSEMBLE PROOF: construct a fixture where the two models
would disagree (mock or use a real filing known to be ambiguous)
and assert extraction_confidence='needs_review' with both
answers recorded — this is the core assertion of this sprint
[ ] A hazard-field AGREEMENT case does not force needs_review purely
from the ensemble (isolate this from validator-triggered
needs_review to prove the ensemble isn't just always failing
closed)
[ ] source_char_start/end are populated and, for at least 3 rows,
slicing reference_filings.extracted_text at those offsets
contains text plausibly related to the extracted field (report
the actual substring, don't just assert non-null)
[ ] Correction logging: log_note_terms_correction produces a row
with target_type='note_terms', org_id NULL, readable under
app_service with no org context
[ ] Report Task 5's actual numbers inline in verify output — if
rows created = 0, FAIL (an extraction sprint that extracted
nothing is not a pass)
[ ] extraction_status column collision from Task 3 step 7: assert
whatever resolution Task 1/3 arrived at is internally consistent
— no filing should be left in a status that both pipelines
would interpret differently
