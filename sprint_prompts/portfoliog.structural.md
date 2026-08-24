PORTFOLIO PHASE G — USER-DEFINED FIELDS. 6 tasks + verification.
Design: docs/PORTFOLIO_REPORTING_DESIGN_V6.md §15, refined in
Part 1. Builds on A1, A2, B, C, D, E, F — all merged. LAST phase
before H (reconciliation/performance/cross-client — designed for,
deliberately not built).

THERE IS NO HUMAN AVAILABLE. Reporting discovery findings means
"report, then immediately continue in the same response" — never
stop and wait. If uncertain whether to continue, continue.

DESIGN REFINEMENT FROM V6 §15, APPLIED IN PART 1 SQL ALREADY:
  This is about WHO AUTHORS a field and WHO CAN SEE it — parallel
  namespaces, not a cascading override chain. Real cases:
  Hollisworks ships an industry-standard classification feed
  (platform-authored); a client org decides whether preferred
  stock is equity or debt (org-authored); a team keeps its own
  view; a user keeps their own. These are USUALLY different
  field_key values and coexist without conflict.

  RLS enforces the HARD boundary only (cross-org isolation +
  platform global-read) — the SAME split already used for
  ownership-basis validation in A2 (no CHECK constraint there
  either; the service layer is the real enforcement). Finer
  narrowing (team membership, user ownership) is THIS SPRINT'S
  job, in the service layer, not RLS.

  portfolio.udf_definitions: owner_scope IN ('platform','org',
  'team','user'). org_id is NULL for platform scope, POPULATED
  for org/team/user (a real udf_def_scope_org_chk enforces this —
  a team or user scoped definition without its owning org_id is
  a schema error, not a runtime one). owner_scope_id is NULL for
  platform, holds team_id or user_id for team/user scope.
  A partial UNIQUE index prevents two ACTIVE definitions with the
  same (org, scope, scope_id, applies_to, field_key) — a genuine
  collision within one namespace, not across namespaces.

  portfolio.udf_values: standard direct org-scoped table,
  UNIQUE on (org_id, definition_id, target_type, target_id) —
  one current value per definition per target.

  RLS proven so far: SELECT sees platform + your own org's rows
  (team/user narrowing NOT yet enforced by RLS — that is this
  sprint's Task 3). INSERT/UPDATE/DELETE require org match, or
  Super-Admin for platform-scope writes.

STANDING RULES: org_id never from request body; Decimal for any
numeric UDF value; no interactive prompts; schema-qualify every
portfolio.* reference (portfolio is NOT on search_path — confirmed
in every prior phase, do not rediscover this).

DELIBERATELY OUT OF SCOPE — DO NOT BUILD:
  * The reconciliation engine, performance calculations, or
    cross-client analysis (Phase H — designed for, not built)
  * Any UI beyond what is needed to prove the resolution logic
  * A general "override" or "merge" mechanism — this is
    deliberately NOT a cascade; see the design refinement above

=== TASK 1: DISCOVER — do not assume ===
Report findings, THEN CONTINUE IMMEDIATELY in the same response.
  1a. Confirm both new tables, their CHECK constraints, the
      partial unique indexes, and the deployed RLS policy counts
      (4 on udf_definitions, 1 on udf_values) exactly as
      described above.
  1b. Confirm the REAL current public.teams table (id, org_id,
      name, description — already confirmed org-scoped) and the
      REAL current mechanism for "is user X a member of team Y"
      (SOC Phase 2's staff_assignments or an equivalent
      membership table) — this sprint's team-scope visibility
      check must use the REAL membership mechanism, not assume
      one.
  1c. Confirm A1's real global-write pattern (_require_super_admin
      + _SuperAdminWrite, per Phase F's own re-read) — this
      sprint's platform-scope definition writes must compose it,
      not reinvent it.
  1d. Confirm A2's real ownership-basis service-layer-enforcement
      pattern (the precedent for "RLS handles the hard boundary,
      Python handles the rest") — this sprint's team/user
      visibility narrowing follows the SAME division of
      responsibility.

=== TASK 2: DEFINE — per scope, composing real patterns ===
Build apps/api/services/portfolio_udf.py:
  create_platform_definition(conn, *, applies_to, field_key,
  label, data_type, options=None) -> composes Task 1c's real
  Super-Admin pattern.
  create_org_definition(conn, *, org_id, applies_to, field_key,
  label, data_type, options=None) -> standard org-write.
  create_team_definition(conn, *, org_id, team_id, applies_to,
  field_key, label, data_type, options=None) -> verify team_id
  genuinely belongs to org_id before creating (a cross-org team
  reference must be refused at creation, not discovered later).
  create_user_definition(conn, *, org_id, user_id, applies_to,
  field_key, label, data_type, options=None) -> verify user_id
  genuinely belongs to org_id.
  A duplicate (same namespace + field_key) must be REFUSED by the
  real partial unique index — prove the database is the actual
  gate, not just application logic.

=== TASK 3: RESOLVE — team/user narrowing, in the service layer
===
resolve_visible_definitions(conn, *, org_id, user_id, applies_to)
-> returns EVERY definition this specific user can see: all
ACTIVE platform-scope rows + this org's own org-scope rows +
every team-scope row for a team this user (per Task 1b's REAL
membership check) actually belongs to + this user's own
user-scope rows. A team-scope definition for a team the user is
NOT a member of must NOT appear — this is the real enforcement
this sprint adds beyond what RLS alone provides.

=== TASK 4: VALUES — round-trip, typed ===
record_udf_value(conn, *, org_id, definition_id, target_type,
target_id, value) -> validates value against the definition's
real data_type (numeric must be Decimal/int/str per the
established float-refusal convention from A2, date must be a
real date, text/select/boolean as appropriate) and UPSERTs on the
real unique constraint (a re-record for the same target updates,
does not duplicate). Refuse a value whose target_type does not
match its definition's applies_to — a numeric field defined for
'commitment' should not silently accept a value against an
'asset'.

=== TASK 5: PARALLEL NAMESPACES, PROVEN NOT TO COLLIDE ===
Prove the core design claim with real data: a platform definition
named 'asset_classification' and an org definition ALSO named
'asset_classification' for the SAME org and applies_to coexist
without error, are both independently readable, and a value can
be recorded against EITHER without ambiguity about which
definition it belongs to (definition_id disambiguates, not
inferred matching).

=== TASK 6: UPDATE PROJECT STATUS ===
Update docs/PROJECT_STATUS.md in the same commit: record Phase G
as built, and — this is the significant one — that the Portfolio
Reporting Layer's designed phases (A1 through G) are now ALL
COMPLETE, with Phase H's items (reconciliation, performance,
cross-client analysis) explicitly still designed-for-not-built.

=== VERIFICATION: apps/api/scripts/verify_portfoliog.py ===
Pass/fail only. No interactive prompts. Idempotent. Teardown at
start AND end — exact before/after count match on every table
touched, not an unconditional TRUNCATE.

Assertions:
  [Y] Report Task 1's four findings explicitly, including the
      REAL team-membership mechanism used
  [Y] A platform definition can be created by Super-Admin context
      and is REJECTED for a non-super-admin caller — and nothing
      was written on refusal
  [Y] Org, team, and user definitions each succeed under their
      correct caller context
  [Y] A team definition for a team_id that does NOT belong to the
      calling org_id is REFUSED at creation
  [Y] A DUPLICATE definition (same namespace + field_key) is
      REFUSED by the real unique index, not just application logic
  [Y] RESOLUTION: a user who IS a member of a team sees that
      team's definitions via resolve_visible_definitions; a
      DIFFERENT user in the same org who is NOT a member of that
      team does NOT see them — both proven, not one inferred
      from the other
  [Y] RESOLUTION: platform definitions appear for every org;
      org-A's org-scope definitions do not appear for org-B
  [Y] VALUES: a numeric value round-trips as exact Decimal; a
      float is refused per the established convention
  [Y] VALUES: recording a value twice for the same target UPDATES
      (via the real unique constraint), does not duplicate
  [Y] VALUES: a target_type mismatched against its definition's
      applies_to is refused
  [Y] PARALLEL NAMESPACES: platform 'asset_classification' and
      org 'asset_classification' for the same org+applies_to both
      exist, both resolve independently, and values recorded
      against each are unambiguous by definition_id
  [Y] Cross-org isolation on values and org/team/user-scope
      definitions, tested against the real app_service connection
  [Y] Teardown: exact before/after count match on every table
      touched, including the two new tables
