# DEALROOM1 — Task 1 discovery (2026-10-22)

Read from the code on `dealroom1-work` (base b07c839) and the live database
(project mmgwmcinimzuhargsazs, via the Supabase MCP as `postgres`). Each
finding names its file and the query that established it.

## 1a. Platform user → CRM person → investing entities

There is **no column linking a `users` row to a CRM entity.** The one link the
app uses everywhere is `delegate_grants` (`delegate_user_id` → `principal_entity_id`):

- `apps/api/services/ownership_tree.py` `get_member_root_entity_ids` — a member's
  own roots are the `principal_entity_id`s of their active grants, oldest first.
- `apps/api/services/delegate_grants.py` `get_delegate_visible_entity_ids` — every
  active grant's principal run through `entity_graph.resolve_entity_set`
  (subtree: own entity + ownership look-through + beneficiary edges).
- `apps/api/services/document_embedding.py` `visible_entity_ids(pool, org, user,
  is_staff)` — the member branch is the above, then `restricted_access.filter_restricted`.
  The staff branch is the STAFF visibility engine (assignments / teams), which
  answers "whom do I cover", not "what do I invest through".

Query used:

```sql
SELECT g.delegate_user_id, g.principal_entity_id, e.entity_type, g.scope
FROM delegate_grants g JOIN entities e ON e.id = g.principal_entity_id
 AND e.valid_to IS NULL AND e.system_to IS NULL
WHERE g.revoked_at IS NULL;
```

**DEALROOM1 rule.** A user's *CRM person* = their oldest active grant whose
principal is an `individual`. Their *investing entities* = the MEMBER engine
(`get_delegate_visible_entity_ids` + `filter_restricted`) for every caller,
staff included, minus the operational types `account`, `spv`, `household`
(CLAUDE.md: deliberately excluded from CRM surfaces). Implemented once in
`services/deal_room.py` `investing_entities` / `crm_person_of`.

## 1b. Who can create and edit deals today; how sponsor_entity_id is set

`apps/api/routers/marketplace.py`:

| Route | Gate |
|---|---|
| `POST /deals` (`create_deal`) | `require_permission(request, "manage_deals")` |
| `PUT /deals/{id}` (`update_deal`) | `manage_deals` |
| `PUT /deals/{id}/status` | member may only `draft → submitted`; else `manage_deals` |
| `POST /deals/{id}/documents` | `manage_documents` |
| `PUT /deals/{id}/documents/{doc}/review` | `manage_documents` |

`sponsor_entity_id` is a free field of `DealCreate` / `DealUpdate` (no check
that it names an entity in the org) and is written by the shared core
`services/deal_creation.insert_deal`. `created_by` is **never** set by it.

Live query:

```sql
SELECT id, name, sponsor_entity_id, created_by, submitted_by FROM deals
WHERE valid_to IS NULL AND system_to IS NULL;
```

[FIND] All 9 live deals have `sponsor_entity_id` NULL and `created_by` NULL; one
(TEST-Submit Test) has `submitted_by` = Joe's super-admin user, who has no
delegate grant and so no CRM person. The prompt's migration rule
("sponsor, else the creator's CRM person") yields **no publisher for any live
deal**. The migration therefore adds a third, reported fallback: the org's own
*house publisher* — one CRM firm entity per org named from `organizations.name`
(none existed: `display_name ILIKE '%2nd act%' OR '%hollis%'` → 0 rows).

The sprint rule "never change an existing route's gate" holds: the existing
`manage_deals` routes are untouched. Publisher / delegate editing is added as
NEW routes under `/api/v1/deal-room/…` (same insert core).

## 1c. The Documents tab and upload path

- Page: `apps/web/app/marketplace/[id]/page.js` (`?tab=documents`) renders
  `components/marketplace/DocumentsList.jsx` with `detail.documents` from
  `GET /api/v1/deals/{id}`.
- Upload: server action `lib/marketplaceActions.js uploadDocumentAction` →
  `uploadAPI('/api/v1/deals/{id}/documents')` → `upload_document` in
  `routers/marketplace.py`: R2 key `deals/{deal_id}/{uuid4}_{filename}`, bucket
  `R2_BUCKET_NAME` (default `2ndactcapital-docs`), `services/storage.upload_bytes`.
- **No deal-document download route exists** (`grep get_signed_url` → only
  entity / SPV / document-review routers).
- `visible_to_members` is enforced in exactly two places, both
  `status = 'approved' AND visible_to_members = true` for non-staff:
  `get_deal` (`routers/marketplace.py`) and `document_linkage._search_attachments`
  (bugfix1). Staff (`is_staff`) see every document.

Live: `SELECT visible_to_members, status, count(*) FROM deal_documents GROUP BY 1,2`
→ one row, `true / approved / 1` (on Stonegate Industrial REIT II).

## 1d. Notification / to-do carriers

Both exist and both fit:

- `member_todos` (org_id, user_id, category, source, related_type, related_id,
  title, detail, action_key, status 'open'…): listed by `GET /dashboard/todos`
  (`routers/dashboard.py`) for the owner; `services/workflow_todos.py` is the
  upsert-by-(source, related) pattern reused here. `todo_generators.regenerate_todos`
  never deletes or rewrites rows from other sources (grep: no DELETE/UPDATE).
- `notifications` via `services/notifications.notification_bus.publish(pool,
  org_id, event_type, title, body, recipients, resource_type=…, resource_id=…)`.

DEALROOM1 uses `member_todos` (source `deal_room`) for "new request" (publisher
+ delegates), "not eligible" (staff), and the bell (`notification_bus`) for
"access granted" / "signed" (investor) — best effort, never blocking the write.

## 1e. PDF generation

**None exists.** `grep -i "reportlab|weasyprint|fpdf|pdfkit|jspdf|pdf-lib|puppeteer|@react-pdf"`
over `apps/api/requirements.txt`, `services`, `routers`, `apps/web/package.json`,
`lib`, `components` → no match. `pdfplumber` is present for READING only.
Phase 1 therefore serves the signed copy as plain rendered HTML with the
signature block (the prompt's stated acceptable fallback); no PDF dependency
is added.
