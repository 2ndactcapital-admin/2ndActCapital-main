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

Full design: `docs/WORKFLOW_SCHEDULER_DESIGN_V1.md`. Discovery: `docs/WORKFLOW_SCHEDULER_DISCOVERY_FINDINGS.md`.

- ✅ **Sprint 1 — Discovery.** Complete.
- ✅ **Sprint A — Permissions fix.** Complete, merged — `author_workflows`/`view_workflow_runs`/`configure_workflow_triggers` now correctly grantable; real "Org Admin" profile seeded and assigned.
- 🔄 **Sprint 2 — Core Engine.** Sprint running now (`schedulercore.structural`) — RRULE-based recurrence, Render Cron Job service, per-org timezone handling, idempotent firing, overlap protection.
- ⬜ **Sprint 3** — CRUD UX (add/edit/delete/pause schedules, dry-run preview).
- ⬜ **Sprint 4** — Run history + logging (run list, per-run cost/duration/status, filterable).
- ⬜ **Sprint 5** — Notifications (wire failure alerts through the real, existing `member_todos`/`_hold_run` mechanism — confirmed real precedent, not a new system).
- ⬜ **Sprint 6** — Natural-language authoring (chat-based schedule creation, including the clarifying-question loop — the one genuinely new interaction pattern in this whole effort).
- ⬜ **Later** — Chaining / entity-scoped sub-schedules (the RMD-at-65 example).

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
