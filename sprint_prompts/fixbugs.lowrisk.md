BUG FIX SPRINT — two known, diagnosed production issues. 2
tasks + verification. Do not expand scope beyond these two
fixes.

=== TASK 1: /admin/profiles and /api/v1/profiles return 404 ===
Profiles DATA is confirmed correctly seeded (4 profiles under
the real org). This is a routing/registration bug, not a data
bug. Steps:
  (a) Read apps/api/routers/profiles.py — find its actual
      declared router prefix/path.
  (b) Read apps/api/main.py — find how (or whether) this router
      is registered/included. The commit that added this router
      (soca.lowrisk) only added 2 lines to main.py — verify that
      registration is actually complete and correct (right
      import, right include_router call, right prefix).
  (c) Also check apps/web/app/admin/profiles/page.js and
      apps/web/lib/permissionActions.js (or wherever the
      frontend calls the profiles API) — confirm the frontend is
      calling the CORRECT path matching whatever the backend
      actually exposes.
  (d) Fix whatever mismatch is found. Confirm both
      /admin/profiles (the page) and /api/v1/profiles (the API)
      work correctly afterward. Also verify /admin/permission-
      sets and its underlying API work (same router file/
      registration pattern, may have the same bug).

=== TASK 2: /admin/platform theme-caching bug ===
Root cause already isolated: apps/web/lib/theme.js's
loadTheme() calls fetchAPI("/api/v1/theme") for the
AUTHENTICATED path WITHOUT cache: "no-store" — unlike the
public-fallback fetch a few lines below it, which DOES specify
it. This causes Next.js Server Component fetch caching to serve
a stale pre-promotion role, so a genuine super_admin sees
"Platform settings are restricted to Super Admins."
  Fix: add cache: "no-store" (or an equivalent revalidate: 0)
  to the authenticated fetchAPI("/api/v1/theme") call in
  loadTheme(). Confirm /admin/platform correctly shows the
  Super Admin view for a user whose DB role is super_admin
  after this fix.

STANDING RULES: org_id never from request body; no interactive
prompts; light theme if UI is touched; do not modify anything
beyond these two specific bugs.

=== VERIFICATION ===
Write verify_fixbugs.py (apps/api/scripts/), same pattern as
prior verify scripts — pass/fail only, no interactive prompts.

Assertions to include:
  [Y] GET /api/v1/profiles returns 200 with the 4 seeded
      profiles (not 404)
  [Y] GET /api/v1/permission-sets (or equivalent) returns 200,
      not 404
  [Y] loadTheme()'s authenticated fetch call includes
      cache: "no-store" (grep-based check on the actual file)
  [Y] npm run build exits 0
  [Y] No hardcoded Signature-palette hex introduced in any
      modified file

Report each assertion explicitly (pass/fail). In your final
summary, state clearly what the actual root cause of the 404
was (the specific mismatch found), since this wasn't confirmed
before this sprint started. Push when 100% pass.
