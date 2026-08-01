MULTI-TENANT URL/ORG RESOLUTION — DISCOVERY ONLY. 1 task, no
code changes, no schema changes. This is a pure audit to answer
real architectural questions before any design/build work is
scoped. Do NOT fix, build, or change anything found — report only.

=== TASK 1: Answer these questions from the REAL, current code
===
  (a) Read the REAL get_org_id(request) implementation (used
      throughout every router this session) — exactly how does it
      determine which org a request belongs to? Confirm: is it
      resolved PURELY from the authenticated user's own
      users.org_id, or is there ANY logic reading a subdomain,
      custom domain, path segment, or query parameter? Quote the
      real function.
  (b) Read the REAL frontend routing config — next.config.mjs
      (already known to have no rewrites), any middleware.js/ts
      file (search broadly, confirm whether one exists at all),
      and vercel.json if present (also already searched once
      before and not found at the app-level, but re-confirm at
      the true repo root and any other location).
  (c) Confirm the REAL current domain setup: is 2ndactcapital.com
      the ONLY domain this app responds to, or does Vercel's
      project configuration (if inspectable from the repo, e.g. a
      committed vercel.json or documented domains list) show
      anything about subdomain or custom-domain support already
      configured or planned?
  (d) Confirm how a user's org_id gets set in the FIRST place —
      re-read the real ensure_user/first-login flow. Is org_id
      assigned based on anything in the URL/request at signup
      time, or purely a hardcoded/default org, or something else?
      This matters directly for question (a)'s answer to make
      sense end-to-end.
  (e) Search the codebase for ANY existing reference to
      subdomain, custom_domain, tenant_domain, or similar
      concepts — even unused/planned/commented-out code — that
      might indicate this was designed for but not finished,
      versus never attempted at all.

=== OUTPUT ===
Write a plain report (not a verify script — this is pure
discovery, no pass/fail assertions apply) to
docs/multitenant_url_audit.md summarizing:
  - The definitive, code-grounded answer to "is org resolution
    based purely on the logged-in user, with nothing in the URL
    distinguishing tenants" — yes or no, with the exact evidence
  - Whether ANY subdomain/custom-domain infrastructure exists in
    any form, even partial/unused
  - What this means concretely for the white-label vision: if a
    second org (a real licensee RIA) existed today, could its
    users reach a distinctly-branded URL, or would they log into
    the exact same 2ndactcapital.com and simply see their own
    org's branding via org_settings once authenticated?
  - Do NOT propose a fix or a design in this report — that is
    explicitly a SEPARATE, later conversation once the real
    current state is understood. This task is audit only.

Commit the report file directly (docs/multitenant_url_audit.md)
— no verify script needed, no merge-gate tier applies since
nothing else changed. Just a straightforward commit + push to the
feature branch for review.
