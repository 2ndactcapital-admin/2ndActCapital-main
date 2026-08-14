HOLLISWORKS PLATFORM ORG + LANDING FIX. 5 tasks + verification.
Three small, related fixes on the same login/resolver surface.

CONTEXT — Part 1 SQL already applied directly:
  - The old S24 platform org (id bb347258-8f28-4f49-8cc9-
    e29ccad82884, formerly name 'Ripasso' / slug
    'ripasso-platform') is now name 'Hollisworks', slug
    'hollisworks', with login_url and enroll_url both set to
    https://admin.hollisworks.com/auth/login.
  - 2nd Act (00000000-0000-0000-0000-000000000001) now has
    login_url https://2ndactcapital.hollisworks.com/auth/login
    and enroll_url https://2ndactcapital.hollisworks.com/enroll
    (both were NULL — the firm-search feature depends on them).

DESIGN DECISION, CONFIRMED — this REVERSES an earlier one, do
not "correct" it back: Hollisworks is now a REAL org row like
any client, NOT a special case kept out of the organizations
table. Rationale: staff land in a normal org context like anyone
else; it can hold real demo data later (admin.hollisworks.com
may become the demo site once 2nd Act is a live RIA); and the
firm-search special case disappears entirely.

IMPORTANT: the slug is 'hollisworks', NOT 'admin' — 'admin'
remains a RESERVED slug (Sprint 1's validation) and must stay
reserved. So admin.hollisworks.com needs an explicit host->org
mapping to the 'hollisworks' slug, since the subdomain and the
slug deliberately differ for this one org.

STANDING RULES: org_id never from request body; no interactive
prompts; light theme.

=== TASK 1: Discover, don't assume ===
  (a) Read the REAL current tenant resolver (services/tenant.py,
      resolve_tenant/extract_subdomain) — confirm exactly how a
      subdomain maps to an org today, and where a host->slug
      mapping for admin.hollisworks.com should live so 'admin'
      stays reserved while still resolving to the 'hollisworks'
      org.
  (b) Read the REAL current app/login/page.js — confirm the
      exact line sending admin-host logins to /admin-console
      (a route that does NOT exist — this is why an authenticated
      staff user currently bounces to the marketing page).
  (c) Read the REAL current firm-search matching logic — confirm
      the existing hardcoded "Hollisworks" special case, which
      should now be REMOVED in favor of normal org matching
      (since Hollisworks is a real org row with a real slug and
      real stored URLs).
  (d) Confirm apps/web/app/admin/ has no page.js at its root
      (14 subdirectories, no index) — Joe navigated to /admin
      from a real menu link and got a 404. Confirm whether a
      real nav item points at bare /admin.
Report all four findings before proceeding.

=== TASK 2: Host->org mapping for admin.hollisworks.com ===
Per Task 1a: make admin.hollisworks.com resolve to the
'hollisworks' org. 'admin' MUST remain a reserved slug — do not
resolve this by simply setting the org's slug to 'admin'. Keep
the mapping explicit and narrow.

=== TASK 3: Fix the post-login landing ===
Per Task 1b: an authenticated user on admin.hollisworks.com
should land in the NORMAL app (/dashboard), exactly like any
other user — their super_admin role naturally surfaces the admin
menu items that already exist. There should be NO separate admin
console. Remove the /admin-console reference.

=== TASK 4: Simplify firm-search ===
Per Task 1c: remove the hardcoded "Hollisworks" special case —
it now resolves through normal org matching. ALSO add sensible
aliases so a staff member typing 'admin', 'hollis', or
'hollisworks' reaches the right place. Use your judgment on
whether that's an alias list in the matching logic or something
cleaner — but do NOT add an extra org row for aliases.

=== TASK 5: Fix bare /admin ===
Per Task 1d: bare /admin currently 404s. Build a minimal index —
either a simple landing page listing the admin sections the
current user actually has permission for, or a redirect to the
first such section. Reuse the REAL existing permission checks
(services/profiles.py user_has_permission and/or rbac) — do not
invent new gating. Keep it simple; a full menu rationalization
(splitting org-scoped vs. platform-scoped admin items) is a
SEPARATE, later effort — do not attempt it here.

=== VERIFICATION ===
Write verify_hollisworksorg.py (apps/api/scripts/) — pass/fail
only, no interactive prompts, teardown-at-start and teardown-
at-end.

Assertions:
  [Y] Report Task 1's four discovery findings explicitly
  [Y] Host 'admin.hollisworks.com' resolves to the 'hollisworks'
      org (id bb347258-...), NOT the default/2nd Act org
  [Y] 'admin' is STILL a reserved slug — creating an org with
      slug 'admin' is still rejected
  [Y] Host '2ndactcapital.hollisworks.com' still resolves to 2nd
      Act correctly (regression check)
  [Y] Host 'hollisworks.com' (bare) still serves the marketing
      page (regression check)
  [Y] An authenticated admin-host login lands at /dashboard, not
      /admin-console
  [Y] Firm-search: 'Hollisworks' resolves via NORMAL org matching
      to the real stored login_url — no hardcoded special case
  [Y] Firm-search: 'admin' and 'hollis' also resolve correctly
  [Y] Firm-search: '2nd Act Capital' still resolves to its real
      stored login_url (regression check)
  [Y] Bare /admin no longer 404s and respects real permissions
  [Y] npm run build exits 0
  [Y] Teardown: zero leftover rows

Report each assertion explicitly. Push when 100% pass — hold for
manual review regardless of tier.
