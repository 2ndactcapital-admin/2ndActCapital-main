Sprint udf02b — CSV Import/Export (STRUCTURAL)

Predecessors, baselines are FACT, do not re-execute unless your change could
plausibly break one:
verify_udf01a.py: 119/0/0
verify_udf01b.py: 104/0/2 (registered debt, not yours)
verify_udf01c.py: 93/0/2 (registered debt, not yours)
verify_udf02a.py: 73/0/0

No new DDL. Reuses portfolio.udf_values write path (record_udf_value),
portfolio.udf_definitions type_params validation, resolve_field_access_bulk,
get_available_columns, build_filter_clause — all from 1a/1c/2a. Read those
four functions in full before writing code. Do not reimplement type
validation, permission checks, or tag validation — call the existing
functions.

SCOPE

Export: GET /udf/records/{target_type}/export?tab_id=&filter=<json>
Same column-visibility and filter rules as 2a's list endpoint. Streams CSV.
Hidden fields excluded. Header row uses api_name. read-only fields included
(export is read-only regardless of access level).

Import: POST /udf/records/{target_type}/import (multipart CSV body)
Row-level failure, not batch failure — one bad row does not fail the batch.
Each row: validate required fields, type_params (reuse existing validators),
value_set membership for select/multiselect, tag create-permission for new
tags. A row targeting a hidden or read-only field for the caller is
rejected for that row. Return: {accepted: [...], rejected: [{row, reason}]}.
No dry-run mode this sprint — real writes, but every accepted row uses the
same append-only value write as everywhere else (never overwrite in place).

OUT OF SCOPE: preview-before-commit UI, saved import templates, scheduled
imports, frontend wiring.

TASK 1 — confirm read

grep -n "def record_udf_value\|def resolve_field_access_bulk\|def get_available_columns\|def build_filter_clause" \
  apps/api/services/portfolio_udf.py apps/api/services/portfolio_udf_records.py

Read all four in full. Report signatures before writing code.

TASK 2 — service layer

export_records_csv(target_type, org_id, user_context, tab_id, filters) ->
  csv stream, reusing 2a's column/filter logic verbatim.

import_records_csv(target_type, org_id, user_context, tab_id, csv_bytes) ->
  {accepted, rejected}. Per row: resolve target record (by external_id or
  provided target_id column — pick one convention, state which, and reject
  rows missing it). For each UDF column in the CSV header, validate against
  that field's access (hidden/read -> reject row), type_params, value_set,
  tag permission. Write via record_udf_value only for accepted rows. Do not
  partially write a row — a row with ANY invalid field is entirely rejected.

TASK 3 — router + verify

Endpoints as specified above, apps/api/routers/udf.py.
verify_udf02b.py, same rigor as predecessors. Do NOT chain-call predecessor
main() functions — state their baselines as a comment, verify only new work
plus one negative-case check per shared function reused (e.g. one call
confirming hidden-field rejection still holds via 2a's function).

Assertions:
- export excludes hidden fields, includes read-only fields, respects filter
- export column headers use api_name
- import: valid row succeeds, values written via append-only path (assert
  system_to on any predecessor row, not overwrite)
- import: invalid type value rejects that row only, batch continues
- import: value outside value_set rejects that row only
- import: row targeting hidden field for caller rejects that row only
- import: row targeting read-only field for caller rejects that row only
- import: new tag without create_tags permission rejects; with permission
  succeeds
- import: missing target-id column rejects that row with clear reason
- import: rejected array includes row number and reason for every failure
- RLS: org A cannot export or import against org B's records
- endpoints: 403 without permission, 200/201 with it
- teardown: every touched table returns to baseline

Run verify_udf02b.py yourself via doppler run, synchronously. Paste full
literal stdout. Do not end turn without real captured output. Do not commit
or push — leave held for review.
