CPSIM06 FIX 2 — one FAIL: a simulator token in a response body. verify_cpsim06.py ended 331 PASS / 1 FAIL (cpsim06fix1_run.out, 2026-11-12); every other section — including all tax-id / date-of-birth checks, guard, conformance, teardown fingerprints and the fast-mode regressions — PASSED. 3 tasks, keep it SHORT. Branch cpsim06-work. Never weaken an assertion. This is a SECRET-HANDLING check: if a person-facing Hollisworks response carries a token, that is CODE and CRITICAL — say so plainly.

LESSONS: STATUS line first; record each result in docs/cpsim/CPSIM06_NOTES.md and COMMIT immediately after every step ("cpsim06 fix 2 (wip): …"); the 90-minute cap kills a leg.

CONFIRMED REAL FACTS, DO NOT RE-DERIVE:
- FAIL (cpsim06fix1_run.out line 323, y_sensitive, verify_cpsim06.py ~line 1451): "no response body carried a simulator key, password or token (the shown-once create answers are the only place one appears, by design) — ['cpsim_rtat…', 'cpsim_rtrt…', 'cpsim_rtat…']". cpsim_rtat = a Realtime ACCESS token, cpsim_rtrt = a Realtime REFRESH token (the simulated password grant, task 3). `leaks = [x for b in S["bodies"] for x in secret_leaks(b)]` — S["bodies"] holds 110 bodies: the Check / Open results, the pre-fill envelope, the Realtime tab AND "every simulator answer".
- Most likely: the simulated Realtime TOKEN endpoint's answer (POST …/token, password / refresh grant) is in S["bodies"]. That answer carries tokens BY PROTOCOL (Altruist's real token endpoint does the same); it goes server-to-server to Hollisworks' Realtime client and is never shown to a person. NOT yet proven.

DECIDE WITH EVIDENCE:
1. Make every entry of S["bodies"] carry its source (method + path, and whether it is a Hollisworks /api/v1 response or a simulator /cpsim answer). Re-run the sensitive section one-off and list exactly which bodies hold each leaked prefix — print only the 10-character prefix, never a full token.
2. CODE (CRITICAL) if ANY Hollisworks /api/v1 response (Check / Open result, pre-fill envelope, the Realtime tab, an error body, a sync result) or any simulator answer OTHER than the token endpoint carries a token, key or password: remove it at the source (token kept server-side in the client's cache / a secret store; the tab shows status / expiry only), and add a render / response test.
3. VERIFY only if the leaks are exclusively the simulator's token-endpoint answers: keep the check over EVERY other body (no blanket exemption of simulator answers), exempt only the token endpoint by exact path and grant, the same way the "shown-once create answers" are exempted, and ADD the stricter checks that make that exemption safe: (a) the token-endpoint answers were fetched by the Realtime client, not by any person-facing route; (b) no Hollisworks /api/v1 body, no audit row, no request_log row (cpsim.request_log stores no token / password — scan it) and this script's output ever carries a cpsim_rt* token or the simulator password; (c) the request_log row for the token call records the grant type only.

WSL memory is limited: do NOT run the full verify_cpsim06.py or npm run build — the runner runs it after this leg. --self-checks-only, --cleanup-only and --only=<section> one-offs MAY be run (`timeout 600` each).

THERE IS NO HUMAN AVAILABLE. Report findings, then continue immediately in the same response. If uncertain, continue.

STANDING RULES: no interactive prompts. There is NO background-process notification mechanism in this tool — never wait for one; run every command synchronously in the foreground. No network. No model call. Never print a secret, key, token, password or full tax id / account number. Fixtures only; never write a real or demo row; never apply a capability loader. Final commit on cpsim06-work with message "cpsim06 fix 2: token in response body — <CODE|VERIFY: one line>".

=== TASK 1: STATUS + DISCOVER === add "fix 2: token-in-response check (in progress)" to the cpsim06 UPDATE line in docs/PROJECT_STATUS.md; step 1 above; findings table (body source, prefix, verdict) in CPSIM06_NOTES "Fix 2 findings". COMMIT.
=== TASK 2: FIX === as decided. COMMIT.
=== TASK 3: PROVE === --only=sensitive (or the section's name) → 0 FAIL, listing every secret check; --self-checks-only → 0 FAIL; --cleanup-only → zero fixture rows. Complete the PROJECT_STATUS line. Final commit.
