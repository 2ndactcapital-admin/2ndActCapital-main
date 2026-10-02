NOTE EXTRACTION B1 — the extraction engine, a hand-checked gold set, and the
evaluation harness that decides which models to use. Results are STAGED: B1
writes nothing to the security master. B2 (later) runs at scale and promotes.

YOU ARE THE SPRINT. Do this work yourself, now, in this session. Do NOT run
run_sprint.sh. Do NOT launch anything in the background. Run every command
synchronously in the foreground and read its output. Nothing will ever notify
you that something finished.

WRITE THE VERIFY SCRIPT BUT DO NOT RUN IT. The operator runs it. Do not run the
evaluation or the pilot either — build them, make them runnable with a dry-run
mode, and stop. The operator runs them and reviews the cost first.

=== PREREQUISITE ===
edgarpipelinea must be merged. Task 1a confirms its objects exist. If they do
not, STOP and report — do not rebuild them.

=== CONFIRMED REAL FACTS — do not re-derive ===

From sprint A (edgarpipelinea):
- portfolio.edgar_index_filings is the manifest, with the full status lifecycle
  (ready_for_extraction -> extraction_submitted -> extracted | needs_review |
  extraction_failed reserved for extraction), document_kind, detected_cusip
  (check digit validated), reference_filing_id, primary_issuer_cik.
- Fetched documents: portfolio.reference_filings, raw HTML and extracted text
  stored GZIPPED in R2 bucket hollisworks-docs; extracted_text is NULL for new
  rows. The existing extractor preserves character offsets into the raw HTML.
- Preliminary pricing supplements and product/underlying supplements are
  already excluded from ready_for_extraction by free rules.

The existing extraction system (built in the structured-notes thread):
- portfolio.securities_global_note_terms (payoff fields, source_char_start/end,
  extraction_confidence, routing_decision, bitemporal), note_terms_field_registry
  (the field definitions), services/note_terms_extraction.py, the review queue.
- 29 rows in document_field_corrections with target_type='note_terms' are MODEL
  DISAGREEMENTS (original = Haiku, corrected = Sonnet, corrected_by NULL) stored
  as if they were human corrections. Nothing has learned from them yet.

The AI layer:
- Every AI call goes through the LiteLLM proxy (v1.96.2, pinned — do not
  upgrade). The proxy loads LiteLLM's LIVE published price list, so cost
  tracking covers new models.
- public.ai_ensemble_configs: Model 1, Model 2 and a System One model per
  task_key, with version snapshots; public.ai_system_one_models: Jev
  ('typesafe-jev', pinned jev-1.13.0, available), called through the proxy's
  /typesafe pass-through. public.platform_model_catalog: the curated LLM list.
- Stored model values are proxy deployment names, never raw upstream ids
  (the proxy returns 400 on raw ids).
- Joe has ruled out Claude models for this task.

=== DECISIONS (Joe) — do not re-litigate ===

1. FIRST, MOVE THE 29 MODEL DISAGREEMENTS OUT of document_field_corrections into
   the new readings table (decision 2), tagged as model readings with their
   original models. Corrections must only ever hold human decisions.

2. EVERY READING IS STORED, with provenance. One new table holds each value any
   source produced for a note's field — rules, EdgarTools, Model 1, Model 2,
   Jev, escalation, or a human — with: source, deployment name, the model
   version the PROVIDER REPORTED, ensemble config id, prompt version, the
   source quote, character offsets into the raw HTML, tokens, cost, latency.
   Resolved values per note go to a STAGING results table, not to
   securities_global or securities_global_note_terms.

3. THE CASCADE, cheapest and most exact first:
   a. RULES for labeled fields: CUSIP (already detected), pricing / valuation /
      maturity dates, the issuer's estimated-value sentence, agent commission
      and fees, price to public, the fee-based (advisory) account price,
      denominations, and the NAMES of distribution participants in the plan of
      distribution, matched against the participants table (decision 11).
      Patterns anchor on their labels; validate everything (a bare
      9-character pattern is not a CUSIP).
   b. EDGARTOOLS (its Prospectus424B parser, which recognises structured-note
      pricing supplements and reads their key-terms table). Pin its version.
      Record its license. Feed it the STORED HTML — it must make ZERO requests
      to the SEC.
   c. TRIM with heading rules, not embeddings: keep the key-terms section, the
      payment-at-maturity / payoff description, coupon and autocall terms,
      observation-date schedules, estimated value and fees, dates, AND the
      (supplemental) plan of distribution section in full — it names the
      selling agents and distributors, splits the fees, and states any
      fee-based-account price. Drop risk factors, tax and ERISA boilerplate. Record the trimmed token count. If BOTH readers return null on
      a critical field, retry once with fuller text before giving up.
   d. TWO READERS: Model 1 and Model 2, called SEPARATELY through the proxy —
      never one model name load-balanced over two deployments — with NO
      fallback. Record the model the provider reports actually answered; a
      mismatch with the requested model is a failed reading.
   e. COMPARE IN CODE: normalise each field; if both readers agree AND the
      source quote exists verbatim in the filing AND the value appears in that
      quote, the field is verified at no further cost.
   f. JEV ONLY ON DISAGREEMENTS: one call per note carrying ALL of that note's
      disputed questions; ask about THE DOCUMENT (a choice between the
      candidate values, or a yes/no on a condition), never "which model was
      right"; criteria are GENERAL rules, never containing the expected answer;
      send only the relevant clauses; stay inside Jev's 32K limit. Required
      probability is higher for critical fields.
   g. ESCALATE what Jev cannot settle confidently to the escalation model
      (configurable). A CRITICAL field still unresolved -> needs_review.
   The skip-second-reader idea (skip Model 2 when rules/EdgarTools agree with
   Model 1 on every critical field and quotes verify) is NOT enabled in B1:
   run both readers always, and MEASURE how often skipping would have been
   safe. Joe decides after the pilot.

4. THE READERS' PROMPT AND SCHEMA:
   - The schema is GENERATED from note_terms_field_registry (one source of
     truth), extended with: estimated value, agent commission / fees, pricing
     date, underlyings, basket type, barrier observation (at maturity only
     vs continuous/daily), price to public, the fee-based / advisory-account
     price, total commissions and fees, and DISTRIBUTION as a LIST of
     participants — each with the name as stated, role (issuer-affiliated
     agent, distribution agent, dealer, placement agent, other) and fee type
     and amount (selling commission, structuring fee, platform or marketing
     fee). Never a single distribution-agent string. Do NOT reduce protection to a yes/no — keep a
     protection TYPE (full, buffer, barrier, none) plus its level. A buffer and
     a barrier must never share a field.
   - Field descriptions carry the trap rules, for example: buffer = losses begin
     only after the underlying falls more than X%; barrier = once breached, the
     full decline applies; principal is protected only if repayment at maturity
     does not depend on the underlying. Fix units and formats (percent as
     70.0, dates YYYY-MM-DD).
   - Every field: null if absent, plus a short EXACT source quote.
   - Structured output with json_schema where the model supports it (the price
     list's supports_response_schema), otherwise json_object — and ALWAYS
     validate with Pydantic model_validate_json. A failed validation is a
     failed reading, never a crash.
   - Fixed instructions and schema FIRST, the filing text LAST, so providers'
     repeated-prompt discounts apply. Never put two filings in one call.
   - Reasoning effort is a per-model setting.

5. CRITICAL FIELDS (for needs_review and Jev thresholds): protection type and
   level, barrier observation, principal conditionality, coupon type
   (contingent vs fixed) and memory, coupon barrier, autocall level and
   frequency, call type (automatic vs issuer), basket type, underlyings,
   maturity date, estimated value, total commissions and fees, and the
   fee-based-account price. Fees and estimated value together ARE the value
   comparison this whole system exists for.

6. MODELS. Register on the proxy, with keys referenced as os.environ/NAME, never
   written in code:
   - gpt-oss-120b via DeepInfra; gemini-2.5-flash-lite via Google;
     mistral-small (24B) via DeepInfra; gpt-5-nano and gpt-5-mini via OpenAI;
     deepseek-v3.2; one tiny model (e.g. Qwen2.5-7B) as a long shot.
   - Keys live ONLY in Doppler's prd_lite_llm config. Any whose key is missing
     is reported BLOCKED; the operator adds it.
   - Add each to platform_model_catalog as 'available' ONLY after a real call
     through the proxy succeeds — the same rule Jev followed.
   - No OpenRouter, and no litellm SDK calls to providers from application code.

7. THE GOLD SET. A table of hand-checked note values (reviewer id, timestamp)
   and a super-admin screen: pick a note, see the trimmed text beside every
   reading, confirm or correct each field. Only humans write gold values. A
   sampler proposes 50-100 notes stratified across issuers, years and product
   types, and DELIBERATELY includes trap cases found by keyword: downside
   threshold / trigger / buffer wording, memory coupons, worst-of baskets,
   daily observation, issuer calls vs automatic calls, conditional principal,
   notes sold through a distribution platform (e.g. iCapital Markets), and
   notes stating a separate fee-based-account price.

8. THE EVALUATION HARNESS (script plus a super-admin results grid). For each
   candidate model, and low vs medium reasoning effort on gpt-oss-120b, on the
   gold set: per-field accuracy, null rate, invented-value rate (quote not
   found in the filing), cost per note, latency. Also: how much of the needed
   text the trimming keeps (does the trimmed text contain each gold quote),
   EdgarTools' coverage and accuracy per field and per issuer, how often
   skip-second-reader would have been safe, Jev's accuracy on disagreements,
   the repeated-prompt discount actually observed, accuracy on the
   distribution and fee fields, and a TALLY of distribution participants by
   issuer and year (which channels appear, how often, with what fees). It
   also reports whether identical terms appear under different CUSIPs for
   different channels — a pricing comparison in itself. It reports; it does NOT
   choose models — Joe picks the ensemble afterwards in the picker.

9. THE PILOT RUNNER: about 2,000 ready notes across issuers and years through
   the full cascade with whichever ensemble is active. Reports disagreement,
   Jev, escalation and needs_review rates, and cost per note.

10. SPENDING. A hard spending cap per run, enforced in code from recorded
    costs: the run stops cleanly when it is reached. Every script has a dry-run
    mode that prints the planned calls and an estimated cost without calling
    anything.

11. DISTRIBUTION PARTICIPANTS are reference data, like the issuer table: a
    global table of participant names, alias spellings, and type
    (issuer affiliate, distribution platform, dealer, wealth manager, other),
    four RLS policies, super-admin writes. SEED it from the data: tally the
    agent names the rules find across the pilot's filings and propose entries;
    do not hard-code a list. Unmatched names are reported, never discarded.

OUT OF SCOPE: writing to securities_global or securities_global_note_terms;
batch mode; the nightly extraction job; changing manifest pipeline_status;
comparisons between notes; preliminary-vs-final term sheets.

=== FACTS THAT CHANGE DECISIONS ===
- RLS policies are per operation and need their context set; a missing policy
  and missing context both look like empty results or "row not found".
- Writes to global tables need SET LOCAL app.is_super_admin = 'true' inside a
  transaction (platform_scope()); never a session-level SET under the pooler.
- Column names come from the live database, not from this prompt.

=== TASK 1: DISCOVER ===
Report findings, then continue immediately.
  1a. Sprint A's objects exist (STOP if not); count ready_for_extraction rows.
  1b. note_terms_field_registry's actual fields, and how note_terms_extraction.py
      calls models today.
  1c. Which provider keys exist in prd_lite_llm (names only).
  1d. EdgarTools' current version and license, and whether Prospectus424B can
      parse from stored HTML with no network access.
  1e. Whether this LiteLLM version can guarantee no fallback per request, and
      whether provider responses report the model that answered.
=== TASK 2: MOVE THE 29 DISAGREEMENTS; CREATE THE READINGS, STAGING AND GOLD TABLES ===
Apply via MCP, verify each with a follow-up query, four RLS policies per table.
=== TASK 3: RULES, EDGARTOOLS, TRIMMING ===
=== TASK 4: READERS, COMPARISON, JEV, ESCALATION ===
=== TASK 5: MODEL REGISTRATION AND REAL-CALL VERIFICATION ===
=== TASK 6: GOLD SET SCREEN AND SAMPLER ===
=== TASK 7: EVALUATION HARNESS, PILOT RUNNER, RESULTS GRID ===
=== TASK 8: STATUS === update docs/PROJECT_STATUS.md.

=== VERIFY: apps/api/scripts/verify_noteextractb1.py ===
WRITE IT. DO NOT RUN IT. Pass/fail only. MUST print 'TOTAL: N PASS, M FAIL'
and exit non-zero on failure. Hydrate secrets from Doppler over HTTPS at
startup. Use fixture snippets and mocked provider responses wherever a real
call is not the point of the assertion; total real-call spend under $1.

Verify-script rules — each one broke a real script:
  - check(passed, label, detail): condition FIRST; isinstance(passed, bool).
  - Never derive any unique value from a slice of a fixture UUID.
  - Set RLS context on EVERY read.
  - Clear audit_log, assistant_activities and agent_proposals BEFORE deleting
    fixture users.
  - Expected counts are DERIVED from the fixtures actually created, never
    hard-coded.
  - Call the app on the SAME event loop (httpx.AsyncClient + ASGITransport,
    initialising its pool in that loop) — verify_modelresearch.py's pattern.
  - Source-code checks target the risky pattern, not a bare character.

Assertions:
  [Y] Task 1's findings reported
  [Y] The 29 disagreements: gone from document_field_corrections, present in
      the readings table with their original values and models; no other
      correction row changed
  [Y] The schema is generated from the registry (every registry field
      present); a buffer and a barrier cannot share a field; Pydantic rejects a
      wrong type and accepts null
  [Y] Rules on fixture snippets: each labeled field; a 9-character string that
      fails the CUSIP check digit is rejected
  [Y] Trimming keeps the plan-of-distribution section in full
  [Y] Distribution is extracted as a list: a fixture naming two participants
      with different fees yields two entries with their roles and fees; an
      unknown participant name is reported, not dropped
  [Y] The fee-based-account price and price to public are separate fields
  [Y] EdgarTools runs on stored HTML with zero SEC requests; version and
      license recorded
  [Y] Trimming on a fixture filing keeps the key terms, payoff and fee sentence,
      drops risk factors; the recall measure is correct on a fixture
  [Y] Two readers are two separate calls to two different deployments; a call to
      an unavailable deployment FAILS rather than being answered by another
      model; the provider-reported model is recorded
  [Y] The prompt prefix is identical across notes (same hash) and the filing text
      comes last; no call contains two filings
  [Y] A fabricated quote is rejected; a real quote's offsets map to the exact
      substring of the raw HTML
  [Y] Agreement plus a verified quote marks a field verified with no Jev call;
      a disagreement produces exactly one Jev call for the note, carrying every
      disputed question
  [Y] No Jev criterion contains a candidate value or a model name
  [Y] An unresolved critical field goes to needs_review; an unresolved
      non-critical field does not
  [Y] Gold values: super-admin entry persists (independent re-read) with the
      reviewer recorded; org admin and member get 403 on the identical request;
      no code path lets a model write a gold value
  [Y] The harness's metrics on a small fixture equal a hand calculation
  [Y] The spending cap stops a run cleanly; dry-run makes zero provider calls
  [Y] securities_global and securities_global_note_terms row counts are
      unchanged
  [Y] No provider key, OpenRouter route, or direct litellm SDK provider call
      appears in application code
  [Y] Teardown: fixture rows gone; exact before/after counts
  [Y] npm run build exits 0
