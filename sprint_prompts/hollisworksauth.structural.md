HOLLISWORKS AUTH0 INTEGRATION — admin.hollisworks.com ONLY. 4
tasks + verification. Wires the NEW, separate Hollisworks Auth0
tenant into the app as a SECOND, additional auth path — used
ONLY for admin.hollisworks.com. 2nd Act's EXISTING Auth0
tenant/login must remain COMPLETELY UNTOUCHED and working exactly
as it does today for every other domain. Federating 2nd Act's
tenant INTO the Hollisworks tenant (SAML Enterprise Connection)
is explicitly OUT OF SCOPE — a separate, later step.

STANDING RULES: org_id never from request body; no interactive
prompts; light theme if any UI touched.

=== TASK 1: Discover, don't assume ===
  (a) Read the REAL current Auth0 SDK integration in this
      codebase (proxy.js, any auth0 config/client initialization)
      — confirm exactly how it's structured, whether it assumes
      exactly ONE tenant/domain, and what would genuinely be
      required to support a SECOND, separate tenant config
      selected based on which domain a request came in on.
  (b) Confirm the real, current environment variable names used
      for 2nd Act's existing Auth0 config — report them exactly
      so new Hollisworks-specific variables can be named without
      any collision risk.
  (c) Confirm how Super Admin / staff authentication currently
      works at the application layer (is_super_admin checks,
      rbac.py) — since Hollisworks staff logging in via the new
      tenant won't have a normal "org_id" the way an RIA client's
      user does, confirm how this should correctly map into the
      existing user/session model without breaking it.
Report all three findings before proceeding.

=== TASK 2: Add the second Auth0 configuration, additively ===
Based on Task 1's findings: introduce Hollisworks-specific
environment variables (distinctly named, per Task 1b, e.g.
HOLLISWORKS_AUTH0_DOMAIN/CLIENT_ID/CLIENT_SECRET — confirm
exact naming with whatever real convention fits) and extend the
Auth0 integration so a request to admin.hollisworks.com uses
THIS config, while every other domain continues using the
EXISTING config completely unchanged. Prove the existing
config's usage is genuinely untouched by this change, not just
assumed.

=== TASK 3: admin.hollisworks.com login flow ===
Build the actual login/callback page content for
admin.hollisworks.com (confirmed not to exist yet, per the
marketing sprint's discovery) using the new Hollisworks tenant
config. A successful login here should correctly establish a
session recognized by the existing is_super_admin/rbac checks
(per Task 1c's findings) — a Hollisworks staff member logging in
here should genuinely be treated as Super Admin by the rest of
the app, not create a new, disconnected session type.

=== TASK 4: Real end-to-end proof ===
Confirm a real login attempt at admin.hollisworks.com genuinely
authenticates against the NEW Hollisworks tenant (not silently
falling through to 2nd Act's tenant), and confirm 2nd Act's own
login flow is completely unaffected — a real, explicit regression
test, not an assumption.

=== VERIFICATION ===
Write verify_hollisworksauth.py (apps/api/scripts/) — pass/fail
only, no interactive prompts, teardown-at-start and teardown-at-
end.

Assertions:
  [Y] Report Task 1's three discovery findings explicitly
  [Y] A request/login flow to admin.hollisworks.com uses the NEW
      Hollisworks-specific Auth0 config (verify via the actual
      domain/client_id referenced in that flow, not just "it
      didn't error")
  [Y] 2nd Act's existing login flow (2ndactcapital.hollisworks.com
      or wherever it currently lives) is PROVEN unchanged — same
      config, same behavior, before and after this sprint
  [Y] A successful admin.hollisworks.com login correctly
      establishes a session recognized as Super Admin by the
      existing rbac/is_super_admin checks
  [Y] Teardown: zero leftover rows

Report each assertion explicitly. Push when 100% pass — hold for
manual review regardless of tier, given this touches the
authentication layer.
