WORKFLOW MANAGER — PHASE 3 (diagram editor + library screen).
3 tasks + verification. First UI phase. Builds on Phase 1
(execution engine) + Phase 2 (deriver + NL generation) — reuse
both, do not duplicate.

CONTEXT: workflow_definitions/workflow_versions/workflow_steps
all exist and are populated by Phase 2's generate_workflow +
workflow_steps_deriver.py. This phase adds the ability to VIEW
and EDIT a definition's current BPMN, with each save producing a
NEW version (never mutating an existing workflow_versions row —
matches this platform's general bitemporal/audit-everything
discipline, same reasoning as ownership_change_log).

CONFIRMED SCOPE ADDITION: include bpmn-js's companion properties
panel (bpmn-js-properties-panel or equivalent current package)
so an admin can edit step properties — action_registry_key,
assigned_role_profile_id, tier override — through a real UI,
not raw XML editing. Raw-XML-only would not be a usable
interface for an Org Admin.

STANDING RULES: org_id never from request body; no interactive
prompts; light theme (whites/creams, Navy #1B2B4B/Gold #C5A880)
matching every other admin screen already built.

=== TASK 1: Discover, don't assume ===
  (a) Find and reuse the EXISTING admin-screen access-gating
      pattern (is_super_admin/is_org_admin, used by the SOC
      Profiles/Permission-Sets/Restricted-Access admin screens)
      — this Library + editor screen should be gated the same
      way, not with new logic.
  (b) Check apps/web/package.json for any existing bpmn-js-
      adjacent dependency before adding one; confirm bpmn-js +
      its properties-panel package install cleanly and their
      CURRENT real package names/versions (do not assume a
      specific version number without checking npm).
  (c) Confirm Phase 2's workflow_steps_deriver.py's real function
      signature (exact reuse point for re-deriving steps after
      a manual edit-and-save).
Report all three findings before proceeding.

=== TASK 2: Library screen ===
New admin screen (e.g. apps/web/app/admin/workflows/page.js +
a WorkflowLibraryManager.jsx component), gated per Task 1a:
  - List workflow_definitions for the org: name, description,
    current version number, a quick step/tier summary (e.g.
    "5 steps, 2 require approval")
  - "New Workflow" — a simple form taking a name + natural-
    language description, calling Phase 2's generate_workflow
  - Each row links to the diagram editor (Task 3) for that
    definition

=== TASK 3: Diagram editor screen ===
New screen (e.g. apps/web/app/admin/workflows/[id]/edit/page.js
+ a WorkflowDiagramEditor.jsx component):
  - Embed bpmn-js (+ properties panel per the scope addition) to
    load and render the definition's CURRENT version's bpmn_xml
  - Properties panel lets the admin view/edit per-step:
    action_registry_key (Service Tasks), assigned_role_profile_id
    (User Tasks, populated from the real Profiles list), and an
    autonomy-tier override (defaulting to Phase 2's computed
    default, editable)
  - "Save" action: takes the edited XML, creates a NEW
    workflow_versions row (version_number = previous + 1,
    is_current = true, sets the previous row's is_current =
    false), and re-derives workflow_steps for the new version via
    Phase 2's deriver (Task 1c) — do NOT touch the old version's
    workflow_steps rows, they remain as historical record
  - Validate on save the same way Phase 2 does: XML must parse
    via SpiffWorkflow, every referenced action_registry_key/
    assigned_role_profile_id must resolve to a real row — reject
    the save with a clear error otherwise, never store invalid
    XML as a new version

=== VERIFICATION ===
Write verify_workflowmgr3.py (apps/api/scripts/) — pass/fail
only, no interactive prompts, teardown-at-start and teardown-
at-end.

Assertions to include:
  [Y] Report Task 1's three discovery findings explicitly
  [Y] Library screen's underlying endpoint returns the correct
      definitions/version/step-summary for a seeded test org,
      and correctly does NOT return a different org's definitions
  [Y] A non-admin user is rejected from both the library and
      editor endpoints (confirms Task 1a's gating is actually
      wired in, not just found and ignored)
  [Y] Saving an edit creates a NEW workflow_versions row
      (version_number incremented), the previous version's
      is_current flips to false, and workflow_steps are
      re-derived for the NEW version only — the old version's
      steps remain untouched
  [Y] Saving an edit with an invalid action_registry_key
      reference is REJECTED, no new version row is created
  [Y] npm run build exits 0
  [Y] No hardcoded Signature-palette hex in any new file
  [Y] Teardown: zero leftover rows

Report each assertion explicitly. Push when 100% pass — hold for
manual review regardless of tier, given this is the first screen
where an admin can produce durable, executable workflow
definitions through direct manual editing
