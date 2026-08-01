HEADLESS MULTI-TENANT — SPRINT 1 (host-header tenant resolver).
4 tasks + verification. Foundational — every later piece (per-
tenant SAML routing, admin-provisioned invites) depends on this
existing first. Does NOT wire actual SAML connection selection
yet (that depends on a still-undesigned per-tenant SAML config
workflow) — this sprint only proves subdomain-to-org resolution
works correctly and safely.

CRITICAL: RLS is genuinely live in production. A pre-auth lookup
(resolving a subdomain before anyone is logged in) has NEITHER
app.current_org_id NOR is_super_admin set, and organizations'
real RLS policy requires one of those. The EXISTING /theme/public
endpoint already solved this exact problem (it does an
unauthenticated slug lookup today) — Task 1 must discover exactly
HOW it does this and reuse the SAME pattern, not invent a second
one.

The real wildcard domain (*.ripasso.com in Vercel/DNS) is NOT yet
provisioned — that is a separate, manual step Joe does outside
this sprint (same class of gate as AWS/Voyage credential setup).
This sprint proves the RESOLVER LOGIC is correct using simulated
Host headers in tests (FastAPI's TestClient supports this) — it
does not require the real DNS to exist to be genuinely verified.

STANDING RULES: org_id never from request body; no interactive
prompts; light theme if any UI touched (none expected — backend
work only).

=== TASK 1: Discover, don't assume ===
  (a) Read the REAL current /theme/public implementation
      (apps/api/routers/org_settings.py) — confirm EXACTLY how it
      performs its unauthenticated organizations lookup under live
      RLS. Does it use a separate bypass-capable connection, a
      SELECT-only carve-out, or something else? Report the real
      mechanism precisely.
  (b) Read the REAL current organizations.slug column — confirm
      whether ANY format validation exists today (it was
      discovered earlier this session as a plain UNIQUE text
      column with no evident validation) — confirm this is still
      true.
  (c) Read the REAL current proxy.js — confirm it still only runs
      Auth0's session middleware (per the earlier audit) with no
      tenant logic, to confirm exactly where new resolver logic
      should be added.
  (d) Read the REAL current POST /orgs (Super Admin org creation)
      — its exact current validation and required fields.
Report all four findings before proceeding.

=== TASK 2: Host-header tenant resolver ===
Build a real resolver (reusing Task 1a's exact pre-auth-safe
lookup pattern):
  - Given a request's Host header, extract the subdomain (e.g.
    "myria" from "myria.ripasso.com").
  - Look up organizations WHERE slug = <subdomain>.
  - If found: make that org's id/slug available for pre-auth use
    (e.g. for a future SAML-connection-selection step — this
    sprint just resolves it, does not act on it yet).
  - If the request is to the bare/default domain (2ndactcapital.
    com, or ripasso.com with no subdomain) OR no matching slug is
    found: fall back to the EXISTING DEFAULT_ORG_ID behavior —
    2nd Act's own current operation must be completely unchanged.
  - Handle a malformed/unexpected Host header gracefully — never
    crash, fall back to default.

=== TASK 3: Slug validation on org creation ===
Extend POST /orgs (Task 1d's real endpoint) to validate a new
org's slug is genuinely DNS-safe before accepting it: lowercase
letters/numbers/hyphens only, reasonable length limits, and
REJECT reserved words that would collide with real platform
routes (at minimum: www, api, admin, app, mail — use judgment on
a reasonably complete list). This validation matters now because
slug is about to become a literal, live subdomain.

=== TASK 4: Prove 2nd Act's own operation is unaffected ===
This is a critical regression check, not optional: confirm every
existing request path (login, dashboard, any real endpoint) for
2nd Act's own users behaves IDENTICALLY before and after this
change — the new resolver logic must be purely additive for the
default-domain case.

=== VERIFICATION ===
Write verify_multitenant1.py (apps/api/scripts/) — pass/fail
only, no interactive prompts, teardown-at-start and teardown-at-
end.

Assertions to include:
  [Y] Report Task 1's four discovery findings explicitly
  [Y] A simulated request with Host header "myria.ripasso.com"
      against a REAL seeded org with slug='myria' correctly
      resolves to that org's real id
  [Y] A simulated request with Host header "2ndactcapital.com"
      (no subdomain) correctly falls back to DEFAULT_ORG_ID —
      2nd Act's own behavior unchanged
  [Y] A simulated request with an UNRECOGNIZED subdomain (no
      matching slug) falls back gracefully to DEFAULT_ORG_ID, no
      error
  [Y] A malformed/garbage Host header does not crash the resolver
  [Y] Creating a new org with an invalid slug (uppercase, special
      characters, or a reserved word like "admin") is correctly
      REJECTED
  [Y] Creating a new org with a valid, available slug succeeds
  [Y] The pre-auth lookup genuinely works under LIVE RLS (test
      against the real app_service connection, not a bypass role
      — this is the assertion that actually proves Task 1a's
      pattern was correctly reused, not just claimed)
  [Y] A real existing 2nd Act endpoint (e.g. login flow or a
      simple authenticated GET) behaves identically to its
      pre-sprint behavior — the regression check from Task 4
  [Y] Teardown: zero leftover rows

Report each assertion explicitly. Push when 100% pass — hold for
manual review regardless of tier, given this touches the request
path for every single user of the platform, including 2nd Act's
own.
