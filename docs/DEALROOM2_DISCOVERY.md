# DEALROOM2 — Task 1 discovery (2026-10-22)

Base: main @ 5760301 (DEALROOM1 merged). Live database read through the
Supabase MCP (`SELECT count(*) …`) on project mmgwmcinimzuhargsazs: **0 rows in
`deal_signatures`, 0 in `deal_agreements`, 0 in `deal_access`**, 2 rows in
`agreement_templates` (`platform_nda v1 draft`, `platform_joinder v1 draft`).
No real signature exists yet, so nothing needs backfilling. The code still
handles Phase 1 rows, and the verify script seeds one so that path is proven.

## 1a. How a deal agreement references its template and version

- `deal_agreements.template_id` (NOT NULL, FK) → `agreement_templates.id`. The
  template row carries `key` and `version`, and the deal agreement has no
  version of its own. `services/deal_room.py` `AGREEMENT_COLS` joins
  `agreement_templates t ON t.id = a.template_id` and reads `t.version AS
  template_version`, `t.body` and `t.fields` **live** on every render.
- Field values live in `deal_agreements.field_values` (jsonb). Edits go through
  `routers/deal_room.py` `edit_agreement` (PATCH): it copies the live row
  (`status 'withdrawn'`, `system_to = now()`, `history_of = id`) and then
  UPDATEs the live row in place, so the id stays stable.
- **Once an agreement is published, its field values cannot change.**
  `edit_agreement` returns 409 "A published agreement is fixed; new versions
  with re-signing come in Phase 2". After investors sign, the only record of
  what they agreed to is the signature row's own `rendered_text` and its
  SHA-256. Nothing else links that row to one set of field values.
- [FIND] **The template body is also read live, and it is mutable.**
  `agreement_templates` has an UPDATE policy (super admin, or the template's
  own org). Editing a template body would change what every future signer of
  every deal using that template sees, while `template_version` stayed at 1.
  Phase 2 closes this in two ways:
  - Each agreement version snapshots the template's title, body and fields.
  - A trigger makes a non-draft template's content immutable.

## 1b. How the signing UI renders and hashes the text

- `components/dealroom/DataRoom.jsx` `InvestorPanel` calls
  `GET /deal-room/deals/{id}/agreements/{aid}/render`. The server runs
  `R.render_for_signer` (template + values → text) and returns the text and
  `T.text_sha256(text)`. The text is shown in a `<pre>`.
- Sign = `POST …/sign` with `rendered_text_sha256`. `R.sign` re-renders on the
  server and refuses with 409 if the hash differs ("The agreement changed
  since you opened it"). It then stores the exact text and its hash.
- **What changes for a PDF:**
  - The thing signed is the file's bytes. Its SHA-256 is computed once from
    the bytes received at upload and is stored on the version.
  - The investor sees the pages as images in a scrolling viewer (all pages).
    The images are rendered server-side by `pypdfium2`, which pdfplumber
    already pulls in, so it is deployed today. The browser never needs a PDF
    plugin, and the viewer knows when the last page has been reached.
  - The Sign request carries the file hash the investor was shown. The server
    compares it to the current version's hash.
  - `deal_signatures.rendered_text` is NOT NULL, and the verifier re-hashes it.
    So for an upload it holds a short statement naming the document, the file
    name and the file's SHA-256. The new `document_sha256` column holds the
    file hash.

## 1c. Can the existing rows tell which version was signed? Can deal_access say "must re-sign"?

- **Signatures.** A signature row carries:
  - `deal_agreement_id`, which is stable across the system-axis history.
  - `template_id` and `template_version`.
  - The full rendered text and its hash.

  That identifies the template version, but **not** the deal-agreement version
  (the field values). The text is the only evidence of those. It also cannot
  represent an upload at all: `template_id` and `template_version` are NOT
  NULL.
- **Phase 2 adds** `agreement_version_id`, `document_sha256` and
  `record_version`. The canonical record (`CANON_FIELDS`) that `record_sha256`
  hashes is FIXED for Phase 1 rows. Changing it would break every existing
  link, so the verifier hashes by `record_version`:
  - 1 = the Phase 1 field set
  - 2 = Phase 1 + `agreement_version_id`, `document_sha256`, `record_version`
- **deal_access.** It cannot represent "must re-sign" today:
  - The status CHECK has no such value.
  - `CHECK ((status = 'granted') = (level_granted IS NOT NULL))` means a
    non-granted row cannot hold the level to restore.

  Phase 2 adds status `resign_required`, plus `prior_level` (restored on
  re-signing) and `resign_after` (the bulk "require re-sign" cut-off).
- **Folders close by themselves.** `deal_room_folder_open`
  (docs/dealroom1_part1.sql) opens anything above `public` only when the live
  row is `granted`. So `resign_required` closes those folders with no change
  to the ONE folder rule.

## 1d. Python PDF options

- **In `apps/api/venv` today:**
  - `pdfplumber` 0.11.10 and `pdfminer.six` (read only)
  - `pypdfium2` 5.12.1 (render; bundles its own pdfium binary in the wheel, no
    system library, already deployed on Render)
  - `python-docx` 1.2.0 (reads DOCX text only)
  - Pillow 12.3
- **Not present:** pypdf and reportlab. `grep -n "pdf" apps/api/requirements.txt`
  finds only pdfplumber.
- **Chosen: `reportlab==4.4.4` + `pypdf==6.1.1`.** Both ship as
  `py3-none-any` wheels, so they are pure Python. `pip download` saved
  `pypdf-6.1.1-py3-none-any.whl` and `reportlab-4.4.4-py3-none-any.whl`. They
  are pinned in requirements.txt.
  - reportlab draws the text pages and the certificate page. Its standard
    fonts use WinAnsi, which covers the templates' em dashes and curly quotes.
    Checked by round-tripping `— “q”` through pypdf `extract_text`.
  - pypdf appends an uploaded PDF's pages and reads them back.
  - fpdf2 was rejected: its core fonts are Latin-1 only, so the em dash fails.
- **DOCX:** not supported. No pure-Python DOCX → PDF converter preserves
  layout. python-docx reads text only, and signing a lossy re-rendering of a
  counterparty's document is not acceptable. **Uploads are PDF only.** The
  upload route refuses anything else with 415, and the UI says so.
