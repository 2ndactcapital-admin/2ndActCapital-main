RBAC — SUPER ADMIN BYPASS FIX. 2 tasks + verification. A
confirmed, real gap: services.rbac.has_permission() only default-
allows when a user has ZERO rows in user_roles — it currently
"works" for Super Admin purely by the accident of that table
being empty for most accounts. If a Super Admin account ever
picks up ANY row in user_roles (as tonight's stray duplicate
identity briefly did), they get silently locked out of every
endpoint using require_permission, with no explicit
is_super_admin escape hatch anywhere in that function — unlike
every RLS policy, restricted_access check, and staff_visibility
(already fixed earlier this session) elsewhere in this platform.

STANDING RULES: org_id never from request body; no interactive
prompts.

=== TASK 1: Discover, don't assume ===
Read the REAL, current has_permission/require_permission
(services/rbac.py) — confirm every call site that uses either
function (grep broadly, not just the ones already known from
tonight — Profiles/Permission-Sets, admin/users, Workflow Manager
Phase 5's granular permissions, etc.). Report the full real list.

=== TASK 2: Add the bypass, matching platform convention exactly
===
Add an explicit is_super_admin check to has_permission — a
Super Admin always returns True, checked FIRST, before the
existing "zero roles = default allow" logic even runs. This must
not change behavior for anyone else: a non-super-admin with zero
roles still gets the existing default-allow; a non-super-admin
with roles still gets the existing strict per-permission check.

=== VERIFICATION ===
Write verify_rbacbypass.py (apps/api/scripts/) — pass/fail only,
no interactive prompts, teardown-at-start and teardown-at-end.

Assertions:
  [Y] Report Task 1's full call-site list explicitly
  [Y] A Super Admin WITH a real row in user_roles (simulating
      tonight's exact scenario) still passes require_permission
      for an arbitrary permission they were never explicitly
      granted — the actual bug, now fixed
  [Y] A non-super-admin with ZERO roles still gets the existing
      default-allow behavior (unchanged)
  [Y] A non-super-admin WITH roles still gets correctly REJECTED
      for a permission they don't have (unchanged, not
      accidentally loosened)
  [Y] Re-run against at least 2 of the real call sites from Task 1
      (e.g. a Profiles endpoint and a Workflow Manager endpoint)
      to confirm the fix works in real, not just isolated, context
  [Y] Teardown: zero leftover rows

Report each assertion explicitly. Push when 100% pass.
