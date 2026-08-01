CHANCERY — PHASE 11b (semantic INDEX + RETRIEVE, org-configurable
embedding model). 4 tasks + verification. pgvector is ALREADY
CONFIRMED ENABLED (v0.8.0) — do not re-attempt enabling it, just
verify it's still there. Voyage AI credentials should now be
provisioned — check for real access before proceeding.

NEW REQUIREMENT — org-configurable embedding model (Mini-Bedrock
pattern): each org should be able to SEE a real list of embedding
model options in settings, matching the actual competitive
landscape (Voyage, OpenAI, Google, Cohere), but ONLY Voyage is
functionally enabled right now. Selecting anything else must be
REJECTED with a clear message ("Voyage is the only model enabled
right now") — enforced on the BACKEND as the source of truth, not
just hidden/disabled in the UI (a client-side-only restriction is
not real enforcement).

STANDING RULES: org_id never from request body; no interactive
prompts; light theme matching every other admin screen.

=== TASK 1: Discover, don't assume ===
  (a) PGVECTOR: re-verify still enabled (SELECT * FROM
      pg_extension WHERE extname='vector') — should already be
      true, just confirm.
  (b) VOYAGE GATE: check for a real VOYAGE_API_KEY and attempt a
      genuine, live embedding call (a trivial test string) to
      confirm real access — do not just check the key's shape.
      IF THIS FAILS: stop, report exactly what was tried and what
      happened, do NOT proceed to Tasks 2-4. This is a legitimate
      possible outcome.
  (c) Re-read the REAL current Mini-Bedrock/TaskRouter org_settings
      pattern (ai.model.default/provider/fallback_chain, from
      earlier this session) — confirm the exact real key-naming
      convention to follow for a NEW ai.embedding.* namespace.
  (d) Re-read the REAL current admin settings UI (org_settings
      editor, wherever Mini-Bedrock's config lives today) — confirm
      whether it can be cleanly extended with a new dropdown, or
      whether a separate small addition is warranted. Reuse the
      real existing pattern, do not invent a new settings paradigm.
  (e) VECTOR DIMENSION: different embedding providers produce
      different-dimension vectors. Since only Voyage is
      functionally enabled, use VOYAGE'S real current output
      dimension for the pgvector column now — design should not
      block on solving multi-provider dimension normalization
      today (that's a real problem for whenever a second provider
      is actually enabled, not this phase).
  (f) Confirm real chunking/text-preparation needs across
      document_extractions, document_template_extractions, and
      document_narrative_extractions — what content should
      actually be embedded per document type.
Report all findings before proceeding. If gate (b) fails, stop —
do not build Tasks 2-4.

=== TASK 2: Org-configurable embedding provider + real Voyage
service ===
  - Add org_settings keys following Task 1c's REAL naming
    convention (e.g. ai.embedding.provider, ai.embedding.model) —
    default every org to Voyage + its intended model.
  - Build a provider abstraction with FOUR listed options (Voyage,
    OpenAI, Google, Cohere) but only Voyage functionally wired to
    a real API call — the other three are named/stubbed, calling
    them should never actually attempt a real API call.
  - BACKEND VALIDATION: any attempt to SET a non-Voyage provider
    via the settings endpoint must be REJECTED with a clear error
    ("Voyage is the only model enabled right now") — this is the
    real enforcement point, not just a disabled UI control.
  - Build apps/api/services/document_embedding.py: embeds a
    document's relevant content (per Task 1f) via the org's
    configured provider (which, practically, is always Voyage
    right now) — using Task 1e's real output dimension for storage.
  - New pgvector-typed table/column, org_id-scoped, RLS applied
    IMMEDIATELY in the same migration as every table this session.
  - Wire embedding generation to fire after successful extraction,
    reusing the SAME SORT-hook pattern already established for K-1/
    narrative extraction — do not invent a new trigger mechanism.

=== TASK 3: Admin UI — the dropdown + wired validation ===
Extend the REAL existing settings UI (per Task 1d's finding) with
a dropdown showing all 4 embedding-provider options. Selecting
Voyage saves normally. Selecting anything else calls the backend,
gets the real rejection from Task 2's validation, and displays
that exact error message clearly to the org admin — prove this is
a REAL round-trip rejection, not a client-side-only restriction
that could be bypassed by calling the API directly.

=== TASK 4: Semantic search + RETRIEVE ===
Build real semantic search: embed a query (via the org's
configured — currently always Voyage — provider), search via
pgvector similarity, respect the SAME visibility engines as
everything else (staff visibility, member resolve_entity_set,
restricted-access filter — reuse, do not bypass), return results
with citations back to source documents. Reuse Phase 9's existing
search UI pattern if it extends cleanly, or a clearly-separate
"semantic search" action if not — report which.

=== VERIFICATION ===
Write verify_chancery11b.py (apps/api/scripts/) — pass/fail only,
no interactive prompts, teardown-at-start and teardown-at-end.

IF GATE (b) FAILS: report [BLOCKED] for every downstream
assertion with the exact reason, same pattern as the Textract
gate — never a false [PASS].

Assertions (assuming the gate passes):
  [Y] Report Task 1's findings explicitly, including proof of a
      REAL successful Voyage embedding call
  [Y] Attempting to set a non-Voyage embedding provider is
      REJECTED by the backend with the specific expected error
      message — test this directly against the endpoint, not just
      the UI
  [Y] Setting Voyage explicitly (the only valid choice) succeeds
  [Y] A real test document's content is correctly embedded and
      stored with the right vector dimension
  [Y] A semantic query correctly returns the relevant document
      AND does NOT return an unrelated one — a real similarity
      proof
  [Y] A restricted-access document does not appear in another
      user's search results (test against the real app_service
      connection)
  [Y] A different org's documents never appear in this org's
      search results (cross-org isolation, real app_service test)
  [Y] Teardown: zero leftover rows

Report each assertion explicitly. Push when 100% pass (or the
honest BLOCKED report if the gate fails) — hold for manual review
regardless of tier. This is the FINAL phase of the entire Chancery
build — be thorough.
