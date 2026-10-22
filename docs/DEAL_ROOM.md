# Deal Room — Phase 1 (DEALROOM1, 2026-10-22)

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

The signed copy is HTML (`GET /deal-room/signatures/{id}/copy`) — no PDF
generator exists in the app (discovery 1e). Signing is a person's own request;
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

## Later phases (not built)

- **Phase 2** — publisher's own agreement uploads; agreement versions with
  re-signing; a master NDA; a certificate page.
- **Phase 3** — watermarking, view-only documents, access expiry, activity
  analytics, Q&A, investor advisors, 506(b)/(c) rules.
