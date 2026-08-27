# Hollisworks / 2nd Act — Outstanding To-Do / Sprint List

**Status as of this session.** Organized by area. Each item marked with real status where known: ✅ done, 🔄 in progress, ⏸️ blocked (external action needed), ⬜ not started.

---

## 1 · LiteLLM Integration

Full design: `docs/LITELLM_INTEGRATION_DESIGN_V1.md`. Discovery: `docs/LITELLM_DISCOVERY_FINDINGS.md`.

- ✅ **Phase A — Deploy.** Complete, verified end-to-end (health check, key generation, persistence all confirmed). Real deployment gotchas captured in design doc §13.5.
- 🔄 **Phase B — Route text calls.** Sprint running now (`litellmphaseb.structural`) — the 16 `services/extraction.py` call sites, with a real rollback path.
- ⬜ **Phase C** — Voyage routed through LiteLLM + re-indexing confirmation dialog (friction, not a lock, per direction).
- ⬜ **Phase D** — Model pick-list UI (org-scoped, filterable, LiteLLM metadata-driven).
- ⬜ **Phase E** — Task-assignment UI, two-tier safe-model hierarchy (org → Hollis), change-confirmation warnings.
- ⬜ **Phase F** — Force-Anthropic emergency bypass (Hollis-level toggle, §7.5 of design).
- ⬜ **Phase G** — Budget UX (warning threshold, graceful degradation, Hollis-wide ceiling).
- ⬜ **Phase H** — Reporting (Hollis-level + org-level, reading LiteLLM's real spend data, redacted).
- ⬜ **Phase I** — Model recommendation tool (calc-based: cost + context-fit + DeepEval accuracy).
- ⬜ **Phase J** — Voice (build fresh — zero existing call sites; real-time speech-to-speech as its own task shape).
- ⬜ **Later** — Guardrails proper (content filtering, prompt-injection defense, DeepEval accuracy floors).

**LiteLLM cleanup items:**
- ⬜ Move `DISABLE_SCHEMA_UPDATE=true` from Render's direct env vars into Doppler's `prd` config (currently Render-only for pragmatic reasons; no longer necessary now that no name collision exists).
- ⬜ Confirm `ENFORCE_PRISMA_MIGRATION_CHECK` has been removed (no longer useful now that migrations are permanently disabled on this service).
- ⬜ Confirm `LITELLM_BASE_URL` is set in Doppler — Phase B's own Task 1c/2 checks this; may already be resolved by that sprint.

---

## 2 · Workflow Scheduler & Automation Engine

Discovery: `docs/WORKFLOW_SCHEDULER_DISCOVERY_FINDINGS.md`. **`docs/WORKFLOW_SCHEDULER_DESIGN_V1.md` does not exist** — it has been referenced from this line since the plan was written and was never created. This phasing table is the real one; there is no second copy to keep in sync.

- ✅ **Sprint 1 — Discovery.** Complete.
- ✅ **Sprint A — Permissions fix.** Complete, merged — `author_workflows`/`view_workflow_runs`/`configure_workflow_triggers` now correctly grantable; real "Org Admin" profile seeded and assigned.
- ✅ **Sprint 2 — Core Engine.** Complete, 65/65 (`9bf4249`) — RRULE-based recurrence, Render Cron Job service, per-org timezone handling, idempotent firing, overlap protection.
- ✅ **Sprint 3 — CRUD UX.** Complete, 91/91 (`ec6ef24`) — add/edit/delete/pause schedules, dry-run preview, DataGrid + detail pane + permission envelope.
- ✅ **Sprint 4 — Run history + logging.** Complete, 82/82 (`schedulerhistory.structural`). Run History screen: DataGrid list filterable **server-side** by status and time period, right-pane detail with the full step timeline, scheduled-vs-manual origin resolved from the run's own stored context, and — for a held run — the engine's real `error_detail` plus the exact alerted-user set read back from `member_todos`.
  - **Per-run cost is NOT built, deliberately.** `ai_decision_log` carries no run identifier and zero workflow run steps have ever invoked AI. There is nothing to correlate; plumbing for it would be speculative.
  - **Per-step duration is reported only for User Tasks**, and the same honesty rule turned out to apply at the **run** level too — see the finding below.
- ⬜ **Sprint 5** — Notifications. **Mostly already satisfied**; see the gap list below for what is genuinely left.
- ⬜ **Sprint 6** — Natural-language authoring (chat-based schedule creation, including the clarifying-question loop — the one genuinely new interaction pattern in this whole effort).
- ⬜ **Later** — Chaining / entity-scoped sub-schedules (the RMD-at-65 example).

**Sprint 4 finding — duration is not measurable for a synchronous run, at either level.**
Postgres `now()` is the *transaction* timestamp. The engine inserts the run row on an
independent connection and completes it on the caller's, whose transaction (through the
RLS pool wrapper) opened **first** — so a run that finishes inside its own
`start_workflow_run` call has `completed_at` **strictly before** `started_at`. Measured at
**-0.36s** on a real manual run. The prompt's premise (and this file's earlier "per-run
duration" line) assumed the run-level interval was sound and only the step-level one was
an artifact; it is not. The API now reports `duration_measured: false` for any non-positive
interval at both levels and the screen prints "not measured" with the reason on hover,
rather than a number. A strictly positive interval — a run that paused at a User Task and
was completed later — is real and is shown.

**Sprint 5 — what `member_todos` already does, and what is genuinely still missing.**
Already real and verified end-to-end this sprint: a held run alerts its starter **and** every
`org_admin` in the org (`create_held_run_alerts`, 5 real recipients in the verification run),
those alerts land in the dashboard todo feed, and the Run History pane reads the exact
recipient set back rather than re-deriving it. What is left is small and specific:
- ⬜ **A User Task with no `assigned_role_profile_id` notifies nobody, silently.**
  `sync_user_task_todos` returns `[]` and the run pauses waiting for a human no one told.
  This is the largest real gap in the notification path.
- ⬜ **Alert todos deep-link to the run LIST, not the run.** `workflow_todos._RUN_CONSOLE_PATH`
  is `/admin/workflows/runs`. Sprint 4 added `?run={id}`, so the alert can now point at the
  actual run — a one-line change that was not possible before this sprint.
- ⬜ **Alert todos outlive their run.** Two orphaned `workflow_run_held` todos are live right
  now, pointing at runs that no longer exist. No cleanup on run deletion.
- ⬜ **No out-of-band channel.** `member_todos` is in-app only; email is blocked on §3 below.
- ⬜ **`notification_bus` is not used by the workflow subsystem at all** (grepped: zero
  references in `services/workflow_*.py` or `routers/workflows.py`). Worth a deliberate
  decision — adopt it or record that `member_todos` is the chosen surface — rather than
  leaving two notification systems and one silent non-choice.

**Real findings still needing attention (not yet own sprints):**
- ⬜ `render.yaml` was confirmed stale (still claims LiteLLM isn't deployed) — Sprint 2 is scoped to fix this in the same edit that adds the cron service declaration; confirm it actually happened.
- ⬜ `2ndactcapital-api`'s real public Render hostname is unknown from outside (returns identical `no-server` response to a nonexistent host on every name variant tried) — worth confirming directly in Render's dashboard, since it may indicate the service was renamed or is reachable only via a custom domain not in the repo.

---

## 3 · AWS / SES — console actions (no code, needs direct AWS access)

From `smtpservice.structural` (9/9 code assertions passed, correctly `BLOCKED` on these):
- ⏸️ Grant `ses:SendEmail` + `ses:SendRawEmail` IAM permission to `Texttrac-Ripasso`, or create a dedicated SES-only IAM user (matching the least-privilege pattern already used for `app_service`/`litellm_service`).
- ⏸️ Check AWS SES sandbox status (requires `ses:GetAccount`, which the current credential also lacks) — sandboxed accounts can only send to verified addresses.
- ⏸️ Set `SES_FROM_EMAIL` in Doppler once a verified sending address is known.
- ⬜ Re-run `smtpservice.structural`'s verify once all three are resolved — the code itself is already correct and waiting.

---

## 4 · Doppler / secrets hygiene

- ⏸️ **Doppler → Vercel sync remains disabled**, deliberately, due to a real naming collision with the native Supabase-Vercel integration (`SUPABASE_URL`, `SUPABASE_PUBLISHABLE_KEY`, `SUPABASE_SECRET_KEY` both managed). Real fix: scope Doppler's Vercel sync to exclude those specific names before re-enabling, letting Supabase's own integration keep exclusive ownership of them.
- ⬜ Confirm local dev fully migrated off any lingering `.env` fallback (the original stale `apps/api/.env` was deleted; worth a final check that nothing else — a test runner, a shell alias — still reads a file-based fallback anywhere).

---

## 5 · Corporate Actions — incomplete application logic

- ⬜ Merger, tender, and delisting have **no application logic** — only split/reverse-split/spinoff were built in Phase F. `record_corporate_action` accepts all six `action_type` values; `apply_corporate_action` only dispatches three. Not urgent — no real merger/delisting event has needed processing yet — but a known, real gap.

---

## 6 · Known bugs, unfixed

- ⬜ `services/allocation_lens.py`'s subtree selector double-counts against look-through buckets — found by Phase C's own portfolio-rollup verify script, confirmed a pre-existing bug in shipped S21 code, deliberately left untouched as out of scope for that sprint. Needs its own small, isolated fix.
- ⬜ `services/permissions.py` (marketplace/SPV/VDR) — never checked for a super-admin bypass gap, flagged very early in this project and never revisited.

---

## 7 · Data / ops gaps

- ⬜ `staff_assignments` backfill (blocking full RBAC restricted-access enforcement rollout — deliberately deferred to avoid lockout).
- ⬜ Chancery source-coordinate tracing degrades to page reference — Textract's real geometry data is discarded by current code, fixable.
- ⬜ Eight hardcoded `"2ndactcapital-docs"` R2 bucket-name fallbacks remain across four routers, despite the bucket migration to `hollisworks-docs` being otherwise complete.
- ⬜ Textract and EDGAR integrations have **not been tested** since tonight's credential recovery/rotation — worth a real, direct test before assuming they still work.

---

## 8 · AI sidebar — missing aggregate queries

- ⬜ Aggregate queries still missing for: SPVs, workflow runs, deals-by-attribute, member investments, documents, notifications.

---

## 9 · Admin UI

- ⬜ Menu rationalization — org-scoped vs. platform-scoped item split needs a real design pass (connects to the Hollisworks/2nd Act dual-tenant admin experience work from early tonight).

---

## 9b · Fee module — backend built (fee31, fee32), two screens still missing

The account layer (fee31) and its connection to the Portfolio Reporting Layer
(fee32) are built, verified and merged/held. Both sprints shipped real,
permission-gated endpoints with no frontend:

- ⬜ **Position ↔ account linkage exception review screen.** `GET /portfolio/position-account-exceptions` and `POST .../{id}/review` exist and are proven; nothing renders them. A position written with an `account_id` whose owner is not one of that account's active owners is deliberately WRITTEN and flagged rather than refused — so until this screen exists, those flags accumulate unread.
- ⬜ **Household precedence override editor.** `GET`/`PUT`/`DELETE /portfolio/precedence/households/{household_id}` exist and are proven. Today an override can only be set through the API or by a script.
- ⬜ **Joint-custody positions split into multiple owner rows on import** — known, confirmed gap carried over from the Portfolio Reporting Layer thread. Explicitly out of scope for fee32; still open.
- ⬜ **`verify_portfolioux4.py` check 1c is self-contaminated and now reports 70/71.** Its "pre-sprint state" is read with `git show HEAD:` and asserts the routers had no permissions envelope — which stopped being true the moment ux4's own commit (`1c64199`) added one. Verified during fee32: the predicate passes at `1c64199^` and fails at `HEAD`, and fee32 changes none of that machinery. Fix is to anchor the ref with `git log -S <marker> … ^` (the pattern `verify_fee32.py` check 5 uses) rather than `HEAD`. Left alone deliberately — ux4 is still HELD and editing its evidence mid-review would be worse than the stale number.

---

## 10 · Major, multi-sprint efforts — not started

- ⬜ **Billing and profit module** — comparable scope to the LiteLLM/Scheduler efforts; not yet scoped with a discovery sprint.
- ⬜ **Financial / cash-flow planning module** — same tier, not yet scoped.

---

## 11 · Backlog — ready or blocked

- ⏸️ Member Business Registration / EIN capture — blocked on carrier confirmation.
- ⬜ Deal Diligence Engine — AI wiring (the scoring UI and deal pipeline are substantially already built; the AI-generation piece is the remaining gap).
- ⬜ Opportunity Management — Pipeline A (member-acquisition funnel) — the real remaining gap in an otherwise largely-built deal pipeline.
- ⬜ Retention policy system — design complete (record-class-based, crypto-shredding, dual-control), DDL was deferred pending S23 (S23 has since landed — worth confirming this is now genuinely unblocked).
- ✅ ~~AWS Secrets Manager migration~~ — **superseded**: Doppler was chosen and implemented instead this session.

---

## 12 · SAML Federation — deliberately paused

- ⏸️ 2nd Act's Auth0 tenant → Hollisworks broker Enterprise Connection (the actual SAML federation).
- ⬜ Org-picker / cross-org UI for Hollisworks staff.
- ⬜ Client-org SAML IdP "linking" screen (the mechanism a real RIA's own IdP would connect to Hollisworks) — confirmed to not exist yet, and cannot meaningfully exist until the federation itself is resumed.

---

## 13 · Deferred / later-tier

- ⬜ Mobile app for advisers (full spec exists from earlier design work).
- ⬜ Live embedded video conferencing with AI-suggested questions (build-vs-buy still open).
- ⬜ Securities-based lending module (5-pass collateral eligibility engine).
- ⬜ Live voice — now tracked under LiteLLM Phase J rather than as a separate item.

---

## Recently completed (for context — not outstanding)

Portfolio Positions/Transactions/Securities screens (all three, with permissions built in or retrofitted); Doppler secrets migration (core); LiteLLM Phase A; workflow BPMN XML-escaping + token-limit fix; the enrollment flow (invite URLs + `/enroll` page); user management (deactivate/delete/edit/last-login/org-configurable expiry); the Auth0 tenant-boundary fixes (host-aware session checks across 41+ pages, `uuid_generate_v4` schema+permission fix, 2nd Act client host-derived callback); workflow permissions fix (Sprint A above).
