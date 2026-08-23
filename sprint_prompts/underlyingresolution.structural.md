UNDERLYING RESOLUTION — match unresolved securities_global_relationships
edges to real global_security_id rows.

5 tasks + verification.

CURRENT STATE (confirmed live, do not re-derive):
  97 unresolved edges in portfolio.securities_global_relationships.
  Top raw_underlying_text values, verbatim from the database:
    "S&P 500 ® Index"                          10
    "the S&P 500 ® Index"                       6
    "Russell 2000 ® Index"                      6
    "the Russell 2000 ® Index"                  5
    "the Common Stock of NVIDIA Corporation"    4
    "Dow Jones Industrial Average SM"           3
    "the S&P 500 ® Futures Excess Return Index" 3
    "EURO STOXX 50 ® Index"                     3
    "the Nasdaq-100 ® Index"                    3
    "Nasdaq-100 ® Index"                        2
    "Nasdaq-100 Index ®"                        2
    "the Common Stock of Tesla, Inc."           2
    "the Dow Jones Industrial Average®"         2
    "the Nasdaq-100 Index ®"                    2
    "Dow Jones Industrial Average ®"            2
  Note the REAL noise pattern: leading "the ", trailing vs. embedded ®,
  spacing around ®, "SM" vs "®" for the same mark. This is not
  hypothetical — normalize against these exact strings.

WHAT THIS SPRINT DOES: builds a normalize -> match -> propose pipeline
that turns raw_underlying_text into a resolved link_state with a
to_global_security_id, for the CLOSED SET of major indices (cheap,
high-value) plus a review queue for everything else (single names,
decrement indices, anything the matcher can't confidently resolve).

WHAT THIS IS NOT — DO NOT BUILD THESE:
  - An automatic resolver for single-name equities. "the Common Stock
    of NVIDIA Corporation" -> NVDA is easy for a human, genuinely
    ambiguous for a string matcher at scale (ticker changes, class
    shares, foreign private issuers). Route these to the review queue,
    do not auto-match on a fuzzy name match alone.
  - A resolver for decrement/risk-control indices. These have NO
    public price series and often no CUSIP/ticker at all. Match them
    to a placeholder securities_global row if one can be created
    cleanly (see Task 3), but do NOT attempt price-series wiring —
    that is out of scope entirely, possibly permanently, per the
    original design discussion.
  - Auto-approval of ANY match above a confidence threshold without
    human confirmation. Per the platform's core governance pattern
    (AI proposes, human confirms), even a 99%-confident string match
    creates a PROPOSAL, never a silent resolution. This is non-
    negotiable regardless of how clean the match looks.
  - Comparability scoring or percentile ranking. That is the sprint
    after this one and depends on resolution being done first.

STANDING RULES: org_id never from request body (N/A, global data);
RLS in the same migration; light theme, Signature palette; verify
scripts pass/fail only; maker-checker enforced at the database level,
not just in application code (per house convention).

THERE IS NO HUMAN AVAILABLE. Report discovery, then continue
immediately. Exceptions are the explicit STOP/BLOCKED gates below.


=== TASK 1: DISCOVER — do not assume ===

  1a. Confirm the 97 count and pull the FULL distinct list of
      raw_underlying_text values (not just top 15) with counts. Report
      it — this is the actual population Task 2's normalizer must
      handle, not a representative sample.

  1b. Confirm securities_global's current row count and report a
      sample of any rows whose security_type='index' — are the major
      indices (S&P 500, Russell 2000, Nasdaq-100, Dow, Euro Stoxx 50)
      ALREADY present as securities_global rows from some other path,
      or does this sprint need to create them? Check before assuming
      either way.

  1c. Confirm the exact CHECK constraint on securities_global_
      relationships.link_state (resolved | unresolved | ambiguous) and
      the resolved-requires-target CHECK constraint, verbatim.

  1d. Find the platform's existing "AI proposes, human confirms"
      pattern implementation closest to this use case — Chancery's
      entity-linkage proposal flow is the most likely precedent.
      Report its actual table shape and reuse the pattern rather than
      inventing a new proposal mechanism.

  1e. Confirm whether securities_global has a UNIQUE constraint that
      would prevent creating a duplicate "S&P 500 Index" row if one
      doesn't already exist (Task 3 needs to create at most ONE row
      per distinct real-world security, not one per raw string
      variant).


=== TASK 2: NORMALIZATION ===

apps/api/services/underlying_normalization.py

  def normalize_underlying_text(raw: str) -> str

Deterministic, no LLM call — this is a cheap cleanup pass, not a
matching problem:
  - Strip leading "the "/"The " (case-insensitive)
  - Normalize ® SM ™ symbols: strip trailing, strip embedded-with-
    space, collapse "Index ®" / "® Index" / "Index®" variants to one
    canonical form
  - Collapse whitespace
  - Title-case is NOT required — preserve original casing except for
    the leading article

Prove this collapses the REAL duplication found in Task 1a — e.g.
"S&P 500 ® Index", "the S&P 500 ® Index" should normalize to the
same string. Write this as a test using the actual strings from 1a,
not synthetic examples.


=== TASK 3: THE CLOSED-SET INDEX MATCHER ===

A small, hand-maintained mapping — NOT a fuzzy-match model — for the
handful of major indices that dominate this corpus. Per the earlier
design principle: "the US market is concentrated in about fifteen to
twenty issuers with a thin tail" applies here too — indices referenced
in notes are an even smaller closed set.

apps/api/services/underlying_index_registry.py

  KNOWN_INDICES: dict[str, dict]  # normalized_name -> {cusip/ticker/
                                  #   name for securities_global lookup}

Seed with, at minimum, the 5 index families visible in Task 1a's data:
S&P 500, Russell 2000, Nasdaq-100, Dow Jones Industrial Average,
EURO STOXX 50. Include the "S&P 500 Futures Excess Return Index"
variant as a SEPARATE entry, not collapsed into "S&P 500 Index" — it
is a different index with a different (worse) price series and
collapsing them would be a real error, not a formatting one.

  async def resolve_or_create_index_security(pool, normalized_name) -> uuid
    - if a securities_global row for this index already exists
      (per 1b), return its id
    - else, INSERT ONE securities_global row (security_type='index',
      price_coverage='unknown' until a later sprint wires a feed)
      and return the new id
    - must be safe to call twice with the same name without creating
      a duplicate (use the constraint from 1e, or an explicit
      SELECT-then-INSERT-if-absent inside a transaction)


=== TASK 4: THE PROPOSAL PIPELINE ===

For EVERY unresolved edge (not just index matches — this task covers
routing ALL 97):

  async def propose_resolution(pool, relationship_id) -> ProposalResult

Logic:
  1. Normalize raw_underlying_text (Task 2).
  2. If it matches a KNOWN_INDICES entry -> propose linking to that
     index's global_security_id via resolve_or_create_index_security.
     confidence = 'high'.
  3. Else if it matches the pattern "the Common Stock of X" or similar
     single-name patterns -> extract the company name, but DO NOT
     auto-resolve to a ticker. confidence = 'needs_manual_match'.
     Store the extracted company name as a hint for the reviewer.
  4. Else -> confidence = 'needs_manual_match', no hint extraction.

CRITICAL: propose_resolution NEVER writes link_state='resolved'
directly. It writes a PROPOSAL using the pattern found in Task 1d
(reuse the existing mechanism — do not invent a parallel one). A
separate, explicitly human-gated confirm step (Task 4's endpoint
below) is what flips link_state to 'resolved' and sets
to_global_security_id. If Task 1d finds no reusable pattern, use
link_state='ambiguous' as the "proposed, awaiting confirmation" state
per the existing three-state design (resolved/unresolved/ambiguous),
and store the proposed target in a new nullable column
proposed_global_security_id — do NOT overload to_global_security_id
with an unconfirmed value, since that column's whole meaning per the
original design is "resolved's target."

Endpoints (super_admin only, mirroring the note-terms queue pattern
from the prior sprint):
  GET  /api/v1/admin/pricing/underlying-queue
    Returns edges with link_state='unresolved' or 'ambiguous', with
    their proposal (if any) and confidence, joined to the note(s)
    referencing them for context.
  POST /api/v1/admin/pricing/underlying-queue/{id}/confirm
    body: { global_security_id: uuid | null, create_new: bool }
    Sets link_state='resolved', to_global_security_id set. This is
    the ONLY code path that ever sets link_state='resolved'.
  POST /api/v1/admin/pricing/underlying-queue/{id}/reject
    Sets link_state='unresolved' explicitly (clears any proposal),
    for a proposal the reviewer disagrees with.


=== TASK 5: UPDATE PROJECT STATUS ===

Update docs/PROJECT_STATUS.md: normalizer built, index registry seeded
(list the 5+ families), proposal pipeline, review queue, and the
EXPLICIT count of edges auto-proposed-high-confidence vs. routed to
manual review after running against the real 97.


=== VERIFICATION: apps/api/scripts/verify_underlyingresolution.py ===

Pass/fail only. No prompts. Idempotent. Teardown at start AND end.
Use APP_SERVICE_DATABASE_URL, fail loudly if it cannot connect.

  [ ] normalize_underlying_text collapses the ACTUAL duplicate pairs
      from Task 1a to the same string — assert on at least 3 real
      pairs (e.g. the S&P 500 and Russell 2000 variants), not
      synthetic examples
  [ ] normalize_underlying_text does NOT collapse "S&P 500 Index" and
      "S&P 500 Futures Excess Return Index" to the same string — these
      are genuinely different securities
  [ ] resolve_or_create_index_security called twice with the same
      normalized name returns the SAME id both times (no duplicate
      securities_global row created) — assert row count unchanged
      between calls
  [ ] PROPOSAL NEVER SETS link_state='resolved' DIRECTLY: run
      propose_resolution against a real high-confidence index match
      and assert link_state is still NOT 'resolved' afterward — this
      is the core governance assertion of this sprint
  [ ] The confirm endpoint IS the only path that sets
      link_state='resolved' — grep the codebase for every UPDATE
      touching link_state and assert exactly one write site sets it
      to 'resolved'
  [ ] Confirm endpoint rejected under app_service without
      is_super_admin
  [ ] Running propose_resolution against ALL 97 real unresolved edges:
      report actual counts — how many got a high-confidence index
      proposal, how many got a single-name hint, how many got neither.
      Assert the index-family edges from Task 1a's data (S&P 500,
      Russell 2000, Nasdaq-100, Dow, Euro Stoxx — at least 30 edges
      per the counts shown) all receive a proposal, not zero.
  [ ] After confirming ONE proposed index resolution end-to-end (using
      real data, not a fixture): assert link_state='resolved',
      to_global_security_id is set and non-null, and it resolves to a
      securities_global row with security_type='index'
  [ ] Reject endpoint clears a proposal back to 'unresolved' cleanly —
      assert proposed_global_security_id (or equivalent) is cleared
  [ ] Queue endpoint returns edges joined to the note_terms rows that
      reference them (via the FROM side of the relationship) — assert
      at least one returned edge carries real note context, not just
      the bare relationship row
  [ ] Global read on the queue works under app_service with no org
      context set
