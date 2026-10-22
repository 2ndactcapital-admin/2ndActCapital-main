DEALROOM2 — Phase 2 of the Marketplace Deal Room: publishers' own agreement
documents (uploaded PDF or text), firm-level template library, agreement
versions with a publisher choice to grandfather or require re-signing, a master
NDA that covers all of a publisher's deals, and a signed-copy PDF with a
certificate page. 9 tasks + verification.

Context: Phase 1 (dealroom1, merged at 5760301, verify 178/178) built
publishers, delegates, nine folders with access levels, platform NDA and
joinder templates (DRAFT), typed-name signatures in an append-only hash chain,
eligibility, automatic/publisher approval and the access grid. docs/DEAL_ROOM.md
is the authoritative description of what exists — read it first. Spec:
"Marketplace Deal Room Spec" (Joe, 2026-10-22). Phase 3 (watermark, view-only,
expiry, activity analytics, Q&A, investor advisors, 506(b)/(c)) is NOT this
sprint.

CONFIRMED REAL FACTS, DO NOT RE-DERIVE (main @ 5760301, docs/DEAL_ROOM.md):
- Code: apps/api/services/deal_room.py, services/deal_room_templates.py,
  routers/deal_room.py (/api/v1/deal-room/…), components/dealroom/*,
  app/marketplace/{access,my-access}. Schema: docs/dealroom1_part1.sql.
- Rights: ONE rule, services.deal_room.deal_rights (Super Admin → staff
  manage_deals / org admin → publisher users via entity_publishers +
  entity_publisher_users → delegates via deal_delegates).
- Templates: agreement_templates seeded platform_nda v1 and platform_joinder
  v1 (status draft) from docs/templates/deal_room_templates_v1.md; field
  resolution order: defaults → publisher CRM → publisher's entries on the deal
  agreement → deal → investing entity CRM + signer → signing date. A missing
  deal-side field blocks publishing the agreement; a missing recipient field
  blocks opening it.
- Signatures: deal_signatures stores the exact rendered text, its SHA-256,
  typed name (must match the account name), consent, time, IP, user agent,
  prev_signature_sha256, record_sha256; chains 'platform' and 'test'; insert
  trigger enforces chain head; append-only (no UPDATE/DELETE policy + trigger;
  fixture teardown exception only on the test chain via
  dealroom.signature_teardown = 'fixture' on a non-app_service session).
  verify_chain_rows + GET /deal-room/signatures/verify (Super Admin).
- Signed copy today: HTML only (GET /deal-room/signatures/{id}/copy); no PDF
  generator exists in the app.
- Settings: deal_room_settings (valid-axis history): eligibility, approval
  (automatic default), teaser sponsor, required agreements, delegates.
  deal_access statuses include granted, signed_awaiting, revoked, declined;
  a revoked or declined investor cannot re-request.
- The investor-side folder rule is ONE SQL function deal_room_folder_open.
- authz1 rules: every route has an explicit gate and an AUTHZ1_ROUTES.json
  entry (authz1_routes.py --check CURRENT); never change an existing gate.
- Documents in R2; deal_documents.r2_key; Render has no system PDF libraries —
  use pure-Python packages (e.g. pypdf + reportlab) and pin them in
  requirements.
- WSL on the operator's laptop has limited memory: the sprint must NOT run
  npm run build or any verify script; the operator runs them afterwards.

THERE IS NO HUMAN AVAILABLE. Report findings, then continue immediately in the
same response. If uncertain, continue.

STANDING RULES: no interactive prompts. There is NO background-process
notification mechanism — never wait for one, never poll in a loop, never start
a script in the background; run every command synchronously in the foreground.
Write the verify script but DO NOT RUN IT, and do not run npm run build. Mock
R2 in tests. Never print a secret. Column names from the live DB. Never change
an existing route's gate. Never modify or delete an existing signature; the
chain stays append-only. Commit ALL sprint files (new and modified, excluding
sprint_prompts/logs) on the current branch at the end.

=== TASK 1: DISCOVER ===
Report findings, then continue immediately.
  1a. How deal_agreements references a template and its version today, and
      what changes when a publisher edits a deal agreement's field values
      after investors have signed.
  1b. How the signing UI renders and hashes the agreement text; what would
      change for a PDF agreement (hash of the file bytes, render in a viewer).
  1c. Whether the deal agreement and signature rows already carry enough to
      tell which version an investor signed, and whether deal_access can
      represent "must re-sign".
  1d. Python PDF options available in apps/api/venv and acceptable on Render
      (pure Python); pick one and say why.

=== TASK 2: AGREEMENT VERSIONS ===
Every agreement an investor can sign is an immutable version: template
versions (platform/org) and deal-agreement versions (template version + the
publisher's field values, or an uploaded file). Editing field values or
replacing an uploaded file creates a new deal-agreement version; the old one
stays. Each signature points to the exact version (and its text or file
SHA-256). The publisher, on saving a new version of an agreement that has
signatures, must choose:
  - Keep existing signers (they stay granted; new investors sign the new
    version), or
  - Require re-signing: affected investors move to a new status
    resign_required, their folders above public close until they sign the new
    version, they are notified, and the grid shows it. Re-signing restores the
    access level they had.

=== TASK 3: PUBLISHER'S OWN DOCUMENTS ===
A deal agreement can be an uploaded document instead of a template: PDF (or
DOCX converted to PDF if a pure-Python path exists; otherwise PDF only, say
so). Stored in R2 under the deal; its SHA-256 recorded. The investor reads it
in an embedded viewer (all pages, scroll-to-end before Sign is enabled), then
signs with the same typed name and consent. Optional fill-in fields are not
supported for uploads (state this in the UI). The joinder's Exhibit A
(Underlying NDA PDF) uses the same storage and is shown before signing.

=== TASK 4: FIRM TEMPLATE LIBRARY ===
Org admins (manage_org_settings) can copy a platform template into the firm's
library, edit its text and fields, and publish versions (status draft →
active). Publishers in that firm can choose firm templates for their deals.
Platform templates stay editable only by super admin. A template in use keeps
its old versions forever.

=== TASK 5: MASTER NDA ===
A publisher can mark one agreement as their master NDA (scope: all their
deals, or selected deals). When an investor requests access to a covered deal
and the same investing entity has already signed that master NDA's current
version (or an earlier version the publisher accepts), the requirement is met
without signing again; the grid shows "covered by master NDA signed <date>".
Revoking access to one deal does not void the master NDA for others.

=== TASK 6: SIGNED COPY PDF AND CERTIFICATE ===
For every signature: a PDF of the signed agreement (rendered text, or the
uploaded PDF) followed by a certificate page listing signer, investing entity,
typed name, consent statement, signed time (UTC and ET), IP, user agent,
agreement version, document SHA-256, record SHA-256, chain position, and a
"verify" reference. Generated on demand (never stored as the source of truth;
the signature row is), downloadable by the investor and the deal's
publisher/delegates/staff, gated by the same rule as the HTML copy. Existing
Phase 1 signatures get PDFs the same way.

=== TASK 7: GRID AND INVESTOR VIEW UPDATES ===
Grid: version signed per agreement, resign_required status and filter,
"covered by master NDA", and a bulk "require re-sign" action. Investor:
My deal access lists each signature with its version and a PDF download;
resign_required deals show what to re-sign.

=== TASK 8: REAL PROOF ===
  - Versions: editing a signed agreement creates a new version; old signatures
    still verify against their own version; keep-signers leaves access intact;
    require-re-sign closes folders above public for affected investors only,
    and re-signing restores the prior level (re-read).
  - Uploads: PDF stored in R2 (mocked), SHA-256 recorded; Sign disabled until
    scrolled; the signature records the file hash; a replaced file is a new
    version.
  - Firm templates: org admin can create and publish; a member and another
    firm's admin cannot see or use them; platform templates editable only by
    super admin; identical requests.
  - Master NDA: covered deal needs no second signature for the same entity; a
    different entity of the same investor must sign; a deal outside the
    master's scope still requires its own NDA.
  - PDF: generated for a template signature and an upload signature; the
    certificate page's hashes equal the database row; refused for callers
    who cannot see the HTML copy.
  - Chain: still append-only; verifier passes over Phase 1 and Phase 2 rows.

=== TASK 9: UPDATE PROJECT STATUS ===
docs/DEAL_ROOM.md (Phase 2 section: versions, re-signing, uploads, firm
library, master NDA, PDF and certificate) and docs/PROJECT_STATUS.md.

=== VERIFICATION: apps/api/scripts/verify_dealroom2.py ===
WRITE IT. DO NOT RUN IT. Pass/fail only, no prompts. MUST print
'TOTAL: N PASS, M FAIL' and exit non-zero on failure. Set AUTHZ_TEST_MODE=1
before importing the app. Hydrate secrets from Doppler over HTTPS. Mock R2.
Wrap every section so an exception is a [FAIL]; teardown at start and end in
FK child-before-parent order from pg_constraint (test-chain signatures via the
Phase 1 fixture teardown path), then re-read every touched table for its exact
pre-test count. Fixture users created explicitly with the auth0_sub the mocked
token carries, is_test = true. Read every UNIQUE (including partial unique
indexes), CHECK and FK constraint from the catalog before seeding and seed to
satisfy them.

Verify-script rules — each one broke a real script:
  - check(passed, label, detail): condition FIRST; isinstance(passed, bool).
  - Never derive a unique value from a fixture-UUID slice.
  - RLS context on every read; app_service with rolbypassrls = false asserted
    for isolation checks; an empty list never passes a check.
  - Exclude this run's own fixtures from any "no leftovers / orphans" check.
  - Never spawn a background process. Regression subprocesses get
    VERIFY_NESTED=1, run after teardown, 1200 s timeout; with VERIFY_NESTED=1
    this script skips its regression section with one '[SKIP] nested run' line.
  - Never touch real deals, documents, signatures or users.

Assertions:
  [Y] Task 1 findings reported (1a–1d) with files and the chosen PDF library
  [Y] Versions immutable; each signature points to its exact version and hash
  [Y] Keep-signers: access unchanged; require-re-sign: only affected investors
      lose folders above public, notified; re-signing restores the level
  [Y] Upload agreement: stored (mocked R2) with SHA-256; Sign gated on
      scroll-to-end (node test); signature records the file hash; replacement
      = new version
  [Y] Firm template library: org admin create/publish; member and other-firm
      admin refused/invisible; platform templates super-admin only; identical
      requests
  [Y] Master NDA: covered deal satisfied for the same entity; different entity
      must sign; out-of-scope deal requires its own NDA; grid shows coverage
  [Y] Signed-copy PDF: generated for template and upload signatures;
      certificate hashes equal the DB row; gated like the HTML copy
  [Y] Chain still append-only (update/delete refused); verifier passes over
      Phase 1 and Phase 2 rows
  [Y] Every new route has a gate and is in AUTHZ1_ROUTES.json (--check
      CURRENT); no existing gate changed
  [Y] node tests for the investor flow, re-sign and grid pass; npm run build
      exits 0 (run by the verify script, not during the sprint)
  [Y] Regression: verify_dealroom1.py and verify_authz1.py each run ONCE as
      flat subprocesses AFTER teardown with VERIFY_NESTED=1; 0 FAIL
  [Y] Teardown leaves zero fixture rows; every touched table at its pre-test
      count; real signatures, deals and note_gold_values unchanged
