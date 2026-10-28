# DEALROOM3 — Task 1 discovery (2026-10-28, main @ 376f8ef)

Read against the live database (Supabase MCP, project dev = live) and the code
on `dealroom3-work` before any change. Live counts at discovery: 9 live deals,
9 live `deal_room_settings` rows, **0 `deal_access` rows**, 1 `deal_documents`
row. So no investor's access can change because of this migration.

## 1a. Every path that hands an investor a document or its bytes

| Path | File | What it returns today | Phase 3 |
|---|---|---|---|
| Room download | `routers/deal_room.py` `download_room_document` (was line 876) | a 300 s signed R2 URL to the ORIGINAL (`storage.get_signed_url`) after `R.member_doc_sql` | Watermark on: the investor gets NO URL. The response names the API's own rendition route (`…/documents/{id}/file`), which streams a freshly watermarked PDF. View-only: 404 for the investor (the same 404 as any refusal). Watermark off and downloads allowed: today's signed URL, unchanged. That is exactly the behaviour existing deals keep until a manager turns watermarking on. Managers always get the original. |
| Room listing | `_room_envelope` | file names of open folders only | unchanged + per-document `can_download` / `can_view` / `blocked_reason` from the server |
| Deal page | `routers/marketplace.py` `get_deal` (`GET /deals/{id}`) | document METADATA (`DOC_SELECT`: no r2_key, no URL) under `member_doc_sql` | metadata only; `member_doc_sql` now also applies the offering rule |
| Marketplace document review / upload | `routers/marketplace.py` `/deals/{id}/documents…` | staff-only write paths, no investor bytes | unchanged |
| Document Search | `services/document_linkage.py` deal attachments (`member_doc_sql('dd', '$6::uuid')`) | name, category, a link to `/marketplace/{id}?tab=documents`; never bytes or a URL | the offering rule joins `member_doc_sql`; advisors resolve through the folder function |
| Agreement page images | `agreement_page` (`…/agreements/{id}/pages/{n}`) | PNGs of the agreement / Exhibit A to an investor who requested access | agreements, not data-room documents: they are read before signing and are what the investor signs, so they are NOT watermarked (the recorded file hash must stay the signed document's). Not a folder document. **[FIND]** |
| Signed copy HTML / PDF | `signed_copy`, `signed_copy_pdf` | the signer's own agreement + certificate | the investor's own signed agreement: never watermarked (it is the record). Expired / paused / revoked investors still read it, as the spec requires ("can see their signatures"). |
| Notifications | `services/notifications.py` `NotificationBus.publish` | title, body and payload text; **no attachment column exists** (`notifications`: id, org_id, event_type, title, body, payload, resource_type, resource_id, …) | the new deal-room events carry no file, no URL and no file name of a folder the recipient cannot open |
| `routers/entity_documents.py`, `routers/spv.py`, `routers/document_review.py` signed URLs | other subsystems (CRM, SPV, review queue) | not deal-room documents | out of scope; untouched |

So the deal-room routes that return document content to an investor are the download, the new rendition route and the new
viewer page images. All three go through one function, `services/deal_room_control.document_policy`.

## 1b. Rendering — pypdfium2 page images and pypdf + reportlab watermarking

- pypdfium2 5.12.1 is present as pdfplumber 0.11.10's dependency, and its wheel bundles pdfium. DEALROOM2 already
  uses it in `services/deal_room_pdf.page_png`. Pillow 12.3.0 is present.
- Probe (apps/api venv): a reportlab overlay was merged onto every page of a 40-page PDF with pypdf in **0.12 s**.
  `extract_text` on page 40 read the stamp back. pdfium rendered page 1 at scale 1.4 in **0.43 s**, 857×1109 px,
  8.9 KB PNG. Peak RSS was **54 MB**.
- Caps (`deal_room_control`): a watermarked download is limited to **400 pages / 40 MB** of source. Above that the
  request is refused (413) and the publisher is asked to split the file, rather than putting the API process at
  risk. The viewer renders one page per request, so it has no page cap.
- Which file types can be watermarked:
  - **PDF**: yes, every page.
  - **PNG / JPEG**: yes. The image is wrapped into a one-page PDF and stamped the same way (reportlab drawImage),
    and the viewer renders it like a PDF page.
  - **Everything else** (DOCX, XLSX, PPTX, CSV, ZIP, …): no. There is no pure-Python office → PDF path.
- Task 3 rule for those other types when watermark is on: investors are refused with "This file type cannot be
  watermarked — ask the publisher for a PDF", unless a manager marked the document `share_unwatermarked`. That
  mark is recorded on the row (who, when) and audited.
- Under view-only, a type that cannot be rendered to pages is not viewable at all. Same message, because the
  original never leaves the server.

## 1c. Expiry and accreditation-lapse hooks

- **Scheduler.** The live Render cron is `workflow_scheduler_tick.py`. After the workflow tick it runs its own
  steps (EDGAR index, replica holdings, `expire_tick`, `proposals_tick`), each in its own try. The new
  `deal_room_control.access_tick(pool)` is one more step in that list. It enumerates orgs with live deal_access
  rows under `services.orgs.not_test_org_sql`, so **is_test orgs are skipped**. Each org runs in its own RLS
  context (`set_rls_context(org, False)`), the same pattern as `proposals_tick`. Verify scripts call
  `org_access_sweep(pool, org_id=…, today=…)` directly for their own org.
- **Compliance write path.** There is exactly ONE: `routers/entities.py` `update_compliance`
  (`PUT /entities/{id}/compliance`, an UPDATE in place inside a transaction). It now calls
  `deal_room_control.recheck_entity` in the same transaction, so a lapse pauses at once and a fix restores at once.
  The sweep catches the one lapse that involves no write: a re-verification date passing.
- **`deal_access` statuses.** The live CHECK is `deal_access_status_v2_check` (viewing, requested, not_eligible,
  signed_awaiting, granted, declined, revoked, resign_required). The migration widens its body to add `expired` and
  `paused` but **keeps the name**: verify_dealroom2 reads `deal_access_status_v2_check` and
  `deal_access_prior_level_check` by name. `prior_level` (today only on resign_required) is widened to `paused` too,
  so a restore returns the level the investor had. `paused_reason` is set exactly on a paused row (a new CHECK).
- **The folder function.** `deal_room_folder_open` opens nothing above public unless the live row is
  `granted`. So `expired` and `paused` close every non-public folder with no change to that rule.
- **The grid.** `GET /deal-room/access-grid` filters `a.status = $n` from the request. The status vocabulary
  comes from config (`deal_room_status_*`), so the two statuses only need their labels (seeded by the migration).

## 1d. The advisor path

- usermgmt1 / `services/invites.py` `create_invite(conn, org_id, email, full_name, role, invited_by, …,
  send_email)` inserts a pending invite `users` row (`invite_status = 'pending'`, auth0_sub NULL) in the
  INVITING org. SES is blocked (memory: invite-flow-and-ses-gate). The outcome is reported in
  `email_delivery` and never inferred.
- **Org.** An advisor belongs to the **deal's org**, the same org as the investor. Investors are same-org with the
  deal: `deal_rights` requires `same_org` for every non-super-admin branch, and an advisor in another org could
  not pass the RLS of the deal's own rows. The advisor gets `users.role = 'member'` and **no RBAC role**, so no
  `view_deals`. Their only way into a deal is the advisor row.
- **Existing user.** An existing user is matched by exact email in the deal's org. An email that exists in
  ANOTHER org is refused (409 naming no one) rather than linked across orgs.
- **Scope (never wider, revoked with it).** `deal_room_advisors` ties the advisor to ONE investor's live access on
  ONE deal. The folder function is extended so that an **active** (acknowledged) advisor is evaluated as the
  investor they advise: the investor's live row must be `granted`, at the investor's level, with the investor's
  named-folder grants. This applies to the room, the download, the viewer, `GET /deals/{id}` and Document Search.
  When the investor stops being `granted` (revoked, expired, paused, re-sign), the advisor's folders close in the
  same statement, because there is no second copy of the grant. Revocation also restates the advisor rows to
  `revoked`.
- `deal_rights` gains an `advisor` role: can_view, never can_manage. Signing, requesting access, asking questions
  and inviting are refused for an advisor (403), whatever folders are open.

## 1e. A "pre-existing relationship" for the 506(b) rule

- Nothing in the CRM records when the firm's relationship with a person began. `entities` has
  `created_at`, `inception_date` (an entity's own formation), `end_date`, `lead_source` and
  `relationship_manager_id`. `entity_relationships` holds ownership / role edges with `effective_date`, which is
  not a firm relationship. `households` has only `created_at`. **[FIND]**
- **Proposed and added:** `crm_relationship_dates` (org_id, entity_id, `established_on date`, `basis` text,
  `recorded_by`, valid-axis history, one live row per entity), set by staff on the CRM person / investing entity.
- **The rule's reading (DRAFT).** The subject is the investor's CRM person plus every entity they may invest
  through (active delegate grants). Their relationship date is the EARLIEST of their recorded `established_on`
  values. When none is recorded, it is the entity row's `created_at::date`, which is the day the firm first
  recorded them, so it never claims an earlier relationship than the CRM shows. A 506(b) teaser is visible only
  when that date is strictly before `deals.published_at::date`.
