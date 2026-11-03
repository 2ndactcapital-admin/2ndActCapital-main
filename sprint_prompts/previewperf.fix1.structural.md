PREVIEWPERF FIX 1 — the preview itself is now fast, but verify_bulkfetchui
still measures 2.2 s end to end. Find the ~1.7 s spent OUTSIDE preview()
in bulkfetchui's request path and remove it (code) or show it is the
harness's own cost (verify) — never raise the 2 s limit. 4 tasks. Branch
previewperf-work (previewperf = ca39a17). Decide code vs verify with
evidence.

CONFIRMED REAL FACTS, DO NOT RE-DERIVE (previewperf's Step 3, 2026-11-03
~10:45 ET, load per CPU 0.01, worker off): verify_previewperf TOTAL 15 PASS,
1 FAIL.
- previewperf's own timing harness: "best e2e 666 ms, server 151 ms … 21
  stmts, 884 B: middleware principal 417 ms/12 stmts, route gate 0 ms/0
  stmts, preview() 162 ms/5 stmts" — all 172 previews identical; preview()
  5 statements (28 before).
- verify_bulkfetchui.py in full (the ONE fail): "live preview JPMorgan 2025
  timed calls: 2205 ms, 2603 ms, 2355 ms; best 2205 ms end to end
  (server-side 508 ms), total 18860, SQL 18860" → "[FAIL] live preview
  returns under 2 s — best of 3 2.205 s". Everything else in it passes.
- So: bulkfetchui's call spends ~1.7 s outside the route's elapsed_ms,
  versus ~0.5 s in previewperf's harness for the same request. Candidates:
  (a) the auth middleware's principal load (12 statements, ~420 ms even in
  the fast harness — is any of it per-request work that can be cached /
  combined safely?); (b) bulkfetchui's api() helper (verify_bulkfetchui.py
  ~766-849): a new ASGI client / connection pool / token per call, a cold
  path, a different user (U_SUPER fixture with many roles?), or timing that
  includes its own setup; (c) server-side 508 ms vs 151 ms — why the same
  preview is slower under bulkfetchui (cold cache, its fixture rows, a
  different body).
- WSL memory is limited: do NOT run npm run build or verify_previewperf.py;
  you MAY run verify_bulkfetchui.py in full ONCE at the end (worker off —
  check edgar_fetch_workers heartbeats first) and both scripts'
  --self-checks-only.

THERE IS NO HUMAN AVAILABLE. Report findings, then continue immediately in the
same response. If uncertain, continue.

STANDING RULES: no interactive prompts; run every command synchronously in the
foreground; no network beyond Doppler hydration; never print a secret.
Caching of the principal is allowed only if it stays correct: invalidated on
role / permission / membership change, never shared across users or orgs,
short TTL stated. Commit on previewperf-work with message "previewperf fix 1:
<cause> → <fix>, code vs verify, <before> ms → <after> ms".

=== TASK 1: DISCOVER === time bulkfetchui's exact call path step by step
(its api() helper, client creation, token, middleware principal per
statement, route, serialisation) for the same body, 3 warm runs; compare with
previewperf's harness; report the table and which part is product code vs
the harness.
=== TASK 2: FIX === product code where the time is real for a real user
(principal loading: fewer round trips / one query / a correct cache); the
verify only where the time is the harness's own setup (then time only the
request, as a real browser call would be timed). No assertion weakened, no
limit raised.
=== TASK 3: PROVE === verify_bulkfetchui.py in full → 0 FAIL (one run, worker
off); previewperf's harness numbers before / after; if the principal changed,
a role change is visible on the very next request (both ways).
=== TASK 4: UPDATE PROJECT STATUS === one UPDATE line under previewperf.
