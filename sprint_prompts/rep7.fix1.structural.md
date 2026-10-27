REP7 FIX 1 — make verify_rep7.py pass: the X-ray's as-of crash, the overlap
figure, the transition tax, and two stale self-checks in verify_rep6.py.
4 tasks + verification. Branch rep7-work (rep7 = d83f3ca). Fix code, not
expectations, unless Task 1 proves an expectation wrong.

Context: verify_rep7.py ran (run_sprint Step 3, 2026-10-26): TOTAL 173 PASS,
7 FAIL. Every other check passed — schema, imports, wash-sale with held-away
data, gain budget, loss bank, staleness, the held-away refusal on every trade
path, gates, RLS, routes, node tests, npm build, teardown. Keep all of it.

CONFIRMED REAL FACTS, DO NOT RE-DERIVE (from that run's output):
- FAIL 1: "an on-demand run is recorded … a viewer cannot record" → 403 500.
- FAIL 2: "an X-ray run is immutable" (follows from FAIL 1 — no run row).
- FAIL 3: the sweep → errors ["household …e7212: 'str' object has no
  attribute 'isoformat'"] on both sweeps.
- FAIL 4: "household access: a view_replication holder with no access gets a
  404 and an empty list; an org admin reads it" → 404 200 500 (the third call
  500s).
- Likely shared cause of 1–4: services/xray.py:127 `_iso(v)` calls
  v.isoformat() on a value that is already a str (e.g. rates["as_of"] from
  tax_profiles.combined_rates at xray.py:518, or a weight / holdings as-of
  read back from JSON). Task 1 confirms every call site and the real types;
  fix at the source of the type, and make _iso accept date | datetime | str |
  None.
- FAIL 5: "transition tax exact (20,000 × (0.2 + 0.038) = 4,760.00) and
  break-even = tax ÷ fee difference (119.00 years; 59.50)" → None None. The
  fixture seeds 2 tax_profiles; the no-profile case passed separately. The
  path is xray._fund_analysis (≈ lines 503–521): profile_cache keyed by
  household_id or primary_entity_id, TP.profile_for_account(conn, org_id, a,
  today.year), TP.combined_rates. Find why the approved profile is not found
  (subject key, tax year, approval status, cache key collision with the
  no-profile household) or why the call raises — then fix.
- FAIL 6: "overlap between funds: one shared constituent, Σ min(weights) =
  0.5" → overlap_weight 0.6 for FUND vs FUND3 (xray.py ≈ 395–403, built from
  fund_weights). Each fund's own holdings-file weights must be used
  (Σ over shared constituents of min(weight in fund A's file, weight in fund
  B's file)). If fund_weights is overwritten or accumulated when the same
  fund is held in two accounts, or keyed by household weight, fix it. Task 1
  states each fund's weight for the shared constituent from the fixture and
  which figure is right.
- FAIL 7: regression verify_rep6.py → 183 PASS, 2 FAIL, both verify_rep6
  SELF-checks made stale by rep7, not rep6 behaviour:
    (a) y_rep5_diff "every changed line of verify_rep5.py is in y_wash …" —
        rep7 edited verify_rep5.py y_join (≈ lines 953 / 979–981: ACCT_HELD
        no longer joins; "rep7 superseded rep5 here").
    (b) "31 new routes, all under /tax/… and /harvest/…" → 50 31 — rep7's 19
        /held-away/… and /xray/… routes now appear as "new" too.
  Fix verify_rep6.py ONLY in those two checks: (a) allow y_join lines that
  implement rep7's held-away refusal (mention ACCT_HELD / held-away / rep7 /
  the refused join and its response check) and nothing else; (b) count the
  routes under /tax/ and /harvest/ (still exactly 31, each gated as before)
  and ignore routes under prefixes added by later sprints (/held-away/,
  /xray/). Say which lines changed.
- Design (Joe): held-away accounts are refused by every trade path,
  including instruction creation (rep2's old manual routing for held-away is
  superseded). Keep it.
- Regression for this fix: verify_rep7.py nests verify_rep6.py (VERIFY_NESTED=1,
  2700 s) — unchanged.
- WSL memory on the operator's laptop is limited: the sprint must NOT run npm
  run build or any verify script.

THERE IS NO HUMAN AVAILABLE. Report findings, then continue immediately in the
same response. If uncertain, continue.

STANDING RULES: no interactive prompts. There is NO background-process
notification mechanism in this tool — nothing will ever notify you that a
script finished. Never wait for one, never poll in a loop, never start a
script in the background; run every command synchronously in the foreground
and read its output directly. Do NOT run verify_rep7.py, verify_rep6.py or npm
run build. You MAY run short synchronous checks (python -c imports, pyflakes,
node --test tests/replication/, a one-off script that calls xray.compute on
nothing — never on real households). No network calls. Never print a secret.
Decimal everywhere. Do not weaken any verify_rep7 assertion; change an
expectation only if Task 1 proves it wrong, and say so with the arithmetic.
Commit ALL changed files (excluding sprint_prompts/logs) on rep7-work at the
end with message "rep7 fix 1: x-ray as-of types, overlap, transition tax;
verify_rep6 self-checks scoped to rep6".

=== TASK 1: DISCOVER ===
Report findings, then continue immediately.
  1a. Every _iso call site in services/xray.py (and routers/xray.py) with the
      real type at each one; which one raised in FAILs 1–4.
  1b. FAIL 5: walk the fixture's tax profile (subject type / id, tax year,
      status) and xray's lookup; name the mismatch.
  1c. FAIL 6: the fixture weights of the shared constituent in FUND and FUND3
      and how fund_weights is built; which of 0.5 / 0.6 is right.
  1d. FAIL 7: the exact verify_rep6.py lines of both checks.

=== TASK 2: FIX services/xray.py (and routers/xray.py if needed) ===
Per 1a–1c. No behaviour change beyond the fixes.

=== TASK 3: FIX verify_rep6.py's two self-checks ===
Per FAIL 7 and 1d. Nothing else in verify_rep6.py.

=== TASK 4: UPDATE PROJECT STATUS ===
One UPDATE line under the rep7 entry in docs/PROJECT_STATUS.md naming the
fixes.

=== VERIFICATION ===
verify_rep7.py already covers every fix; do not change its checks unless 1b /
1c prove an expectation wrong. Write nothing new to run; the operator runs
verify_rep7.py.
