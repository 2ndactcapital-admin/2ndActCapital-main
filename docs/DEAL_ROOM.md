# Deal Room — Phases 1, 2 and 3 (DEALROOM1, DEALROOM2 2026-10-22; DEALROOM3 2026-10-28)

The Marketplace Deal Room: who may publish a deal, the data room's nine
folders and their access levels, the agreements an investor signs, the
eligibility check, approval, and the access grid. Spec: "Marketplace Deal Room
Spec" (Joe, 2026-10-22). Code: `apps/api/services/deal_room.py`,
`services/deal_room_templates.py`, `routers/deal_room.py` (all under
`/api/v1/deal-room/…`), front end `components/dealroom/*`,
`app/marketplace/{access,my-access}`. Schema: `docs/dealroom1_part1.sql`.
Discovery: `docs/DEALROOM1_DISCOVERY.md`. Migration record:
`docs/DEALROOM1_MIGRATION.json`. Verify: `apps/api/scripts/verify_dealroom1.py`.

## Roles and rights

| Who | What | How it is resolved |
|---|---|---|
| Super Admin | every deal, every org | `Principal.is_super_admin`, checked first |
| Staff (`manage_deals`) | every deal in the firm | role grant in the deal's org |
| Org admin (`manage_org_settings`) | every deal in the firm | role grant in the deal's org |
| Publisher user | the deals of the publisher entity they act for | `entity_publishers` + `entity_publisher_users` |
| Delegate | one deal | `deal_delegates` |
| Investor (`view_deals`) | the teaser and 01 Overview of a member-visible deal; more as access opens | `deal_access` + folder levels |

One rule: `services.deal_room.deal_rights`. Anyone else gets 403 on every write
(404 when the deal is not even visible to them).

**Publisher** is a CRM capability on an entity (person or firm). Staff / org
admins grant it (`POST /deal-room/publishers`); anyone with `submit_deal` can
make their own CRM person one (`POST /deal-room/publishers/self`: "everyone
can publish at launch"). Creating a deal needs a publisher entity the user acts
for, or staff. Default publisher: the creator's CRM person; staff with none
fall back to the org's **house publisher** (the firm's own entity, created on
demand).

Existing marketplace routes keep their gates (`PUT /deals/{id}` is still
`manage_deals`); publisher/delegate editing is the new `/deal-room` surface.

## Folders and levels

Nine standard folders per deal, created by the shared `insert_deal` core for
every creation path. Labels come from config (`deal_room_folders`), default
levels from code:

| Folder | Default level |
|---|---|
| 01 Overview | public |
| 02 Offering Memorandum, 03 Presentations, 04 Financials, 07 Q&A, 09 Other | after_agreements |
| 05 Legal & Structure, 06 Due Diligence | after_approval |
| 08 Subscription | named_only |

Levels: **public** (anyone who can view the deal); **after_agreements** (access
granted); **after_approval** (granted at the after_approval level, an explicit
publisher decision); **named_only** (granted and named on the folder). A
subfolder is as restrictive as its most restrictive ancestor. Publishers change
levels and add subfolders; the nine are never deleted. The investor-side rule
is ONE SQL function, `deal_room_folder_open(folder, user)`, used by the room,
the signed-URL download, `GET /deals/{id}` and Document Search. A closed
folder's response carries no file names; every refused download is the same 404.

## Templates (DRAFT — counsel review before outside publishers)

`platform_nda` v1 and `platform_joinder` v1 are seeded from
`docs/templates/deal_room_templates_v1.md` (status `draft`, with the file
section's SHA-256). The file's two `[optional: …]` notes become `{{#if}}`
blocks. Values come from: field defaults → the publisher's CRM record →
the publisher's entries on the deal agreement → the deal → the investing
entity's CRM record and the signer → the signing date. A missing required
deal-side field blocks publishing with a message naming each field; a missing
recipient field blocks opening it the same way.

## Signatures and the chain verifier

The investor reads the fully rendered text, types their full name (it must
match their account's name), ticks "I agree to sign electronically", and signs,
in sort order. `deal_signatures` stores the exact text, its SHA-256, the typed
name, consent, time, IP and user agent, plus `prev_signature_sha256` and
`record_sha256` = SHA-256 of the canonical JSON record including the previous
hash. Chains: `platform` (real signers) and `test` (`users.is_test` fixtures, so
a verify teardown never breaks the real chain). An insert trigger refuses any
row that does not extend its chain's head.

Append-only: no UPDATE / DELETE RLS policy (app_service matches zero rows) and a
trigger that raises on UPDATE / DELETE / TRUNCATE. The single exception is the
fixture teardown: a non-app_service session that sets
`dealroom.signature_teardown = 'fixture'`, and only on `test`-chain rows.
`services.deal_room.verify_chain_rows` recomputes every link and reports the
first break; `GET /deal-room/signatures/verify?chain=` (Super Admin).

The signed copy is HTML (`GET /deal-room/signatures/{id}/copy`); Phase 2 adds
the PDF with a certificate page (below). Signing is a person's own request;
it is in no agent action registry.

## Settings

`deal_room_settings` (one live row per deal, valid-axis history): eligibility
rule (`none` | `accredited` default | `qualified_purchaser` | `qualified_client`),
approval (`automatic` default | `publisher`), teaser sponsor (`name` |
`code_name` + code name), plus the deal's required agreements and its delegates.

Eligibility reads the chosen investing entity's current `compliance_records`
row: KYC `approved` and OFAC `passed`/`false_positive` under every rule;
accreditation `self_certified`/`third_party_verified` and not past
re-verification; QP / QC read from `accreditation_basis` (no dedicated column
exists). Not eligible → the investor sees what is missing and staff get a to-do.

Approval: on the last signature, automatic → `granted` at after_agreements;
publisher → `signed_awaiting` with a to-do for the publisher's users and the
delegates.

## The grid (Marketplace → Deal access)

`GET /deal-room/access-grid`, server-side: scope by role (publisher users and
delegates: their deals; `manage_deals` / org admin: the firm; Super Admin:
all), group by deal / publisher / firm / all, filter by status / deal /
publisher / unsigned, sort and page. Columns: investor, entity, deal,
publisher, eligibility, one column per required agreement (signed date or —),
level, status, decided by/at. `POST /deal-room/access/decide` — approve
(choose level), decline (reason required), revoke — single or bulk; each row
is re-checked against the caller's scope and audited in the same transaction.
Revoke stops access at once and keeps the signatures; a revoked or declined
investor cannot re-request. Investors never see other investors' access,
names or counts. The menu lists the grid for `manage_deals` only (a strict
gate — `/users/me` cannot express "publisher of a deal"); publishers,
delegates and org admins reach it from the deal page and My deal access.

## Phase 2 (DEALROOM2, 2026-10-22)

Schema: `docs/dealroom2_part1.sql` (applied by `apps/api/scripts/_apply_dealroom2.py
--schema`). Discovery: `docs/DEALROOM2_DISCOVERY.md`. Verify:
`apps/api/scripts/verify_dealroom2.py`. New code: `services/deal_room_library.py`
(firm library), `services/deal_room_pdf.py` (PDF + certificate); everything else
extends `services/deal_room.py` and `routers/deal_room.py`.

### Agreement versions

A deal agreement (`deal_agreements`, id stable) is the slot an investor signs.
What they sign is its current **`agreement_versions`** row, which is
immutable: no UPDATE / DELETE policy, plus a trigger, and the only exception is
the fixture teardown path.

| Kind | What a version holds |
|---|---|
| Template agreement | A snapshot of the template's title, body and fields, the publisher's field values, and Exhibit A's file SHA-256 |
| Uploaded PDF | The file's SHA-256, file name and page count |

**When versions are written:**
- Publishing writes version 1 (`initial`).
- A draft is still edited in place, as in Phase 1.
- A published agreement changes only through a new version:
  - `POST …/agreements/{id}/versions` (field values or Exhibit A)
  - `POST …/agreements/{id}/versions/file` (a replacement PDF)

**Re-signing.** Once an agreement has a signature, the publisher must choose:
- **`keep`** — existing signers stay granted. New investors sign the new
  version.
- **`resign`** — this applies to every live granted or awaiting investor on the
  deal, plus every covered deal when the agreement is a master NDA. Those whose
  requirement is no longer met move to **`resign_required`**:
  - `prior_level` holds the level they had.
  - Their folders above public close at once. `deal_room_folder_open` opens only
    for `granted`, and that rule is unchanged.
  - They get a to-do and a notification.
  - Signing the new version restores `prior_level` (or, if they were still
    awaiting approval, goes back through approval).

**Which signatures count.** A signature counts when both hold:
- Its version is at or above the agreement's floor. The floor is the latest
  `initial` or `resign` version; for a master NDA, see below.
- It was made after the access row's `resign_after`. That cut-off is set by the
  grid's bulk **Require re-sign** action.

`services.deal_room.signature_state` is the one rule. Signing, approval, the
room, the grid and My deal access all read it.

**Signature rows.** Every Phase 2 signature records:
- `agreement_version_id`
- `document_sha256` — the rendered text's hash for a template, the file's hash
  for an upload
- `record_version = 2`

An upload's `rendered_text` is a statement naming the document, the file and
its SHA-256.

**Hashing by format.** `record_sha256` hashes the canonical record for the
row's format:
- **1** = the fixed Phase 1 field set (`CANON_FIELDS`)
- **2** = that set plus `agreement_version_id`, `document_sha256` and
  `record_version`

So Phase 1 rows still verify. `verify_chain_rows` also checks that a Phase 2
row's document hash is accounted for by its text.

### Uploaded agreements (PDF only)

`POST /deal-room/deals/{id}/agreements/upload` (multipart: file and title) works
like this:
- **Validation.** The file must start with `%PDF-` and be readable by pypdf.
  DOCX is refused with 415, because no pure-Python DOCX → PDF path keeps layout.
- **Storage.** The file goes to R2 under `deals/{deal}/agreements/…` as a
  `deal_documents` row outside every folder. Its SHA-256 is recorded on the
  agreement.
- **Reading.** The investor reads every page in the in-app viewer
  (`components/dealroom/AgreementViewer.jsx`). The page images come from
  `GET …/agreements/{id}/pages/{n}`, rendered by pypdfium2. **Sign stays
  disabled until the viewer has been scrolled to the end**
  (`lib/dealRoom.signEnabled`).
- **Signing.** The investor signs with the same typed name and consent. The
  request carries `file_sha256`, and the server compares it to the current
  version's hash.
- **No fill-in fields.** Optional fill-in fields are not supported for uploads,
  and the UI says so.

A joinder's Exhibit A (the Underlying NDA PDF) uses the same storage. It is
shown, page by page, after the text and before signing. Its hash is fixed on
the version.

### Firm template library

`/deal-room/library` (page `app/marketplace/library`, linked from My deal access
for org admins).

**Who can do what:**
- **Org admins** (`manage_org_settings`) can copy a platform template into the
  firm library, or write a new one.
- **Super Admin only** can create or edit a platform template. Scope
  `platform` and edits to platform rows also require `require_super_admin`.

**Lifecycle:**
- A draft is edited while no deal uses it.
- Publishing moves it `draft → active` and retires the firm's previous active
  version of the same key.
- To change an active version, create a new version.

The `agreement_templates_guard` trigger enforces this: identity never changes;
the text of a non-draft version never changes; a non-draft version is never
deleted. Old versions are kept forever.

**Who can use firm templates:**
- `GET /deal-room/templates` offers a firm's **active** templates only to that
  firm's publishers, delegates, staff and org admins.
- A member never sees them, and neither does another firm (RLS plus an explicit
  org filter).
- Requiring a template on a deal checks the same rule (`usable_for_deal`).

### Master NDA

`PUT /deal-room/publishers/{entity}/master-nda` marks one published agreement on
one of the publisher's deals as their master NDA, with scope `all` or
`selected` (`deal_master_ndas`, one live per publisher, with history).

**Coverage.** On a covered deal, the master is a requirement listed first. It
is satisfied by a signature from the **same investing entity** on any deal,
when either holds:
- the signature is on the current version, or
- it is on a version at or above `accepted_min_version`, if the publisher set
  one.

So a covered deal needs no second signature for that entity. Access is granted
on request when nothing else is left to sign, and the grid shows **"covered by
master NDA signed <date>"**.

**Limits:**
- A different entity of the same investor must sign the master (on the covered
  deal). It renders as written, for its home deal.
- A deal outside the scope still requires its own NDA.
- Revoking access to one deal leaves the master signature, and every other
  deal, untouched.

### Signed copy as PDF, with a certificate page

`GET /deal-room/signatures/{id}/pdf` uses the same gate as the HTML copy: the
signer, or a manager of the deal. It is built **on demand** from the signature
row and is never stored; the row is the source of truth. The libraries are
reportlab 4.4.4 and pypdf 6.1.1, both pure Python and pinned.

**Pages, in order:**
1. The rendered text, or the uploaded PDF's own pages (refused if their bytes
   no longer hash to `document_sha256`).
2. Exhibit A.
3. A certificate page.

**The certificate page lists:**
- signer, investing entity, typed name, and the consent statement
- signed time in UTC and ET
- IP and user agent
- agreement and version
- document, text, record and previous-record SHA-256
- chain position and record format
- the verify reference (`GET /deal-room/signatures/verify?chain=…`)

Phase 1 signatures get PDFs the same way.

### Grid and investor views

**The grid:**
- Each agreement cell shows the version signed (`v2 · date`), "covered by
  master NDA signed <date>", or a stale `signed v1 — v3 needed`.
- The status filter includes `resign_required`.
- Bulk actions now include **Require re-sign**.

**My deal access:**
- Every signature is listed with its version, entity, HTML copy and PDF.
- A `resign_required` deal lists what to re-sign.

**The data room:**
- Investors see "An agreement for this deal has changed".
- Managers get these controls, each only under `can_write === true`:
  - versions, with the keep / re-sign choice
  - PDF upload and replacement
  - "Use as master NDA"

## Phase 3 (DEALROOM3, 2026-10-28) — control and insight

Schema: `docs/dealroom3_part1.sql` (applied by `apps/api/scripts/_apply_dealroom3.py --schema`; `--check` re-proves
it). Discovery: `docs/DEALROOM3_DISCOVERY.md`. Verify: `apps/api/scripts/verify_dealroom3.py`. Code:
`services/deal_room_control.py` (one rule each), `services/deal_room_watermark.py` (pure PDF functions), routes in
`routers/deal_room.py`, front end `components/dealroom/{DocumentViewer,QAPanel,AdvisorsPanel,ActivityPanel}.jsx`.

### Settings (new rows in `deal_room_settings`, valid-axis history as before)

| Setting | Values | Default |
|---|---|---|
| Downloads | `allowed` · `view_only` | allowed |
| Watermark | `on` · `off` | **on for new deals**; every deal that existed before the migration keeps `off` (today's signed-URL download) until a manager turns it on |
| Access period | `none` · `until_date` (+ date) · `days_after_approval` (+ 1–3650 days) | none |
| Expiry notice | days before the end date (0–90) | 7 |
| Notify publisher on | any of `request`, `signature`, `first_folder_open`, `question` | request, signature, question |
| Offering type | `not_set` · `rule_506b` · `rule_506c` · `other` (DRAFT rules, below) | not_set |

Labels come from config (`deal_room_downloads_*`, `deal_room_watermark_*`, `deal_room_access_period_*`,
`deal_room_notify_*`, `deal_room_offering_*`, …), seeded for every org by the migration.

### Documents: watermark and view-only

ONE policy: `deal_room_control.document_policy`. Every document route goes through it. The folder rule
(`deal_room_folder_open`) has always decided which folders open. The policy decides what an opened document gives:

| Caller / room | Download | Viewer |
|---|---|---|
| Manager (publisher, delegate, staff, org admin, Super Admin) | the original (signed URL) | pages, unwatermarked |
| Investor/advisor, watermark off, downloads allowed | today's signed URL | pages |
| … watermark on | **no URL**: `…/documents/{id}/download` names `…/documents/{id}/file`, which streams a freshly watermarked PDF | pages, each watermarked |
| … view_only | none: the same 404 as any refusal | pages only; the UI says view-only cannot stop screenshots |

**What can be watermarked:**
- PDF and PNG / JPEG (an image becomes one stamped page).
- Office and other files are refused for investors with "This file type cannot be watermarked — ask the publisher for
  a PDF", unless a manager marks the document **share unwatermarked**. That mark is recorded on the row (who, when)
  and audited.

Downloads are capped at 400 pages / 40 MB of source. Agreement pages and signed copies are never watermarked: they are
the signed record.

**The stamp, on every page:** the reader's name (an advisor's reads "…, advisor to <investor>"), the investing entity,
the deal, the date and time (UTC), and the watermark id.

**The watermark id lookup.** Each rendition (a download, or one viewer page) first writes a `deal_room_watermarks`
row: reader, entity, the investor an advisor acts for, document, page, source SHA-256 and stamp text. Its id is what
is printed. A manager resolves a leaked copy with `GET /deal-room/deals/{id}/watermarks/{watermark_id}`. The register
is append-only.

### Access expiry and pause

- **Approval stamps `deal_access.access_ends_on`.** It comes from the access period: the date given, or the approval
  date plus N days. A level change keeps it; restores carry it.
- **The sweep** (`access_tick` in the scheduler cron; never an `is_test` org):
  - The end date itself is still open; the next day the row becomes `expired`. The investor and the publisher are told.
  - The "expiring" notice goes N days before, once per end date.
  - A granted row whose investing entity no longer passes becomes `paused`. That means the re-verification date has
    passed, KYC or OFAC no longer clears, or under 506(c) the accreditation is no longer third-party verified. The
    paused row keeps `prior_level` and a `paused_reason`; the investor and staff are told.
  - A paused row whose entity passes again is restored at its level, logged (`deal_access.restore`).
- **At once.** `PUT /entities/{id}/compliance` runs the same pause / restore for that entity in its own transaction.
  That route now also accepts `accreditation_verified_date` / `next_reverification_due`.
- **Extend.** A manager extends from the grid (`decide` action `extend` + `ends_on`, audited). An expired row reopens
  at the level it last had.
- **What closes.** `deal_room_folder_open` opens nothing above public unless the row is `granted`. So expired, paused,
  revoked and re-sign rows close at once. Those investors still see their signatures and signed copies.

### Activity log and retention

`deal_room_activity` records every investor and advisor action:
- `room_open`
- `folder_open` (the first one can notify the publisher)
- `view_page` (with its watermark id)
- `download` (with its watermark id when stamped)
- `question`
- `answer_read`

Managers read it per investor, per document or as every event (`GET …/activity?view=investor|document|events`,
`&format=csv`). Investors never see anyone's activity. Entering the room shows: "Opens, page views and downloads in
this data room are recorded, and the publisher can see them."

**Retention.** The activity log and the watermark register are append-only:
- No UPDATE / DELETE policy exists, so app_service matches zero rows.
- A trigger refuses UPDATE / DELETE / TRUNCATE.
- The only exception is a verify teardown on an `is_test` org (operator DSN + `dealroom.activity_teardown`).

Rows are kept after access ends, after a revoke and after the deal closes. There is no delete path.

### Q&A

Investors with granted access ask a question, either generally or about a folder they can open. Managers answer,
withdraw (with a reason) or merge duplicates (with a reason). Questions are kept on the system axis (id stable,
history rows); a re-answer archives the previous answer.

**Who sees an answer.** Every investor (and active advisor) whose access reaches the question's folder; a general
question reaches after_agreements. Each of them is notified, and so is the asker.

**Asker identity.** It is shown to managers only. Investors get `mine` and nothing else. The 07 Q&A folder lists the
answered questions the reader may see.

### Investor advisors

**Inviting.** An investor with granted access names an advisor (name, firm, role, email):
- An existing user of the deal's org is linked.
- An unknown email is invited through `services.invites.create_invite`, into the deal's org.
- An address in another firm is refused (409).

**Acknowledgement first.** The advisor signs an acknowledgement (typed name + consent + the text's hash: same
confidentiality, the investor responsible) before seeing anything.

**Scope.** Once active, the folder function evaluates them as the investor: exactly the investor's open folders, never
more. They cannot sign, request, ask or invite (403).

**Ending.**
- Revoking or declining the investor revokes their advisors.
- Expiry or pause closes the advisors' folders in the same statement.
- The investor can remove an advisor, and so can the publisher (reason recorded).

Advisors appear on the grid under their investor, and in My deal access.

### Return-or-destroy notice

Revoke can carry `send_return_notice`. The joinder's return-or-destroy notice is then:
- recorded against the revoked row (text + SHA-256), in `deal_room_return_notices`, valid-axis;
- sent in-app;
- acknowledged by the investor with their typed name (in the room or in My deal access).

The grid shows the notice as sent, then as acknowledged.

### Notifications

All through `NotificationBus`, in-app and email (recorded `pending` for the existing worker), published after the
deciding transaction commits.
- **Investor:** access approved, new document in a folder they can open, access expiring, access expired / paused /
  restored, question answered, return-or-destroy notice.
- **Publisher users + delegates:** new request, signature, question asked, first open of a folder. Each one only when
  the deal's setting includes it.

### Offering rules — DRAFT, pending counsel review

Labelled so in the UI (config labels and the envelope's `notices.offering_draft`).
- **rule_506b.** The teaser, room, deal page, marketplace list and Document Search are visible only to an investor
  whose relationship with the firm began strictly before the deal's `published_at` date. One SQL definition:
  `deal_room_offering_open`.
  - The relationship date is the earliest `crm_relationship_dates.established_on` over the entities the user acts for.
    When none is recorded, it is that entity's CRM `created_at`.
  - Staff record it with `PUT /deal-room/relationship-dates/{entity}`.
  - Publisher users and delegates always see their deal. An advisor is judged by their investor.
- **rule_506c.** Eligibility additionally requires accreditation `third_party_verified`; self-certified is refused.
- **not_set / other.** Today's behaviour.

## Later phases (not built)

- Office-file conversion to PDF (so office files can be watermarked), and an investor-facing view of their own activity.
