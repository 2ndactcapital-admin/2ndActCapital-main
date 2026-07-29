WORKFLOW MANAGER — PHASE 2 (NL-to-BPMN generation). 4 tasks +
verification. Builds on Phase 1's proven execution engine
(services/workflow_engine.py, SpiffWorkflow). No UI yet — this
phase is the generation SERVICE only.

CONTEXT: Phase 1 proved a hand-written BPMN test fixture
executes correctly, but Phase 1 did NOT build a GENERIC parser
that derives workflow_steps rows from arbitrary BPMN XML — its
test fixture's workflow_steps rows may have been created
manually alongside the fixture, not derived from the XML itself.
CONFIRM this before assuming — if generic derivation already
exists, extend it; if not, this phase must build it as a
prerequisite for NL generation (generated XML is useless without
deriving step metadata from it).

CRITICAL TIER-DEFAULT NUANCE — do not default every Service Task
to the same tier: per this project's established 3-tier model
(Tier 1 = durable proposed-object, draft-until-approved; Tier 2 =
confirm-and-log; Tier 3 = execute freely, fully autonomous), a
Service Task wrapping a WRITE-capable action must NOT silently
default to Tier 3 (fully autonomous, no human gate) — that would
make unreviewed autonomous writes the silent default, which is
backwards. Sensible defaults, apply unless the XML's own
extension metadata explicitly overrides:
  - User Task -> always Tier 1
  - Service Task, underlying action is READ-ONLY -> Tier 3 (safe)
  - Service Task, underlying action is WRITE-capable -> Tier 2
    (confirm-and-log) by default — Tier 3 for a write action is
    an explicit, deliberate authoring choice, never a default
  - Business Rule Task (DMN) -> Tier 3 default (a rule evaluation
    is deterministic)
  - Send Task -> Tier 1 default (never send without approval by
    default; the mapping table already structurally gates a Send
    Task behind a preceding User Task in the diagram itself)
  - Gateways / Start / End events -> no tier (pure control flow,
    not an action)

STANDING RULES: org_id never from request body; no interactive
prompts; light theme if UI touched (none expected this phase).

=== TASK 1: Discover, don't assume ===
  (a) Confirm whether Phase 1 built any generic BPMN-XML-to-
      workflow_steps derivation, or whether its test fixture's
      steps were manually inserted. Report which.
  (b) Read the real action registry's access_type field/values
      (referenced in Phase 1's findings as
      services.action_registry.REGISTRY / AssistantAction) —
      confirm exactly how read vs. write is represented.
  (c) Check whether Sprint 27 (TaskRouter — a decision-log +
      per-org fallback-chain mechanism for AI calls) is ACTUALLY
      merged and working, or whether it's still in progress/not
      done. If TaskRouter is real, NL generation should call
      through it. If not, fall back to the existing Mini-Bedrock
      mechanism (org_settings-driven model selection) — do not
      block this phase on TaskRouter's status, just report which
      one is actually available and use it.
Report all three findings before proceeding.

=== TASK 2: Generic BPMN-to-workflow_steps deriver ===
Build (or extend, per Task 1a's finding)
services/workflow_steps_deriver.py: given a workflow_version_id
and its bpmn_xml, parse via SpiffWorkflow's parser (reuse Phase
1's parsing code, do not duplicate) and create one workflow_steps
row per actionable BPMN element (Service Task, Business Rule
Task, User Task, Send Task) with step_key/step_type extracted
from the XML, autonomy_tier set per the default table above
(reading action_registry_key's access_type from the registry for
Service Tasks), and action_registry_key/assigned_role_profile_id
extracted from BPMN extension elements if present in the XML (a
standard BPMN mechanism both bpmn-js and SpiffWorkflow support
for custom metadata — use it, do not invent a non-standard
convention). Gateways/events get no workflow_steps row.

=== TASK 3: NL-to-BPMN generation service ===
Build services/workflow_nl_generator.py:
  - Given a natural-language process description + org_id, fetch
    REAL context for that org: the actual seeded Profiles (for
    valid assigned-role choices) and actual action_registry
    entries (for valid Service Task references) — the AI must
    ONLY ever reference real IDs/keys that exist, never invent
    plausible-sounding ones (same discipline as the document
    classifier's closed reference-list matching).
  - Call the AI (via whichever mechanism Task 1c found) to
    generate BPMN XML matching the primitive-to-BPMN mapping
    table, embedding action_registry_key/assigned_role_profile_id
    via BPMN extension elements per Task 2's convention.
  - VALIDATE before accepting: the XML must parse via
    SpiffWorkflow (Task 1's parser), and every embedded
    action_registry_key/assigned_role_profile_id must resolve to
    a REAL existing row — if generation produced an invalid
    reference, retry once with an error-correction prompt, then
    fail clearly (never silently store broken/unvalidated XML).
  - On success: create a workflow_definitions row + a
    workflow_versions row (version_number=1, is_current=true) +
    derive workflow_steps via Task 2.

=== TASK 4: Structurally enforce "generate once, never
regenerate" ===
The generation function/endpoint from Task 3 must ONLY ever be
callable to create a BRAND-NEW workflow_definition — it must not
accept an existing workflow_definition_id and must have no code
path that could overwrite an existing workflow_versions row's
bpmn_xml. Prove this structurally (e.g. the function signature
has no way to target an existing definition), not just as a
documented policy.

=== VERIFICATION ===
Write verify_workflowmgr2.py (apps/api/scripts/) — pass/fail
only, no interactive prompts, teardown-at-start and teardown-
at-end.

Assertions to include:
  [Y] Report Task 1's three discovery findings explicitly
  [Y] Given a real, existing BPMN XML (e.g. Phase 1's test
      fixture or a new hand-written one covering a Service Task,
      User Task, and gateway), the deriver correctly creates
      workflow_steps rows with correct step_type per element
  [Y] A Service Task wrapping a KNOWN READ-only action defaults
      to Tier 3
  [Y] A Service Task wrapping a KNOWN WRITE-capable action
      defaults to Tier 2, NOT Tier 3 (proves the safe-default
      nuance is actually implemented, not just documented)
  [Y] A User Task always defaults to Tier 1 regardless of any
      other factor
  [Y] NL generation given a simple description produces XML that
      parses successfully via SpiffWorkflow
  [Y] Every action_registry_key and assigned_role_profile_id in
      the generated XML resolves to a REAL existing row (no
      invented/hallucinated references)
  [Y] Attempting to call the generation function/endpoint with
      an existing workflow_definition_id either has no such
      parameter at all, or is rejected — proves regeneration is
      structurally impossible, not just discouraged
  [Y] Teardown: zero leftover rows

Report each assertion explicitly. Push when 100% pass — hold for
manual review regardless of tier, given this is AI-generated
content that becomes durable, admin-owned infrastructure.
