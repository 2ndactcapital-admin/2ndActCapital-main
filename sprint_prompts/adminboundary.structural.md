ADMIN SURFACE TENANT BOUNDARY — DISCOVERY ONLY.
1 task. No schema changes, no code changes, unless Task 1 finds
something that needs an immediate hard stop. This sprint's output is
a REPORT, not a build.
WHY THIS SPRINT EXISTS: three prior sprints (S31, underlyingresolution,
notetermsrouting) gated global structured-notes data behind checks that
all reduce to "is_super_admin=true" — a Postgres session variable set
somewhere in the backend, and a Postgres RLS check. Nothing in any of
those sprints verified WHICH condition sets that variable, whether it
can ever be true for an account belonging to a tenant org (e.g. 2nd Act
Capital) rather than Hollisworks staff, or whether the admin routes
are reachable from any tenant's own domain/deployment.
CONFIRMED LIVE, before this sprint starts (do not re-derive):
public.users has NO is_super_admin column. Its columns are:
id, org_id (NOT NULL), email, full_name, auth0_sub, role (NOT NULL,
text), created_at, updated_at, profile_id, manager_id, and invite-
related fields. This is almost certainly the table the app's own
login/session logic actually populates from Auth0.
auth.users DOES have is_super_admin (boolean, nullable). This is
Supabase's own built-in auth schema — used for Supabase Auth, which
per DEVELOPMENT_ENVIRONMENT.md is NOT how this app authenticates
(Auth0 is, via two separate tenants). It is plausible auth.users is
largely unused/vestigial in this app, or it is plausible it is
actively used for something. UNKNOWN — find out, do not assume
either way.
Every RLS policy verified in this session's sprints checks
current_setting('app.is_super_admin', true) = 'true'. That GUC has
to be SET by backend code on the DB connection for each request.
WHICH condition triggers that SET is the central unknown this
sprint exists to resolve.
WHAT THIS IS NOT — DO NOT BUILD THESE:
Any fix. If a real gap is found (a tenant-org user CAN reach
super-admin gating), STOP, report exactly what was found with
real account/org evidence, and wait for a decision on the fix
rather than choosing one. This is a security-boundary question;
the fix approach (session claim check vs. role check vs. separate
domain enforcement) needs a human decision, not a default.
Any change to auth.users, public.users, or any RLS policy.
Any change to Auth0 configuration — this sprint can only read
what's in the codebase and the database, not Auth0's dashboard.
If Auth0 configuration is the missing piece, report that plainly
as something ONLY Joe can check (dashboard access), not something
Claude Code can verify from the repo.
STANDING RULES: read-only investigation. No migrations. No merges
needed if nothing was changed — Part 4 may be "nothing to merge."
THERE IS NO HUMAN AVAILABLE. Report discovery, then continue
immediately to the next sub-item. Do not stop and wait between 1a-1h
unless one of them produces the STOP condition below.

=== TASK 1: DISCOVER — the actual authorization chain ===
1a. Find where 'app.is_super_admin' is SET on a database connection.
grep the whole apps/api tree for it (both the string literal and
any constant/helper wrapping it). Report the exact file, function,
and the CONDITION that causes it to be set to 'true' — read the
actual code, quote the real boolean expression, do not paraphrase
or guess.
1b. Trace that condition back to its source. If it reads
current_user.role, report every value 'role' can hold (query
DISTINCT role FROM public.users) and confirm which value(s) map
to super-admin. If it reads a session/JWT claim instead, find
where that claim is set during login/token exchange and report
it verbatim from the code.
1c. Confirm whether public.users.auth0_sub can be correlated to WHICH
Auth0 tenant issued the login (2nd Act's tenant vs. the
Hollisworks staff tenant, per DEVELOPMENT_ENVIRONMENT.md section
2). Is the issuing tenant recorded anywhere at all — in
public.users, in a JWT claim the backend reads, in Auth0
application/connection config visible in the repo (not the
Auth0 dashboard)? If NOTHING in the codebase distinguishes which
Auth0 tenant a session came from, report this explicitly as a
finding, not a non-finding.
1d. Query (read-only, via Supabase MCP): every row in public.users
where role indicates elevated/admin access (whatever 1b's
DISTINCT query reveals), joined to their org_id and the org's
name/domain from whatever table names orgs. Report EVERY org
that has at least one such user — do not filter to only the
orgs you expect. This is the direct test of Joe's question: does
any 2nd Act (or other tenant) user currently carry elevated
access.
1e. Confirm whether admin.hollisworks.com (or however the admin
surface is actually addressed) is:
(i)  a separate Vercel deployment/project with its own domain, or
(ii) a route inside the SAME Next.js app that also serves
tenant-facing pages, reachable at e.g.
2ndactcapital.hollisworks.com/admin/... if the route guard
ever failed
Check apps/web's routing config, vercel.json / project settings
references in the repo, and any middleware that gates by
hostname vs. by role. Report which it actually is — this
determines whether a role-check bug is the ONLY thing standing
between a tenant user and these screens, or whether there's also
a network/deployment-level wall.
1f. Specifically re-verify the three sprints' gating as actually
written in the merged code (not the verify script's fixture
logic, the REAL request-handling code path): S31's pricing
router, underlyingresolution's endpoints, notetermsrouting's
queue endpoints. Confirm each one's auth dependency ultimately
resolves through the SAME mechanism found in 1a, and flag if any
of the three do something different from the other two (that
would itself be a bug — inconsistent gating across three
features that are supposed to have identical access rules).
1g. Check whether public.users has any row where org_id points to
2nd Act Capital's org AND role is whatever 1b identifies as
elevated. This is 1d filtered to the specific case Joe asked
about. Report by exact email/org, not just a count.
1h. Report whether org_settings or any other config table has a
per-org flag that's SUPPOSED to gate this module (recalling the
earlier finding that features.* was proposed but never built) —
i.e., is there currently ANY entitlement layer at all beyond the
super-admin role check, or is role-check the entire boundary
today.
*** STOP CONDITION ***
If 1d or 1g finds a REAL, currently-existing user account in a
tenant org (2nd Act or otherwise) with elevated/super-admin access,
STOP after completing the rest of Task 1's read-only items, and lead
the report with that finding first, in plain language, with the
specific account. Do not treat this as routine — it is the exact
scenario this sprint exists to check for.

=== TASK 2: REPORT ===
Produce a single clear report (in the sprint's final summary, and as
docs/admin_boundary_discovery_findings.md) answering, in this order:
Can a 2nd Act (or other tenant) user, TODAY, reach the structured-
notes admin surfaces? Yes/no, with the exact evidence from 1a-1g.
What is the ACTUAL mechanism gating these routes — quote the real
code, not a description of what it's supposed to do.
Is there any deployment/domain-level separation, or is role-check
the entire boundary (1e, 1h)?
Are the three sprints' gating mechanisms consistent with each
other (1f)?
What Joe needs to check OUTSIDE the repo (Auth0 dashboard,
Vercel project settings) that this sprint cannot verify from code
alone.
A plain recommendation — but only as a suggestion for the next
sprint, not something this sprint should build.
No verify script for this sprint — there is nothing to assert pass/
fail against; the deliverable is the report itself. State this
explicitly rather than fabricating a checklist.
