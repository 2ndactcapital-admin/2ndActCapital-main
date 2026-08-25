DOPPLER — SECRETS MANAGEMENT SETUP + MIGRATION. 5 tasks +
verification. Real, recurring problem this fixes: APP_SERVICE_
DATABASE_URL has gone stale in local apps/api/.env across THREE
consecutive sprint runs tonight (portfolioux1, ux2, ux3), each
silently falling back to SET LOCAL ROLE instead of using the real
credential. This is exactly the class of drift Doppler exists to
prevent — one source of truth instead of values manually synced
across Render, local .env, and wherever else they've been pasted.

THERE IS NO HUMAN AVAILABLE. Report findings, then continue
immediately in the same response. If uncertain, continue.

*** CRITICAL: this sprint reads and moves REAL, LIVE credentials
(database passwords, AWS keys, Voyage key, Auth0 secrets). Never
print a real secret value in full in any report, log, or commit
message — reference by variable NAME only. If a real value must
be verified, confirm its PRESENCE and that a test connection
succeeds, never echo the value itself. ***

STANDING RULES: no interactive prompts.


Doppler has three configs: development, staging, production.
staging is NOT currently used by anything real — map production
to Render/Vercel's live services and development to local
`doppler run --`. Do not attempt to wire staging into anything.

=== TASK 1: DISCOVER — the full real inventory ===
Report findings, THEN CONTINUE IMMEDIATELY in the same response.
  1a. Enumerate EVERY real secret currently in use across Render
      (API service) and local apps/api/.env — by NAME only, not
      value. Known from tonight: DATABASE_URL, APP_SERVICE_
      DATABASE_URL, HOLLISWORKS_AUTH0_DOMAIN/CLIENT_ID/
      CLIENT_SECRET/SECRET/AUDIENCE, AUTH0_*, AWS_*, VOYAGE_
      API_KEY, ANTHROPIC_API_KEY if present. Report the REAL,
      complete list found in the actual environment, not this
      known-partial one.
  1b. Confirm the SAME for Vercel (NEXT_PUBLIC_API_URL and
      anything else frontend-relevant, per the earlier env-var-
      scoping incidents this session).
  1c. Confirm render.yaml's real, current declared vs. actually-
      used gap (already found by the LiteLLM discovery sprint:
      AWS_*/VOYAGE_API_KEY used in prod but absent from the
      manifest) — this sprint's migration should close this gap
      as a natural side effect, not leave it.
  1d. Confirm whether the Doppler CLI/SDK is installable in this
      environment (pip/npm) and whether a service-token-based
      fetch can be tested without exposing the token's value in
      any log.

=== TASK 2: WIRE — Render + Vercel read from Doppler ===
Configure both Render and Vercel to source their environment
variables from Doppler's own native integrations (both platforms
support this directly) rather than manually-set values. Do NOT
have application code call the Doppler SDK directly for this —
use the platform-native integration so no code change is needed
in apps/api or apps/web.

=== TASK 3: MIGRATE — every real secret, once, correctly ===
For every name found in Task 1: confirm it now resolves correctly
through Doppler on both Render and Vercel (a real, live request
that depends on that variable succeeds — e.g. a DB connection, a
real Auth0 token validation), THEN remove the old, manually-set
value from Render/Vercel's own dashboards so Doppler is the
single source, not a duplicate. Fix Task 1c's render.yaml gap in
the same pass.

=== TASK 4: FIX — local development ===
Document (in docs/DEVELOPMENT_ENVIRONMENT.md) the real, correct
local dev flow: `doppler run -- <command>` instead of a hand-
maintained apps/api/.env, so APP_SERVICE_DATABASE_URL and every
other local credential can never drift from Doppler's own value
again. This is the actual fix for tonight's recurring failure.

=== TASK 5: REAL PROOF ===
  - A real API request depending on DATABASE_URL succeeds via the
    Doppler-sourced value on Render.
  - A real frontend request depending on NEXT_PUBLIC_API_URL
    succeeds via the Doppler-sourced value on Vercel.
  - Running a real local command through `doppler run --` connects
    successfully using the SAME credential Render uses — proving
    drift is now structurally impossible, not just fixed once.
  - render.yaml's real gap (Task 1c) is closed.

=== VERIFICATION: apps/api/scripts/verify_doppler.py ===
Pass/fail only. No interactive prompts. NEVER print a real secret
value — assert presence and successful use only.

Assertions:
  [Y] Report Task 1's four findings explicitly (names only, never
      values)
  [Y] A real DB-dependent request succeeds via Doppler on Render
  [Y] A real frontend request succeeds via Doppler on Vercel
  [Y] `doppler run --` locally connects using the same credential
      Render uses — proven by a successful real query, not by
      comparing values
  [Y] render.yaml's AWS_*/VOYAGE_API_KEY gap is closed
  [Y] No real secret value appears anywhere in this verify
      script's own output or logs
