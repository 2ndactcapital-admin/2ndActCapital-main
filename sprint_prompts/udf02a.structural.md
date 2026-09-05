# Sprint udf02a — DataGrid Columns & List Filters (STRUCTURAL)

**Tier:** `.structural` — held for manual merge review.
**Database:** Supabase project `mmgwmcinimzuhargsazs`.
**Branch:** cut fresh from current `main` (pull first — do not assume any stale branch is current; this sequence has had branches drift before).
**Predecessors:** udf00 discovery, Sprint 1a (definitions, 119/0/0), Sprint 1b (tabs/layout, 104/0/2), Sprint 1c (field-level security, 93/0/2, platform-scope RLS gap since patched).

## Regression baseline — already confirmed, do not re-derive as a discovery step

verify_udf01a.py: 119 PASS, 0 FAIL, 0 FIND
verify_udf01b.py: 104 PASS, 0 FAIL, 2 FIND (registered debt: record_type_id unused, udf_tabs has no bi-temporal columns)
verify_udf01c.py: 93 PASS, 0 FAIL, 2 FIND at time of last run — the platform-scope RLS gap has since been patched directly (migration apps/api/migrations/udf01c_fix_platform_scope_rls_gap.sql, already merged to main). Confirm current state by running verify_udf01c.py once, for real, as part of Task 1.

You WILL re-run all three verify scripts once, for real, in Task 1, specifically because udf01c's baseline changed since it was last measured. You will NOT re-run them again after that except as this sprint's own final regression gate in Task 3.

## No new DDL this sprint (2a is read-only)

This sprint adds no tables and no columns. It exposes existing data (portfolio.udf_definitions, portfolio.udf_values, portfolio.udf_tag_assignments) through new read paths. Sprint 2b, not this one, will handle writes (CSV import).

## Before writing any code — mandatory re-verification

Run:
grep -n "def resolve_field_access_bulk\|def resolve_tab_visibility\|def get_resolved_layout" apps/api/services/portfolio_udf.py apps/api/services/portfolio_udf_tabs.py apps/api/services/portfolio_udf_layouts.py

Read all three in full. This sprint's column-resolution and filter logic must reuse resolve_field_access_bulk for permission filtering — it must not reimplement permission checks inline.

Run:
grep -n "PositionsGrid\|inline_editable\|editable" apps/web/components/*.jsx apps/api/routers/*.py | head -40

Confirm the exact shape of the permissions + vocabularies envelope this sprint must extend — same envelope 1b and 1c both matched. Quote it.

Run against the live database:
select value_text, value_numeric, value_date, value_json from portfolio.udf_values limit 0;

Confirm the four typed columns backing UDF values (EAV) — this sprint's filter operators must be typed per udf_definitions.data_type, reading from the correct column, not string-matching everything through value_text.

Report findings before writing any code. If anything doesn't match this document's assumptions, stop and report rather than silently substituting.

## Scope boundary

In scope:
- A function returning the set of UDF columns available for a given (target_type, tab_id, org_id, user_context) — respecting both tab visibility and per-field FLS access, exactly as get_resolved_layout already does for the detail view.
- Filter operators per data type: text/long_text/email/url/phone -> contains/equals; integer/numeric/currency/percent -> equals/gt/lt/between; date/datetime -> equals/before/after/between; boolean -> equals; select/multiselect -> in/not-in against the field's value set; tags -> has-tag/has-any-of/has-all-of.
- A list endpoint that returns rows of a target type with their UDF columns inlined, filtered server-side by the above operators, and permission-filtered per row (a hidden field never appears in the row payload; a read field appears but is flagged non-editable in the response envelope, matching the existing is_read_only pattern from 1c).
- Sort by a UDF column (single column, ascending/descending).

Explicitly out of scope:
- CSV import/export — Sprint 2b.
- Any new DDL, any change to udf_definitions/udf_values/udf_tag_assignments schema.
- Frontend DataGrid.jsx component changes — backend only.
- Multi-column sort, saved filter views, or filter combination logic beyond simple AND across fields.

Report anything you skipped and why.

## Design

Column availability: a field is an available column for a caller if its tab is visible to them (resolve_tab_visibility) AND its resolved access is not hidden (resolve_field_access / resolve_field_access_bulk). A read-access field is a visible, filterable, sortable column that cannot be written to via this sprint's endpoints.

Filter safety: every filter value must be parameterized — no string-built SQL. Numeric filters must reject a value that fails the field's own type_params validation (e.g. more decimal places than the field's scale) rather than silently truncating. Tag filters query udf_tag_assignments by normalized_code, not raw tag_code, matching 1a's case-fold behavior.

Performance: a list of N records each needing M UDF columns must not be N×M queries. Use the same batching discipline resolve_field_access_bulk established in 1c — report the actual query count for a representative case (10 records x 5 UDF columns) in your verify output.

## Task 2 — Service layer

2a-1: get_available_columns(target_type, tab_id, org_id, user_context) — returns ordered list of {definition_id, api_name, label, data_type, type_params, access} for columns the caller may see, excluding hidden.

2a-2: build_filter_clause(definition_id, operator, value, data_type, type_params) — returns a parameterized SQL fragment, or raises for an invalid operator/type combination (e.g. contains on a numeric field is invalid — reject explicitly, don't silently coerce).

2a-3: list_records_with_udf(target_type, org_id, user_context, filters=[], sort=None, limit=50, offset=0) — the core query. Applies FLS-based column visibility, applies filters via 2a-2, applies sort, returns rows with UDF values inlined under a namespaced key (e.g. udf_values: {definition_id: value}) so they don't collide with the base record's own columns.

## Task 3 — Router and verification

3a — Endpoint:
GET /udf/records/{target_type}?tab_id=&filter=<json>&sort=&limit=&offset=

filter is a JSON array of {definition_id, operator, value}. Response envelope matches the permissions + vocabularies pattern established in 1b/1c.

3b — verify_udf02a.py, same rigor as its predecessors. Real writes (to seed test data), real filter queries, real permission checks, real HTTP calls, teardown to baseline.

Execution requirement — do not skip this. Run doppler run -- python3 apps/api/scripts/verify_udf02a.py yourself, synchronously, and put its full literal stdout in your final response. No background process, no "waiting for notification," no summary in place of the actual output. If you cannot complete execution in this turn, say so exactly and stop.

Assertions to include:
- verify_udf01a.py, verify_udf01b.py, verify_udf01c.py all green (re-run once, at the start, since udf01c's baseline changed; report their current actual counts)
- a hidden field never appears as an available column
- a read field appears as a column but is flagged non-editable (match 1c's is_read_only naming if applicable)
- tab-hidden excludes every field in that tab from available columns, regardless of individual field grants
- each data type's valid operator succeeds; an invalid operator for that type is rejected with a clear error
- a numeric filter value violating the field's own scale is rejected, not silently truncated
- a select filter with in against the field's value set works; a value outside the value set is rejected
- a tag filter has-tag matches case-insensitively via normalized_code
- sort ascending and descending both produce correctly ordered results
- query count for 10 records x 5 UDF columns is bounded and reported explicitly
- RLS: org A's list_records_with_udf call never returns org B's records or UDF values
- endpoint: 403 without view_portfolio, 200 with it
- teardown: every touched table returns to its exact pre-sprint row count

Do not merge. Do not push until every assertion is PASS, FAIL, or explicitly BLOCKED with a stated reason.
