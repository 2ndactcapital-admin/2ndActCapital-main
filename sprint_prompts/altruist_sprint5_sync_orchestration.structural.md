ALTRUIST SPRINT 5 — Sync orchestration: a real, triggerable endpoint for identity resolution + positions/transactions sync, plus auto-trigger on connect. 4 tasks + verification.

Context: Sprint 4 (merged, 45113ae) wired the OAuth connection lifecycle into real API endpoints (connect/callback/status/disconnect) but deliberately left identity resolution (Sprint 2, altruist_identity.py) and positions/transactions sync (Sprint 3, altruist_positions_sync.py) unreachable from the app — they're still only ever called by their own verify scripts. This sprint closes that gap: a real, admin-triggerable "sync now" endpoint that runs resolve_identity then sync_resolved_accounts end-to-end for the caller's org, plus wiring it to fire automatically right after a successful OAuth connect so a newly-connected org isn't left empty until someone remembers to trigger it manually.

CONFIRMED REAL FACTS, DO NOT RE-DERIVE:
- apps/api/routers/altruist_connection.py (Sprint 4) has the connect/callback/status/disconnect routes, the router registration pattern in main.py, and the exact permission-check pattern this app uses (matching custody_import.py's shape: get_user_id + rbac.require_permission) — reuse that exact pattern for this sprint's new endpoint(s), do not invent a different one. The permissions view_custody_connections/manage_custody_connections already exist and are granted to the admin role — reuse manage_custody_connections (or confirm in Task 1 whether a separate sync-specific permission is warranted).
- altruist_identity.py's resolve_identity/list_resolved_altruist_accounts (Sprint 2) and altruist_positions_sync.py's sync_resolved_accounts (Sprint 3) are live and proven at the function level — reuse both verbatim, do not reimplement.
- require_active_connection and ensure_fresh_token (Sprint 4) already guard/refresh before any Altruist call — reuse, do not reimplement.
- No live Altruist sandbox credentials exist yet (re-confirm live via `doppler secrets --only-names`, do not assume) — this sprint's real proof drives the sync endpoint through the app's real HTTP test client, with the underlying Altruist HTTP calls monkeypatched exactly like Sprints 1-4.
- org_id for 2nd Act Capital = 00000000-0000-0000-0000-000000000001.
- Known, low-priority, UNRELATED issue (do not fix as part of this sprint's main scope, but DO apply the one-line fix in Task 5 while you're in the area): apps/api/scripts/verify_altruist_sprint1_openapi_connection.py currently asserts call_households raises NotImplementedError — that stopped being true once Sprint 2 implemented it for real, so Sprint 1's verify script now fails on unmodified main. Update that one assertion to reflect real behavior so future regression checks across all sprints report cleanly.

THERE IS NO HUMAN AVAILABLE. Report findings, then continue immediately in the same response. If uncertain, continue.

STANDING RULES: no interactive prompts; org_id never taken from the request body or path — always the authenticated caller's org; the sync endpoint must be idempotent regardless of how many times it's called (already proven at the function level in Sprint 3 — this sprint re-proves it through the real endpoint); a sync attempt for an org with no active connection must fail with a clean, real HTTP error (via require_active_connection), never a raw 500; auto-trigger-on-connect must not make the connect/callback endpoint itself slow or fragile — if a genuine background-task mechanism doesn't already exist in this app (confirm in Task 1), running the sync inline right after storing the connection, before returning the response, is an acceptable interim approach — just don't let a sync failure prevent the connection itself from being correctly stored.

=== TASK 1: DISCOVER ===
Report findings, THEN CONTINUE IMMEDIATELY in the same response.
  1a. Read altruist_connection.py's callback route in full to find exactly where store_new_connection is called, so the auto-sync trigger can be added right after it correctly.
  1b. Confirm resolve_identity's and sync_resolved_accounts' exact function signatures and return shapes (what do they return on success — counts? a list of results? nothing?) so the new endpoint's response can report real, accurate numbers rather than guessed ones.
  1c. Confirm whether any background-task/async-job mechanism already exists in this app (FastAPI BackgroundTasks usage elsewhere, a queue, anything) — if genuinely nothing exists, note that explicitly so Task 2 uses the inline-fallback approach described in STANDING RULES.
  1d. Confirm whether manage_custody_connections (Sprint 4) is the right permission for triggering a sync, or whether a distinct permission makes more sense given this app's existing permission-granularity conventions — follow whatever precedent Task 1 finds, don't invent a new convention.

=== TASK 2: BUILD ===
Based on Task 1's real findings:
  - An admin-only POST endpoint (e.g. /api/altruist/sync) that calls require_active_connection, then resolve_identity, then sync_resolved_accounts for the caller's org, and returns a real summary: accounts resolved, accounts still unmatched (in account_import_exceptions), positions synced, transactions synced — all counts pulled from what resolve_identity/sync_resolved_accounts actually report, not guessed.
  - Wire the same sync to fire automatically right after a successful OAuth callback stores a new connection (inline or via whatever background mechanism Task 1 found), without blocking or risking the connection's own persistence if the sync itself fails.
  - Apply the one-line Sprint 1 verify-script fix described in CONFIRMED REAL FACTS.

=== TASK 3: SANDBOX SMOKE TEST ===
Confirm live (do not assume a prior sprint's finding still holds) via `doppler secrets --only-names` that no ALTRUIST_* sandbox credentials exist yet. If still absent: report BLOCKED, do not attempt a live call, move immediately to Task 4 in the same response.

=== TASK 4: REAL PROOF (none of this needs live Altruist credentials) ===
  - A caller without the required permission is refused on the sync endpoint while an admin caller succeeds — proven on the identical request, only the caller's permission changed.
  - A full round trip via the app's real HTTP test client: connect -> synthetic callback (monkeypatched token exchange + monkeypatched households/accounts/positions/transactions responses) -> auto-sync fires -> the sync endpoint's response AND an independent re-read of the database both show the correct resolved account, correct positions, correct transactions.
  - Idempotency proven through the real endpoint: calling /api/altruist/sync twice in a row produces identical row counts the second time, diffed not asserted — matching Sprint 3's function-level proof, now proven at the HTTP layer.
  - Reproduce the failure state first: calling the sync endpoint for an org with no active connection returns a clean, real HTTP error (not a 500), before any network call is attempted.
  - Cross-org isolation: an org B caller triggering their own sync cannot affect or read org A's resolved accounts/positions/transactions, on the identical endpoint with only the caller's org changed.
  - Regression check: re-run Sprints 1-4's verify scripts and confirm they all report clean (including the Sprint 1 fix applied in Task 2).

=== TASK 5: UPDATE PROJECT STATUS ===
Write the verify script to apps/api/scripts/verify_altruist_sprint5_sync_orchestration.py — filename must match this sprint's name, "altruist_sprint5_sync_orchestration", exactly, before the tier suffix. Update docs/PROJECT_STATUS.md: sync is now triggerable via a real endpoint and fires automatically on connect, note the Sprint 1 verify-script fix, note that everything through Sprint 5 is still proven only against synthetic responses pending Sprint 0's real sandbox credentials. Commit everything in one commit, matching the established convention (e.g. `sprint: altruist_sprint5_sync_orchestration.structural - sync orchestration endpoint + auto-trigger on connect, N/N PASS + 1 BLOCKED (no sandbox credentials), HELD for manual review`).

=== VERIFICATION: apps/api/scripts/verify_altruist_sprint5_sync_orchestration.py ===
Pass/fail only. No interactive prompts. One [Y]/[BLOCKED] assertion per Task 3/4 proof above, explicitly listed, plus a teardown check confirming every table this script writes to is back at its pre-test row count.
