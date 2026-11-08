# REP15 — Task 1 discovery (2026-11-08)

Read from the code at `6a83941` (rep15-work's base: rep14 and disclosures1 merged, schema snapshot refreshed) and the
live database (read-only queries). Nothing here was assumed from the sprint prompt. Counts are as of 2026-11-08 and
are printed by the verify, never frozen into a check.

## 1a. The model registry — tables, version lifecycle, ownership, the library, how an account joins

- **Tables (rep3, `docs/rep3_part1.sql`, schema `portfolio`).** `models` (system axis; `org_id` is the owner — one
  registry per org; `source` upload | library_copy | library_subscription | fund_replica | composite; `kind`
  long_only | long_short since rep13), `model_versions` (system axis), `model_version_positions` and `model_sleeves`
  (valid axis), `model_uploads` + `model_upload_events` (the permanent upload log), `model_column_mappings`,
  `model_library_entries`, `model_subscriptions`, `model_events`.
- **Version lifecycle.** draft → submitted (a `model_version_approval` human task, maker = the submitter) →
  approved → superseded; rejected returns to draft; abandoned. Maker-checker in three places: the service
  (`MAKER_CHECKER_RULE`), the engine, and the CHECK `model_versions_maker_checker_chk` (checker never in
  `maker_ids`). `model_versions_guard` freezes an approved version (only approved → superseded). One approved and one
  open version per model (partial unique indexes). Approval emits `model.version_approved`; rep5 turns it into
  rebalance PROPOSALS (`rebalance_proposals.on_model_version_approved`) — never a trade.
- **The library.** One table, two shelves: shelf `platform` (`owner_scope 'platform'`, `org_id` NULL, written only
  under `app.is_super_admin`, read by every org) and shelf `org`. `list_library` lists both. Copy = an independent org
  model; subscribe = an org model (`library_subscription`) that receives a draft + `model_version_review` task (no
  maker, `system_open`) on every new approved version of the entry's model.
- **[FIND] rep3's platform shelf is an UNGATED "publish to every tenant" path.** A Super Admin can publish any model
  of the Hollisworks org to every org today, and the SELECT carve-out
  (`model_is_platform_published` / `model_version_is_platform_readable`) then exposes that model's row, its live
  approved version AND its positions to every org. There is no regulatory gate. Live: **0 platform entries, 0
  subscriptions, 0 models in any org** — nothing real depends on it. rep15 does NOT build on it (it exposes the
  provider's raw registry rows); instead it gates it for REAL orgs: publishing to the platform shelf needs the real
  platform org's gate open unless the publisher is a fixture (is_test) user, and loading from it needs the gate
  unless the org is is_test — so verify_rep3's own fixture flow still runs.
- **How an account joins.** `public.account_model_links` (rep5): `model_id` + `follow = 'live'` (the CHECK admits
  nothing else) — an account always follows its model's live approved version. Every change is a pending version +
  an `account_model_link_approval` task (maker-checker). `account_models.model_brief` reads the model in the
  caller's org only.
- **Naming.** `models_reserved_name_chk` reserved "Holdings of" for fund_replica (rep4) and long_short (rep13).
  rep15 extends it to the two new sources and makes it REQUIRED for them (`models_provided_chk`).

## 1b. How a tenant can read a platform-org row without breaking RLS

- **Existing patterns.** (1) Platform rows with `org_id` NULL and `owner_scope 'platform'`, SELECT `USING (true)` or
  `shelf = 'platform'`, writes only under `app.is_super_admin` (custodian_capabilities, the security master,
  `model_library_entries`). (2) A carve-out on SELECT through a SECURITY DEFINER boolean helper with a pinned
  search_path that answers one question (rep3's two helpers; the org preauth resolver).
- **The safe way chosen.** A tenant never reads the provider's registry (`models` / `model_versions` / positions /
  uploads stay strictly org-isolated). It reads `portfolio.catalog_versions` — a frozen snapshot of the model's
  TARGET WEIGHTS only — through the SELECT carve-out `status IN ('published','retired') AND
  portfolio.catalog_visible(provider_org_id)`; `catalog_visible` is true only while the provider's gate is open and
  the reader's `is_test` matches the provider's (`provider_tenant_allowed`). The provider reads tenants only through
  two SECURITY DEFINER functions that return status, account counts and model assets — no member, account,
  position, household or person.

## 1c. Fee schedule / fee calc and the GL posting API

- **Fee schedules / calc** (`services/fee_schedules.py`, `fee_calc.py`, `fee_runs.py`; fee35–fee43): per-account
  schedule assignments, runs, lines, invoices, receipts. A model-provider fee is owed by an ORG to the provider, not
  by an account to the adviser, so it does not fit `fee_run_lines` (one line per billed account / entity); rep15 keeps
  its own accruals + invoices and posts through the shared GL API.
- **The posting API** (`services/ledger/posting.py`): `build_entry_on_conn(conn, vehicle_id, transaction_type_code,
  entry_date, amount, dims, …, vehicle_kind, memo, source_event_id, reverse_sides)` builds a draft entry from the
  org's `posting_templates` (+ `posting_template_lines` by `chart_of_accounts.code`); `post_on_conn` calls
  `fn_post_journal_entry` (balance checked, `posted_at` set). Reversal: `fn_reverse_journal_entry(entry, reason, user)`
  inserts the mirrored entry with `reverses_entry_id` and posts it. Vehicles: `SPV` or `LEDGER_BOOK`
  (`ledger_books`, per org, system axis, `ledger_books_code_uq`). Immutability: `trg_guard_posted_lines` refuses any
  change to a posted entry's lines ("reverse instead"); `jl_one_side` CHECK; `fn_validate_line_org` keeps account and
  entry in one org. fee43's verify tore its GL rows down by `ALTER TABLE … DISABLE TRIGGER` — rep15 adds the narrow
  documented path instead (`ledger.teardown = 'fixture'`, not app_service, is_test orgs' entries only).
- **[FIND] The Hollisworks platform org has NO general ledger**: 0 chart-of-accounts rows, 0 books, 0 posting
  templates (2nd Act's org has 28 accounts, books RIA_OPERATING / CLUB_DUES and 12 templates). The spec also says the
  fee is "owed to 2nd Act". So WHICH org's books carry the revenue is a provider-record field (`ledger_org_id`, the
  provider org by default) and an operator decision; opening the gate provisions book `MODEL_PROVIDER`, accounts 1230
  Model-Provider Fee Receivable / 4800 Model-Provider Fee Revenue and template `MODEL_PROVIDER_FEE` in that org
  (1230 and 4800 are free in 2nd Act's chart too).

## 1d. The disclosure library's resolver and acknowledgement calls

- **Resolver** (`services/disclosures.py`): `resolve_many(conn, keys, *, org_id, household_id, account_id, as_of)` —
  one query, the most specific PUBLISHED version in force (platform → org → household → account; latest effective,
  then highest version). `resolve_safe` never raises (savepoint) and falls back to the exact pre-library text for
  keys in `FALLBACK_SOURCES`. A tenant-specific (org-scope) version therefore overrides the platform default by the
  resolver's own rule — rep15 needs no second resolver.
- **Acknowledgement.** `acknowledge(conn, *, org_id, user_id, resolved, subject, screen)` records version id + hash
  (composite FK), idempotent per (user, version, subject); `ack_state` / `require_current` refuse unless the version
  in force NOW was acknowledged for the subject (a newer version means acknowledging again). Subject types are a
  CHECK (`disclosure_ack_subject_chk`) — rep15 adds `model_subscription`.
- **[FIND] The defaults loader requires a default file for EVERY key** (`load_disclosure_defaults.py`). The ADV and
  the sub-advisory agreement must never be text in code, so rep15 adds `disclosures.NO_PLATFORM_DEFAULT` (the loader
  skips them and refuses a default file for them; verify_disclosures1's dry-run expectation follows). Only
  `model_provider.member_notice` gets a platform default ("have counsel review") plus its offline fallback.
- **[FIND] disclosures1's verify never wrote a platform row of a REAL key.** rep15's subscription test needs a
  platform default of `model_provider.subadvisory_agreement`; verify_rep15 writes one only when no real one exists,
  drafted by a fixture Super Admin and removed by that drafter id. While it exists no real org can act on it (the
  real gate is OFF; a real tenant can never subscribe to a fixture provider).
