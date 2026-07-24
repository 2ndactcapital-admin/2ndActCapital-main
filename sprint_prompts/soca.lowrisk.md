SOC PHASE A — Profiles & Permission Sets admin UI. 3 tasks +
verification. Follow-on to the completed 6-phase SOC/RBAC
design — the BACKEND for this already exists (Phase 1:
services/profiles.py, profiles/permission_sets/
user_permission_sets tables, users.profile_id) but has NO admin
UI at all. This sprint is UI-only on top of that existing,
already-verified backend — do NOT modify the backend logic
itself unless you find it's genuinely missing something the UI
needs (report if so, don't silently patch around a gap).

CONTEXT: read services/profiles.py FIRST to confirm its real
function signatures before building anything — do not assume.
Also find and read whatever profile_permissions /
permission_set_permissions junction tables Phase 1 actually
built (their exact shape, especially the permission_key format
matching the action registry) before building a UI to edit them.
The existing StaffVisibilityManager.jsx / TradingAuthorityManager.
jsx / RestrictedAccessManager.jsx (from prior SOC phases) are the
UI pattern to follow for consistency — read at least one before
building, match its structure/style rather than inventing a new
pattern.

STANDING RULES: org_id never from request body; no interactive
prompts; light theme (whites/creams, Navy #1B2B4B/Gold #C5A880)
matching every other admin screen already built.

=== TASK 1: Profiles management screen ===
New admin screen (e.g. apps/web/app/admin/profiles/page.js +
a ProfilesManager.jsx component), Org Admin accessible (own org)
+ Super Admin (any org, same pattern as other admin screens):
  - List existing profiles for the org (should show the 4 seeded
    ones: Member, Community Member, Adviser, CSA/Ops)
  - Create a new profile (name + description)
  - Edit a profile's permission grants — show the full action-
    registry permission list with a checkbox/toggle per
    permission indicating whether this profile grants it
  - Delete/deactivate a profile (confirm no users currently
    assigned before allowing delete, or handle reassignment)

=== TASK 2: Permission Sets management screen ===
Same pattern, for permission_sets:
  - List, create, edit permission grants (same action-registry
    checklist UI as Task 1, reused as a shared component if
    practical — don't duplicate the whole checklist UI twice)
  - Assign/remove a permission set from a specific user (this is
    the ADDITIVE layer — a user already has a profile; permission
    sets add extra grants on top)

=== TASK 3: Wire profile assignment into user management ===
Find the existing user management screen (the one showing users
with an editable role dropdown, mentioned by Joe). Add a second
control alongside the existing role dropdown: a profile
selector, letting an admin set which profile (from Task 1's
list) a user has via users.profile_id. Keep the EXISTING role
dropdown (member/org_admin/super_admin) completely untouched —
profile is a NEW, separate, additive field, not a replacement.

=== VERIFICATION ===
Write verify_soca.py (apps/api/scripts/), same pattern as prior
verify scripts — pass/fail only, no interactive prompts,
idempotent, teardown-at-start and teardown-at-end.

Since this is primarily UI, combine backend-reachability checks
with a build check:
  [Y] The 4 seeded profiles (Member, Community Member, Adviser,
      CSA/Ops) are readable via whatever API endpoint Task 1's
      screen calls
  [Y] Creating a new profile via the API succeeds and is
      retrievable
  [Y] Toggling a permission grant on a profile persists correctly
      (grant it, confirm true; remove it, confirm false)
  [Y] Creating a permission set and assigning it to a test user
      correctly ADDS a capability beyond their profile (reuse
      the same additive-check logic proven in Phase 1's verify)
  [Y] Setting a user's profile_id via the Task 3 UI's underlying
      endpoint persists correctly, and users.role is UNCHANGED
      by this action (confirms the two fields stay independent)
  [Y] npm run build exits 0
  [Y] No hardcoded Signature-palette hex introduced in any new
      file (reuse brand_sweep_grep.sh's hex pattern)
  [Y] Teardown: zero leftover rows, confirm via count(*)

Report each assertion explicitly (pass/fail). Push when 100%
pass.
