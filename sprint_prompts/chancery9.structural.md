CHANCERY — PHASE 9 (contextual surfacing — the Documents panel).
4 tasks + verification. NOT a standalone search page — a reusable
component any entity/SPV/deal/transaction page can embed, showing
documents linked via Phase 5's linkage tables. Search remains a
SEPARATE, secondary, explicit action — not the primary way users
find documents. Semantic/vector search is explicitly OUT OF SCOPE
(that's Phase 11's INDEX/RETRIEVE work, not this phase) — any
search built here is basic metadata/filename matching only.

CONTEXT: document_entity_links and document_record_links (Phase
5) already support exactly the query this phase needs — Phase 5
explicitly built "list documents linked to a given entity" for
this purpose. REUSE it, do not rebuild.

STANDING RULES: org_id never from request body; no interactive
prompts; light theme (whites/creams, Navy #1B2B4B/Gold #C5A880)
matching every other screen.

=== TASK 1: Discover, don't assume ===
  (a) Re-read the REAL existing "documents linked to an entity"
      query/endpoint from Phase 5 — confirm its exact current
      signature and whether it already supports the generic
      record_links case (SPV/deal/transaction) or only entities —
      extend it if the generic case isn't covered yet, do not
      duplicate it.
  (b) Read the REAL current page structure for at least 3 record
      types this panel should embed into (entity detail page, SPV
      detail page, and one more — deal or a transaction view,
      whichever has the clearest existing page structure to embed
      into) — confirm where a new panel/tab would fit cleanly in
      each.
  (c) Re-confirm Phase 6's real UI conventions (reuse directly,
      same as Phase 6 did for its own conventions) — this panel
      should look and behave consistently with the rest of the
      admin/CRM UI, not introduce a new visual pattern.
Report all three findings before proceeding.

=== TASK 2: The reusable Documents panel component ===
Build a single, genuinely reusable component (e.g.
DocumentsPanel.jsx) taking a record type + record id as props:
  - For record_type='entity': use Phase 5's entity-linked-
    documents query.
  - For any other record_type (spv/deal/transaction/etc.): use
    the generic document_record_links query.
  - Display each linked document with basic metadata: filename,
    doc_family/category, current status (sorted/confirmed/
    pending_review/etc.), linked-by (system vs. a specific user),
    and upload date.
  - Clicking a document navigates to Phase 6's real review/confirm
    screen for that document (reuse, do not duplicate that
    screen's logic).
  - Handle the empty case cleanly (a record with zero linked
    documents shows a clear, unalarming empty state, not an error
    or a blank gap).

=== TASK 3: Embed the panel into real pages — prove reusability
===
Embed the SAME component (not three separate copies) into the
THREE real page types identified in Task 1b. This is the actual
point of the phase — a component built once, used in genuinely
different contexts, not a one-off screen.

=== TASK 4: Secondary, explicit basic search ===
Build a simple, separate search capability (its own page or a
clearly separate action, not blended into the panel above) — text
matching on filename, doc_family/category, and a basic ILIKE
match against extracted_text. This is NOT semantic search (Phase
11) — keep it simple and say so clearly in the code/UI copy if
relevant, do not oversell what this does.

=== VERIFICATION ===
Write verify_chancery9.py (apps/api/scripts/) — pass/fail only,
no interactive prompts, teardown-at-start and teardown-at-end.

Assertions to include:
  [Y] Report Task 1's three discovery findings explicitly
  [Y] The panel correctly returns linked documents for a real
      entity (via Phase 5's entity-link query)
  [Y] The panel correctly returns linked documents for a real
      generic record (e.g. an SPV, via document_record_links)
  [Y] A record with ZERO linked documents returns a clean empty
      result, not an error
  [Y] The SAME component is confirmed embedded in at least 3
      distinct real page types (verify via the actual page files,
      not just the component's existence)
  [Y] Basic search correctly matches on filename AND on a
      substring within extracted_text for a real seeded document,
      and correctly returns nothing for an unrelated search term
  [Y] A different org's user cannot see this org's linked
      documents or search results (test against the real
      app_service connection)
  [Y] npm run build exits 0
  [Y] No hardcoded Signature-palette hex in any new file
  [Y] Teardown: zero leftover rows

Report each assertion explicitly. Push when 100% pass — hold for
manual review regardless of tier.
