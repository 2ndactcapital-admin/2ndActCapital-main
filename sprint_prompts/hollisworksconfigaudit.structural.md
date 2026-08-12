HOLLISWORKS — COMPREHENSIVE CONFIG FIELD AUDIT. 3 tasks +
verification. THREE bugs found tonight, ALL the exact same shape:
a config field in the Hollisworks Auth0 client silently fell back
to a shared, 2nd-Act-scoped value instead of using a Hollisworks-
specific one (tenant domain/clientId — fixed; appBaseUrl — fixed;
audience — CURRENTLY BROKEN: real error "Service not found:
https://api.2ndactcapital.com" persists even with
HOLLISWORKS_AUTH0_AUDIENCE now set in Vercel). Stop fixing these
one at a time through live testing — find and fix ALL of them in
one pass.

STANDING RULES: org_id never from request body; no interactive
prompts. 2nd Act's own login is CONFIRMED WORKING right now — do
not regress it, verify this explicitly for every field checked.

=== TASK 1: Enumerate EVERY config field passed to BOTH Auth0
client constructions ===
Read the REAL, current auth0.js (2nd Act) and auth0Hollisworks.js
side by side. List EVERY single configuration field either one
passes to the SDK (domain, clientId, clientSecret, secret,
appBaseUrl, audience, scope, session config, routes config,
anything else — be exhaustive, do not stop at the fields already
known to have been buggy). For EACH field in auth0Hollisworks.js,
determine: does it correctly derive from a Hollisworks-specific
source (env var or host-derived), or does it fall back to
anything shared with 2nd Act (directly or via a default)? Report
a complete table: field name | Hollisworks source | falls back to
2nd Act? (yes/no) | currently broken in production? (yes/no/
unknown).

=== TASK 2: Fix EVERY field found to have this pattern ===
For every field Task 1 identified as falling back to a shared/
2nd-Act value: fix it to derive correctly from a Hollisworks-
specific source, applying the SAME fail-loud discipline already
established in the two prior fixes (throw a clear error if the
Hollisworks-specific value genuinely cannot be determined — NEVER
silently fall back to 2nd Act's value for ANY field). Specifically
confirm the real reason HOLLISWORKS_AUTH0_AUDIENCE being set in
Vercel did NOT resolve the audience error — find the actual
current bug (similar shape to the appBaseUrl bug — likely reading
the wrong env var, or a fallback operator ordering issue, or the
audience being passed to the wrong SDK config location entirely).

=== TASK 3: Prove EVERY field, not just the ones already known
to fail ===
For each field found in Task 1, write a real, specific proof
(not just "the client constructed without erroring") that the
ACTUAL value used for a real admin.hollisworks.com request is the
correct, Hollisworks-specific one — the same standard of proof
already used for domain/appBaseUrl (asserting the EXACT expected
value, and separately proving what the OLD, buggy value would
have been for contrast where relevant).

=== VERIFICATION ===
Write verify_hollisworksconfigaudit.py (apps/api/scripts/) —
pass/fail only, no interactive prompts, teardown-at-start and
teardown-at-end.

Assertions:
  [Y] Report Task 1's complete field-by-field table explicitly
  [Y] For EVERY field identified as broken/at-risk: prove the
      REAL correct Hollisworks-specific value is now used (exact
      value assertion, not just "no error")
  [Y] Specifically: audience for a real admin.hollisworks.com
      request is EXACTLY https://api.hollisworks.com, never
      https://api.2ndactcapital.com
  [Y] 2nd Act's own client construction is confirmed completely
      unaffected for EVERY field checked — explicit regression
      proof per field, not just once generally
  [Y] Every fixed field fails loud (throws) if its Hollisworks-
      specific source is genuinely missing/malformed — never
      silently reuses 2nd Act's value
  [Y] Teardown: zero leftover rows

Report each assertion explicitly. Push when 100% pass — hold for
manual review regardless of tier. This is the THIRD sprint fixing
this same bug shape tonight — be genuinely thorough, this must be
the last one.
