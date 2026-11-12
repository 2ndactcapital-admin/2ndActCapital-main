CPSIM05 FIX 3 — the one remaining FAIL: approval-request latency. verify_cpsim05.py (full run, cpsim05fix2_run.out) ended 220 PASS / 1 FAIL; every other section, the teardown fingerprints and every fast-mode regression PASSED. 3 tasks, keep it SHORT. Branch cpsim05-work. Never weaken an assertion; never loosen a gate.

LESSONS: STATUS line first; record each result in docs/cpsim/CPSIM05_NOTES.md and COMMIT immediately after every one-off ("cpsim05 fix 3 (wip): …"); the 90-minute cap kills a leg — do not run anything long.

CONFIRMED REAL FACTS, DO NOT RE-DERIVE:
- FAIL (cpsim05fix2_run.out line 343): "the approval request answered in 7.73 s — well under the 15 s client timeout (it schedules, it does not wait) — 7.725…". Bound in verify_cpsim05.py (~line 2030) is CLIENT_TIMEOUT_S / 3 = 5 s. The --only=timing one-off in fix 2 read 6.95 s.
- PASSED in the same section: "every automatic step scheduled inside a request started only after that request's response was sent (the after-response gate)" — so the FIX send / fill / P&L is NOT inside the request. The FIX message log list answered in 0.59 s with ONE query.
- Fix 2's evidence (CPSIM05_NOTES §7 proof table, `--only=timing` row): the time is the approval + the choke-point execute (instructions created) done synchronously in the request; each DB round-trip WSL → Supabase costs ≈ 55–120 ms, so 7 s ≈ 60–120 round-trips. Not yet proven whether that is environment latency, a cpsim05 addition, or an N+1 in the approve / execute path.

DECIDE WITH EVIDENCE (CODE vs VERIFY):
1. Count the queries the approval request runs (a query counter on the request's connection / SQLAlchemy event or asyncpg wrapper the verifies already use — reuse, don't invent) and break them down by caller: approval precheck (rep7 held-away, SoD), choke-point execute, instruction creation, fix_sim scheduling.
2. Baseline: the SAME approval for the SAME proposal shape on an account whose route is MANUAL (no simulator) — query count and seconds.
3. CODE if cpsim05's path adds round-trips that scale with trades / lines (an N+1 in instruction creation, a per-line route lookup, per-line capability reads, repeated settings reads) or does any fix_sim work before the response — fix it (batch reads; schedule, don't do) and show the count drop. Also CODE if the baseline approve path itself has an N+1 — fix minimally and say so.
4. VERIFY only if the simulated approval's query count equals the manual baseline's (± the constant one or two scheduling writes) and does not grow with the number of lines: then the 5 s wall-clock bound is measuring WSL → Supabase latency, not the product. Replace it with the property it was meant to prove, made STRICTER not looser: (a) the after-response gate (keep); (b) the simulated approval's query count ≤ the manual baseline's + a fixed small constant, and constant in line count (a 2-line and a 6-line proposal issue the same number); (c) its seconds ≤ manual baseline × 1.5 measured in the same section; (d) under the 15 s client timeout absolute. Do not merely raise 5 s.

WSL memory is limited: do NOT run the full verify_cpsim05.py or npm run build. --self-checks-only and --only=timing one-offs MAY be run (`timeout 600` each).

THERE IS NO HUMAN AVAILABLE. Report findings, then continue immediately in the same response. If uncertain, continue.

STANDING RULES: no interactive prompts. There is NO background-process notification mechanism in this tool — never wait for one; run every command synchronously in the foreground. No network. No model call. Never print a secret. Fixtures only; never write a real or demo row; never apply a capability loader. Final commit on cpsim05-work with message "cpsim05 fix 3: approval latency — <CODE|VERIFY: one line>".

=== TASK 1: STATUS + DISCOVER === add "fix 3: approval latency (in progress)" to the cpsim05 UPDATE line in docs/PROJECT_STATUS.md; query counts + breakdown + manual baseline into CPSIM05_NOTES "Fix 3 findings". COMMIT.
=== TASK 2: FIX === as decided. COMMIT.
=== TASK 3: PROVE === --only=timing → 0 FAIL (list the counts / seconds measured); --only=auto → 0 FAIL (nothing else regressed); --self-checks-only → 0 FAIL; --cleanup-only → zero. Complete the PROJECT_STATUS line. Final commit.
