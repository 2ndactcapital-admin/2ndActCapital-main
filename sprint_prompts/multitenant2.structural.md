HEADLESS MULTI-TENANT — SPRINT 2 (admin-provisioned invite flow
+ email delivery). 5 tasks + verification. TWO REAL, POTENTIALLY-
BLOCKING GATES — check both honestly, same discipline as the AWS
Textract/Voyage gates earlier this session. Do NOT proceed past a
failed gate.

CONTEXT: users table already has invite_token (unique), invite_
status, invited_by, invited_at, invite_expires_at (Part 1 SQL
applied directly — no new RLS needed, users already has a real
policy from earlier this session).

STANDING RULES: org_id never from request body; no interactive
prompts; light theme if any UI touched.

=== TASK 1: Discover, don't assume — TWO gates ===
  (a) SES CREDENTIAL GATE: check for real AWS SES credentials
      (may reuse the SAME AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY
      from the Textract setup, or need separate SES-specific
      permissions — confirm which). Attempt a real, minimal SES
      call.
  (b) SES SANDBOX GATE — genuinely different from (a): even with
      valid credentials, a NEW SES setup starts in "sandbox mode"
      and can ONLY send to individually pre-verified email
      addresses, not real inboxes broadly. Check the account's
      real sandbox status (SES has an API for this). IF IN
      SANDBOX: this is a REAL, EXPECTED possible blocker — report
      it clearly (moving to production sending requires an actual
      AWS review request, not something buildable in this
      sprint) and do NOT proceed to Tasks 3-5 (which need real
      email sending). Task 2 (the invite data model/token logic)
      can still be built regardless, since it doesn't require
      email delivery itself to be proven.
  (c) Re-read the REAL current ensure_user (services/users.py) —
      exact current logic, to know precisely where invite-
      matching needs to hook in.
Report all findings before proceeding. If gate (b) is sandboxed,
build Task 2 only and stop — report Tasks 3-5 as blocked with the
exact reason.

=== TASK 2: Invite creation + token logic (no email dependency)
===
  - An endpoint for an Org Admin (or Super Admin) to create a
    pending user record: sets invite_token (a real, cryptographically
    random value), invite_status='pending', invited_by, invited_at,
    invite_expires_at (a reasonable default, e.g. 7 days).
    auth0_sub remains NULL until the invite is completed.
  - A function to validate a presented invite token: exists,
    status='pending', not expired.

=== TASK 3 (only if gate b passes): Email delivery ===
Build a real SES-based email service sending the actual invite
email with an enrollment link containing the token.

=== TASK 4 (only if gate b passes): Enrollment completion —
modify ensure_user's real flow ===
Based on Task 1c's real findings: when an invite token is present
in the enrollment flow, MATCH to the existing pending user record
(update its auth0_sub, email, invite_status='accepted') rather
than creating a new row via the standard auto-create path. A
login with NO invite token continues today's existing default
behavior UNCHANGED (2nd Act's own users, and the future password
back-door, are not broken by this change).

=== TASK 5 (only if gate b passes): Expiry + revocation ===
  - A token past invite_expires_at is correctly rejected as
    invalid (not silently treated as valid).
  - An admin can revoke a pending invite (invite_status='revoked')
    before it's used.

=== VERIFICATION ===
Write verify_multitenant2.py (apps/api/scripts/) — pass/fail
only, no interactive prompts, teardown-at-start and teardown-at-
end.

IF GATE (b) IS SANDBOXED: report [BLOCKED] for Tasks 3-5's
assertions with the exact reason, same pattern as Textract —
never a false [PASS]. Task 2's assertions still run normally.

Assertions:
  [Y] Report Task 1's findings explicitly
  [Y] Creating an invite produces a real pending user row with a
      real random token, correct expiry
  [Y] An expired token is correctly rejected
  [Y] A revoked token is correctly rejected
  [Y] (if gate b passes) A real invite email is genuinely sent via
      SES
  [Y] (if gate b passes) Completing enrollment with a valid token
      updates the EXISTING pending row (not a new row) — confirm
      via row count and id match, not just "it worked"
  [Y] (if gate b passes) A login attempt with NO token still uses
      today's existing default behavior, completely unchanged —
      the regression check
  [Y] A different org's admin cannot create/view/revoke invites
      for another org (test against the real app_service
      connection)
  [Y] Teardown: zero leftover rows

Report each assertion explicitly. Push when 100% pass (or the
honest partial-blocked report if gate b fails) — hold for manual
review regardless of tier.
