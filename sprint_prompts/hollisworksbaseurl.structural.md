HOLLISWORKS — CALLBACK BASE URL FIX. 3 tasks + verification. A
real, confirmed bug: logging in at admin.hollisworks.com/auth/
login correctly reaches the HOLLISWORKS Auth0 tenant (confirmed:
error appears on dev-gy85vzuf6mruzv3j.us.auth0.com, proving
tenant SELECTION works), but the app sends
redirect_uri=https://2ndactcapital.com/auth/callback — the WRONG
base domain. Real, exact Auth0 error message: "unauthorized_
client: Callback URL mismatch. https://2ndactcapital.com/auth/
callback is not in the list of allowed callback URLs."

STANDING RULES: org_id never from request body; no interactive
prompts. 2nd Act's own login is CONFIRMED WORKING right now — do
not regress it.

=== TASK 1: Discover, don't assume — find the real base-URL
config ===
  (a) Read the REAL current auth0Hollisworks.js and
      resolveAuthTenantForHost() (from the last sprint's fix) —
      confirm EXACTLY which config field the Auth0 SDK uses to
      construct redirect_uri (commonly appBaseUrl/baseURL, but
      confirm the REAL field name this SDK version actually uses
      — do not assume).
  (b) Confirm whether this field is currently being set at ALL
      for the Hollisworks client construction, or whether it's
      falling through to a shared/default env var (e.g.
      APP_BASE_URL) that's hardcoded/defaulted to
      2ndactcapital.com.
  (c) Confirm the REAL equivalent config for 2nd Act's own
      client construction — how does IT correctly get its own
      base URL today, so the fix follows the same real pattern
      rather than inventing a new one.
Report all three findings before proceeding.

=== TASK 2: Fix — derive the base URL from the ACTUAL request
host ===
Based on Task 1's findings: the Hollisworks client construction
must derive its base-URL field from the REAL incoming request's
Host header (admin.hollisworks.com) — the SAME way tenant
selection itself already correctly does — not from a shared,
hardcoded default. Apply the SAME "fail loud, never silently use
the wrong host" discipline as the previous sprint's Bug 2 fix —
if this can't be correctly determined, throw a clear error rather
than silently falling back to 2nd Act's base URL again.

=== TASK 3: Real, live-equivalent proof ===
Prove the ACTUAL constructed redirect_uri value for a real
request to admin.hollisworks.com/auth/login is
https://admin.hollisworks.com/auth/callback — not just that the
right tenant/domain was selected (already proven last sprint),
but that THIS SPECIFIC value is now correct too. Also prove 2nd
Act's own constructed redirect_uri is completely unaffected.

=== VERIFICATION ===
Write verify_hollisworksbaseurl.py (apps/api/scripts/) —
pass/fail only, no interactive prompts, teardown-at-start and
teardown-at-end.

Assertions:
  [Y] Report Task 1's three discovery findings explicitly
  [Y] A real request to admin.hollisworks.com/auth/login
      constructs a redirect_uri starting with
      https://admin.hollisworks.com — assert the EXACT value,
      not just "it changed"
  [Y] 2nd Act's own login-initiation still constructs its
      correct, unchanged redirect_uri — the regression check
  [Y] If the base-URL cannot be determined for some edge case,
      confirm it fails loudly rather than silently defaulting to
      2nd Act's domain again
  [Y] Teardown: zero leftover rows

Report each assertion explicitly. Push when 100% pass.
