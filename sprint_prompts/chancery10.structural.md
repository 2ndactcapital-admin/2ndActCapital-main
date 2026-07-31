CHANCERY — PHASE 10 (VDR upload → deal-creation proposal). 4
tasks + verification. Marketplace-specific — this is the intake
mechanism FOR the backlogged Deal Diligence Engine, not a
separate capability. Introduces AGGREGATE reasoning across an
entire document batch, genuinely different from every prior AI
call in this platform, which has been per-document.

CONTEXT: vdr_deal_proposals already exists (Part 1 SQL applied
directly, RLS + policy in the same migration) — proposed_fields
is a flexible jsonb column since the exact real deals schema is
not yet confirmed (Task 1 must confirm it). created_deal_id has
no FK constraint yet (deliberately, pending Task 1's schema
confirmation) — add one if warranted once the real deals table
structure is confirmed.

Reuses Phase 1's batch-drop mechanism (document_drops) directly
— a VDR upload IS a multi-file drop, nothing new needed there.

STANDING RULES: org_id never from request body; Decimal for any
monetary figures if the deal schema includes them; no interactive
prompts; light theme if any UI touched.

=== TASK 1: Discover, don't assume ===
  (a) Confirm the REAL, current deals table schema (referenced
      throughout this project via api.js's createDeal/getDeal/
      listDeals but not independently re-verified this session)
      — exact columns, which are required vs. optional, exact
      types. proposed_fields must be designed to match this real
      shape, not a guessed one.
  (b) Confirm the REAL createDeal service/endpoint (routers/
      marketplace.py or wherever it actually lives) — its exact
      real signature and validation rules. This phase MUST reuse
      this exact mechanism to create an approved deal — never a
      second, parallel deal-creation path.
  (c) Confirm there is no existing aggregate-cross-document
      analysis pattern anywhere in this codebase already (search
      broadly) — this phase introduces a genuinely new capability
      shape, confirm it doesn't duplicate something that exists.
Report all three findings before proceeding.

=== TASK 2: Aggregate VDR analysis ===
Build apps/api/services/vdr_analysis.py:
  - Given a document_drop_id, aggregate the extracted_text from
    EVERY document in that drop (via document_extractions).
  - Call the AI (via the existing TaskRouter/call_claude_json
    mechanism — reuse, do not reinvent) to identify deal-level
    fields matching Task 1a's REAL deals schema (e.g. deal name/
    sponsor, asset class, deal type, a brief thesis/summary) from
    the aggregated content.
  - Store the result as a vdr_deal_proposals row (status=
    'pending') — NEVER auto-create a deal. This is a proposal
    only, same discipline as every other propose-not-create
    pattern on this platform.
  - If the AI cannot confidently identify enough real fields to
    propose (e.g. the documents don't clearly describe ANY single
    deal), report this honestly rather than proposing a low-
    confidence/mostly-empty record — your judgment on what
    "confident enough" means, but do not force a proposal from
    weak signal.

=== TASK 3: Proposal review — approve/reject ===
Build endpoints to:
  - List pending vdr_deal_proposals for an org
  - APPROVE: call Task 1b's REAL createDeal mechanism with the
    approved (possibly human-edited) fields, record the resulting
    real deal's id in created_deal_id, then link EVERY document
    in the original drop to the new deal via Phase 9's proven
    document_record_links mechanism (record_type='deal')
  - REJECT: mark rejected, no deal created, no links made
  - Both update status/reviewed_by/reviewed_at

=== TASK 4: Minimal UI entry point ===
A simple way to mark a document-drop upload as "this is a VDR for
a new deal" (vs. a normal document drop) — could be as simple as
a checkbox/flag on the existing upload UI that, when checked,
triggers Task 2's analysis after the drop completes. Reuse the
existing upload UI, do not build a separate VDR-specific upload
screen. A minimal proposal-review UI (list pending proposals,
approve/reject) — check if the existing document_link_proposals
review UI from Phase 5 can be extended/reused for this different
proposal type, or if a small separate addition is genuinely
warranted; report which you built and why.

=== VERIFICATION ===
Write verify_chancery10.py (apps/api/scripts/) — pass/fail only,
no interactive prompts, teardown-at-start and teardown-at-end.

Assertions to include:
  [Y] Report Task 1's three discovery findings explicitly
  [Y] A real, multi-document test VDR (e.g. 2-3 documents with
      genuine, consistent deal-describing content) produces a
      vdr_deal_proposals row with real, correctly-identified
      fields matching the deals schema — NOT a real deal created
      yet
  [Y] Approving a proposal creates a REAL deal via Task 1b's
      actual createDeal mechanism (confirm it appears in the real
      deals table), records created_deal_id correctly, and links
      ALL documents from the original drop to it via
      document_record_links
  [Y] Rejecting a proposal creates NO deal and NO links
  [Y] A document set with weak/inconsistent/non-deal content does
      NOT force a low-confidence proposal (test this honestly —
      confirm the actual behavior, whatever Task 2 determined
      "not confident enough" means)
  [Y] A different org's user cannot see this org's VDR proposals
      (test against the real app_service connection)
  [Y] Teardown: zero leftover rows, including any real deal record
      created during testing

Report each assertion explicitly. Push when 100% pass — hold for
manual review regardless of tier, given this is the first
aggregate-cross-document AI capability in the platform.
