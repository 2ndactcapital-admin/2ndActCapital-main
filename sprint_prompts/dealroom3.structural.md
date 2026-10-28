DEALROOM3 — Marketplace Deal Room, phase 3 of 3: control and insight —
watermarking and view-only documents, access expiry (and pause on a lapsed
accreditation), a per-investor activity log, Q&A, investor advisors,
notifications, a return-or-destroy notice on revocation, and offering-type
rules (506(b) / 506(c)). 10 tasks + verification.

Context: spec "Marketplace Deal Room Spec" (Docs, Joe 2026-10-22), sections
"Deal settings" (rows Downloads, Watermark, Access period, Notify publisher
on), "What else to consider" (eligibility on the investing entity and pause
on lapse, offering rules, revocation cannot unsee, activity tracking, Q&A,
investor's advisors, notifications, retention) and "Phasing" row 3. Decided
(Joe): anyone publishes at launch; approval automatic after signing; the nine
folders stay; investors never see who else has access; typed-name signature
with a consent box. Agreement templates are DRAFT until counsel reviews them;
the 506(b) / 506(c) rules below are DRAFT too and are labelled so in the UI
until counsel confirms.

CONFIRMED REAL FACTS, DO NOT RE-DERIVE (main @ 376f8ef, merge lotmethod;
docs/DEAL_ROOM.md describes phases 1–2; Task 1 confirms exact signatures):
- Rights: one rule, services.deal_room.deal_rights (Super Admin first; staff
  manage_deals; org admin manage_org_settings; publisher users via
  entity_publishers + entity_publisher_users; delegates via deal_delegates;
  investors view_deals + deal_access + folder levels). Anyone else 403 on
  writes, 404 when the deal is not visible.
- Folders: nine per deal; levels public | after_agreements | after_approval |
  named_only; ONE SQL function deal_room_folder_open(folder, user) used by the
  room, the signed-URL download, GET /deals/{id} and Document Search; a closed
  folder's response carries no file names; every refused download is the
  same 404.
- Download today: routers/deal_room.py:876 download_room_document returns a
  300-second signed R2 URL to the ORIGINAL file (storage.get_signed_url) once
  R.member_doc_sql passes. deal_documents(id, file_name, r2_key, r2_bucket,
  folder_id, deleted_at, …).
- Settings: deal_room_settings (one live row per deal, valid-axis history):
  eligibility none | accredited (default) | qualified_purchaser |
  qualified_client; approval automatic (default) | publisher; teaser sponsor
  name | code_name; required agreements; delegates. Eligibility reads the
  chosen investing entity's current compliance_records row (KYC approved,
  OFAC passed / false_positive; accreditation self_certified /
  third_party_verified and not past re-verification; QP / QC from
  accreditation_basis).
- Access: deal_access statuses incl. granted, signed_awaiting, declined,
  revoked, resign_required (dealroom2); the grid GET /deal-room/access-grid
  and POST /deal-room/access/decide (approve / decline / revoke, bulk,
  audited); revoke keeps signatures; a revoked / declined investor cannot
  re-request.
- Agreements (dealroom2): immutable agreement_versions, master NDA
  (deal_master_ndas / _deals), signed-copy PDF with certificate page
  (services/deal_room_pdf.py, reportlab). The platform joinder lets an
  investor share with their own advisors and makes the investor responsible
  for them; it has a return-or-destroy clause.
- Libraries present: reportlab 4.4.4, pypdf 6.1.1, pdfplumber 0.11.10 (its
  pypdfium2 dependency can render pages to images — Task 1 confirms).
- Notifications: services/notifications.py NotificationBus.publish(pool,
  org_id, event_type, title, body, recipient_user_ids, …) — in_app delivered
  synchronously, other channels recorded pending for a worker; tables
  notifications, notification_recipients, notification_delivery_log,
  user_notification_preferences. member_todos exist for staff to-dos.
- Users: usermgmt1 built user invitation and linking (Task 1 finds the invite
  path for an advisor who has no account yet).
- Verify cleanup: lotmethod added scripts/_verify_cleanup.py (fast, scoped by
  fixture id, existence-checked; --cleanup-only flag). Use it; never an FK
  walk from a broad parent.
- The live Render scheduler skips is_test orgs; every fixture org MUST be
  is_test = true.
- Regression: this sprint changes the deal-room download path, settings and
  access lifecycle → re-run verify_dealroom2.py ONCE, flat, after teardown,
  VERIFY_NESTED=1, 2700 s. If verify_dealroom2 still uses an FK-walk
  teardown, move it onto _verify_cleanup.py (teardown code only — no
  assertion changes; say which lines).
- WSL memory on the operator's laptop is limited: the sprint must NOT run npm
  run build or any verify script.

THERE IS NO HUMAN AVAILABLE. Report findings, then continue immediately in the
same response. If uncertain, continue.

STANDING RULES: no interactive prompts. There is NO background-process
notification mechanism in this tool — nothing will ever notify you that a
script finished. Never wait for one, never poll in a loop, never start a
script in the background; run every command synchronously in the foreground
and read its output directly. Write the verify script but DO NOT RUN IT, and
do not run npm run build. No network calls. Never print a secret, a full
account number or a signed URL. Column names from the live DB. Bi-temporal
rule 3 for every new table; RLS policies for EVERY operation each new table
will take. Investors never see other investors' names, access, counts,
questions' askers or activity. Every new restriction defaults to today's
behaviour except where the spec sets another default (watermark ON for new
deals — existing deals keep their current download behaviour until a manager
turns it on; say so in the migration). Labels and vocabularies from the API
envelope. Light/cream UI with the existing tokens. Commit ALL sprint files
(excluding sprint_prompts/logs) on the current branch at the end.

=== TASK 1: DISCOVER ===
Report findings, then continue immediately.
  1a. Every path that hands an investor a document or its bytes (download,
      Document Search, previews, the signed-copy PDF, any attachment in
      notifications) — all must go through the new controls.
  1b. Rendering: can pypdfium2 (via pdfplumber) render PDF pages to PNG in
      the API process within memory limits; page-size cap; which file types
      can be watermarked (PDF yes; images maybe; office files no) and what
      happens to the others (Task 3 rule).
  1c. Where access expiry and accreditation-lapse checks hook in (the
      scheduler tick, skipping is_test orgs) and how a paused / expired
      status fits deal_access's CHECK and the grid's filters.
  1d. The advisor path: how a person with no account is invited (usermgmt1),
      which org they belong to, and how an advisor's access is tied to the
      investor's grant (never wider, revoked with it).
  1e. What "pre-existing relationship" can be read from the CRM today (e.g. a
      household / entity created_at, an adviser relationship start date) for
      the 506(b) rule; propose the field to add if none fits.

=== TASK 2: SCHEMA ===
Design from Task 1 (org-scoped; names indicative):
  - deal_room_settings gains: downloads (allowed | view_only), watermark
    (on | off), access_period (none | until_date | days_after_approval + its
    value), notify_publisher_on (set: request, signature, first_folder_open,
    question), offering_type (not_set | rule_506b | rule_506c | other).
    New deals default watermark on; migrated deals keep watermark off and
    downloads allowed (today's behaviour).
  - deal_access: statuses expired and paused (accreditation lapsed), with
    access_ends_on and paused_reason; history kept.
  - deal_room_activity (append-only; never deleted when access ends or the
    deal closes): user, investing entity, deal, folder, document, action
    (room_open | folder_open | view_page | download | question | answer_read),
    page, watermark id, at.
  - deal_room_watermarks: one row per watermarked rendition (id printed on
    the page), user, document, at — so a leaked copy traces to a person.
  - deal_room_questions / deal_room_answers: question (asker hidden from
    other investors), folder or general, status open | answered | withdrawn;
    answer by publisher users / delegates / staff, published to every
    investor whose access level reaches the question's folder level.
  - deal_room_advisors: investor grant → advisor user, name, firm, role
    (lawyer | accountant | financial_adviser | other), acknowledgement
    (typed name + consent, timestamp), status invited | active | revoked.
  - deal_room_return_notices: on revoke (optional), the notice sent, the
    investor's acknowledgement.
  - CRM: the relationship-established date field from 1e, if needed.
Apply with scripts/_apply_dealroom3.py through Doppler, synchronously; VERIFY
it landed; save docs/dealroom3_part1.sql.

=== TASK 3: WATERMARK AND VIEW-ONLY ===
  - When watermark is on, a download is a watermarked rendition generated on
    request (pypdf + reportlab overlay on every page: investor name,
    investing entity, deal, date and time, watermark id), streamed from the
    API — never a signed URL to the original. Non-watermarkable types: when
    watermark is on they are view-blocked for investors with a plain reason
    ("this file type cannot be watermarked — ask the publisher for a PDF"),
    unless the manager marks that document "share unwatermarked" (recorded).
  - View-only: the original never leaves the server; the viewer requests
    page images (watermarked when watermark is on) one page at a time; no
    download control; the UI says plainly that view-only deters copying but
    cannot stop screenshots.
  - Managers always get the original.
  - Every rendition and page view writes deal_room_activity.

=== TASK 4: ACCESS EXPIRY AND PAUSE ===
  - Access period applied on approval (access_ends_on); the daily sweep moves
    granted → expired at the end date, notifies the investor 7 days before
    (setting) and the publisher on expiry; a manager can extend (audited).
  - Accreditation lapse (re-verification date passed, or KYC / OFAC no longer
    passing) on the investing entity → paused, folders close at once (the
    folder-open function respects it), investor and staff told; restoring the
    compliance record restores access automatically (logged).
  - Expired / paused investors can see their signatures but no folders.

=== TASK 5: ACTIVITY LOG ===
Notice to investors on entering the room ("opens and downloads are
recorded"); a per-investor and per-document activity view for managers
(first open, last open, documents viewed / downloaded, pages), exportable as
CSV; retained after access ends and after the deal closes (no delete path;
retention noted in DEAL_ROOM.md).

=== TASK 6: Q&A ===
Investors with access ask questions (general or tied to a folder they can
open); managers answer; an answer is published to every investor whose level
reaches the question's folder (general → after_agreements); the asker's
identity is never shown to other investors; a manager may merge duplicates
and withdraw a question (reason recorded). The 07 Q&A folder lists answered
questions. Notifications per Task 8.

=== TASK 7: INVESTOR ADVISORS ===
An investor with access invites a named advisor (name, firm, role, email);
an existing user is linked, otherwise invited through the usermgmt1 path; the
advisor signs an acknowledgement (typed name + consent: bound by the same
confidentiality, the investor is responsible) before seeing anything; the
advisor sees exactly the investor's open folders, cannot sign, subscribe,
ask questions in the investor's name, or invite others; revoking or expiring
the investor's access ends the advisor's at once; advisors appear on the grid
under the investor; the publisher can remove an advisor (reason recorded).

=== TASK 8: NOTIFICATIONS, RETURN-OR-DESTROY, OFFERING RULES ===
  - NotificationBus events: investor — access approved, new document in a
    folder they can open, access expiring, access expired / paused, question
    answered; publisher users + delegates — new request, signature, question
    asked, and first open of a folder when that setting is on. In-app; email
    recorded pending for the existing worker.
  - Revoke gains an optional "send return-or-destroy notice" (the joinder's
    clause): the notice is recorded, sent in-app, and the investor's
    acknowledgement is recorded on the grid.
  - Offering type (DRAFT rules, labelled "pending counsel review"):
    rule_506b → the teaser is visible only to investors whose relationship
    with the firm was established before the deal's publication date (1e);
    rule_506c → eligibility additionally requires accreditation
    third_party_verified (self_certified is not enough); not_set / other →
    today's behaviour. The rule applies everywhere the teaser or the room is
    reachable (marketplace list, deal page, Document Search).

=== TASK 9: API, PERMISSIONS, SCREENS ===
Routes under /api/v1/deal-room/… gated through deal_rights, in AUTHZ1_ROUTES;
envelopes with permissions and vocabularies. Screens: deal settings (the new
rows); the data room (viewer, Q&A tab, advisors panel, activity notice);
manager views (activity per investor / document, Q&A queue, advisors on the
grid, expiry / pause status and extend); My deal access (expiry date,
paused reason, advisors, return-or-destroy acknowledgement).

=== TASK 10: REAL PROOF, then PROJECT STATUS ===
Proof:
  - Watermark: a downloaded PDF's every page carries the investor's name,
    entity, deal, timestamp and a watermark id that resolves to that user;
    the original's bytes are never returned to an investor (no signed URL in
    any investor response); a non-watermarkable file is refused with the
    reason unless marked "share unwatermarked"; managers get the original.
  - View-only: no download route answers for the investor; page images
    render; every page view is logged.
  - Expiry: approval sets access_ends_on; the sweep expires at the end date
    (edge: the end date itself still open, the next day closed — compute
    dates at the call); 7-day notice; extend works and is audited; is_test
    orgs are skipped by the live scheduler but a verify tick of its own org
    runs.
  - Pause: lapsed re-verification → paused at once, folders close (the
    folder-open function and Document Search agree), restored when the
    record is fixed.
  - Activity: every action recorded; investors never see anyone's activity;
    retained after revoke and after the deal closes; CSV export matches.
  - Q&A: answer visible to exactly the investors whose level reaches the
    folder; asker hidden; withdrawn / merged handled; notifications sent.
  - Advisors: acknowledgement required first; advisor sees exactly the
    investor's open folders, no more; cannot sign / ask / invite; revoked
    with the investor; on the grid.
  - Revocation notice recorded and acknowledged.
  - 506(b): teaser hidden from an investor whose relationship began after
    publication, shown to one before; 506(c): self-certified refused,
    third-party verified admitted; not_set unchanged.
  - Migration: existing deals keep downloads allowed / watermark off; every
    existing access row unchanged.
  - Org isolation both directions (rolbypassrls = false); gates both ways on
    identical requests; node tests show no manager control without the flags
    and no download control under view-only.
  - Routes: no existing gate changed; authz1_routes.py --check CURRENT.
  - Regression: verify_dealroom2.py ONCE, flat, after teardown,
    VERIFY_NESTED=1, 2700 s.
Then update docs/PROJECT_STATUS.md and docs/DEAL_ROOM.md (phase 3 section:
every setting, the watermark id lookup, retention, the DRAFT offering rules),
and add verify_dealroom3.py to scripts/regression_set.txt.

=== VERIFICATION: apps/api/scripts/verify_dealroom3.py ===
WRITE IT. DO NOT RUN IT. Pass/fail only, no prompts. MUST print
'TOTAL: N PASS, M FAIL' and exit non-zero on failure. Set AUTHZ_TEST_MODE=1
before importing the app. Hydrate secrets from Doppler over HTTPS. No network
calls (mock R2 reads/writes with in-memory bytes; never touch real R2
objects). Wrap every section so an exception is a [FAIL]; use
scripts/_verify_cleanup.py for teardown at start and end (rows this run
created, by fixture id), with the teardown GUCs any append-only / immutable
table needs; re-read every touched table for its exact pre-test count.
Compute every expected date at the call. Fixture orgs and users is_test =
true (assert it); users-RLS pattern from earlier verifies. Read UNIQUE /
CHECK / FK constraints from the catalog before seeding and check every seed
literal against its lookup or CHECK. Reserved fixture prefixes that match no
real row (assert it). Support --cleanup-only. The regression subprocess gets
VERIFY_NESTED=1, runs after teardown, 2700 s timeout; with VERIFY_NESTED=1
this script skips its regression section with one '[SKIP] nested run' line.

Verify-script rules — each one broke a real script:
  - check(passed, label, detail): condition FIRST; a real bool.
  - Never derive a unique value from a fixture-UUID slice.
  - RLS context on every read; app_service with rolbypassrls = false asserted
    for isolation checks; an empty list never passes a check.
  - Every import at the top of the file; never `x, y = (await f(a) for a …)`.
  - A failing check's detail prints the response body / culprit rows.
  - Never touch real deals, documents, access rows, signatures,
    notifications or users: count + checksum before and after.

Assertions:
  [Y] Task 1 findings 1a–1e
  [Y] Schema landed, RLS for every operation, migration keeps today's
      behaviour for existing deals and access rows
  [Y] Watermark: per-page stamp with traceable id; originals never reach an
      investor; non-watermarkable refused unless shared unwatermarked
  [Y] View-only: no investor download; page images; every view logged
  [Y] Expiry: dates, both edges, notice, extend; Pause on lapse and restore
  [Y] Activity log: complete, private to managers, retained, CSV
  [Y] Q&A: level-scoped visibility, asker hidden, withdraw / merge
  [Y] Advisors: acknowledgement first, exact scope, no signing / asking /
      inviting, revoked with the investor, on the grid
  [Y] Notifications sent for every listed event; return-or-destroy notice
      recorded and acknowledged
  [Y] 506(b) / 506(c) DRAFT rules exact; not_set unchanged
  [Y] Org isolation both directions; gates both ways; node tests
  [Y] Every new route gated and in AUTHZ1_ROUTES.json; --check CURRENT
  [Y] node --test tests/ passes; npm run build exits 0 (run by this script)
  [Y] Regression: verify_dealroom2.py ONCE, flat, after teardown,
      VERIFY_NESTED=1, 2700 s; 0 FAIL
  [Y] Teardown leaves zero fixture rows; every touched table at its pre-test
      count; real rows unchanged
