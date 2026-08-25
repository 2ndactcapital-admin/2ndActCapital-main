2ND ACT CLIENT — HOST-DERIVED appBaseUrl. 4 tasks + verification.
Real, confirmed live bug, same class already fixed once for the
Hollisworks client (hollisworksbaseurl sprint). Real error:

Callback landed on https://2ndactcapital.com/auth/callback
(the bare domain) after a signup initiated on
https://2ndactcapital.hollisworks.com — "the state parameter is
invalid" — because lib/auth0.js's appBaseUrl is a STATIC env var
(APP_BASE_URL=https://2ndactcapital.com), not host-derived. This
was never exercised from the .hollisworks.com subdomain before
tonight's enrollment flow — every prior 2nd Act login happened
on the bare domain, where the static value was correct.

THERE IS NO HUMAN AVAILABLE. Reporting discovery findings means
"report, then immediately continue in the same response" — never
stop and wait. If uncertain whether to continue, continue.

*** EXTREME CAUTION: lib/auth0.js has been deliberately left
BYTE-FOR-BYTE UNCHANGED all session, specifically because 2nd
Act's EXISTING bare-domain login has worked correctly throughout.
Do NOT change its fundamental shape. The fix must be the SAME
host-derivation PATTERN already proven for auth0Hollisworks.js
(hollisworksbaseurl sprint), applied narrowly, NOT a rewrite. ***

STANDING RULES: no interactive prompts.

=== TASK 1: DISCOVER — do not assume ===
Report findings, THEN CONTINUE IMMEDIATELY in the same response.
  1a. Re-read the REAL current lib/auth0.js — confirm exactly how
      appBaseUrl is currently set (static APP_BASE_URL env var,
      confirmed suspected but VERIFY against real code).
  1b. Re-read the REAL current auth0Hollisworks.js's
      hollisworksAppBaseUrl() — the proven, host-derived pattern
      from the hollisworksbaseurl sprint. This fix reuses the
      SAME derivation logic, not a new implementation.
  1c. Confirm every REAL host that legitimately needs to resolve
      to 2nd Act's client via getAuthClientForHost (per
      authForHost.js — confirmed earlier: "every other host" maps
      to the 2nd Act client). List every one that could plausibly
      be hit in production: 2ndactcapital.com,
      2ndactcapital.hollisworks.com, www.2ndactcapital.com if it
      exists — confirm the real list, do not assume.

=== TASK 2: FIX — host-derived appBaseUrl for 2nd Act's client ===
Apply the SAME host-derivation function/pattern from Task 1b to
lib/auth0.js's appBaseUrl construction — deriving it from the
REAL incoming request Host header, exactly as already proven safe
for the Hollisworks client. A request from 2ndactcapital.com must
continue to produce https://2ndactcapital.com (BYTE-IDENTICAL
existing behavior — this is the regression that must not happen).
A request from 2ndactcapital.hollisworks.com must now correctly
produce https://2ndactcapital.hollisworks.com.

=== TASK 3: AUTH0 DASHBOARD — confirm the callback URL is
allowed ===
Per the established explicit-listing convention (never wildcards):
confirm https://2ndactcapital.hollisworks.com/auth/callback is
present in 2nd Act's OWN Auth0 tenant's Allowed Callback URLs
(this is 2nd Act's tenant, dev-smmrfubsfscif3t1.us.auth0.com —
NOT the Hollisworks tenant). Report whether it is already present
or needs to be added — this sprint can report the finding but
cannot itself edit the Auth0 dashboard; if missing, this MUST be
called out explicitly as a manual step for Joe.

=== TASK 4: REAL PROOF, BOTH DIRECTIONS ===
  - A request originating from 2ndactcapital.com produces
    redirect_uri EXACTLY https://2ndactcapital.com/auth/callback
    — byte-identical to pre-fix behavior. THE REGRESSION CHECK,
    given this file's history tonight.
  - A request originating from 2ndactcapital.hollisworks.com
    produces redirect_uri EXACTLY
    https://2ndactcapital.hollisworks.com/auth/callback.
  - Prove the OLD, buggy value for contrast (what the static
    APP_BASE_URL would have produced) matches the exact real
    error observed: https://2ndactcapital.com/auth/callback for
    a request that should have produced the .hollisworks.com one.

=== VERIFICATION: apps/api/scripts/verify_twoactbaseurl.py (or
the real, appropriate frontend test location — confirm in Task 1,
this touches apps/web) ===
Pass/fail only. No interactive prompts.

Assertions:
  [Y] Report Task 1's three findings explicitly
  [Y] 2ndactcapital.com still produces the EXACT original
      redirect_uri — byte-for-byte regression proof
  [Y] 2ndactcapital.hollisworks.com now produces the CORRECT,
      host-matching redirect_uri
  [Y] Pre-fix contrast: the OLD logic reproduces the EXACT
      observed bug (wrong domain in redirect_uri)
  [Y] Report Task 3's Auth0-dashboard finding explicitly —
      present or needs a manual add
  [Y] Teardown: zero leftover rows
