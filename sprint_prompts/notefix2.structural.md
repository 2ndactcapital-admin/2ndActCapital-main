NOTEFIX2 — fix the systematic extraction errors found by checking all 50 gold-1
notes against their filings: affiliated agents, junk underlyings, buffer vs
barrier, digital payouts, blank defaults, fee totals, numbers lifted from
hypothetical examples, derived fields from the wrong reading; plus a way to see
the second-reader findings inside the review screen. 9 tasks + verification.

Context: the gold-1 pre-fill (run 683e1565-db22-400f-bdd6-cb5e3f04bfe9,
gpt-5-mini, 50 notes) was checked field by field against each SEC filing. About
1,440 fields matched; 237 were flagged (54 critical). Joe reviewed the findings
and agreed. They are in docs/gold/gold1_second_reader_v1.json (operator places
it). The errors are mostly NOT model capacity: they are missing definitions,
missing rules and missing checks. This sprint fixes those so cheap models can
pass. It does NOT write gold values, does NOT run any model for real, and does
NOT touch securities_global_note_terms.

CONFIRMED REAL FACTS, DO NOT RE-DERIVE (checked live 2026-10-14):
- note_gold_values: 0 rows. note_gold_candidates gold-1: 50, all 'proposed'.
- portfolio.distribution_participants exists with columns id, canonical_name,
  participant_type, aliases, status, observed_count, notes, created_by,
  created_at, updated_at — and 0 rows. No affiliation column.
- Registry fee fields structuring_fee_pct, other_fees_pct, total_fees_pct,
  proceeds_to_issuer_pct are 'range' (notefix1, applied live).
- The hypothetical-example detector finds those sections in 132 of 138 finals
  (notefix1, docs/NOTEFIX1_DISCOVERY.md).
- Per-field validation and coercion exist (notefix1): one bad field never fails
  a note.
- note_term_readings.origin CHECK allows: cascade, evaluation,
  migrated_correction, gold_review. note_extraction_runs.run_kind CHECK
  includes gold_prefill and evaluation.
- Baselines (do NOT re-run except as stated): verify_notefields 156/156,
  verify_goldset 128/128 standalone (takes ~7 minutes), verify_notefix1 passes
  with the 600s regression timeout.
- Claude models are ruled out for extraction. No OpenRouter. All calls through
  the LiteLLM proxy. gpt-5-mini needs max_tokens >= 32000.

THE FINDINGS (counts from docs/gold/gold1_second_reader_v1.json):
  F1 Affiliated broker-dealer labelled distribution_agent or dealer (~30
     notes): BofA Securities / BofAS, MLPF&S, J.P. Morgan Securities (JPMS),
     Morgan Stanley & Co. (MS & Co.), Morgan Stanley Smith Barney / Morgan
     Stanley Wealth Management, Goldman Sachs & Co. (GS&Co.), Barclays Capital
     Inc., UBS Securities LLC, UBS Financial Services Inc., Citigroup Global
     Markets (CGMI). Unaffiliated platforms/dealers named in the plan of
     distribution are often missing.
  F2 Junk underlyings (~25 notes): a table header ("CUSIP", "Barrier Value"), a
     definition sentence ("The closing level of the underlying on the final
     valuation date", "The underlying index with the lesser index percent
     change") or one whole sentence listing all underlyings, emitted as an
     extra list entry. Also placeholder initial levels lifted from a
     hypothetical table (#49: 26000 / 2500 / 90 / 45).
  F3 Buffer vs barrier (critical): BofA "Threshold Value" and Goldman "trigger
     buffer level" notes lose 1:1 from the first dollar below the level —
     these are BARRIERS — but were read as buffers (#6, #7, #24). A "buffer
     rate" (111.11%, 117.65%) was stored as a second buffer_pct (#22, #42).
     An 85% level in a buffered note stored as barrier_pct (#30).
  F4 Digital / fixed payouts in the wrong field: a digital percentage stored
     as participation_rate (#8, #27); a fixed final-date premium stored as
     cap_pct (#15, #19, #35); a threshold settlement amount as cap (#30).
  F5 Blank where a default applies: currency (22), coupon_memory (16),
     no_call_months (16), calculation_agent (20), call_type when there is no
     call (#35, #37, #38).
  F6 Fee totals: only an underwriting discount stated but total_fees_pct blank
     (#1); total 9.26% vs a 3.75% commission (#36).
  F7 Numbers lifted from the wrong place: coupon 6% where the filing states
     7.50% (#46) and 11.00% (#49); trade date read as maturity (#2); a filing
     typo date ("January 28, 2018 (the pricing date)") used for tenor (#1).

THERE IS NO HUMAN AVAILABLE. Report findings, then continue immediately in the
same response. If uncertain, continue.

STANDING RULES: no interactive prompts. There is NO background-process
notification mechanism in this tool — nothing will ever notify you that a script
finished. Never wait for one. Run every command synchronously in the
foreground. Do NOT call any model for real. Write the verify script but DO NOT
RUN IT. RLS policies in the same migration as any new table. Never print a
secret value. Column names come from the live database, not this prompt. UI:
light theme, whites and creams, matching the existing admin screens.

=== TASK 1: DISCOVER ===
Report findings, then continue immediately.
  1a. Which code path emits the junk underlyings entries (the model reader, the
      rules list parser, EdgarTools, or a merge step) — show one real example
      from run 683e1565 with its reader/origin metadata.
  1b. How distribution participants are matched today and where the role is
      set.
  1c. Where derived fields (tenor_months, max_principal_loss_pct, the
      estimated-value unit) read their inputs: a single reading, or the
      resolved/agreed value.
  1d. Whether the hypothetical-example detector exposes character spans that
      quote verification and rules can use to exclude text.
  1e. Exactly how registry descriptions reach the reader prompt, and whether
      any few-shot examples exist.
  1f. Whether any payoff recomputation self-check exists and what it compares.

=== TASK 2: AFFILIATED AGENTS (F1) ===
Add affiliated_issuer_group (text, nullable) to distribution_participants
(migration). Seed the participants listed in F1 with canonical names, aliases
(e.g. "BofAS", "BofA Securities, Inc.", "Merrill Lynch, Pierce, Fenner & Smith
Incorporated", "MLPF&S", "JPMS", "MS & Co.", "MSSB", "Morgan Stanley Wealth
Management", "GS&Co.", "CGMI"), participant_type and affiliated_issuer_group
matching structured_note_issuers.issuer_group names (read them live). Rule: a
distribution member that matches a participant affiliated with the note's own
issuer group is role issuer_affiliated_agent regardless of the reader; the
change is recorded as a rule correction. Unknown names are still reported,
never dropped. The calculation agent is matched the same way.

=== TASK 3: LIST HYGIENE (F2) ===
Validate list members before they become readings: drop (and record as
dropped, with the reason) an underlyings member whose name is a known table
header, a definition sentence (no ticker AND no initial level AND name longer
than ~8 words, or starting "The closing level", "With respect to", "For each",
"The underlying ... with the", etc.), or a single member whose name lists
several underlyings. Cross-check initial levels against the key-terms table
when one exists. Same hygiene for distribution members.

=== TASK 4: DEFAULTS AND DERIVATIONS (F5, F6, F7 typo) ===
  - Defaults, each recorded as derived with the rule name, only when the
    filing says nothing to the contrary: currency 'USD'; call_type 'none' when
    no call/redemption language exists; coupon_memory false when a contingent
    coupon exists and no memory/accrual-of-missed-coupons language exists;
    no_call_months from the first call observation date minus the issue date
    when a schedule exists.
  - Total fees: when only an underwriting discount / agent's commission is
    stated, total_fees_pct = that amount (derived). The existing price − fees ≈
    proceeds check proposes the corrected total when the stated total fails it.
  - Every derived field (tenor_months, max_principal_loss_pct, the
    estimated-value unit, no_call_months) computes from the RESOLVED value of
    its inputs (the cascade's agreed value), never from a single reading; if
    the inputs disagree, the derived field is left null and needs_review.

=== TASK 5: HYPOTHETICAL SPANS AND COUPON CROSS-CHECK (F2 levels, F7) ===
  - For coupon_rate_pa, underlyings initial_level, pricing/issue/maturity
    dates and barrier/buffer levels, a reading whose quote lies ONLY inside a
    detected hypothetical-example section is rejected for that field (recorded
    with reason 'hypothetical_section'). It may still be used by the payoff
    check.
  - Coupon cross-check: when a per-period coupon amount and frequency are
    stated, coupon_rate_pa must equal amount × periods per year / denomination
    (±0.01%); a mismatch is needs_review with both numbers.
  - Maturity must be after pricing; a date equal to the trade/pricing date is
    rejected for maturity_date and final_valuation_date.

=== TASK 6: FIELD DEFINITIONS AND EXAMPLES (F3, F4) ===
Update these registry descriptions (used verbatim in the reader prompt) and
docs/NOTE_FIELDS.md so they agree:
  - barrier_pct: "Level (as % of initial) below which the investor bears the
    full decline from the initial level, from the first dollar. Use this even
    when the filing calls it a 'threshold value', 'trigger value', 'trigger
    buffer level' or 'downside threshold'. Test: at a final level just below
    it, does the investor lose (initial − final)/initial? Then it is a barrier."
  - buffer_pct: "The size of the decline (as %) the investor is protected
    from: losses equal only the decline BEYOND this amount. 'Buffer amount:
    10%' → 10. Never the buffer rate or downside gearing (e.g. 111.11%)."
  - protection_type: "buffer only when losses start after the buffer amount;
    barrier when crossing the level exposes the full decline."
  - participation_rate: "Upside participation in the underlying's gain only.
    Never a digital or fixed percentage."
  - cap_pct: "Maximum return when the upside is participation-based. Not a
    fixed premium or digital return."
  - fixed_payout_amount / fixed_payout_threshold_pct: "A fixed amount per
    $1,000 paid when the underlying ends at or above a threshold (digital,
    'contingent digital return', 'step', fixed final-date premium), and that
    threshold as % of initial."
  - distribution role issuer_affiliated_agent: "the issuer's own broker-dealer
    affiliate (the filing says 'an affiliate of ...' or 'wholly owned
    subsidiary')."
Add a small few-shot block to the reader prompt built from real corrected
cases in docs/gold/gold1_second_reader_v1.json (one BofA threshold-value
barrier, one buffer-rate note, one digital, one affiliated agent), each with
the filing quote and the correct values. Keep the block under ~1,500 tokens.

=== TASK 7: PAYOFF CHECK FOR PROTECTION TYPE (F3) ===
Implement (or extend, per 1f) a self-check that recomputes the payment at
maturity from the resolved protection fields at final levels just below and
above each level (e.g. 59.99 and 60.01 of initial for a 60 level) and compares
it with the filing's hypothetical table rows when present. A mismatch is
needs_review naming the field and both numbers. It must catch a barrier read
as a buffer (59.99 → $599.90 in the filing vs $999.90 computed).

=== TASK 8: SECOND-READER FINDINGS IN THE REVIEW SCREEN ===
Load docs/gold/gold1_second_reader_v1.json into a new table
portfolio.note_gold_second_reader (reference_filing_id, field_key, prefill
jsonb, suggested jsonb, evidence text, severity, kind, source text, loaded_at;
RLS global pattern; idempotent loader script keyed on filing + field). The
review screen shows, on each flagged field, a "Second reader" note with the
suggestion and the evidence quote, and a per-note count. It NEVER writes
note_gold_values; the reviewer still confirms, corrects or marks absent.
Add a filter in the batch view: notes with critical second-reader flags first.

=== TASK 9: STATUS ===
Update docs/PROJECT_STATUS.md: F1–F7 and what now handles each (rule,
default, definition, check), the new table and the review-screen note, and the
operator step after merge: re-run an evaluation (not a pre-fill) on gold-1
once gold values exist, then score_against_gold.

=== VERIFICATION: apps/api/scripts/verify_notefix2.py ===
WRITE IT. DO NOT RUN IT. Pass/fail only, no prompts. MUST print
'TOTAL: N PASS, M FAIL' and exit non-zero on failure. Hydrate secrets from
Doppler over HTTPS at startup. Mock all model calls; real spend $0. Wrap setup,
teardown and every section so an exception is a [FAIL], never a crash;
teardown at start and end in FK child-before-parent order read from
pg_constraint, then re-read every touched table for its exact pre-test count.
note_term_readings has an immutability trigger: disable/re-enable around the
scoped delete and fail loudly if rows remain. Every reading the test writes
carries the test's run_id and an origin the live CHECK allows ('cascade').
Seeded affiliate participants are real reference data, not fixtures: teardown
must NOT delete them.

Verify-script rules — each one broke a real script:
  - check(passed, label, detail): condition FIRST; isinstance(passed, bool).
  - Every fixture value satisfies live CHECK and FK constraints (pg_constraint,
    contype read as ::text). Never invent an origin, status or role.
  - Never derive a unique value from a slice of a fixture UUID.
  - RLS context on every read. Expected counts from what the test created.
    An empty list never passes a check.
  - Regex checks against docs must match the doc's real formatting (bold
    markers included) — print the matched text.
  - Nested regressions run only after this script's own teardown, with a 600s
    timeout each.

Assertions (use the real cases as fixtures; reproduce the error FIRST, then
show it fixed):
  [Y] Task 1's six findings reported
  [Y] F1: a BofA note listing "BofAS" as distribution_agent → resolved role
      issuer_affiliated_agent, recorded as a rule correction; an unknown dealer
      is reported, not dropped; the affiliates are seeded with
      affiliated_issuer_group matching live issuer_group names
  [Y] F2: members named "CUSIP", "Barrier Value", "The closing level of the
      underlying on the final valuation date" and a one-sentence list are
      dropped with reasons; real members (NDX 13,112.65, SPX 3,750.77) kept
  [Y] F2/F7: initial levels and a coupon quoted only from a hypothetical
      section are rejected with reason 'hypothetical_section'
  [Y] F3: a "Threshold Value 60%" note whose hypothetical row pays $599.90 at
      59.99 → protection_type barrier, barrier_pct 60, buffer_pct null; a
      "buffer rate 111.11%" never becomes buffer_pct; the payoff check flags a
      fixture still marked buffer
  [Y] F4: "Digital Percentage: 40.00%" → fixed_payout fields, not
      participation_rate; a fixed final-date premium never becomes cap_pct
  [Y] F5: currency USD, call_type none, coupon_memory false, no_call_months
      from the schedule — each derived with its rule name, and NOT applied
      when the filing states otherwise (a EUR note stays EUR)
  [Y] F6: underwriting discount only → total_fees_pct derived; a stated total
      failing price − fees ≈ proceeds is flagged with the proposed total
  [Y] F7: the 2018 typo case: tenor computed from the resolved 2019 pricing
      date (≈84 months), or null + needs_review when pricing readings disagree;
      a maturity equal to the trade date is rejected; coupon 6% vs a stated
      $18.75 quarterly on $1,000 → needs_review (7.50%)
  [Y] The reader prompt contains the new definitions verbatim and the few-shot
      block (token count printed, under ~1,500)
  [Y] Second reader: the loader imports gold1_second_reader_v1.json (237
      rows), is idempotent (a second run adds 0), the review API returns the
      note on a flagged field, and no note_gold_values row is written
  [Y] Permission gate on the review API's second-reader data: org admin
      refused, super-admin admitted, on the identical request
  [Y] Regression: verify_notefields.py, verify_goldset.py and
      verify_notefix1.py each run ONCE as flat subprocesses (600s), AFTER
      teardown; all 0 FAIL
  [Y] securities_global_note_terms row count unchanged
  [Y] Teardown leaves zero fixture rows; seeded affiliates and second-reader
      rows remain; every touched table at its pre-test count otherwise
