SMTP / EMAIL SENDING SERVICE. 5 tasks + verification. Confirmed
gap from tonight's earlier discovery: NO email-sending code
exists anywhere in the API. No SES/SMTP/SendGrid/Postmark/Resend
client in services/ or routers/. The SES credential gate
previously failed (the Textract IAM user had zero SES
permissions) and no send call was ever written. This currently
blocks a REAL, ALREADY-SHIPPED feature: invite creation returns
an enrollment_url for manual sharing because there is no way to
actually email it.

THERE IS NO HUMAN AVAILABLE. Report findings, then continue
immediately in the same response. If uncertain, continue.

STANDING RULES: no interactive prompts. Never print a real secret
value in any output.

=== TASK 1: DISCOVER — real current AWS IAM state ===
Report findings, THEN CONTINUE IMMEDIATELY in the same response.
  1a. Confirm the REAL, CURRENT IAM permissions on the AWS
      credentials this deployment uses (restored/rotated during
      tonight's Doppler migration) — does SES send permission
      exist today, or is this still the same gap from before?
      Report honestly; do not assume it was fixed just because
      credentials were rotated.
  1b. Confirm whether AWS SES itself is out of "sandbox mode" for
      this AWS account (a real, separate AWS-side requirement —
      sandbox SES can only send to verified addresses, which
      would silently fail for real invites to real prospective
      members).
  1c. Confirm the REAL current invite-creation code path
      (services/invites.py) — the exact point where a send call
      needs to be added, and confirm the real enrollment_url
      shape it should send.

=== TASK 2: BUILD — the send path ===
A real SES send function, called from invite creation. Fail loud
if credentials/permissions are missing (per the established
credential_state() pattern from portfolio_altruist.py) — never
silently fall back to "just return the URL" without an explicit,
visible signal that email sending is unavailable.

=== TASK 3: A REAL, MINIMAL TEMPLATE ===
A plain, real invite email — org name, enrollment_url, a real
expiry-date mention pulling from invite.expiry_days (the org-
configurable setting built earlier tonight). No design system
work needed — plain text or minimal HTML is sufficient for this
sprint.

=== TASK 4: REAL PROOF ===
  - Report Task 1's IAM/sandbox findings explicitly and honestly.
  - IF SES is genuinely usable: a real invite creates AND sends a
    real email, proven via SES's own send confirmation (message
    ID), not just "the function was called."
  - IF SES is NOT genuinely usable (sandbox mode, missing
    permission): the action fails loud with a specific, actionable
    message — and Task 2's fallback (returning the URL for manual
    sharing) remains available, clearly distinguished from a
    silent failure.
  - Cross-org: an org's invite email correctly reflects that
    org's own name/branding, not another org's.

=== TASK 5: UPDATE PROJECT STATUS ===
Update docs/PROJECT_STATUS.md with the honest current state:
whether real sending works end-to-end, or is blocked on a real,
named AWS-side action item (exiting SES sandbox, granting IAM
permission) that requires Joe's action outside this sprint.

=== VERIFICATION: apps/api/scripts/verify_smtpservice.py ===
Pass/fail only. Never print a real secret or a real email address
sent to in full output.

Assertions:
  [Y] Report Task 1's three findings explicitly and honestly
  [Y] IF usable: a real invite triggers a real SES send with a
      confirmed message ID
  [Y] IF NOT usable: fails loud with a specific, actionable
      message naming the real gap
  [Y] The manual-URL fallback still works regardless
  [Y] Cross-org: email content reflects the correct org
  [Y] Teardown: zero leftover rows
