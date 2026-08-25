INVITE URL + REAL ENROLLMENT PAGE. 5 tasks + verification. Real,
confirmed live bug: creating an invite returns a bare relative
path ("/enroll?invite_token=...") with no domain — unusable to
share. Confirmed separately: no /enroll page exists at all yet —
fixing the URL alone would produce a link that 404s.

THERE IS NO HUMAN AVAILABLE. Reporting discovery findings means
"report, then immediately continue in the same response" — never
stop and wait. If uncertain whether to continue, continue.

STANDING RULES: org_id never from request body; no interactive
prompts; light theme.

=== TASK 1: DISCOVER — do not assume ===
Report findings, THEN CONTINUE IMMEDIATELY in the same response.
  1a. Read the REAL current services/invites.py::create_invite
      and routers/invites.py — confirm exactly where the bare
      relative enrollment_url is constructed.
  1b. Confirm organizations.enroll_url is populated for the real,
      live orgs (2nd Act, Hollisworks) and confirm its real
      stored format (e.g. "https://2ndactcapital.hollisworks.com
      /enroll") — this is what the fix builds from.
  1c. Confirm the REAL current Auth0 signup/enrollment mechanism
      available (per-tenant client per getAuthClientForHost) —
      does completing enrollment mean directing the invited
      person through a normal Auth0 signup flow for the correct
      tenant, and if so what does that redirect actually look
      like today.

=== TASK 2: FIX — fully-qualified enrollment URL ===
create_invite must build the returned URL from the creating org's
REAL organizations.enroll_url + the invite_token query param —
never a bare relative path.

=== TASK 3: BUILD — real /enroll page ===
A real page at /enroll that: reads invite_token from the query
string, looks up the pending invite server-side, confirms it is
not expired and not already accepted, and if valid, hands off to
the CORRECT tenant's Auth0 signup flow (per Task 1c). On
completion, mark invite_status='accepted' and correctly link the
resulting auth0_sub to the existing pending users row (matching
the established "match, don't duplicate" pattern from earlier
Auth0 work this session).
An expired or already-used token shows a clear, honest message —
never a silent failure or generic error.

=== TASK 4: REAL PROOF ===
  - A real invite's returned URL is fully qualified and points at
    the correct org's real subdomain.
  - A real, valid token walks through /enroll successfully and
    the pending row is updated correctly (accepted, auth0_sub
    linked) — proven against the live database.
  - An expired token and an already-accepted token both show
    clear, distinct honest messages, proven separately.

=== TASK 5: UPDATE PROJECT STATUS ===
Update docs/PROJECT_STATUS.md: record the enrollment flow as
genuinely complete end-to-end for the first time, and note this
closes the gap flagged by the superadminmenu sprint.

=== VERIFICATION: apps/api/scripts/verify_enrollurl.py ===
Pass/fail only. No interactive prompts.

Assertions:
  [Y] Report Task 1's three findings explicitly
  [Y] create_invite returns a fully-qualified URL using the real
      org's stored enroll_url — not a relative path
  [Y] A real, valid invite token completes enrollment through
      /enroll and the row updates correctly
  [Y] An expired token shows a clear, distinct message
  [Y] An already-accepted token shows a clear, distinct message
  [Y] Cross-org: an org cannot generate or redeem an invite tied
      to a different org's token
  [Y] Teardown: zero leftover rows
