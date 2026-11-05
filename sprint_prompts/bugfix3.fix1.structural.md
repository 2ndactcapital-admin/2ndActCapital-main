BUGFIX3 FIX 1 — make verify_bugfix3.py pass, and fix the real reason the
Olivo family shows $0 net worth. 4 FAILs: three are verify defects that
together mean the CRITICAL leak was never actually tested (must be fixed so
the leak is reproduced, then shown closed); one is a REAL code defect
confirmed in the live database. 4 tasks. Branch bugfix3-work. Decide code vs
verify with evidence; never weaken an assertion.

CONFIRMED REAL FACTS, DO NOT RE-DERIVE (verify_bugfix3 run, 2026-11-05
~15:45 ET: TOTAL 136 PASS, 4 FAIL; regression verify_bugfix2.py 181 PASS,
0 FAIL (own final TOTAL line); everything else — member-reachable routes
scoped, private investments without commitment, provisional import rows
live, addresses editable, permission-true CRM controls, fixed-point money —
PASSED; keep it):
- FAIL 1 "BEFORE (9504fff): GET /portfolio/positions called list_positions
  with NO member / visibility filter": the check requires the word "scope"
  to be absent from the pre-fix route body, but the pre-fix docstring says
  "Org-scoped from JWT claims" — so the check fails on a comment. VERIFY
  defect: assert the pre-fix body calls list_positions(…) and does NOT call
  visible_entity_ids / any member filter (match calls, not words).
- FAIL 2 "REPRODUCED: … returns F2's positions … — []": the verify calls
  list_positions on a bare pool connection (services.database.get_pool) with
  no org RLS context, so RLS returns nothing. VERIFY defect: run the pre-fix
  call path with the SAME org context the route sets (app.current_org_id /
  the tenant gate's connection setup), so it returns F1 AND F2 rows — the
  leak reproduced — then the fixed route returns F1 only.
- FAIL 3 "leak raised — InvalidRecursionError: recursive reference to query
  'fam' must not appear within its non-recursive term": visible_expected
  (verify_bugfix3.py ~:669) writes WITH RECURSIVE fam AS (anchor UNION
  recursive-1 UNION recursive-2) — Postgres parses (anchor UNION
  recursive-1) as the non-recursive term. VERIFY defect: one anchor, ONE
  recursive term joining fam to a sub-select that UNIONs both ownership
  edge sets (entity_relationships ownership-type rows and entity_ownership).
  Because this raised, the whole leak section never ran — after the fix
  every leak check must execute and pass.
- FAIL 4 "Recalculate … gross moves by exactly +1,000,000 — gross 0.00,
  before 0.00": REAL CODE DEFECT, confirmed live (read-only query,
  2026-11-05): the Olivo demo has 23 live positions, ALL 23 with no taxonomy
  key on the position or its asset's default_taxonomy_key; entity_holdings
  has 0 rows for every demo entity; 31 rollup requests ran "done". The
  rollup skips a position without a taxonomy key, so net worth is $0. The
  positions came from portentry1's Add asset / Add private investment /
  manual-asset paths (and possibly the integrated import), which do not set
  a taxonomy key. Fix: (a) every creation path assigns a taxonomy key from
  the asset kind / class (real estate, collectible, operating business,
  insurance cash value, private fund, direct deal, SAFE / note, hedge fund,
  mutual fund, money market, muni, cash, option, digital asset — from the
  config taxonomy, never hard-coded labels); (b) the rollup REPORTS skipped
  positions (count + ids in the result and on the household) instead of a
  silent $0; (c) scripts/backfill_taxonomy_keys.py — sets the missing key
  on existing positions / assets by the same mapping, --dry-run first
  (prints counts per kind), then a real run; idempotent; touches only the
  taxonomy column; the operator runs it — the sprint does NOT run it on
  real or demo data.
- WSL memory is limited: do NOT run verify_bugfix3.py, verify_bugfix2.py or
  npm run build; --self-checks-only and one-off scripts on fixtures MAY be
  run.

THERE IS NO HUMAN AVAILABLE. Report findings, then continue immediately in the
same response. If uncertain, continue.

STANDING RULES: no interactive prompts. There is NO background-process
notification mechanism in this tool — nothing will ever notify you that a
script finished. Never wait for one. Run every command synchronously in the
foreground. No network. Never print a secret. Fixtures only — never write a
demo-olivo or real row (the backfill script is written and dry-run-tested on
fixtures, not run on real data). Commit on bugfix3-work with message
"bugfix3 fix 1: <one line per fix, code vs verify>".

=== TASK 1: DISCOVER === 1–4: cause, code or verify, evidence (4: which
creation paths leave the key empty; the taxonomy keys each kind should map
to; the rollup's skip branch).
=== TASK 2: FIX === the verify (1–3) and the code (4 a–c).
=== TASK 3: PROVE === one-off on fixtures: the pre-fix path WITH org context
returns F2's distinctive asset (reproduced); the fixed route returns F1 only;
visible_expected returns F1's family; an asset created through each creation
path gets a taxonomy key and rolls up (household gross = sum of values);
Recalculate after a +1,000,000 valuation moves gross by exactly 1,000,000; a
position deliberately left without a key is REPORTED as skipped; the
backfill --dry-run on a fixture org lists the right counts and a real run on
the fixture sets them, a second run changes nothing.
verify_bugfix3 --self-checks-only → 0 FAIL.
=== TASK 4: UPDATE PROJECT STATUS === one UPDATE line under bugfix3, plus
the operator step: run scripts/backfill_taxonomy_keys.py --dry-run, then the
real run, then Recalculate on the Olivo household.
