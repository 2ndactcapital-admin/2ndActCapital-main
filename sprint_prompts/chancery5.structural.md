CHANCERY — PHASE 5 (entity/transaction linkage + propose-new-
record). 4 tasks + verification. Builds on Phases 1-4 (documents/
document_extractions/document_drops, chancery_intake.py, the
K-1 template extraction from Phase 3). Later phases (review
screen, Workflow Manager integration, correction-learning,
contextual surfacing UI, VDR-to-deal-proposal) are explicitly
OUT OF SCOPE — this phase only builds linkage.

CONTEXT: document_entity_links (many-to-many, document<->entity,
UNIQUE per pair), document_record_links (polymorphic — record_
type text + record_id uuid, for linking to a transaction/SPV/
deal/anything else, UNIQUE per document+type+id),
document_link_proposals (proposed_link_type, proposed_name,
proposed_record_type, status default 'pending') all already
exist (Part 1 SQL applied directly, RLS enabled + policy on all
three in the same migration). "Folder" is interpreted as
documents appearing organized under their linked entity via
document_entity_links — NOT a separate folder hierarchy; flag
clearly in your report if this interpretation seems wrong given
what you find in the codebase.

STANDING RULES: org_id never from request body; no interactive
prompts; light theme if any UI is touched.

=== TASK 1: Discover, don't assume ===
  (a) Find and read the REAL entity-matching/dupe-check mechanism
      from Sprint 17's EntityPicker (find-or-create + dupe-check
      via POST /entities/stub, referenced in earlier design work)
      — its actual current function signature and matching logic
      (exact name match? fuzzy? something else?). This phase must
      REUSE this exact mechanism for matching an extracted party
      name against existing entities — do not build a second,
      different matching algorithm.
  (b) Confirm no existing document-linkage system already exists
      anywhere in this codebase (search broadly) that this might
      duplicate or should extend instead.
  (c) Confirm the REAL field name(s) in Phase 3's K-1
      mapped_fields structure that represent a party name (e.g.
      partner/shareholder name) — re-read the actual Phase 3 code,
      do not assume the exact field name from memory.
Report all three findings before proceeding.

=== TASK 2: Manual linkage endpoints ===
Build real endpoints (reuse existing auth/admin-gating patterns
from prior Chancery phases, do not invent new gating logic):
  - Link a document to one or more entities directly (creates
    document_entity_links rows)
  - Unlink a document from an entity
  - Link a document to another record type generically (e.g.
    record_type='spv', record_id=<uuid>) — creates a
    document_record_links row
  - List all links (entity + record) for a given document
  - List all documents linked to a given entity (the query that
    will power Phase 9's contextual surfacing later — build it
    correctly now even though the UI panel itself is a later
    phase)

=== TASK 3: Automatic entity linkage for K-1 documents ===
When a K-1 document (from Phase 3's template extraction)
completes successfully:
  - Extract the real party-name field (per Task 1c's finding)
  - Attempt to match it against existing entities using Task 1a's
    REAL, reused matching mechanism
  - On a confident match: automatically create a
    document_entity_links row (created_by = NULL, indicating
    system-created, distinct from a human manually linking)
  - On NO confident match: create a document_link_proposals row
    (proposed_link_type='entity', proposed_name=the extracted
    name) instead — NEVER auto-create a new entity, only propose

=== TASK 4: Proposal review — approve/reject ===
Build endpoints to:
  - List pending document_link_proposals for an org
  - APPROVE a proposal: if proposed_link_type='entity' and no
    matching entity was found, this should hand off to the REAL
    entity creation flow (Task 1a's EntityPicker mechanism) so a
    human creates the entity properly, THEN links the document to
    it — do not create a bare/incomplete entity row directly,
    route through the real creation path
  - REJECT a proposal: mark it rejected, no entity/link created
  - Both actions update document_link_proposals.status,
    reviewed_by, reviewed_at

=== VERIFICATION ===
Write verify_chancery5.py (apps/api/scripts/) — pass/fail only,
no interactive prompts, teardown-at-start and teardown-at-end.

Assertions to include:
  [Y] Report Task 1's three discovery findings explicitly
  [Y] Manually linking a document to TWO different entities
      succeeds (proves many-to-many, not single-entity)
  [Y] Manually linking a document to a generic record (e.g.
      record_type='spv') succeeds and is retrievable
  [Y] A K-1 document with a party name matching a REAL existing
      test entity is AUTOMATICALLY linked (document_entity_links
      row created with created_by=NULL)
  [Y] A K-1 document with a party name matching NOTHING creates a
      document_link_proposals row instead — NO entity was
      auto-created
  [Y] Approving a proposal correctly hands off to the real entity-
      creation mechanism (Task 1a) and then creates the link —
      NOT a bare/direct entity insert bypassing that flow
  [Y] Rejecting a proposal updates its status correctly with no
      entity or link created
  [Y] Listing documents linked to a given entity returns the
      correct set (the query Phase 9 will need)
  [Y] A different org's user cannot see this org's links or
      proposals (test against the real app_service connection)
  [Y] Teardown: zero leftover rows across all three new tables

Report each assertion explicitly. Push when 100% pass — hold for
manual review regardless of tier.
