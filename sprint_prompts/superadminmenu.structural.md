SUPER ADMIN MENU + USER MANAGEMENT — DISCOVERY + FIX. 5 tasks +
verification. Real, reported symptom: a confirmed super_admin
user sees many pages missing from the sidebar, and cannot
successfully create/manage other users via /admin/users.

CONTEXT — confirmed live in the database:
  jlarizza@culmina.io exists, role='super_admin', org_id = 2nd
  Act's org. Profiles are confirmed EMPTY (none created).
  Cross-org browsing (an org-picker) is CONFIRMED NOT BUILT — do
  not treat that as a bug to fix in this sprint; it is real,
  separate, future work.

  *** URGENT, MORE LIKELY ROOT CAUSE, DISCOVERED LIVE TONIGHT: ***
  A real user (jlarizza@gmail.com) is CURRENTLY actively logged
  into admin.hollisworks.com with a working Auth0 session — but
  ZERO rows exist for this email in the users table, confirmed
  live via direct query, both before AND during this active
  session. A session with no backing user row would explain a
  near-total breakdown of anything role/org/permission-based far
  more completely than a menu-gating gap alone. TASK 1a below
  investigates this FIRST, as the likely actual root cause.

THERE IS NO HUMAN AVAILABLE. Reporting discovery findings means
"report, then immediately continue in the same response" — never
stop and wait. If uncertain whether to continue, continue.

STANDING RULES: org_id never from request body; no interactive
prompts.

DELIBERATELY OUT OF SCOPE — DO NOT BUILD:
  * A cross-org picker / cross-org browsing UI — confirmed
    separate, future work, not this sprint
  * Any change to RLS policies themselves — this is about
    APPLICATION-LAYER logic, not the database layer, which has
    already been proven correct repeatedly

=== TASK 1: DISCOVER — do not assume ===
Report findings, THEN CONTINUE IMMEDIATELY in the same response.
  1a. *** DO THIS FIRST *** Find WHERE and WHEN ensure_user (or
      whatever the real current equivalent is) is supposed to
      fire for a Hollisworks-tenant login. Report precisely why
      it has not fired for the real, live session described
      above. Check whether it is called from proxy.js middleware,
      a specific API route, dashboard's own session check, or
      somewhere else — and whether that call path is genuinely
      reachable for the Hollisworks tenant's auth flow (which was
      only recently fixed to stop looping — confirm the fix
      didn't incidentally bypass whatever step creates the user
      row). Report the exact, real reason no row was created.
  1b. Read the REAL current sidebar/menu component(s) — find
      EVERY place a menu item's visibility is gated (permission
      check, role check, or both). For each gate found, report
      whether it has an explicit is_super_admin bypass or only
      checks granular Profile/Permission-Set membership.
  1c. Cross-reference against Profiles being confirmed empty: for
      any menu item gated ONLY on a granular permission with NO
      super-admin bypass, a super_admin with no Profile assigned
      would see it disappear. Report every such item found by
      name/route.
  1d. Read the REAL current /admin/users page and its backing
      endpoint(s) — confirm exactly what "create/manage a user"
      currently does: does it actually insert a row, and does it
      correctly set org_id from the ADMIN'S OWN caller context
      (never from a request body, per standing rule) or from
      something else? Report the real, current behavior.
  1e. Confirm whether the invite-email sending piece
      (multitenant2b's scope) was ever actually completed, or
      whether user creation today only inserts a pending row with
      no notification path at all. Report honestly which.

=== TASK 2: FIX — the user-provisioning gap (Task 1a), if real ===
If Task 1a confirms a genuine gap (the row-creation step never
fires, or fires but fails silently, or is unreachable for this
tenant's flow): fix it so a real Hollisworks-tenant login
correctly creates or updates the corresponding users row, with
role/org_id set per whatever the established Hollisworks-staff
convention is (per_org_write patterns already proven elsewhere —
do not invent a new one). Prove jlarizza@gmail.com specifically
ends up with a real row after a fresh login.

=== TASK 3: FIX — super-admin bypass on menu gating ===
For every menu item Task 1b/1c found gated WITHOUT a super-admin
bypass: add one, following the SAME pattern already proven
correct elsewhere (is_super_admin checked FIRST, before any
granular permission check).

=== TASK 4: FIX — user creation, if Task 1d found it broken ===
If Task 1d found a real defect, fix it using established org-
write conventions. If already correct, report that explicitly —
do not make speculative changes.

=== TASK 5: REAL PROOF + PROJECT STATUS ===
  - jlarizza@gmail.com, after a fresh login, has a real users row
    with a sensible role/org_id — prove this against the live
    database, not a mock.
  - A super_admin with ZERO Profiles sees the FULL menu.
  - A regular user's menu is UNCHANGED — the regression check.
  - A real user can be created via /admin/users with the correct
    org_id.
  - Update docs/PROJECT_STATUS.md with everything Task 1 found
    and fixed, including the honest invite-email status, and
    reconfirm the org-picker/cross-org UI remains separate,
    tracked, not-yet-built work.

=== VERIFICATION: apps/api/scripts/verify_superadminmenu.py (or
the real frontend-appropriate location — confirm convention in
Task 1) ===
Pass/fail only. No interactive prompts.

Assertions:
  [Y] Report Task 1's five findings explicitly
  [Y] A fresh Hollisworks-tenant login produces a real users row
      — proven against the live database
  [Y] A super_admin with zero Profiles sees every menu item —
      real proof per item
  [Y] A regular user's menu is provably unchanged
  [Y] User creation via /admin/users produces a row with the
      correct org_id, sourced from caller context
  [Y] Report Task 1e's honest invite-email finding explicitly
  [Y] Teardown: zero leftover rows
