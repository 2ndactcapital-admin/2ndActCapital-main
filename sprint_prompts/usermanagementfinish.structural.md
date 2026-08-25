USER MANAGEMENT — COMPLETING THE LIST. 7 tasks + verification.
Builds on real, live-confirmed fixes tonight: extensions grant,
enrollurl (fully-qualified invite URLs + real /enroll page +
users_preauth_invite_lookup RLS carve-out), twoactbaseurl. Schema
already applied for this sprint: users.is_active,
users.deactivated_at, users.deactivated_by, users.last_login_at.

THERE IS NO HUMAN AVAILABLE. Reporting discovery findings means
"report, then immediately continue in the same response" — never
stop and wait. If uncertain whether to continue, continue.

STANDING RULES: org_id never from request body; no interactive
prompts; light theme.

DELIBERATELY OUT OF SCOPE — DO NOT BUILD:
  * SAML IdP linking — explicitly paused, separate decision
  * A hard "delete" that removes FK-referenced history — see
    Task 5's real scope

=== TASK 1: DISCOVER — do not assume ===
Report findings, THEN CONTINUE IMMEDIATELY in the same response.
  1a. Re-read get_org_id (or ensure_user's org assignment) —
      confirm exactly why a Hollisworks-tenant login lands in the
      DEFAULT org rather than the Hollisworks org. Find the real
      fix point.
  1b. Re-read the real current invite endpoint's role parameter —
      confirm ALLOWED_INVITE_ROLES and whether profile_id is
      accepted anywhere in the create-invite path today (already
      known: the column exists on users, unused by invite).
  1c. Confirm the real current /admin/users edit endpoint (if any
      exists at all) — what fields can currently be changed.
  1d. Confirm the real org_settings convention (dotted keys,
      jsonb values, DEFAULT_SETTINGS precedent) — this sprint
      adds invite-expiry and inactivity-timeout keys following
      it exactly.
Report all four findings before proceeding.

=== TASK 2: FIX — Hollisworks-tenant users land in the Hollisworks
org ===
Per Task 1a: a Hollisworks-issued token must set org_id to the
REAL Hollisworks org (bb347258-8f28-4f49-8cc9-e29ccad82884), not
the default org. A 2nd Act token's org assignment is UNCHANGED —
prove this explicitly as the regression check.

=== TASK 3: Profile-aware invite ===
Extend the invite endpoint to accept an optional profile_id
alongside role, validated against the REAL profiles table for the
caller's own org. role stays required (member/org_admin); profile
is an additional, optional grant — do not remove the existing
role field.

=== TASK 4: Edit user — name ===
A real edit endpoint/UI allowing full_name to be changed for a
user in the caller's own org. org_id is never accepted from the
request body — the target user's org must match the caller's org
context or the caller must be super_admin.

=== TASK 5: Deactivate + delete ===
deactivate: sets is_active=false, deactivated_at=now(),
deactivated_by=caller — an inactive user must fail login/session
checks going forward (confirm and wire the REAL check point, per
Task 1's discovery of how sessions are validated today).
delete: given FK references likely exist (invites, audit trails,
created_by columns elsewhere), a HARD delete may not be safe.
Discover real FK dependents on users.id before deciding — if hard
delete would orphan real data, implement it as a stronger form of
deactivation instead (e.g. anonymizing PII while preserving the
row for referential integrity) and report this decision plainly
rather than silently picking one.

=== TASK 6: Org-configurable expiry + inactivity ===
Add two org_settings keys following Task 1d's real convention:
  invite.expiry_days (default 7)
  user.inactivity_timeout_days (default 90)
Both readable/writable via the real org settings admin surface.
invite creation must use the ORG'S configured expiry (falling back
to the default when unset) — prove a custom value changes the
actual invite_expires_at written.

=== TASK 7: UI — deactivate/delete controls + last login display +
wire last_login_at ===
  - /admin/users edit view gets deactivate/reactivate and delete
    controls, gated on the appropriate permission.
  - last_login_at is actually WRITTEN somewhere real (the natural
    hook is wherever ensure_user's ON CONFLICT DO UPDATE already
    runs on every login) — prove a real login updates it.
  - /admin/users list displays last_login_at.

=== VERIFICATION: apps/api/scripts/verify_usermanagementfinish.py
===
Pass/fail only. No interactive prompts.

Assertions:
  [Y] Report Task 1's four findings explicitly
  [Y] A Hollisworks-tenant login gets org_id = the real
      Hollisworks org — proven against the live database
  [Y] A 2nd Act token's org assignment is UNCHANGED — regression
  [Y] An invite can carry an optional, validated profile_id
  [Y] A user's full_name can be edited within the caller's org;
      cross-org edit is refused
  [Y] Deactivation sets the real columns AND a deactivated user's
      subsequent session/login check genuinely fails — not just
      the flag being set
  [Y] Report Task 5's real hard-delete-safety finding and which
      approach was taken
  [Y] A custom invite.expiry_days value changes the real
      invite_expires_at written on a new invite
  [Y] A real login updates last_login_at, proven via direct query
      before/after
  [Y] Cross-org isolation on every new endpoint, tested against
      the real app_service connection
  [Y] Teardown: zero leftover rows
