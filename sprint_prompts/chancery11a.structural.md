CHANCERY — PHASE 11a (narrative metadata extraction). 4 tasks +
verification. No new external dependencies — pure extension of
the existing AI-extraction pattern (same TaskRouter/call_claude_
json mechanism as K-1 extraction and VDR analysis), applied to
the narrative document family instead. Full semantic INDEX/
RETRIEVE (Voyage embeddings + pgvector) is DELIBERATELY a
SEPARATE, later sprint (11b) — do not build any embedding/vector
logic here.

CONTEXT: document_narrative_extractions already exists (Part 1
SQL applied directly, RLS + policy in the same migration).
Confirmed real narrative categories (from Phase 2/4 discovery):
llc_formation, trust_instrument, will, estate_plan,
operating_agreement. document_entity_links (Phase 5) has a
nullable link_role text column, currently populated with
'k1_party' for K-1 auto-links — this phase should populate it
with MORE DESCRIPTIVE roles for narrative documents (e.g.
'trustee', 'beneficiary', 'managing_member') where the extraction
identifies a specific relationship, not a generic label.

STANDING RULES: org_id never from request body; no interactive
prompts; light theme if any UI touched.

=== TASK 1: Discover, don't assume ===
  (a) Confirm no existing narrative-extraction pattern exists
      anywhere in this codebase (only K-1/tabular extraction
      exists so far via textract_extraction.py — confirm this,
      do not assume).
  (b) Re-read the REAL current document_entity_links schema and
      Phase 5's real auto-link function signature — confirm how
      to extend/call it for narrative documents with a specific
      identified role, not just reuse it as-is if it needs a
      genuinely different call shape.
  (c) Confirm the REAL SORT hook pattern from the Phase 3
      completion sprint (chancery3b) — the exact mechanism that
      fires K-1 extraction when category='k1' — replicate this
      SAME pattern for narrative categories, do not invent a
      different hook mechanism.
Report all three findings before proceeding.

=== TASK 2: Narrative extraction service ===
Build apps/api/services/narrative_extraction.py:
  - Given a narrative document's extracted_text, call the AI (via
    the existing TaskRouter/call_claude_json mechanism — reuse,
    do not reinvent) to extract: a brief summary, a structured
    list of key provisions (provision type + description), key
    dates with descriptions, and key parties with their
    identified roles.
  - Store the result in document_narrative_extractions.
  - Handle a document with genuinely sparse/unclear content
    gracefully — a short or ambiguous narrative document may not
    yield much structure; do not force elaborate fake structure
    from thin content.

=== TASK 3: Enhanced entity linkage with real roles ===
When key_parties includes a name that matches a real existing
entity (reuse the SAME exact matching mechanism from Phase 5/K-1
auto-link, Task 1b's finding — do not build a second matcher):
  - Create/update a document_entity_links row with link_role set
    to the SPECIFIC identified role (e.g. 'trustee', not just
    'related') — created_by=NULL (system-created, same convention
    as K-1 auto-linking).
  - On no match: create a document_link_proposals row (Phase 5's
    existing propose-not-create mechanism), same discipline as
    every other uncertain-match case on this platform.

=== TASK 4: Wire into SORT ===
Replicate Task 1c's real K-1 hook pattern for narrative
categories: when SORT classifies a document into ANY of the
confirmed narrative categories (llc_formation/trust_instrument/
will/estate_plan/operating_agreement), automatically fire this
extraction as part of that same flow.

=== VERIFICATION ===
Write verify_chancery11a.py (apps/api/scripts/) — pass/fail only,
no interactive prompts, teardown-at-start and teardown-at-end.

Assertions to include:
  [Y] Report Task 1's three discovery findings explicitly
  [Y] A real, generated test narrative document (e.g. a simple
      trust instrument naming a real trustee) is correctly
      extracted: summary present, at least one real provision, at
      least one real key party with a specific role
  [Y] A key party matching a real seeded entity is auto-linked
      with the SPECIFIC identified role (not a generic label) —
      created_by=NULL
  [Y] A key party matching NOTHING creates a real
      document_link_proposals row, no entity/link auto-created
  [Y] A document with genuinely sparse content does not produce
      forced/fake elaborate structure — report the actual honest
      behavior
  [Y] SORT correctly fires this extraction for a narrative
      category and correctly does NOT fire it for a tabular
      category (e.g. k1) — proves the hook is scoped correctly
      in both directions
  [Y] A different org's user cannot see this org's narrative
      extractions (test against the real app_service connection)
  [Y] Teardown: zero leftover rows

Report each assertion explicitly. Push when 100% pass — hold for
manual review regardless of tier.
