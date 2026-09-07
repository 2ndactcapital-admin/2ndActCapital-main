ALTRUIST SPRINT 4 — Wire OAuth connect/disconnect/status into real API endpoints, plus automatic token refresh. 4 tasks + verification.

Context: Sprints 1-3 built the full engine — require_active_connection, build_authorize_url, exchange_code_for_tokens, refresh_access_token (Sprint 1, altruist_oauth.py), resolve_identity/list_resolved_altruist_accounts (Sprint 2, altruist_identity.py), sync_resolved_accounts (Sprint 3, altruist_positions_sync.py) — but NONE of it is reachable via an HTTP route yet. Everything so far is service-layer Python only, called directly by verify scripts. This sprint exposes it: real API endpoints so an admin/staff user can actually initiate an Altruist connection from the app, see its status, disconnect it, and have an expiring access token refreshed automatically rather than failing silently the next time a sync runs.

CONFIRMED REAL FACTS, DO NOT RE-DERIVE:
- altruist_oauth.py has generate_state/create_oauth_state/consume_oauth_state, build_authorize_url, exchange_code_for_tokens, refresh_access_token, get_active_connection, store_new_connection, persist_refresh — reuse all of these; do not reimplement.
- altruist_identity.py has resolve_identity/list_resolved_altruist_accounts; altruist_positions_sync.py has sync_resolved_accounts (Sprint 3) — reuse, do not reimplement; this sprint is about exposing connection lifecycle, not identity/positions sync itself (those stay internal for now unless Task 1 finds a reason an endpoint is needed for them too).
- consume_oauth_state already rejects missing/expired/used/cross-org state (Sprint 1, proven at the function level) — this sprint re-proves the same four cases through the real HTTP callback route, not just the function.
- org_id for 2nd Act Capital = 00000000-0000-0000-0000-000000000001.
- No live Altruist sandbox credentials exist yet (re-confirm live via `doppler secrets --only-names`, do not assume) — this sprint's real proof drives the new HTTP routes via FastAPI's own test client; the actual Altruist code-exchange step is still monkeypatched exactly like Sprints 1-3, just now triggered through a real request/response cycle instead of calling the service functions directly.

THERE IS NO HUMAN AVAILABLE. Report findings, then continue immediately in the same response. If uncertain, continue.

STANDING RULES: no interactive prompts; org_id never taken from the request body or path — always the authenticated caller's org via whatever middleware/dependency this app already uses; only the correct role/permission can initiate or disconnect a connection — Task 1 must confirm what that check should be by finding how OTHER admin-only endpoints in this app enforce it, do not invent a new permission-check pattern; connection status responses must never include a raw token value in any form; this is application code — if Task 1 finds automatic token-refresh genuinely needs a new table (e.g. a scheduled-job/refresh-log table) with no existing shape to reuse, STOP after Task 1 and report the needed shape as a [FIND] rather than guessing DDL — an on-demand "refresh if expiring soon" check (see Task 2) is an acceptable interim answer if no real job scheduler exists in this app yet.

=== TASK 1: DISCOVER ===
Report findings, THEN CONTINUE IMMEDIATELY in the same response (unless the STOP condition above applies).
  1a. Find how new API routes are registered in this app (main.py router registration, the decorator/dependency chain that resolves the authenticated caller + their org_id + their permission, e.g. how an existing admin-only endpoint is written end to end) so this sprint's new routes match that convention exactly rather than inventing a parallel one.
  1b. Confirm whether an existing "connect a custodian" / integration-settings UI or API pattern already exists anywhere in the app (even for a different custodian, or as a design/mockup only) that Altruist's connect/disconnect/status routes should match for consistency.
  1c. Confirm how (or whether) background/scheduled jobs already run in this app — a cron-like mechanism, a task queue, or nothing yet — since real automatic token refresh eventually needs to run on a timer, not just on-demand; if nothing exists, note that explicitly so Task 2 can build the on-demand fallback described above instead.
  1d. Re-read build_authorize_url/exchange_code_for_tokens/consume_oauth_state closely to confirm the exact redirect_uri and state/callback parameter shape they expect, so the new HTTP callback route's request parsing matches exactly.

=== TASK 2: BUILD ===
Based on Task 1's real findings (and only if the STOP condition did not apply):
  - An admin-only endpoint that starts the OAuth flow: creates a state row via create_oauth_state, returns the authorize URL from build_authorize_url for the frontend to redirect the user to.
  - The OAuth callback route Altruist would redirect back to: consumes the state via consume_oauth_state, exchanges the code for tokens via exchange_code_for_tokens, persists via store_new_connection.
  - A connection-status endpoint: returns whether the caller's org has an active connection, its environment (sandbox/production), token_expires_at, last_refreshed_at — never a raw token value in any field.
  - An admin-only disconnect endpoint: closes the active connection row (bitemporal close matching this table's existing convention, never a hard delete).
  - Automatic token refresh: wire refresh_access_token to run before actual expiry, using whatever scheduling mechanism Task 1 found — or, if none exists, an on-demand "refresh if expiring soon" check inside the connection-status endpoint (and/or require_active_connection itself), clearly documented as an interim approach pending a real scheduler.

=== TASK 3: SANDBOX SMOKE TEST ===
Confirm live (do not assume a prior sprint's finding still holds) via `doppler secrets --only-names` that no ALTRUIST_* sandbox credentials exist yet. If still absent: report BLOCKED, do not attempt a live call, move immediately to Task 4 in the same response.

=== TASK 4: REAL PROOF (none of this needs live Altruist credentials) ===
  - A caller without the required admin permission is refused on the connect and disconnect endpoints (this app's real refusal pattern, discovered in Task 1) while an admin caller succeeds — proven on the identical request, only the caller's permission changed.
  - The connection-status endpoint's actual response body is asserted to contain no raw token value in any field, not just inspected by eye.
  - A full round trip via the app's real HTTP test client: initiate connect -> synthetic callback with a valid state (monkeypatched token exchange) -> status endpoint reports connected -> disconnect -> status endpoint reports disconnected.
  - Reproduce the failure state first, through the real HTTP callback route (not the underlying function directly): missing, expired, already-used, and cross-org state are each rejected — four distinct cases, same request shape that succeeds when state is valid.
  - Cross-org isolation: an org B caller cannot see org A's connection status via the status endpoint, and cannot disconnect org A's connection via the disconnect endpoint, on the identical request with only the caller's org changed.
  - Token-refresh proof: a connection whose token is set to expire imminently gets refreshed by whichever mechanism Task 2 built, confirmed via an independent re-read of the row (not just "the call didn't error").

=== TASK 5: UPDATE PROJECT STATUS ===
Write the verify script to apps/api/scripts/verify_altruist_sprint4_connection_api.py — filename must match this sprint's name, "altruist_sprint4_connection_api", exactly, before the tier suffix. Update docs/PROJECT_STATUS.md: connection lifecycle now reachable via real API endpoints, automatic refresh strategy noted (real scheduler vs. interim on-demand check — whichever Task 1/2 landed on), Sprint 5 (Realtime API webhooks, or Custodial Flat Files, or a real sandbox smoke test once Sprint 0 lands credentials) unblocked for the parts that don't require live credentials. Commit everything in one commit, matching the established convention (e.g. `sprint: altruist_sprint4_connection_api.structural - connection lifecycle API + auto-refresh, N/N PASS + 1 BLOCKED (no sandbox credentials), HELD for manual review`).

=== VERIFICATION: apps/api/scripts/verify_altruist_sprint4_connection_api.py ===
Pass/fail only. No interactive prompts. One [Y]/[BLOCKED] assertion per Task 3/4 proof above, explicitly listed, plus a teardown check confirming every table this script writes to is back at its pre-test row count.
