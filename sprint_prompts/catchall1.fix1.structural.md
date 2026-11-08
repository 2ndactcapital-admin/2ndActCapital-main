CATCHALL1 FIX 1 — make verify_catchall1.py pass. 3 FAILs: a fixture
security the verify leaked into the live platform master (2 FAILs, one
cause), and a stale static marker in the nested verify_rollup2.py. 4 tasks.
Branch catchall1-work. Decide code vs verify with evidence; never weaken an
assertion.

CONFIRMED REAL FACTS, DO NOT RE-DERIVE (verify_catchall1 run, 2026-11-07
~18:00–18:30 ET: TOTAL 118 PASS, 3 FAIL; bulk-approve wording, the shared
busy indicator, direct-asset Edit / End / history and the net-worth core
following them, the option contract defined from the picker, the Lows, the
three pre-existing verify fixes, isolation and own routes all PASSED — keep
them):
- FAIL 1 + 2: "portfolio.securities_global at its pre-test count — 15999 →
  16000" and "real portfolio.securities_global unchanged (count + checksum
  by primary key) — 15999 → 16000 rows". The extra live row is
  id 1933ecd0-2cbe-464d-9f88-d4aa53250231, name "CA1VSPY 2026-12-18 660
  CALL (listed, american, physical-settled, ×100)", security_type option,
  system_from 2026-11-07 22:46:55 UTC — the master security rep8's option-
  contract service creates for the contract the verify defined from the
  picker (Task 5). The verify's teardown removes the contract by id and its
  fixture securities in FIX_SECS, but NOT the option security the service
  created; its name starts "CA1VSPY …" (no space, so the "CA1V " name prefix
  check does not see it) and the reserved-prefix check only looks at
  identifiers LIKE 'CA1V%'. VERIFY defect: (a) record the security id the
  contract service returns (or look it up from the contract row) and tear it
  down (and its identifiers) through the documented platform path, before the
  contract; (b) the reserved-prefix assertion and the teardown also match
  security NAMES starting 'CA1V' (no space) and option securities whose
  underlying is a fixture security; (c) at START, the cleanup removes this
  already-leaked row 1933ecd0… (it is a fixture: prove it — underlying is the
  fixture CA1VSPY security, created during the run — then delete it and its
  identifiers / contract remnants by id; nothing else). If the evidence shows
  the contract service should NOT create a master security for a listed
  contract, that is CODE — say so and fix it; otherwise keep the behaviour.
- FAIL 3: nested verify_rollup2.py (own final TOTAL): 160 PASS, 1 FAIL —
  "[FAIL] Task 3: backfill --funds — apps/api/scripts/backfill_taxonomy_keys.py:
  needle missing". verify_rollup2.py ~line 100 looks for the exact text
  "async def funds(conn, org: str, *, dry_run: bool) -> int:"; the function
  is now "async def funds(conn, org: str, *, dry_run: bool, refine: bool =
  False) -> int:" (secmaster2 added --refine; catchall1 also edited the
  file). VERIFY defect (a static marker tied to an exact signature): match
  the function by name and its dry_run parameter (a regex), still asserting
  --funds exists; grep verify_rollup2's other needles against the current
  files and fix any other stale one the same way.
- WSL memory is limited: do NOT run verify_catchall1.py, verify_rollup2.py
  or npm run build; --self-checks-only, --cleanup-only and one-off scripts
  MAY be run.

THERE IS NO HUMAN AVAILABLE. Report findings, then continue immediately in the
same response. If uncertain, continue.

STANDING RULES: no interactive prompts. There is NO background-process
notification mechanism in this tool — nothing will ever notify you that a
script finished. Never wait for one. Run every command synchronously in the
foreground. No network. Never print a secret. Fixtures only — the ONE live
row you may delete is the leaked fixture 1933ecd0… after proving it is one.
Commit on catchall1-work with message "catchall1 fix 1: <one line per fix,
code vs verify>".

=== TASK 1: DISCOVER === 1 (the contract service's created security;
1933ecd0's underlying and creation; the teardown order); 3 (every
verify_rollup2 needle vs the current files).
=== TASK 2: FIX === the teardown + prefix checks (a, b), the start-cleanup of
the leaked row (c), verify_rollup2's needles.
=== TASK 3: PROVE === run verify_catchall1 --cleanup-only: the leaked row is
gone, securities_global back to 15999 (live count — the count before the
run), nothing else touched (list what was deleted); a one-off defining a
fixture contract then tearing it down leaves no row; verify_rollup2
--self-checks-only and verify_catchall1 --self-checks-only → 0 FAIL.
=== TASK 4: UPDATE PROJECT STATUS === one UPDATE line under catchall1.
