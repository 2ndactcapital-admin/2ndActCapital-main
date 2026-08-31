FEE MODULE — SPRINT fee40 (fee chat interface). 3 tasks +
verification. NO PART 1 SQL — confirmed live before writing this
prompt: assistant_conversations, ai_decision_log, and
document_field_corrections already exist and cover everything this
sprint needs (chat state, model-call logging, correction learning).
document_field_corrections has document_id/template_extraction_id
(nullable — confirm live) alongside a generic target_type/target_id
pair; reuse it for fee-schedule corrections via target_type='FEE_SCHEDULE'
rather than building a parallel table, UNLESS Task 1's discovery finds
document_id is NOT NULL, in which case report that as a finding and
propose the smallest fix (a nullable-column migration) rather than
building a duplicate correction-logging table.

THE CORE RULE, non-negotiable: the model NEVER computes a fee. It
emits a structured FeeSpec (JSON only, no prose) that a deterministic
resolver and validator process. Every dollar amount the advisor sees
on the confirmation screen must come from an actual call to fee35's
calculation engine against real, resolved data — never a number the
model generated directly. If this rule and a feature request ever
conflict, the rule wins; report the conflict rather than bending it.

CONTEXT, settled:
- fee34's validation service (services/fee_validation.py or wherever
  it actually lives — confirm in Task 1) is the gate a proposed
  schedule must pass before saving, exactly as it already gates
  manual schedule creation. This sprint does not reimplement any of
  those rules.
- fee35's engine (services/fee_calc.py) is the ONLY thing that
  produces the worked-example dollar figure. Called with real,
  resolved inputs (a real household's real balances via
  account_balances_daily / portfolio.positions), not synthetic
  placeholder numbers.
- Model calls route through whatever the standing model-resolution
  path is (check org_settings['ai.model.default'] / the TaskRouter
  pattern if it exists yet — report what you find, do not assume
  S27 TaskRouter has landed if it hasn't).
- Every model call this sprint makes must be logged to ai_decision_log
  with the actual model+version, not just "the model" — consistent
  with the standing rule that model version is logged, never just an
  alias.

OUT OF SCOPE: narrative generation (fee41 — this sprint produces a
FeeSpec and a validated schedule, not client-facing prose). Anything
that writes a fee_run or touches fee36. Any Altruist-API-shaped work.

STANDING RULES: org_id never from request bodies. Decimal everywhere
in anything downstream of the model (the model's own JSON output may
contain numbers as JSON numbers/strings; the resolver must convert to
Decimal immediately on the way in, never pass a JSON float through to
fee34/fee35). No interactive prompts in scripts. Light theme, 2nd Act
Signature palette from org_settings for any UI.

=== TASK 1: Discover, don't assume ===
Confirm live: assistant_conversations and ai_decision_log's actual
schema and how they're used elsewhere in the codebase (find at least
one existing caller pattern to follow, do not invent a new
convention). Confirm document_field_corrections' document_id
nullability. Confirm whether an org-level default model
(org_settings['ai.model.default']) or a TaskRouter already exists and
is callable, or whether this sprint needs to call a model directly —
report which, do not guess. Confirm fee34's validation service's real
import path and function signature, and fee35's engine's real function
signature for a single-account calculation. Report all of this before
writing any code.

=== TASK 2: NL -> FeeSpec -> resolver -> diff ===
1. Structured-output prompt: given a natural-language description of
   a fee arrangement, the model returns ONLY a JSON FeeSpec — schedule
   fields (rate_type, tier_method, tiers, billing_frequency, etc.
   matching fee_schedules' real columns from Task 1), proposed
   exclusions/discounts/credits, and an explicit `unresolved` array
   for anything ambiguous (do not let the model guess a valuation
   method or an ordering_policy it wasn't told — unresolved names
   real gaps). No prose, no markdown fencing — parse failures on
   malformed JSON must be handled cleanly, not crash the endpoint.
2. Deterministic resolver: turns any named entity/security/account
   references in the FeeSpec into real ids by querying the live
   tables (entities, portfolio.securities_global, accounts) — fuzzy
   matches go to a disambiguation list, not a silent best-guess pick.
3. Diff view: proposed FeeSpec fields vs. current state (for an edit)
   or vs. org defaults (for a new schedule), with unresolved fields
   visually distinct from resolved ones.

=== TASK 3: Validation gate + worked example + correction logging ===
1. Before save, run the proposed schedule through fee34's real
   validation service. Surface every validation error returned,
   mapped to the specific field, exactly as fee34's own admin UI
   would show them — do not write a second, different validation
   message set.
2. Worked example: once a schedule is far enough along to have real
   tiers, call fee35's engine against a REAL household's REAL current
   balances (the advisor's own client, or a designated demo household
   if none is selected) and show the actual computed fee. This is the
   screen the design doc calls "the single highest-value screen in
   the whole module" — advisors verify a schedule by recognizing a
   dollar figure, not by reading field values.
3. Every time an advisor edits a model-proposed field before saving,
   log it via document_field_corrections (or the Task-1-confirmed
   real path) with target_type='FEE_SCHEDULE_SPEC', the field name,
   original vs. corrected value. This is what lets recurring
   corrections get fed back later — this sprint logs them, it does
   not yet need to build the feedback loop that changes model
   behavior from them.

=== VERIFICATION ===
Write scripts/verify_fee40.py — pass/fail only, no interactive
prompts, app_service for RLS checks, teardown discipline.
Assert:
  1. A well-formed NL fee description produces a FeeSpec whose
     resolved schedule, when run through fee34's REAL validator,
     passes (on a fixture designed to be valid) and whose declared
     tiers/rates match what the description actually said.
  2. An ambiguous description (missing valuation method, e.g.)
     produces an `unresolved` entry for that field rather than a
     guessed default — prove the model was NOT permitted to silently
     fill it in.
  3. A malformed/non-JSON model response is handled cleanly (a typed
     error, not a crash) — simulate this rather than relying on a
     real model actually misbehaving.
  4. The worked-example dollar figure for a test schedule against a
     known household's known balances EXACTLY matches calling fee35's
     engine directly on the same inputs — this sprint's number IS
     fee35's number, never a separate computation that happens to
     agree.
  5. A schedule that fails fee34's validation surfaces the SAME error
     fee34's own validator would produce directly — not a paraphrase,
     not a generic message.
  6. Editing a model-proposed field before save writes a real
     document_field_corrections (or equivalent) row with the correct
     target_type, field name, and both values.
  7. Every model call in this flow produced a real ai_decision_log
     row naming the actual model and version used — not "the model"
     as a placeholder string.
  8. Cross-org isolation is preserved throughout (the resolver must
     never resolve an entity/account/security belonging to a
     different org).
  9. No table's row count differs from its pre-test count after the
     script exits.
Report actual results, then stop. Do not proceed to fee41 in this
same run.
