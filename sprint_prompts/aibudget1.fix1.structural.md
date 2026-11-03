AIBUDGET1 FIX 1 — make verify_aibudget1.py pass: its own 129 checks pass;
the one FAIL is in the verify_ensemblesystemone regression. 3 tasks. Branch
aibudget1-work (aibudget1 = ee254da; verify fix 439b27e). Decide code vs
verify with evidence; never weaken an assertion.

CONFIRMED REAL FACTS, DO NOT RE-DERIVE:
- verify_aibudget1.py (re-run 2026-11-03 ~04:52 ET, after 439b27e keyed the
  edgar_index_filings fingerprint by accession_number): TOTAL 129 PASS,
  1 FAIL — "verify_ensemblesystemone.py: 0 FAIL — ('85', '1');
  ['[FAIL] an INSERT failing after the retire rolls the retire back too —
  one transaction (#2 still active, still 2 rows) — raised=TypeError
  active_mid=True rows=2']".
- That check (verify_ensemblesystemone.py ~845-875) injects a failure by
  patching ens.llm_options so claude-haiku's model_version is None ("NOT
  NULL on model_1_version → INSERT fails") and expects
  asyncpg.NotNullViolationError from activate_ensemble, raised AFTER the
  retire, with the retire rolled back. Now a TypeError is raised instead —
  most likely BEFORE the retire, in aibudget1's new code in
  services/ai_ensembles.activate_ensemble (budget / public-data handling
  that touches model_version), so the rollback property is no longer
  exercised (state is consistent: #2 still active, 2 rows).
- WSL memory is limited and vhk2 is running in another worktree: do NOT run
  verify_aibudget1.py, verify_ensemblesystemone.py or npm run build.

THERE IS NO HUMAN AVAILABLE. Report findings, then continue immediately in the
same response. If uncertain, continue.

STANDING RULES: no interactive prompts; run every command synchronously in the
foreground; no network; never print a secret. A one-off script may use a
fixture task_key and must delete every row it creates and confirm zero
remain. Commit on aibudget1-work with message "aibudget1 fix 1: <cause>,
code vs verify".

=== TASK 1: DISCOVER === Reproduce the TypeError with a one-off script
(same patch, a fixture task_key): the full traceback, where it is raised,
and whether the retire had happened. Decide: CODE defect if activate_ensemble
now crashes on input it handled before (a None version must either be
refused with a clear validation error or reach the INSERT as before) or if
the retire and insert are no longer one transaction; VERIFY defect only if
the code is right and the injection no longer reaches the INSERT for a
legitimate reason — then inject the failure another way that still fails
the INSERT after the retire (e.g. a NOT NULL / CHECK on a column the new
code does not pre-read), keeping the same assertion (NotNullViolation or
CheckViolation after the retire; retire rolled back; #2 active; 2 rows).
=== TASK 2: FIX === the code or the verify, as Task 1 decided; prove with
the one-off script that the injected INSERT failure now happens after the
retire and the retire is rolled back. Also run
verify_ensemblesystemone.py --self-checks-only (quick) → 0 FAIL.
=== TASK 3: UPDATE PROJECT STATUS === one UPDATE line under aibudget1.
