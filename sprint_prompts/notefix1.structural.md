NOTEFIX1 — four fixes found by the first real gold pre-fill: fee fields
become ranges, one bad field no longer rejects a whole note, a wider
hypothetical-example detector, and the gold verify's regression runs after
its own teardown. 5 tasks + verification.

The gold pre-fill (run e6da8fcd-b4e7-4300-9bac-48dee6e54697, batch gold-1,
gpt-5-mini, $1.00) filled 49 of 50 notes. The 50th failed because the model
correctly read "up to 0.40%" for a field the registry calls a scalar. Joe has
NOT started reviewing (0 gold values, all 50 candidates 'proposed'), so the
fix lands before any gold value depends on the old shapes.

CONFIRMED REAL FACTS, DO NOT RE-DERIVE (checked live 2026-10-13):
- Live economics fields and shapes: agent_commission_pct range,
  estimated_value_pct range, estimated_value_per_1000 range,
  fee_based_account_price range; price_to_public_pct scalar,
  proceeds_to_issuer_pct scalar, structuring_fee_pct scalar,
  other_fees_pct scalar, total_fees_pct scalar.
- The failure, stored in the run report for note 6380efa7-5a87-4202-8b6c-
  3bcaf34030aa: "schema validation failed: 1 validation error for
  NoteTermsReading structuring_fee_pct.value Decimal input should be ...
  input_value={'min': None, 'max': 0.4, 'bound': 'up_to'}". Only 5 values
  (rules and derivations) were saved for that note.
- Pre-fill quotes show bound wording on structuring_fee_pct ("a structuring
  fee of up to 0.85%"), other_fees_pct ("a referral fee of up to $7.50 per
  $1,000") and total_fees_pct. Proceeds to issuer is commonly stated as "at
  least" in these filings.
- note_gold_values: 0 rows. note_gold_candidates for gold-1: 50, all
  'proposed'. The pre-fill run's readings are immutable
  (note_term_readings_no_update trigger).
- Hypothetical-example-table detector fired on 1 of 138 fetched finals; nearly
  every final pricing supplement contains a hypothetical returns table.
- verify_goldset.py: 127/128. Its one FAIL was the nested verify_notefields.py
  run, which saw goldset's live fixtures; notefields standalone is 156/0.
- Baselines (do NOT re-run except as stated below): verify_notefields 156/156,
  verify_goldset 127/128 (the 1 = the nested-regression artifact).
- Claude models are ruled out. No OpenRouter. All calls through the LiteLLM
  proxy. gpt-5-mini needs max_tokens >= 32000.

THERE IS NO HUMAN AVAILABLE. Report findings, then continue immediately in the
same response. If uncertain, continue.

STANDING RULES: no interactive prompts. There is NO background-process
notification mechanism in this tool — nothing will ever notify you that a script
finished. Never wait for one. Run every command synchronously in the foreground.
Do NOT call any model for real and do NOT re-run the pre-fill — the operator
does. Write the verify script but DO NOT RUN IT. Never print a secret value.
Column names come from the live database, not this prompt.

=== TASK 1: DISCOVER ===
Report findings, then continue immediately.
  1a. Every place that validates a reading against the registry shape (the
      Pydantic NoteTermsReading model, schema.build_field_specs, normalize,
      the cascade, the review API) and exactly where one field's error
      becomes a whole-reading failure.
  1b. How the review screen chooses which pre-fill run to show for a batch, so
      a second gold_prefill run for gold-1 is picked up cleanly.
  1c. Which self-checks read the fields changing shape (price - fees ≈
      proceeds, estimated value < price) and how they must read a range.
  1d. Why the hypothetical-example detector misses: run it over the 138
      fetched finals in cohort 54024e47-096c-4120-bcf3-e3403ad2ef7b (read-only,
      text from R2 or reference_filings) and report the headings and table
      wording actually used per bank.

=== TASK 2: FEE FIELDS BECOME RANGES (decision C) ===
Change value_shape to 'range' for structuring_fee_pct, other_fees_pct,
total_fees_pct and proceeds_to_issuer_pct (a migration, and the registry
loader / docs/NOTE_FIELDS.md so they agree). price_to_public_pct stays a
scalar unless 1a/1d shows bound wording on it; if it does, report and change
it too. A single stated amount sets min = max (decision C). Update the
self-checks per 1c: price - fees ≈ proceeds uses the range ends that make the
check meaningful (state the rule in PROJECT_STATUS), and never fires on a
range whose bounds are both consistent.

=== TASK 3: ONE BAD FIELD NEVER REJECTS A NOTE ===
Validation becomes per field: a field that fails its shape is dropped from
that reading and recorded (field, error, raw value) on the reading's run
report and as a needs_review reason on the note; every other field is kept.
Additionally, a range-shaped value returned for a scalar field, or a scalar
returned for a range field, is COERCED when unambiguous (scalar -> min = max;
a range with min = max -> scalar) and recorded as coerced, not dropped.
A whole reading still fails only when the response is not parseable at all.

=== TASK 4: HYPOTHETICAL-EXAMPLE DETECTOR ===
Widen the detector from 1d's real wording (e.g. "Hypothetical Payment at
Maturity", "Hypothetical Examples", "Hypothetical Returns", "Examples of
Hypothetical Payments", "What Is the Total Return ... Assuming", tables of
hypothetical final values / percentage changes). Report the new hit rate over
the 138 finals per bank. It must still stay quiet on a document with no such
section (a product supplement without examples, a plain note).

=== TASK 5: GOLD VERIFY REGRESSION ORDER + STATUS ===
  - verify_goldset.py: run the verify_notefields.py regression only AFTER its
    own teardown has completed and re-read zero fixture rows.
  - Add to prefill_gold.py: --supersede, which allows a new gold_prefill run
    for a batch that already has one, only while the batch has zero gold
    values; the review screen then shows the newest completed run (per 1b).
    Refuse --supersede once any gold value exists for the batch.
  - Update docs/PROJECT_STATUS.md: the four shape changes, per-field
    validation and coercion, the detector hit rate, and the operator step
    "re-run the gold-1 pre-fill with --supersede".

=== VERIFICATION: apps/api/scripts/verify_notefix1.py ===
WRITE IT. DO NOT RUN IT. Pass/fail only, no prompts. MUST print
'TOTAL: N PASS, M FAIL' and exit non-zero on failure. Hydrate secrets from Doppler
over HTTPS at startup. Mock all model calls; real spend $0. Wrap setup, teardown
and every section so an exception is a [FAIL], never a crash; teardown at start
and end in FK child-before-parent order read from pg_constraint, then re-read
every touched table to confirm its exact pre-test count. note_term_readings has
an immutability trigger: disable/re-enable around the scoped delete and fail
loudly if rows remain. Every reading the test writes carries the test's run_id.

Verify-script rules — each one broke a real script:
  - check(passed, label, detail): condition FIRST; isinstance(passed, bool).
  - Every fixture value satisfies the live CHECK and FK constraints, read from
    pg_constraint (contype read as ::text).
  - Never derive a unique value from a slice of a fixture UUID.
  - RLS context on EVERY read. Expected counts derived from what the test
    created. An empty list never passes a check.
  - Any nested regression runs only after this script's own teardown.

Assertions:
  [Y] Task 1's four findings reported, including 1d's per-bank wording
  [Y] REPRODUCE FIRST: a mocked reader returning {'min': None, 'max': 0.4,
      'bound': 'up_to'} for structuring_fee_pct is accepted and stored as a
      range with the bound wording as its quote (before the change this
      rejected the whole note — show the old registry shape in the detail)
  [Y] The four fields are 'range' in the live registry and in NOTE_FIELDS.md;
      price_to_public_pct as decided in Task 2
  [Y] A single amount "0.40%" stores min = max = 0.40
  [Y] Per-field validation: a reading with one malformed field keeps every
      other field; the bad field is recorded with its error and the note gets
      a needs_review reason; an unparseable response still fails the note
  [Y] Coercion both ways is applied and recorded as coerced
  [Y] Self-checks: a consistent range-shaped fee set stays quiet; a crafted
      inconsistency fires with a reason
  [Y] Detector: fires on a crafted snippet for each new heading variant, stays
      quiet on a document without examples; the 138-final hit rate is printed
  [Y] --supersede creates a second gold_prefill run while the batch has no gold
      values, the review API then serves the newer run's readings, and
      --supersede is REFUSED (nothing written) once a gold value exists
  [Y] Regression: verify_notefields.py and verify_goldset.py each run ONCE, as
      flat subprocesses (180s timeout), AFTER this script's teardown; both 0 FAIL
  [Y] securities_global_note_terms row count unchanged
  [Y] Teardown leaves zero fixture rows; every touched table at its pre-test count
