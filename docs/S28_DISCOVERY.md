# S28 — AI drift monitor: Task 1 discovery (2026-11-07)

Read-only. SQL against the live database (the dev MCP connection, then `scripts/ai_drift_check.py --dry-run` on the
app's own `app_service` DSN inside a READ ONLY transaction). Code paths read from the working tree at `3362b7f`.

## 1a. Every writer of `ai_decision_log`

**The S27 router owns the write.** `services/extraction.py` — `_execute_chain` (the TaskRouter: org model, fallback
chain, effort, LiteLLM transport) calls `_safe_log` → `_write_ai_decision`, the ONE `INSERT INTO ai_decision_log` of
the router. `_safe_log` is non-blocking (a failed write is printed, never raised). Every `call_claude_json` /
`call_claude_text` / `call_claude_with_tools` call goes through it, with the caller's `task_type`.

| Writer (file) | task_type values | cost | latency | effort | bypass |
|---|---|---|---|---|---|
| `services/extraction.py` `_execute_chain` (the router) — callers: `routers/dashboard.py` `member_brief`; `routers/assistant.py` `assistant`; `services/document_classifier.py` `document_classifier`; `extraction.py` `crm_extraction`, `profile_extraction`; `services/note_terms_extraction.py` `note_terms_extraction`, `note_terms_hazard_ensemble`, `note_terms_underlyings`; `services/workflow_nl_generator.py` `workflow_generation`; `services/fee_spec.py` `fee_schedule_spec`; `services/fee_narratives.py` `fee_narrative_polish`; `services/narrative_extraction.py` `narrative_extraction`; `services/vdr_analysis.py` `vdr_analysis`; `services/assistant_actions/crm.py` `crm_draft_note`; `routers/marketplace.py` `deal_summary`; `routers/investment_profile.py` `foundation_reply`, `client_brief`, `brief_themes` | success: computed from usage; **every failure path writes NULL** | always (`max(1, …)`; the two policy refusals — not-authorised, public-data-only — write a literal **1 ms**) | `effort_requested` always resolved; `effort_used` only when sent | always (`transport != litellm`) |
| `services/document_embedding.py` (its own chain executor, writes through `ex._safe_log`) | `embedding_document`, `embedding_query` | success: computed; failure: NULL | always | **never** (embeddings take no effort) | always |
| `services/edgar_inventory.py` `log_decision` — **outside the router**: the proxy chokepoint, its own INSERT on the run's connection, Hollisworks org | `edgar_inventory`, `edgar_inventory_grouping` | from the proxy's response | from the proxy call | **never** | **always false** (hard-coded) |
| `scripts/verify_litellmphaseb.py` (a verify; direct INSERT) | its own fixture rows | — | — | — | — |

`services/note_terms_extraction.py` also READS the log (`model_used` back, to prove a two-model ensemble really used two
models). `services/litellm_credentials.py` mentions it only in a docstring.

**Gaps found.**
- **47 rows with NULL cost** (document_classifier 18, member_brief 17, workflow_generation 12) — all `success = false`,
  every one an HTTP 400 from the provider (2026-08-23 … 2026-09-07). NULL cost is by design on every failure path, so
  cost per call is computed over non-NULL cost only.
- **0 rows with NULL latency.** But a policy refusal is logged as 1 ms, so latency is computed over SUCCESSFUL calls
  only (`services/ai_drift.py`).
- **Two task types written outside the router**: `edgar_inventory`, `edgar_inventory_grouping` (no effort columns,
  bypass hard-coded false, model_used = the proxy's reported model). The monitor reads them the same way; their
  bypass and effort rules can never fire.
- **`effort_requested` / `effort_used` are NULL on all 662 rows** — no org has an effort configured — and
  **`litellm_bypassed` is false on all 662** — so the effort-downgrade and bypass rules have no history yet (they say
  "not enough data" / "ok at 0 %" until there is some).
- **[FIND] verify artifacts in REAL orgs' logs: ~91 of 662 rows.** `workflow_generation` (default org, 28 rows,
  `latency_ms = 1`, a fixed `cost_usd = 0.001700`, success — a stubbed provider: 2026-07-29 / 08-26 / 09-18 / 09-30),
  `edgar_inventory` + `edgar_inventory_grouping` with `model_used = 'verify-edgarcohorts-model'` (Hollisworks org, 63
  rows, latency 5 ms), `verify_lag_calibration` (default org, 2 rows). They are other sprints' rows in real orgs; this
  sprint did not delete them. Consequence for the monitor: a verify that writes into a REAL org's log looks like real
  traffic (a "new" model such as `verify-edgarcohorts-model` would raise a model-change alert). The drift job itself
  never reads a test org (services.orgs), and S28's verify writes only into its own test orgs.

## 1b. The live log profile per task_type (2026-11-07)

662 rows, 2026-07-29 → 2026-11-07, 11 task types, 2 orgs (2nd Act default org, Hollisworks). Cost and latency below
are `percentile_cont` (interpolated) from SQL for the profile; the monitor's own definitions are exact (median =
mean of the two middle values; p95 = nearest rank) — see `services/ai_drift.py`. Latency p95 here = successful calls
(`percentile_disc`, nearest rank).

| task_type | rows | first → last | active days | calls/active day | cost/call median / p95 (USD) | latency median / p95 (ms, success) | fallback | failure | bypass | effort ↓ | models used (month:model) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| member_brief | 237 | 07-29 → 11-07 | 20 | 11.9 | 0.001566 / 0.002113 | 3334 / 4461 | 7.2 % | 7.2 % | 0 % | — | 07: claude-sonnet-4-6 · 08: claude-sonnet-4-6, **claude-haiku-4-5-20251001** · 09: claude-haiku-4-5-20251001 · 10–11: **claude-sonnet** |
| document_classifier | 92 | 07-31 → 08-23 | 3 | 30.7 | 0.001004 / 0.001223 | 1431 / 2173 | 0 % | 19.6 % | 0 % | — | claude-haiku-4-5-20251001 |
| crm_extraction | 77 | 11-05 → 11-05 | 1 | 77 | 0.000570 / 0.000917 | 1188 / 1676 | 0 % | 0 % | 0 % | — | claude-haiku |
| edgar_inventory | 54 | 10-08 → 10-19 | 5 | 10.8 | 0.011418 / 0.011418 | 5 / 5 | 0 % | 0 % | 0 % | — | verify-edgarcohorts-model (verify artifact) |
| note_terms_extraction | 54 | 08-23 → 08-23 | 1 | 54 | 0.013303 / 0.015696 | 8140 / 10700 | 0 % | 0 % | 0 % | — | claude-haiku-4-5-20251001 |
| note_terms_hazard_ensemble | 54 | 08-23 → 08-23 | 1 | 54 | 0.026921 / 0.032853 | 8059 / 18903 | 0 % | 0 % | 0 % | — | claude-sonnet-4-6 |
| workflow_generation | 48 | 07-29 → 11-04 | 8 | 6.0 | 0.001700 / 0.011313 | 1 / 9137 | 0 % | 25.0 % | 0 % | — | claude-haiku-4-5-20251001 → claude-haiku (09) |
| assistant | 24 | 07-30 → 11-04 | 5 | 4.8 | 0.010199 / 0.015039 | 3308 / 6941 | 0 % | 0 % | 0 % | — | claude-sonnet-4-6 → claude-sonnet (10) |
| edgar_inventory_grouping | 18 | 10-08 → 10-19 | 5 | 3.6 | 0.008425 / 0.016658 | 12287 / 64248 | 0 % | 0 % | 0 % | — | gpt-5-mini-2025-08-07 (requested gpt-5-mini), verify-edgarcohorts-model |
| embedding_query | 2 | 09-17 → 10-21 | 2 | 1 | 0.0000005 / 0.00000095 | 557 / 618 | 0 % | 0 % | 0 % | — | voyage-3.5 |
| verify_lag_calibration | 2 | 09-17 → 09-17 | 1 | 2 | 0.000038 / 0.000038 | 1018 / 1070 | 0 % | 0 % | 0 % | — | claude-haiku (verify artifact) |

Effort downgrade rate is "—" everywhere: no row has `effort_requested` set. Calls in the last 24 h: **16** (member_brief
only); last 7 days: member_brief 174, crm_extraction 77, assistant 14, workflow_generation 4.

**Model changes visible in history** (each is exactly what the model-change rule is for): member_brief
claude-sonnet-4-6 → claude-haiku-4-5-20251001 on 2026-08-24 (17 rows, every one `fallback_used = true` and
`success = false` — the August provider-400 burst), then the logical alias `claude-sonnet` from 2026-10-01 (an org
configuration change: requested and used are both the alias); assistant and workflow_generation moved to the logical
aliases (`claude-sonnet`, `claude-haiku`) the same way.

**Model mismatch without fallback**: 9 rows, all `edgar_inventory_grouping` `gpt-5-mini → gpt-5-mini-2025-08-07` —
the proxy reporting the DATED version of the very alias it was asked for. The mismatch rule treats a date-pinned
resolution of the requested alias as a match (otherwise every call of that task would alarm forever); a real version
change of that kind is still caught by the model-change rule. With that rule there are 0 mismatches in the live log.

**Cadence.** The busiest live task (member_brief) makes ~12 calls on an active day; everything else is bursty (a
whole day's batch in one run). The job runs **every 6 hours** (`17 */6 * * *` UTC): a run sees ~3 new calls, so hourly
would re-read nearly the same 24 h window six times and put 24 runs a day on the platform Runs screen for no gain in
what it can see. Detection delay is ≤ 6 h; the evaluation is two reads of the log.

## 1c. Minimum sample, and which tasks have too little history today

**The rule: one event in the recent window must never cross a threshold on its own.**
- rate rules (fallback / failure / bypass / effort downgrade), threshold N points: one event is 100 / n points, so the
  recent window needs **n ≥ ⌈100 / N⌉** — 10 calls for a 10-point rule, **20** for the 5-point failure rule;
- p95 latency: with fewer than 20 values the nearest-rank p95 IS the maximum, so one slow call moves it — **20**;
- median cost: a median of 5 needs 3 high values to move — **5**;
- baseline: **20** for every comparative rule (the smallest sample whose p95 is not its maximum, and a 5-point
  reference rate); model mismatch needs no baseline (it compares requested with used inside the recent window);
  model change needs 20 baseline calls to say "the baseline never had it".

The baseline minimum was first seeded at 30 and lowered to 20 (threshold **version 2**, the same day, through the
monitor's own versioned write — history kept) after replaying the one real incident in the log: on 2026-08-24 the
member_brief baseline had **24** calls, and with 30 the monitor would have said "not enough data" through a 100 %
fallback burst. With 20 it raises **model change** after the first 2 recent calls (23:00 UTC) and **fallback rate
100 % vs 0 %** by 12:00 the next day (`docs/S28_DRY_RUN.txt`, replay section).

**Today (now = 2026-11-07 20:22 UTC, recent = last 24 h, baseline = the 28 days before):**
- **member_brief (2nd Act)** — the only task with a recent window: 16 recent / 174 baseline calls. Enough for cost,
  fallback, bypass, model change, mismatch; **not enough for p95 latency and failure rate** (16 < 20).
- **Not enough data — no recent calls**: assistant (baseline 21), crm_extraction (77), edgar_inventory (48),
  edgar_inventory_grouping (8), embedding_query (1), member_brief in the Hollisworks org (2), workflow_generation (4).
- **No baseline at all** (nothing in the last 29 days): document_classifier, note_terms_extraction,
  note_terms_hazard_ensemble, verify_lag_calibration.
- **Effort downgrade: no history anywhere** (no effort configured on any org).

A low-volume task can be given a per-task override (lower `min_recent`) on the page; the platform default stays at the
rule above.

## 1d. The alert path held platform-job runs use, and the job switch screen

- **Table:** `public.member_todos` (no unique key beyond `id`; idempotency is SELECT-then-INSERT/UPDATE keyed on
  `(user_id, org_id, source, related_type, related_id)`; statuses `open` / `done` / `dismissed`).
- **Service:** `services/workflow_todos.py` — `create_held_run_alerts` (source `workflow_run_held`, related_type
  `workflow_run`), called by `services/workflow_engine.py` when a run HOLDS; recipients = the run's starter (for a
  platform job, the trigger creator = the platform org's Super Admin, `security_master_jobs.platform_job_owner`) ∪ the
  org's `manage_org_settings` holders (`rbac.get_users_with_permission`); an empty recipient set writes an `audit_log`
  row (`_record_undelivered_alert`) instead of failing silently. `security_master_jobs.close_held_run` marks the
  todos done. The AI budget / model-availability / credential alerts already reuse the same `_upsert_todo`.
- **Screen:** the dashboard todo list (`routers/dashboard.py`, `status = 'open'`), each todo's `action_key` linking to
  the console page.
- **S28 reuse:** `workflow_todos.create_ai_drift_alert_todos` / `close_ai_drift_alert_todos` (source `ai_drift`,
  related_type `ai_drift_alert`, related_id = the alert's id) — same `_upsert_todo`, same undelivered-audit fallback.
  The alert itself (one open per org / task / rule, its numbers, its history) lives in `public.ai_drift_alerts` +
  `public.ai_drift_alert_events`; the todo is how people are told, never a second channel.
- **Job switch:** BUGFIX4's `POST /admin/security-data/master/jobs/{job_key}/schedule` (Super Admin, audited
  `security_master.schedule_switched`) on **Security master → Platform jobs**
  (`apps/web/components/securityData/SecurityMaster.jsx`, `data-testid="platform-jobs"`), which lists every entry of
  `services.security_master_jobs.JOBS`. S28 adds `ai_drift_monitor` ("system:AI — drift monitor") to `JOBS` and its
  `FETCHES` text — no new switch; the job is seeded with its trigger **inactive** (`scripts/_apply_s28.py
  --seed-jobs`, run 2026-11-07: trigger `9f9e2aa9-…`, `is_active = false`).
