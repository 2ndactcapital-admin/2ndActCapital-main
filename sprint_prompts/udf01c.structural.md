# Sprint udf01c — Field-Level Security (STRUCTURAL)

**Regression baseline — already confirmed, do not re-run as a discovery step:**

```
verify_udf01a.py: 119 PASS, 0 FAIL, 0 FIND
verify_udf01b.py: 104 PASS, 0 FAIL, 2 FIND (record_type_id unused; udf_tabs
  has no bi-temporal columns — both pre-existing registered debt, not this
  sprint's concern)
```

These were run directly, by hand, immediately before this prompt was written — not by a prior Claude Code session. Treat this as ground truth. You do not need to re-run either script during Task 1. You WILL re-run both once, at the very end of Task 3, as this sprint's own regression gate — but not before.

Predecessors: `docs/discovery/UDF_DISCOVERY_REPORT.md` (udf00), Sprint 1a, Sprint 1b. Part 1 DDL for this sprint is already applied directly to the live database and confirmed: `portfolio.udf_field_permissions` exists with 3 indexes (PK + two partial uniques) and RLS enabled. Do not attempt to create it.

## Before writing any code — mandatory re-verification (schema only, no script re-runs)

Every prior sprint in this sequence has found the schema not matching prompt assumptions at least once. Do not skip this. This step is schema introspection only — fast, a few queries — not a script execution step.

Run against the live database:
- Full column list for `portfolio.udf_definitions`, `portfolio.udf_tabs`, `portfolio.udf_tab_permissions`
- Full column list for `public.profiles`, `public.permission_sets`, `public.profile_permissions`, `public.permission_set_permissions`
- Constraint definitions on `portfolio.udf_tab_permissions`
- Confirm `portfolio.udf_field_permissions`' actual column list and constraints match: `id`, `definition_id`, `profile_id`, `permission_set_id`, `access` (CHECK hidden/read/edit), `created_at`, `created_by`, plus the one-grantee CHECK and the two partial unique indexes.

Then in the codebase:
- Read `resolve_tab_visibility` and `set_tab_visibility` in `apps/api/services/portfolio_udf_tabs.py` in full. Quote their actual signatures. `resolve_field_access` and `set_field_access` in this sprint must follow the identical parameter shape and combination logic — do not reinvent it.
- Confirm how `GET /udf/definitions`, `GET /udf/values/...`, and `GET /udf/layouts/{tab_id}` in `apps/api/routers/udf.py` currently serialize field data. Quote the exact response-building code.
- Confirm whether `services.rbac`'s permission check and `services.profiles`' profile/permission-set resolution are both reachable from the same request context in `routers/udf.py`, or whether the router currently only resolves one.

Report all findings before writing any code. If anything doesn't match this document's assumptions, stop and report rather than silently substituting.

## Scope boundary

**In scope:** three-state field access (`hidden`/`read`/`edit`) via `portfolio.udf_field_permissions`, binding to both `profile_permissions` and `permission_set_permissions` paths, serializer-level enforcement across the three existing endpoints named above, and updating `get_resolved_layout` to filter by the caller's actual field access.

**Explicitly out of scope:** DataGrid columns, list filters, CSV import (Sprint 2). Record types (still reserved columns only). `udf_tab_audit` / bi-temporal columns on `udf_tabs` (registered debt from 1b — do not fix here). Frontend consumption of the new `access` field (backend only).

Report anything you skipped and why.

## Design

Precedence: tab hidden (via `resolve_tab_visibility`) wins outright — every field in a hidden tab is hidden regardless of any field-level grant. Check this first and short-circuit. On a visible tab, `resolve_field_access` decides per field. Most-restrictive-wins across the two grant paths, mirroring `resolve_tab_visibility` exactly: if a profile-level grant says `read` and a permission-set-level grant on the same field says `edit` for the same caller, the caller gets `read`. Test both directions independently. No grant at all on a visible tab defaults to `edit`, matching 1b's precedent — flag explicitly if you think FLS should default differently given its sensitivity.

## Task 2 — Service layer

**2a:** `set_field_access(definition_id, profile_id=None, permission_set_id=None, access)` — exactly one grantee. Upsert semantics, not duplicate rows.

**2b:** `resolve_field_access(definition_id, tab_id, user_context)`. Calls `resolve_tab_visibility` first; if false, return `'hidden'` immediately without checking field grants. If tab visible, check both grant paths on the field; most-restrictive wins; no grant defaults to `'edit'`.

**2c:** `resolve_field_access_bulk(definition_ids, tab_id, user_context)` returning a `{definition_id: access}` map in a bounded number of queries, not one query per field. Report the actual query count for a 10-field layout.

**2d:** Update the response-building code identified in Task 1 so a `'hidden'` field's key is absent from the response entirely (not null, not present-empty — test this distinction explicitly). A `'read'` field appears but any write to it is rejected with a clear error naming the field and its access level. An `'edit'` field behaves as today.

**2e:** `get_resolved_layout` must exclude hidden fields' layout items from the section tree entirely (not just the value), flag read fields `is_read_only: true` regardless of the layout's own `is_read_only` setting, and leave edit fields unchanged. A field's layout-level `is_read_only` and its FLS-resolved access combine as most-restrictive-wins.

## Task 3 — Router and verification

**3a:** Add `PUT /udf/fields/{definition_id}/permissions` (body: `profile_id` or `permission_set_id`, `access`) and `GET /udf/fields/{definition_id}/permissions` (list of current grants, admin visibility). Update the three existing endpoints in place to apply field-level filtering — do not create parallel v2 endpoints.

**3b:** Write `apps/api/scripts/verify_udf01c.py`, same rigor as `verify_udf01a.py` and `verify_udf01b.py`: real writes, real RLS checks, real HTTP calls via `TestClient`, teardown to baseline confirmed by row count on every touched table including all 1a and 1b tables.

**Execution requirement — read this twice before Task 3 begins.** Three prior sessions in this sprint sequence have ended a turn claiming to be "waiting for a background run" or "waiting for a completion notification" for a verify script. **No such mechanism exists in this tool.** A `-p` invocation is a single synchronous turn: if you do not run the command and read its output yourself, in this turn, before writing your final response, the work has not happened, regardless of what you say happened. Concretely:

- Run `doppler run -- python3 apps/api/scripts/verify_udf01c.py` yourself, synchronously, via your Bash tool.
- Wait for it to return. It may take one to several minutes — that is normal and expected. Do not treat a several-minute-long single tool call as a signal to stop and wait for something else; it is not asynchronous, it just takes a while.
- Paste its full literal stdout into your final response — every `[PASS]`/`[FAIL]`/`[FIND]` line, not a summary count.
- If the command errors, times out, or you cannot get it to complete in this turn, say exactly that, and show the exact error — do not describe a plan to run it later, do not claim a background process is handling it.

Assertions to include in `verify_udf01c.py`:

- `verify_udf01a.py` and `verify_udf01b.py` both still green at baseline (119/0/0 and 104/0/2) — run both for real, as this sprint's own regression gate, at the end of Task 3
- every Part 1 object, constraint, index exists as specified
- tab-hidden wins outright: a field with an explicit `edit` grant is still `hidden` when its tab is hidden for the caller
- profile-level `read` wins over permission-set-level `edit` on the same field
- permission-set-level `hidden` wins over profile-level `edit` on the same field (the other direction, tested independently)
- no grant at all defaults to `edit` on a visible tab
- a hidden field's key is absent from `GET /udf/definitions` response — assert the key doesn't exist, not that its value is null
- a read field appears in the response but a write to it via `PUT /udf/values/...` is rejected, naming the field
- an edit field's write succeeds normally (positive control)
- `get_resolved_layout`: a hidden field's layout item is absent from the section tree entirely
- `get_resolved_layout`: a read field's item carries `is_read_only: true` even when the layout definition itself set `is_read_only: false`
- `resolve_field_access_bulk` query count is bounded, reported explicitly for a 10-field layout
- RLS: org A cannot read org B's field permission grants
- every router endpoint: 403 without the relevant permission, success with it
- teardown: every touched table returns to its exact pre-sprint row count, including all 1a and 1b tables

Do not merge. Do not push until every assertion is PASS, FAIL, or explicitly BLOCKED with a stated reason.
