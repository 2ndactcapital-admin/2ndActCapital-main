# Sprint udf00 — UDF Discovery (READ-ONLY)

**Tier:** `.discovery` — no DDL, no migrations, no application code, no branch merge.
**Database:** Supabase project `mmgwmcinimzuhargsazs` (Postgres 17).
**Output:** a single report file, `docs/discovery/UDF_DISCOVERY_REPORT.md`, plus a pass/fail summary printed to stdout.

## Hard constraints

- **Read-only.** No `CREATE`, `ALTER`, `DROP`, `INSERT`, `UPDATE`, `DELETE`, no migration files, no seed data. If you believe a write is needed to answer a question, do not perform it — record the question as unanswered and say why.
- Do not create, modify, or delete any application code.
- Do not propose a schema, write DDL, or design tables. This sprint establishes **what exists**, nothing more. Design happens after I read the report.
- No interactive prompts of any kind. No note-entry step, no "save these findings?" step. The script runs, checks, and reports.
- Report what you could **not** determine as explicitly as what you could.

## Context you need

Portfolio Reporting Layer **Phase G** shipped "user-defined fields across platform/org/team/user scopes" as part of a 389-assertion run already merged to main. **Assume UDF functionality already exists in some form.** The single most important output of this sprint is an accurate picture of what Phase G actually built. Do not assume greenfield.

---

## TASK 1 — Broad sweep

Run an `ILIKE` sweep across `information_schema.tables` and `information_schema.columns` for every one of these patterns, in both table names and column names:

```
%udf%          %user_defined%   %custom_field%   %customfield%
%attribute%    %picklist%       %pick_list%      %value_set%
%valueset%     %layout%         %field_def%      %definition%
%tag%          %custom_tab%     %metadata%
```

For every distinct table that matches, report:

- schema, table name, and whether it is a table, view, or materialized view
- exact row count (`count(*)`, real count — not a `reltuples` estimate)
- whether RLS is enabled (`pg_class.relrowsecurity`) and the policy names on it
- for views: whether `security_invoker = true` is set

Also sweep for any `jsonb` column on any table whose name suggests it holds user-defined values (`udf_values`, `custom_values`, `attributes`, `extra`, `metadata`, `data`). Report the table, column, and row count of rows where that column is non-null and non-empty.

Report the full match list even where the match looks incidental. I want the false positives visible, not filtered out by your judgment.

---

## TASK 2 — Introspect the Phase G implementation

For every table Task 1 identified as genuinely UDF-related, produce:

**Structure**
- Every column: name, data type, nullability, default
- Primary key, all foreign keys (both directions — what it references and what references it), unique constraints, check constraints, indexes (including index type — flag any GIN)
- Any triggers on the table, with the trigger function name and what it does

**Enums**
- Any enum type used by these tables, resolved via `pg_type` joined to `pg_enum` and `pg_namespace`, with all values in sort order

**Data**
- A sample of up to 20 real rows per table (redact nothing — this is our own dev data)
- If a scope column exists (platform/org/team/user or similar), the distinct values present and a count per value
- If `org_id` exists, the distinct orgs represented and count per org

**Specifically answer these, each as a yes / no / could-not-determine with the evidence:**

1. Where do UDF **values** live — a JSONB column on a parent record, an EAV table, physical columns, or something else?
2. Which parent objects are UDF-enabled today? Is `entities` (CRM) among them, or is this portfolio-only?
3. Is there a concept of field **type** (text/decimal/picklist/etc.)? What types are supported, and where is the type stored?
4. Are there **type parameters** — precision, scale, length, min, max — anywhere? In what form?
5. Is there any **picklist / value set** concept, or are all fields free-form?
6. Is there any **permission** binding on UDFs — field-level, tab-level, or any join to the SOC permission-set tables?
7. Is there any **layout / placement / ordering** metadata?
8. Is there any **audit** of definition changes, or any **history** of value changes?
9. Is there any **soft-delete** or active/inactive state on definitions?
10. Does anything reference these tables from application code — routers, services, serializers, the DataGrid, the BPMN action registry? Grep the repo and list every file and line.

---

## TASK 3 — Answer the four blocked design questions from the live schema

These four are blocking the Sprint 1a DDL. Answer each from evidence in the database and repo, or state plainly that the evidence does not settle it.

**A — Field-level security.** Does the existing SOC permission model have any per-field granularity, or is it strictly per-action? Look at the Sprint-11 action registry and the profiles / permission-sets tables. If per-field security were added, what is the natural join — permission_set_id, profile_id, or both? Report the actual table and column names it would bind to.

**B — Layout metadata.** Does anything in the repo already render a configurable field layout (sections, column spans, ordering)? Check the CRM entity detail page and its tab components. If a layout convention already exists in the frontend, describe it — we should extend it rather than invent a parallel one.

**C — Value history / retention.** Is there any existing append-only journal, audit table, or history-tracking trigger anywhere in this database that records old/new values on record updates? Name every one you find and how it is populated (trigger, application code, or both). This determines whether a UDF value journal is a new pattern or an instance of an existing one.

**D — Tags.** Does a tag concept already exist? There is a known `entity_document_tags` table with an `is_fixed` flag from the Chancery work. Report its full structure, how `is_fixed` is used, whether tags there are free-form or constrained, and whether that vocabulary is scoped per-org. If a reusable tag pattern exists, say so — we may adopt it rather than build a second one.

Also report: does a `reference_data` table exist, what lists does it hold, and is it a viable home for UDF value sets or genuinely a different concern?

---

## Report format

Write `docs/discovery/UDF_DISCOVERY_REPORT.md` with one section per task and a final block titled **"Blocking questions — answered / unanswered."** For each of the ten Task 2 questions and the four Task 3 questions, one line: the question, the answer, and the specific table/file/line that supports it.

Print to stdout a pass/fail line per task — pass meaning the task completed and the report section is written, fail meaning something prevented it. Then exit.

Do not push, do not open a PR, do not merge. Leave the report file on the branch for manual review.
