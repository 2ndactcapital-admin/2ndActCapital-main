DEALROOM1 — Phase 1 of the Marketplace Deal Room: publishers on CRM records,
deal-scoped publisher and delegate rights, a fixed nine-folder data room per
deal with folder access levels, the platform NDA and joinder with fill-in
fields and a lightweight in-house e-signature, an eligibility check from the
CRM, automatic approval by default, and an access grid to approve and revoke.
10 tasks + verification.

Context: the approved spec is the "Marketplace Deal Room Spec" doc (Joe,
2026-10-22). Phase 1 is this sprint; Phase 2 (publisher's own agreement
uploads, versions with re-signing, master NDA, certificate page) and Phase 3
(watermark, view-only, expiry, activity analytics, Q&A, investor advisors,
506(b)/(c) rules) come later — do not build them. Joe's decisions: everyone
can publish at launch (2nd Act staff, outside sponsors, placement agents);
default approval is AUTOMATIC after signing; the nine folders stand; agreements
start from the generalised templates in docs/templates/deal_room_templates_v1.md
(DRAFT, counsel to review); investors never see who else has access; signature
is a typed name plus a consent box.

CONFIRMED REAL FACTS, DO NOT RE-DERIVE (live DB / main, 2026-10-22):
- public.deals: id, org_id, name, deal_status, deal_stage (from the deal_stages
  config, bugfix1), sponsor_entity_id (CRM link), sponsor_name_override,
  submitted_by, created_by, published_at, bi-temporal valid_*/system_* columns.
- public.deal_documents: id, org_id, deal_id, file_name, file_type, r2_key,
  r2_bucket, document_type, processing_status, extracted_data, uploaded_by,
  status, reviewed_by, visible_to_members (one boolean — the only access
  control today). bugfix1 made deal attachments searchable under the deal's
  visibility rule.
- public.compliance_records (per CRM entity, bi-temporal): kyc_status,
  ofac_screen_status, accreditation_status, accreditation_basis,
  accreditation_verified_date, next_reverification_due, pep_status.
- public.deal_interest (deal_id, user_id, entity_id, status, amount_interest,
  investment_stage), deal_votes, member_investments exist.
- Permissions vocabulary includes view_deals, manage_deals, vote_deal,
  indicate_interest, score_deal. authz1 (merged): services/authz.py Principal
  from the DB, fail-closed, Super Admin first; AUTHZ1_ROUTES.json must list
  every route with its gate (authz1_routes.py --check).
- Documents live in R2 (bucket hollisworks-docs pattern; deal_documents.r2_key).
- Brand: content light/cream/white, navy frame; Rule 1 (labels from
  config/API), bi-temporal writes, permission envelope with no truthy fallback,
  statement_cache_size=0, org_id only from the session.
- The custody cliff: no agent may execute trades, money movement, filings or
  GL posting. Signing an NDA is a person's own action in the UI, never an
  agent's.

THERE IS NO HUMAN AVAILABLE. Report findings, then continue immediately in the
same response. If uncertain, continue.

STANDING RULES: no interactive prompts. There is NO background-process
notification mechanism — never wait for one, never poll in a loop, never start
a script in the background; run every command synchronously in the foreground.
Write the verify script but DO NOT RUN IT. No real R2 writes in tests (mock).
Never print a secret. Column names from the live DB. Every new route gets an
explicit gate and an entry in AUTHZ1_ROUTES.json. Never change an existing
route's gate. Do not delete real documents or deals.

=== TASK 1: DISCOVER ===
Report findings, then continue immediately.
  1a. How a platform user maps to their CRM person and the entities they may
      invest through (delegate_grants, entity relationships, visible_entity_ids)
      — the investing entity list for step 2 of the investor path.
  1b. Who can create and edit deals today (gates on deal routes), and how
      sponsor_entity_id is set.
  1c. The deal detail page's Documents tab and upload path (component, API,
      R2 key pattern), and how visible_to_members is enforced.
  1d. Whether any notification / to-do mechanism can carry "access granted",
      "new request" and "signed" events (member_todos, notifications).
  1e. How PDFs are generated anywhere in the app today (for the signed copy),
      or that none exists.

=== TASK 2: PUBLISHERS AND DELEGATES (SCHEMA + RIGHTS) ===
  - CRM capability "publisher" on an entity (person or firm), with the users
    allowed to act for it. deals.publisher_entity_id (default: the creating
    user's CRM person; staff may set any publisher entity in the org).
  - deal_delegates (deal_id, user_id, granted_by, bi-temporal or
    granted/revoked timestamps).
  - Rights: a deal's publisher users and delegates may edit it, manage its
    documents, settings and access; staff with manage_deals keep firm-wide
    rights; org admin and super admin see and control all in scope. A member
    who is not publisher, delegate or staff cannot edit any deal (403).
    Create-deal requires the user to act for a publisher entity (or staff).
  - Migration: existing deals get publisher_entity_id from sponsor_entity_id
    where set, else the creator's CRM person; report each.

=== TASK 3: DATA ROOM FOLDERS ===
deal_folders (deal_id, key, label, sort_order, access_level, parent_id for
publisher subfolders). The nine standard folders (labels from config, Rule 1):
01 Overview (public), 02 Offering Memorandum (after_agreements),
03 Presentations (after_agreements), 04 Financials (after_agreements),
05 Legal & Structure (after_approval), 06 Due Diligence (after_approval),
07 Q&A (after_agreements), 08 Subscription (named_only), 09 Other
(after_agreements). Access levels: public, after_agreements, after_approval,
named_only. Created for every deal on creation and backfilled for existing
deals; existing deal_documents move into 09 Other keeping their current
visibility (visible_to_members false → named_only? no: false → after_approval;
true → after_agreements; report the mapping per document). Publisher can
change a folder's level and add subfolders, not delete the nine.
Upload, move and delete documents by folder (publisher / delegate / staff).

=== TASK 4: AGREEMENT TEMPLATES ===
Copy docs/templates/deal_room_templates_v1.md into the repo (already placed by
the operator; if absent, stop that task and report). agreement_templates (key,
version, title, body with {{fields}}, field definitions, scope platform|org,
status). Seed platform_nda v1 and platform_joinder v1 from the file, marked
DRAFT. deal_agreements (deal_id, template_id, sort_order, field values,
exhibit document id for the joinder's Underlying NDA PDF). Rendering fills
fields from the deal settings and the recipient's CRM record; a missing
required field blocks publishing the agreement with a clear message.

=== TASK 5: SIGNING ===
deal_signatures (deal_id, deal_agreement_id, template_id, template_version,
rendered_text_sha256, user_id, entity_id signed for, typed_name, consent
boolean, signed_at, ip, user_agent, prev_signature_sha256, record_sha256). The
investor reads the fully rendered agreement, types their full name, ticks "I
agree to sign electronically", and signs; agreements are signed in sort order.
record_sha256 = SHA-256 over the canonical record including
prev_signature_sha256 (a platform-wide chain); a verifier function recomputes
the chain and reports the first break. Signatures are append-only (no update
or delete policy; RLS and a trigger refuse both). The investor and the
publisher can download the signed text (HTML or PDF per 1e; plain rendered
HTML with the signature block is acceptable in Phase 1).

=== TASK 6: ELIGIBILITY AND APPROVAL ===
Deal settings (Phase 1 subset): agreements required (ordered list), eligibility
rule (none | accredited | qualified_purchaser | qualified_client; default
accredited), approval (automatic | publisher; DEFAULT automatic), sponsor shown
in teaser (name | code name), delegates. deal_access (deal_id, user_id,
entity_id, status: viewing | requested | not_eligible | signed_awaiting |
granted | revoked, level granted, decided_by, decided_at, reason; history kept).
Eligibility reads the chosen entity's current compliance_records row (and
KYC/OFAC clear); not_eligible tells the investor what is missing and opens a
staff to-do. When all agreements are signed: automatic → granted
(after_agreements level); publisher → signed_awaiting with a to-do for the
publisher and delegates. after_approval folders always need an explicit
approval for that level; named_only needs the investor named on the folder.

=== TASK 7: INVESTOR EXPERIENCE ===
On the deal page: the teaser and 01 Overview for anyone who can view the deal;
"Request access" → choose investing entity → eligibility result → sign each
agreement → access result. Folders render with a lock and the reason when not
yet open. Investors never see other investors' access, names or counts. A
"My deal access" page lists the investor's deals, what they signed and when,
with the signed copies.

=== TASK 8: THE ACCESS GRID ===
A grid (DataGrid, server-side) at Marketplace → Deal access, scoped by role:
publisher users and delegates see their deals; staff with manage_deals and org
admin see the firm; super admin sees all. Columns: investor, investing entity,
deal, publisher, eligibility (status + verified date), each required agreement
(signed date or —), access level, status, decided by/at. Group by deal,
publisher, firm, all; filter by status, deal, publisher, unsigned. Actions,
single or bulk: approve (choose level), decline with reason, revoke. Every
action audited; revoke stops access immediately and keeps signatures.

=== TASK 9: REAL PROOF ===
  - Rights: a non-publisher member cannot edit a deal (403); the deal's
    publisher user and a delegate can; another publisher's user cannot edit
    this deal; staff can; identical requests.
  - Documents: a document in each access level is returned only to callers who
    meet it (anonymous-to-the-deal member, signed member, approved member,
    named member, publisher, staff, other org); downloads (signed R2 URLs)
    refused the same way; folder listings never leak file names of closed
    folders beyond the folder's existence.
  - Signing: rendered text hash matches what was shown; consent and typed name
    required; signature rows cannot be updated or deleted (RLS + trigger,
    proven by attempting both); chain verifier passes, and detects a tampered
    fixture row.
  - Eligibility: an entity without accreditation → not_eligible, nothing
    opens, staff to-do created; accredited entity proceeds.
  - Approval: automatic grants after_agreements on the last signature;
    publisher mode waits; after_approval folders need explicit approval.
  - Grid: each role sees only its scope; approve/decline/revoke change access
    immediately (re-read) and are audited; investors cannot reach the grid.
  - Migration: every existing deal has its nine folders, a publisher, and its
    documents mapped as reported.

=== TASK 10: UPDATE PROJECT STATUS ===
docs/PROJECT_STATUS.md and a short docs/DEAL_ROOM.md: roles and rights,
folders and levels, templates (DRAFT — counsel review before outside
publishers), the signature record and chain verifier, settings, the grid, and
what Phases 2 and 3 add.

=== VERIFICATION: apps/api/scripts/verify_dealroom1.py ===
WRITE IT. DO NOT RUN IT. Pass/fail only, no prompts. MUST print
'TOTAL: N PASS, M FAIL' and exit non-zero on failure. Set AUTHZ_TEST_MODE=1
before importing the app. Hydrate secrets from Doppler over HTTPS. Mock R2.
Wrap every section so an exception is a [FAIL]; teardown at start and end in
FK child-before-parent order from pg_constraint, then re-read every touched
table for its exact pre-test count (append-only signature fixtures are removed
by teardown through a privileged teardown path that the app role cannot use —
document how). Fixture users created explicitly with the auth0_sub the mocked
token carries, is_test = true. Seed fixtures so they satisfy every live
UNIQUE, CHECK and FK constraint (read them from pg_constraint before seeding —
a seed that violated a partial unique index broke bugfix1's first run).

Verify-script rules — each one broke a real script:
  - check(passed, label, detail): condition FIRST; isinstance(passed, bool).
  - Never derive a unique value from a fixture-UUID slice.
  - RLS context on every read; app_service with rolbypassrls = false asserted
    for isolation checks; an empty list never passes a check.
  - Never spawn a background process. Regression subprocesses get
    VERIFY_NESTED=1, run after teardown, 1200 s timeout; with VERIFY_NESTED=1
    this script skips its regression section with one '[SKIP] nested run' line.
  - Never touch real deals, documents or users except the reported migration.

Assertions:
  [Y] Task 1 findings reported (1a–1e), each with its file and query
  [Y] Publisher and delegate rights matrix (non-publisher member, publisher
      user, delegate, other publisher, staff, org admin, super admin) on deal
      edit, document upload, settings and access actions — identical requests
  [Y] Every existing deal migrated: publisher set, nine folders, documents
      mapped per the reported rule (counts equal SQL)
  [Y] Folder access: each level returns documents only to qualifying callers;
      signed-URL downloads refused identically; closed folders leak no file names
  [Y] Templates seeded from the docs file (version 1, DRAFT); rendering fills
      every field; a missing required field blocks with a named message
  [Y] Signing: hash of rendered text recorded; typed name and consent
      required; update/delete of a signature refused (RLS and trigger); chain
      verifier passes and flags a tampered fixture row
  [Y] Eligibility: unaccredited entity → not_eligible + staff to-do; accredited
      → proceeds; KYC/OFAC not clear → not_eligible
  [Y] Approval: automatic (default) grants after_agreements on the last
      signature; publisher mode → signed_awaiting + to-do; after_approval and
      named_only need explicit decisions
  [Y] Investor view: locked folders show the reason; no other investor's
      access, names or counts are ever returned
  [Y] Grid: scope per role; approve/decline/revoke take effect immediately
      (re-read), audited; investors refused the grid (403)
  [Y] Every new route has a gate and is in AUTHZ1_ROUTES.json (--check CURRENT);
      no existing route's gate changed
  [Y] npm run build exits 0; node tests for the investor flow and grid pass
  [Y] Regression: verify_authz1.py, verify_bugfix1.py and verify_navregroup.py
      each run ONCE as flat subprocesses AFTER teardown with VERIFY_NESTED=1;
      0 FAIL
  [Y] Teardown leaves zero fixture rows; every touched table at its pre-test
      count; real note_gold_values unchanged
