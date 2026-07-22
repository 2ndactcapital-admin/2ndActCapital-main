SPRINT 24 — White-label config. Full sweep & replace.
LARGEST-SURFACE-AREA SPRINT TO DATE — proceed systematically,
task by task, and report progress per task rather than only at
the end.

CONTEXT: Ripasso is the licensable SOFTWARE product (name
matches the Ripasso Holdings entity). 2nd Act Capital is a
CLIENT/tenant of Ripasso — the wealth management firm, not the
software. org_settings already exists (Part 1 SQL applied):
org_id + setting_key + setting_value (jsonb, NOT NULL — always
JSON-encode scalars: '"USD"'::jsonb not 'USD') + category +
is_public + updated_at/updated_by. Natural key for upserts is
(org_id, setting_key). NOT bitemporal — plain upsert, no
valid_from/valid_to. Two orgs seeded: "Ripasso" (platform org —
Super Admins belong here, not scoped to any client) and
"2nd Act Capital" (client #1, fully seeded with real branding
under 23 setting keys — read them to see the actual key
namespace: brand.name, brand.short_name, brand.logo_url,
brand.favicon_url, brand.color.*, brand.font.*, footer.*,
locale.base_currency, naming.*).

Read docs/schema_snapshot.sql FIRST to confirm org_settings
landed exactly as described. Do not trust this prompt's
description over the live snapshot.

STANDING RULES: org_id never from request body; Decimal for
money; no interactive prompts; light theme (whites/creams)
throughout any UI touched — this sprint is literally ABOUT
making that theme configurable, so get it right.

=== TASK 1: Settings service (backend) ===
Build a settings service (apps/api/services/org_settings.py or
similar) with:
  - get_setting(org_id, key) -> value, with a documented
    in-code DEFAULT_SETTINGS fallback map (matching today's
    2nd Act values) for any org that hasn't set a given key yet
    — this protects future client onboarding before their Org
    Admin configures branding.
  - get_all_settings(org_id) -> dict, for bulk fetch (e.g. to
    hydrate a theme provider on page load)
  - set_setting(org_id, key, value, updated_by) -> upsert on
    (org_id, setting_key), JSON-encoding scalars correctly
  - Permission check: only super_admin (any org) or org_admin
    (own org only) may call set_setting. Reads are open to any
    authenticated user of that org (needed to render the theme).
Expose via API endpoints (GET/PUT on something like
/orgs/{org_id}/settings) following existing router patterns.

=== TASK 2: RBAC — super_admin and org_admin roles ===
users.role is currently free text, only 'member' in use, no
CHECK constraint (confirmed via live schema — do not assume a
different structure). Add support for 'super_admin' and
'org_admin' as valid role values:
  - Do NOT add a CHECK constraint yet (the full role taxonomy
    isn't finalized platform-wide — this would risk blocking
    values other parts of the app may assume). Just start using
    the new string values.
  - Permission-check helpers: is_super_admin(user) (role ==
    'super_admin'), is_org_admin(user, org_id) (role ==
    'org_admin' AND user.org_id == org_id), and a combined
    can_manage_org_settings(user, org_id) used by Task 1's
    set_setting permission check.
  - super_admin should be able to operate across ANY org_id
    (not just their own) — their own org_id is 'Ripasso'
    (platform org) but that must not restrict which orgs they
    can manage.

=== TASK 3: Super Admin settings screen ===
New admin-only screen (gated to is_super_admin): list all
organizations, select one, view/edit its full settings grouped
by category (branding / footer / locale / naming). Also a
"Create new org" flow here (name + slug), since onboarding a
new Ripasso client starts with a super_admin creating their org
row.

=== TASK 4: Org Admin settings screen ===
New admin screen (gated to is_org_admin, scoped to their own
org_id only): same category-grouped settings editor as Task 3,
but restricted to their own organization — cannot see or select
other orgs. Live color-swatch preview for the brand.color.*
keys (show the actual color next to the hex input).

=== TASK 5: Theme provider (frontend) ===
Build (or extend if one exists) a theme context/provider that
loads the current user's org settings on app load and exposes
brand.color.* / brand.font.* / brand.name / brand.logo_url as
CSS variables or a theme object consumed throughout the app —
this is what Task 6's sweep will point everything at.

=== TASK 6: FULL SWEEP — replace every hardcoded value ===
This is the core deliverable. In TWO PASSES:

PASS A — INVENTORY FIRST. Grep the ENTIRE codebase (apps/web
AND apps/api, including emails/PDF generation if any) for:
  - The literal strings "2nd Act", "2nd Act Capital", "2ndAct"
  - Every Signature palette hex value: #1B2B4B #C5A880 #E8D5A3
    #9AA6BF #FAF9F6 #F5F1EB #FFFFFF #0F172A #334155 #64748B
    #E2E8F0 (case-insensitive, with and without #)
  - Hardcoded footer URLs, support email addresses
Report the full count and file list BEFORE making any changes.

PASS B — REPLACE. Systematically replace every hit from Pass A
with a reference to Task 5's theme provider (frontend) or Task
1's settings service (backend — e.g. generated PDFs, emails).
Exceptions that are OK to leave as literal hex values: the
DEFAULT_SETTINGS fallback map itself (Task 1) and this sprint's
own seed SQL — those are allowed to contain the values since
they ARE the default/seed data, not app logic assuming them.

VERIFY PASS B WORKED: re-run the SAME grep from Pass A. It must
return ZERO hits outside the two allowed exception files. If
anything remains, it is a sprint failure — fix it, don't report
partial completion as done.

=== VERIFICATION ===
Write scripts/verify_sprint24.py (apps/api/scripts/), same
pattern as verify_sprint22/23.py — pass/fail only, no
interactive prompts, idempotent fixtures, teardown-at-start AND
teardown-at-end (disable/re-enable any triggers exactly like
Sprint 22's fix if the fixtures touch posted ledger data).

Assertions to include:
  [Y] org_settings table + constraints match the snapshot
  [Y] get_setting returns the correct value for an existing key
  [Y] get_setting falls back to DEFAULT_SETTINGS for a missing
      key on a freshly-created test org
  [Y] set_setting upserts correctly (create then update the
      same key, confirm final value)
  [Y] A 'member' role CANNOT call set_setting (403/permission
      denied)
  [Y] An 'org_admin' CAN set their own org's settings
  [Y] An 'org_admin' CANNOT set a DIFFERENT org's settings
      (403/permission denied)
  [Y] A 'super_admin' CAN set settings on ANY org
  [Y] THE SWEEP: grep the codebase from within the verify
      script itself for "2nd Act" literal strings and Signature
      hex values outside the two allowed exception files —
      assert ZERO matches. This is the hard gate on Task 6.
  [Y] Teardown: zero leftover rows, triggers/constraints intact

Report each assertion explicitly (pass/fail). Push when 100%
pass — but flag clearly if the sweep assertion is the one still
failing, since that's the highest-value/highest-risk part of
this sprint and deserves explicit attention even on a passing
run overall (report the count found, even at zero, so it's
visible the check actually ran).
