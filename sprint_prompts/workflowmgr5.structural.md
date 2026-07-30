WORKFLOW MANAGER — PHASE 5 (Permissions — final phase of Wave 2).
3 tasks + verification. Replaces the blanket admin gate used by
Phases 3-4 with granular, action-registry-based permissions,
reusing the existing SOC Profiles/Permission-Sets system — do
NOT invent a new permission scheme.

CONTEXT: Phase 3 confirmed workflow endpoints currently gate via
services.rbac.can_manage_org_settings (a coarse "any admin" check
mirroring routers/profiles.py). This phase replaces that with
specific permissions: authoring/editing a workflow, publishing a
new version, viewing the run console, and configuring the
scheduler/triggers should be SEPARATELY grantable, not bundled.

STANDING RULES: org_id never from request body; no interactive
prompts; light theme if any new UI is touched.

=== TASK 1: Discover, don't assume ===
  (a) Read the REAL action registry module (services.action_
      registry, confirmed in Phase 2's findings) — its actual
      format for defining a new AssistantAction entry (key,
      access_type, whatever else it requires). Confirm whether
      any workflow-manager-specific action keys already exist,
      or if none do.
  (b) Read the EXISTING Profiles/Permission-Sets admin UI
      (ProfilesManager.jsx / PermissionSetsManager.jsx /
      PermissionChecklist.jsx, built during the SOC follow-on
      sprint) — confirm whether PermissionChecklist.jsx
      DYNAMICALLY renders every entry from the real action
      registry (meaning new entries would appear automatically,
      no new screen needed) or whether it's hardcoded/limited to
      a fixed list (meaning this phase would need to build or
      extend a screen). Report which is actually true.
  (c) Read how an existing endpoint elsewhere in the codebase
      checks a SPECIFIC granular permission (not the blanket
      admin check) — e.g. however Profiles/Permission-Sets
      themselves enforce action-registry-based checks — and
      reuse that exact pattern/helper function.
Report all three findings before proceeding.

=== TASK 2: Add workflow-manager action-registry entries ===
Based on Task 1a's real format, add new fixed action-registry
entries (do not guess the key-naming convention — match
whatever pattern the existing registry actually uses) for at
minimum: author/edit a workflow (create + save new versions),
publish (nothing extra beyond save in this platform's model, but
confirm), view the run console, and configure the scheduler/
triggers. Do NOT grant these to any existing seeded Profile by
default — a newly-added permission should require deliberate
assignment, not silently expand what current profiles can already
do.

=== TASK 3: Wire granular checks into Phases 3-4's endpoints ===
Replace the blanket can_manage_org_settings gate on each
workflow-related endpoint (library, editor/save, run console,
run drill-in, scheduler/triggers, version history) with the
SPECIFIC corresponding permission from Task 2, checked via
whatever real mechanism Task 1c found. If Task 1b found the
existing Profiles UI is genuinely dynamic, no new frontend screen
is needed — confirm the new entries appear there automatically.
If Task 1b found it is NOT dynamic, build the minimal necessary
extension to that existing UI (do not build a whole separate new
screen if the existing one can be extended instead).

=== VERIFICATION ===
Write verify_workflowmgr5.py (apps/api/scripts/) — pass/fail
only, no interactive prompts, teardown-at-start and teardown-
at-end.

Assertions to include:
  [Y] Report Task 1's three discovery findings explicitly
  [Y] The new action-registry entries exist and are NOT granted
      to any existing seeded Profile (Member/Community Member/
      Adviser/CSA-Ops) by default
  [Y] A user whose Profile/Permission Set grants the specific
      "author workflow" permission CAN reach the library/editor
      endpoints; a user WITHOUT it is rejected — even if that
      user otherwise has some other admin-adjacent permission
      (proves the check is genuinely granular, not just a
      renamed blanket check)
  [Y] Same proof for the run-console-view permission and the
      scheduler-configure permission independently — a user with
      ONE of the three granted and not the others can access
      only that corresponding endpoint
  [Y] If Task 1b confirmed the Profiles UI is dynamic: confirm
      the new permissions are visible/toggleable there without
      any frontend code change. If not: confirm whatever minimal
      UI extension was built actually works.
  [Y] npm run build exits 0
  [Y] Teardown: zero leftover rows

Report each assertion explicitly. Push when 100% pass — hold for
manual review regardless of tier, given this changes real access
control on every workflow endpoint built in Phases 3-4.
