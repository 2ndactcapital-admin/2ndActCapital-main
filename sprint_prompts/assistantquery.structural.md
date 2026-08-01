AI SIDEBAR — MISSING AGGREGATE/FILTER QUERY CAPABILITY. 4 tasks +
verification. Two confirmed real failures reported by Joe: "how
many investments are there" and "how many entities reside in CT"
both returned honest "I don't have a tool for that" responses.
Pattern: the assistant has single-lookup tools (given an ID/name,
fetch details) but nothing for aggregate counts or attribute-
filtered queries across a whole collection.

STANDING RULES: org_id never from request body; no interactive
prompts; respect EVERY existing visibility engine exactly as
every other feature does — staff_visibility, member
resolve_entity_set, restricted-access filter. These new actions
must query THROUGH those, never bypass them, since this is a
user-facing AI surface with real data-exposure risk if scoped
wrong.

=== TASK 1: Discover, don't assume ===
  (a) Read the REAL, current action registry (services/
      action_registry.py) in full — every existing action, its
      access_type, required_permission. Confirm there is
      genuinely NO count/list/filter-style action for entities or
      investments/deals today (Joe's two examples), and check
      whether the SAME gap exists for other collections (SPVs,
      workflow runs, member investments, etc.) — report a full
      honest survey, not just the two examples.
  (b) Confirm the REAL existing backend list/filter endpoints
      already available (listEntities, listDeals, etc., referenced
      throughout this session's lib/api.js) — their real query-
      param support (can listEntities already filter by state/
      address today? does it support a count-only response, or
      only full result lists?).
  (c) Confirm the REAL current entity address schema — does
      entity_addresses actually store a real, queryable state/
      province field (seeded via the state/ca_province reference
      data from earlier this project) that "entities in CT" could
      filter against?
  (d) Confirm the REAL current visibility-scoping mechanism used
      by whatever calls the assistant's tools today (how does an
      existing single-lookup action apply staff_visibility/
      resolve_entity_set/restricted-access?) — the new actions
      must reuse this EXACT pattern.
Report all four findings before proceeding.

=== TASK 2: Add count/filter actions — reuse existing endpoints,
don't rebuild ===
Based on Task 1's findings, add new AssistantAction registry
entries (matching the real existing registration pattern):
  - A count/list action for entities, supporting real filterable
    attributes (state, at minimum — per Task 1c's real schema;
    add others if Task 1b reveals they're already supported by
    the underlying endpoint).
  - A count/list action for investments/deals, supporting
    reasonable real filters (status/stage, matching what search
    already supports per Joe's first example).
  - Both MUST route through the real visibility-scoping mechanism
    from Task 1d — a member using this must only ever see their
    own permitted set, a staff user only their assigned/visible
    set, exactly as every other part of this platform works.
  - If the underlying endpoint doesn't yet support a count-only
    mode efficiently (Task 1b), add one rather than fetching a
    full list just to count its length — but only if genuinely
    needed, don't add complexity Task 1 didn't confirm is required.

=== TASK 3: Wire into the assistant's real tool-calling flow ===
Confirm these new actions are correctly registered and callable
by the assistant (test via the REAL chat/message flow, not just
direct backend calls) — reuse whatever real mechanism routes an
existing action from user question to registry to backend call.

=== TASK 4: If Task 1a found the SAME gap elsewhere (SPVs,
workflow runs, etc.) ===
Do NOT build fixes for those now — report them clearly as a
follow-up list for a LATER, separately-scoped sprint. This
sprint closes Joe's two confirmed real examples plus whatever
else can be closed with the SAME two reused endpoints (entities/
investments) — not a platform-wide sweep in one sprint.

=== VERIFICATION ===
Write verify_assistantquery.py (apps/api/scripts/) — pass/fail
only, no interactive prompts, teardown-at-start and teardown-at-
end.

Assertions:
  [Y] Report Task 1's four discovery findings explicitly,
      including the full honest survey of where else this gap
      exists
  [Y] A real count query for entities filtered by state (e.g.
      "CT") returns the CORRECT real count for a seeded test
      scenario — not just "some number," the actual right one
  [Y] A real count query for investments filtered by status
      returns the correct real count
  [Y] A member/staff user with LIMITED visibility gets a count
      scoped to ONLY what they can see, not the org's full total —
      proves the visibility engine is genuinely applied, not
      bypassed (this is the single most important assertion given
      this is a user-facing data-exposure surface)
  [Y] A different org's data is never included in either count
      (cross-org isolation, test against the real app_service
      connection)
  [Y] The assistant's real chat flow can actually invoke these
      new actions end-to-end (not just the backend function in
      isolation)
  [Y] Teardown: zero leftover rows

Report each assertion explicitly. Push when 100% pass — hold for
manual review regardless of tier, given this is a live, user-
facing production surface.
