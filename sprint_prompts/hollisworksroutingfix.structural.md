HOLLISWORKS ROUTING FIXES — two real, confirmed live bugs. 4
tasks + verification. CRITICAL: 2nd Act's own core login (e.g.
2ndactcapital.com/login) is CONFIRMED WORKING right now — do not
break it. Both bugs below are real, observed in production
tonight, not hypothetical.

BUG 1: 2ndactcapital.com (the ORIGINAL, separate marketing
domain — NOT 2ndactcapital.hollisworks.com) now incorrectly shows
the Hollisworks marketing page instead of 2nd Act's own. The
tenant resolver was built reasoning about *.hollisworks.com
subdomains — 2ndactcapital.com is a completely different root
domain and was never accounted for.

BUG 2: admin.hollisworks.com/login redirects to 2ND ACT'S Auth0
tenant (dev-smmrfubsfscif3t1.us.auth0.com) instead of the NEW
Hollisworks tenant (dev-gy85vzuf6mruzv3j.us.auth0.com). A user
CAN authenticate against the wrong tenant, then gets "the state
parameter is invalid" on return. The previously-verified
auth0Hollisworks.js/authForHost.js utility logic was proven
correct in ISOLATED testing — this bug means the REAL, deployed
route files are not correctly invoking that logic, or something
about actual request handling differs from the test harness.

STANDING RULES: org_id never from request body; no interactive
prompts; light theme if any UI touched.

=== TASK 1: Discover, don't assume — find the REAL deployed
files, not just the proven utilities ===
  (a) Find the ACTUAL Next.js route file(s) Vercel serves for
      admin.hollisworks.com/login and its callback — confirm
      EXACTLY whether/how they call authForHost.js's host
      selection. Report the real, current code, do not assume it
      matches what the sprint's own isolated tests exercised.
  (b) Find the ACTUAL resolver/routing logic path a request to
      2ndactcapital.com (bare, NOT .hollisworks.com) takes through
      this app — confirm whether 2ndactcapital.com is served by
      the SAME Vercel project/deployment as everything else
      (per earlier discovery, it likely is) and exactly why it's
      falling into the Hollisworks-marketing branch.
  (c) Confirm whether Vercel's build/deployment caching could
      explain why previously-verified logic isn't reflecting in
      production — e.g. is the live deployment genuinely running
      the latest merged code, or could a stale build be involved?
      Check real deployment timestamps/commit hashes if possible.
Report all three findings, with the REAL root cause identified
for EACH bug, before writing any fix.

=== TASK 2: Fix Bug 1 — 2ndactcapital.com routing ===
Based on Task 1b's real finding: ensure a request to the bare
2ndactcapital.com domain (distinct from 2ndactcapital.hollisworks.
com) correctly renders 2nd Act's OWN marketing page, not
Hollisworks'. Do not regress 2ndactcapital.hollisworks.com's
correct behavior in fixing this.

=== TASK 3: Fix Bug 2 — admin.hollisworks.com login tenant
selection ===
Based on Task 1a's real finding: ensure the ACTUAL deployed
login-initiation route for admin.hollisworks.com genuinely uses
the Hollisworks tenant's Auth0 client (dev-gy85vzuf6mruzv3j...),
and that the callback handling is consistent with whichever
tenant initiated the flow — no state-parameter mismatch. Do NOT
regress 2nd Act's own login flow, which is confirmed working
right now — prove this explicitly, do not just assume.

=== TASK 4: Real, live-equivalent end-to-end proof for BOTH ===
Beyond automated tests, this sprint's verification must be
provably equivalent to a REAL browser hitting these REAL domains
— not just calling internal functions directly in isolation
(the gap that let both bugs through the first time). Use
TestClient with REAL Host headers matching production exactly,
and trace the FULL route (page render / login redirect / callback)
end to end.

=== VERIFICATION ===
Write verify_hollisworksroutingfix.py (apps/api/scripts/) —
pass/fail only, no interactive prompts, teardown-at-start and
teardown-at-end.

Assertions:
  [Y] Report Task 1's three discovery findings, including the
      REAL root cause identified for each bug
  [Y] A request with Host header "2ndactcapital.com" (bare, not
      .hollisworks.com) renders 2nd Act's own marketing page
  [Y] A request with Host header "2ndactcapital.hollisworks.com"
      STILL correctly renders 2nd Act's app (no regression)
  [Y] A request with Host header "hollisworks.com" STILL
      correctly renders the Hollisworks marketing page (no
      regression)
  [Y] The login-initiation flow for admin.hollisworks.com
      genuinely redirects toward the HOLLISWORKS tenant's real
      domain (dev-gy85vzuf6mruzv3j...), not 2nd Act's
  [Y] 2nd Act's own login-initiation flow (whatever real domain
      it uses) STILL correctly redirects toward 2ND ACT'S tenant
      (dev-smmrfubsfscif3t1...) — the critical regression check,
      given this is confirmed working in production right now
  [Y] Teardown: zero leftover rows

Report each assertion explicitly. Push when 100% pass — hold for
manual review regardless of tier. Given 2nd Act's login is a
LIVE, working production system, be extremely careful not to
regress it — verify this explicitly, do not assume.
