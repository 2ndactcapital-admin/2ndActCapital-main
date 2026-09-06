ALTRUIST SPRINT 1 — Open API connection scaffold (OAuth2 authorization-code flow + per-tenant credential storage). RESUMING — Tasks 1-2 are done; complete Tasks 3-5 and commit.

CONTEXT — READ BEFORE DOING ANYTHING: a prior leg of this same sprint already ran Task 1 (discover) and Task 2 (build) successfully and left real, uncommitted work in this worktree. DO NOT redo Task 1's discovery or Task 2's build — verify the files below still exist as described, then continue from Task 3.

ALREADY DONE, DO NOT REDO:
- `docs/schema_snapshot.sql` was refreshed against the live database (modified, uncommitted).
- `apps/api/migrations/altruist_sprint1_openapi_connection.sql` creates `altruist_oauth_states` and `altruist_connections`, both with org-isolation RLS matching the standard `(org_id = (nullif(current_setting('app.current_org_id', true), ''))::uuid OR current_setting('app.is_super_admin', true) = 'true')` pattern, full bi-temporal columns on `altruist_connections` matching `account_groups`, and a partial unique index on `(org_id, environment) WHERE system_to IS NULL`.
- `apps/api/services/altruist_oauth.py` implements: CSRF state generation/consumption, `build_authorize_url`, `exchange_code_for_tokens`, `refresh_access_token`, Fernet-based application-layer encryption (interim — no KMS key exists yet, this is a recorded [FIND], not a defect), `store_new_connection`/`persist_refresh`/`get_active_connection`, and `require_active_connection` — a guard that raises `AltruistNotConnected` before any network call, which `call_households` (a stand-in for Sprint 2's real endpoint) already uses correctly.
- Task 1 correctly discovered `apps/api/services/portfolio_altruist.py` (a BLOCKED credential-gated probe from an earlier, unrelated sprint, using a different `ALTRUIST_CLIENT_ID/SECRET/BASE_URL` env-var scheme) and `apps/api/services/altruist_one.py` (an unrelated cost/benefit evaluator for the Altruist One subscription program) and correctly concluded neither should be reused or duplicated, since neither implements OAuth2 or per-org credential storage.

THERE IS NO HUMAN AVAILABLE. Report findings, then continue immediately in the same response. If uncertain, continue. Do not stop after Task 3 just because it's blocked — Tasks 4 and 5 do not depend on Task 3 succeeding.

STANDING RULES: no interactive prompts; credential values are never logged in plaintext; this repo's own convention (see `git log --oneline`, e.g. "portfoliob.structural — ingestion + source precedence (50/50, 2 BLOCKED)") is to still write and run a full verify script when some assertions are genuinely blocked by missing external credentials — mark those assertions BLOCKED, do not omit them and do not fabricate a pass.

=== TASK 3: SANDBOX SMOKE TEST ===
Confirm no Altruist sandbox credentials exist in this project's Doppler config (`doppler secrets --only-names`). If absent, as expected: report this plainly as BLOCKED, do not attempt a live call, and move on immediately to Task 4 in the same response.

=== TASK 4: REAL PROOF (none of this needs live Altruist credentials) ===
  - Reproduce the failure state first: calling `require_active_connection`/`call_households` for an org with no `altruist_connections` row raises `AltruistNotConnected` — prove this before showing the connected case.
  - Prove RLS isolation both directions on `altruist_connections` and `altruist_oauth_states`: a session scoped to org A cannot read or write org B's row, on the identical query with only `app.current_org_id` changed; org A's own row IS readable in the same test, same table, same script.
  - Prove token refresh persistence using a synthetic `TokenResponse` (no live call): force `persist_refresh`, then re-read the row via an independent query and confirm the new expiry/token was actually written.
  - Prove `consume_oauth_state` rejects a missing, expired, already-used, and cross-org state parameter — four distinct cases, each on the same request shape that succeeds when state is valid — and that the CSRF failure message doesn't reveal which of the four reasons applied (already true by construction; verify it holds).

=== TASK 5: VERIFICATION + PROJECT STATUS ===
Write `apps/api/scripts/verify_altruist_sprint1_openapi_connection.py` — pass/fail only, no interactive prompts, one `[Y]`/`[BLOCKED]` assertion per Task 4 proof plus the Task 3 credential check marked `[BLOCKED]` with the reason. Update `docs/PROJECT_STATUS.md` to reflect: `altruist_connections`/`altruist_oauth_states` live, OAuth2 authorization-code flow implemented, sandbox smoke test blocked pending Sprint 0 credentials, Sprint 2 (identity resolution) unblocked for the parts that don't require a live call.

Then commit everything (schema snapshot, migration, service, verify script, PROJECT_STATUS) in one commit, matching this repo's own convention for a `.structural` sprint (e.g. `sprint: altruist_sprint1_openapi_connection.structural - OAuth2 scaffold, N/N PASS + 1 BLOCKED (no sandbox credentials), HELD for manual review`).
