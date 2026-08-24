DASHBOARD SESSION CHECK — HOST-AWARE FIX. 3 tasks + verification.
Real, confirmed live bug: apps/web/app/dashboard/page.js imports
the FIXED 2nd Act client (@/lib/auth0) directly and checks the
session with it, regardless of host. admin.hollisworks.com/login
correctly authenticates via getAuthClientForHost (the Hollisworks
tenant), but dashboard/page.js's session check never recognizes
that session, redirects back to /login, which redirects back to
/dashboard — a real, observed browser redirect loop ("too many
redirects").

STANDING RULES: no interactive prompts. 2nd Act's own dashboard
access is CONFIRMED WORKING right now — do not regress it.

=== TASK 1: DISCOVER — do not assume ===
Report findings, THEN CONTINUE IMMEDIATELY in the same response.
  1a. Confirm dashboard/page.js's real current session check
      (already known: `import { auth0 } from "@/lib/auth0"` used
      directly, host-unaware). Find every OTHER page under
      apps/web/app that ALSO imports @/lib/auth0 directly rather
      than using getAuthClientForHost — dashboard is confirmed,
      there may be others with the identical bug.
  1b. Confirm getAuthClientForHost's real signature and return
      shape (used correctly in login/page.js and proxy.js) — this
      fix must call it the SAME way, not reinvent host detection.
Report both findings before proceeding.

=== TASK 2: FIX — every page found in Task 1a ===
For dashboard/page.js AND every other page Task 1a found with the
same pattern: replace the direct `@/lib/auth0` import with
getAuthClientForHost(host), reading the real Host header the same
way login/page.js already does. The redirect-on-no-session target
must remain per-host correct too (a Hollisworks-tenant session
with no valid session should redirect back through the SAME host-
aware login path, not assume 2nd Act's).

=== TASK 3: PROVE THE LOOP IS CLOSED, BOTH DIRECTIONS ===
  - A real Hollisworks-tenant session (admin.hollisworks.com)
    passes dashboard's session check and renders — no redirect.
  - A real 2nd Act session on 2nd Act's own host STILL passes
    correctly — the regression check.
  - No session on EITHER host correctly redirects once, not in a
    loop.

=== VERIFICATION: apps/api/scripts/verify_dashboardhostfix.py
(or the equivalent apps/web test location if this project's
frontend tests live elsewhere — confirm the real convention in
Task 1, do not assume apps/api is right for a frontend-only fix) ===
Pass/fail only. No interactive prompts.

Assertions:
  [Y] Report Task 1's two findings explicitly, including every
      additional file found with the same host-unaware pattern
  [Y] A Hollisworks-tenant session passes dashboard's check —
      real proof, not a signature check
  [Y] A 2nd Act session STILL passes dashboard's check unchanged
      — the regression check
  [Y] No session on either host produces exactly ONE redirect,
      not a loop
  [Y] Every other file found in Task 1a is fixed identically and
      proven the same way

Report each assertion explicitly. Push when 100% pass.
