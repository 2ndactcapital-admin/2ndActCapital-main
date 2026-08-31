FEE MODULE — SPRINT fee41 (narrative generation). 3 tasks +
verification. Part 1 SQL (fee_narrative_templates, fee_narratives) is
already applied by Joe directly via Supabase MCP — confirm it live
before writing any code.

CONTEXT, settled:
- household_id is on fee_narratives deliberately. Golden-source
  precedence (fee32's household-level override on top of
  resolve_precedence) means the valuation-method language for the
  SAME schedule can genuinely differ across two households — one
  reported to the client via Addepar, one via the org default. Do not
  treat this column as optional or derive it solely from the schedule.
- input_hash MUST cover both the schedule's full state (fields, tiers,
  exclusions, discounts, credits it references) AND the resolved
  precedence set for that household at render time. A household
  switching reporting platforms after a narrative was rendered must
  correctly mark that narrative is_stale — hash it into staleness, do
  not treat precedence as out of scope for what "the schedule changed"
  means.
- fee40's F40-I finding applies here too: docs/schema_snapshot.sql
  will not capture this sprint's CHECK constraints or RLS policies.
  Follow fee40's precedent — if this sprint's migration needs anything
  beyond what Part 1 already applied, record it as idempotent,
  self-verifying Python (scripts/_apply_fee41_part1.py style), not a
  bare .sql file nobody re-checks.
- Numeric-invariance is the ONLY thing that makes an LLM polish pass
  safe here. Extract every number and every defined term from the
  polished output; assert set-equality against the deterministic
  render; on ANY mismatch, discard the polish and use the
  deterministic text unpolished. This is not a nice-to-have — a
  polished agreement that silently drops or alters a number is a
  contract defect, not a copy-editing quirk.

OUT OF SCOPE: actually attaching a rendered narrative to a signed
document in Chancery (that integration point can be noted as a TODO,
not built). ADV Part 2A comparison beyond a stub — adv_check_status
defaults to UNCHECKED and this sprint does not need a real ADV source
loaded to function; report it as a real gap, don't fake a comparison
against data that doesn't exist yet. Anything Altruist-API-shaped.

STANDING RULES: org_id never from request bodies. Decimal everywhere
any dollar figure or rate passes through template substitution — a
rate must render as "1.00%" or "100 bps" from a real Decimal value,
never a string concatenation that could silently drop a digit. No
interactive prompts. Light theme for any UI, 2nd Act Signature
palette from org_settings.

=== TASK 1: Discover, don't assume ===
Confirm live: both new tables exactly as deployed. Confirm fee32's
actual household-precedence resolution function/table
(portfolio_precedence_household_overrides, resolve_precedence) and
its real current signature — do not assume this prompt's paraphrase
of it is current, fee32 may have evolved since. Confirm fee34's
fee_schedules/fee_schedule_tiers/fee_exclusions/fee_discounts/
fee_credits real shapes for token substitution. Report findings,
including exactly what "the resolved precedence set for a household"
concretely consists of as data (so input_hash can be computed
correctly) before writing any code.

=== TASK 2: Deterministic template engine ===
Token-substitution renderer: given a fee_schedule (with its tiers/
exclusions/discounts/credits) and a household's resolved precedence
set, render fee_narrative_templates.body_template into
rendered_text. Every token must resolve to a real value — a template
referencing a field the schedule doesn't have (e.g. a tier token on
an untiered FLAT schedule) must fail loudly at render time, not
silently emit a blank or a template artifact. Compute input_hash from
the actual schedule state + precedence set (Task 1's finding), not a
placeholder. Support versioned templates (template_code + version,
per Part 1) so an org can update its house language without
retroactively changing already-rendered, already-delivered narratives.

=== TASK 3: Optional LLM polish + staleness ===
1. Numeric-invariance-gated polish: send the deterministic render to
   the model for prose polish only; extract every number/defined term
   from both the input and the output; assert set equality; on
   mismatch, log the divergence and return the UNPOLISHED deterministic
   text, never the altered one. This must be provably enforced, not a
   prompt instruction alone — same standard as fee40's grounding check
   (verified independently of what the model was told to do).
2. Staleness: a fee_narratives row's is_stale flips to true when
   either the schedule's own state changes (fee34 already versions
   schedules — a new version means old narratives against the retired
   version stay valid for what they described, but a narrative against
   a DRAFT-then-edited-in-place schedule must go stale) or the
   household's resolved precedence set changes. Implement the actual
   check, not a documentation comment describing when it should
   happen.
3. adv_check_status stays UNCHECKED by default; wire the field and its
   CHECK constraint correctly but do not fabricate an ADV comparison
   against data that doesn't exist — report this as an explicit,
   named gap in your final report, matching fee38's TLH-tax-alpha
   pattern of separating "not built" from "silently faked."

=== VERIFICATION ===
Write scripts/verify_fee41.py — pass/fail only, no interactive
prompts, app_service for RLS checks, teardown discipline.
Assert:
  1. Both tables deployed, RLS on, expected constraint/policy shape.
  2. A schedule with graduated tiers renders correctly into a template
     referencing tier tokens; the same template against a FLAT
     schedule with no tiers fails loudly rather than emitting blanks.
  3. Two households on the same schedule but different resolved
     precedence (e.g. one Addepar, one org-default) produce narratives
     with genuinely different valuation-method language — prove this
     is real, not a copy-paste with one field swapped.
  4. Editing a DRAFT schedule in place correctly marks any narrative
     referencing it as is_stale; a narrative against an already-
     APPROVED, unedited schedule does not go stale on unrelated
     activity.
  5. A household's precedence override changing correctly marks that
     household's existing narratives is_stale, independent of whether
     the schedule itself changed.
  6. The numeric-invariance gate: a polish that preserves every number
     and defined term is accepted; a polish that alters, drops, or
     adds a number is REJECTED and the deterministic text is returned
     instead — prove this with an actual mismatching case, not just
     the passing one.
  7. A rate renders with full Decimal precision preserved (no silent
     truncation or float artifact) in the final text.
  8. Cross-org isolation on both tables via app_service.
  9. No table's row count differs from its pre-test count after the
     script exits.
Report actual results, including the honest gap on adv_check_status,
then stop. Do not proceed to fee42 in this same run.
