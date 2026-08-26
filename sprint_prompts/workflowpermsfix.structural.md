WORKFLOW PERMISSIONS — ZERO GRANTS FIX. 3 tasks + verification.
Real, confirmed gap (schedulerdiscovery.lowrisk): author_workflows,
view_workflow_runs, configure_workflow_triggers all exist in the
deployed permissions catalog but have ZERO grants anywhere — only
super_admin can reach any workflow endpoint today.

THERE IS NO HUMAN AVAILABLE. Report findings, then continue
immediately. If uncertain, continue.

=== TASK 1: DISCOVER ===
Confirm the real current role_permissions and profile_permissions
grant shape for view_portfolio/manage_portfolio (the proven
precedent, per schedulerdiscovery's own findings) — which real
roles and profiles hold each. Confirm which real roles/profiles
SHOULD reasonably hold each of the three workflow permissions,
using view_portfolio/manage_portfolio's role list as the direct
template (org_admin and above for configure/author, broader for
view_runs) — report your reasoning, do not silently assume.

=== TASK 2: GRANT — both axes ===
Grant the three workflow permissions on BOTH role_permissions AND
profile_permissions, matching the real precedent's dual-axis
shape. author_workflows and configure_workflow_triggers to
org_admin/admin/super_admin-tier roles; view_workflow_runs
somewhat broader, following the view_portfolio precedent's
inclusion of more read-only roles.

=== TASK 3: REAL PROOF ===
An org_admin (not super_admin) can now reach all nine workflow
endpoints that require these permissions, proven live against the
real ASGI app — not just a database grant existing.

=== VERIFICATION: apps/api/scripts/verify_workflowpermsfix.py ===
  [Y] Report Task 1's findings
  [Y] Both grant axes populated for all three permissions
  [Y] An org_admin can reach every previously-403'd endpoint
  [Y] A member without the grant is still correctly refused
  [Y] Teardown: zero leftover rows
