DEALROOM3 FIX 1 — make verify_dealroom3.py pass: one CODE 500 on the
share-unwatermarked route (plus its cascade), one verify-script bool bug that
stopped the pause section early, and one missing phrase in the discovery doc.
4 tasks. Branch dealroom3-work (dealroom3 = bbb8b23).

Context: verify_dealroom3.py ran (run_sprint Step 3, 2026-10-28): TOTAL 221
PASS, 4 FAIL. Everything else passed — watermark stamping and lookup,
view-only, expiry with both edges, the activity log, all of Q&A, all of
advisors, notifications, 506(b) / (c), isolation, gates, routes, node tests,
npm build, teardown, and the regression verify_dealroom2.py. Keep all of it.

CONFIRMED REAL FACTS, DO NOT RE-DERIVE:
- FAIL 1 "1b reported with its files and evidence — missing ['cannot be
  watermarked — ask the publisher for a PDF']": docs/DEALROOM3_DISCOVERY.md's
  1b section lacks that exact user-facing reason. Add it where 1b describes
  the non-watermarkable rule (the route already returns "This file type
  cannot be watermarked — ask the publisher for a PDF.").
- FAIL 2 "the publisher marks it 'share unwatermarked' — recorded (who) and
  audited (same request: 403 / 200)" → the PUBLISHER's request returned 500;
  the row stayed share_unwatermarked = false, by = null; no audit row. The
  investor's identical request correctly got 403. Route:
  routers/deal_room.py:1172 share_unwatermarked — one transaction: the UPDATE
  of deal_documents (share_unwatermarked = $3, share_unwatermarked_by = CASE
  WHEN $3 THEN $4 END, share_unwatermarked_at = CASE WHEN $3 THEN now() END,
  updated_at = now() … RETURNING id, share_unwatermarked,
  share_unwatermarked_at) then an INSERT into audit_log (org_id, user_id,
  action, resource_type, resource_id, payload jsonb — those columns exist).
  The cause is unknown — reproduce it (a one-off synchronous script calling
  the route through the app's test client against your own is_test fixture
  org and document, cleaned up by id afterwards; never real deals) and read
  the real exception. Candidates: a CHECK / trigger on deal_documents (e.g.
  an immutability or by-requires-at constraint, or a trigger that rejects
  updates to a document in a room), asyncpg parameter-type inference for $3
  / $4 inside CASE, the deal_documents RLS UPDATE policy for the publisher's
  context, or _jsonable on the returned row. Fix the CODE; keep the gate.
- FAIL 3 "once shared unwatermarked, the investor receives that file as it
  is" → still refused — a cascade of FAIL 2 (the flag never set). Re-check
  after the fix by reading services/deal_room_control.py:135 (shared =
  bool(doc.get('share_unwatermarked'))) — it must read the live column.
- FAIL 4 "pause raised — AssertionError: check() got 'Accreditation lapsed:
  re-verification was due 2026-10-27.' (str) for 'the room shows the paused
  reason'" at scripts/verify_dealroom3.py:951: `env["my_access"]["status"] ==
  "paused" and env["my_access"]["paused_reason"]` returns the reason string.
  Wrap the condition in bool(...) (the reason itself is correct). The rest of
  y_pause after line 951 (restore when the record is fixed, etc.) never ran —
  do not change those checks; they run next time.
- WSL memory on the operator's laptop is limited: do NOT run
  verify_dealroom3.py, verify_dealroom2.py or npm run build.

THERE IS NO HUMAN AVAILABLE. Report findings, then continue immediately in the
same response. If uncertain, continue.

STANDING RULES: no interactive prompts. There is NO background-process
notification mechanism in this tool — nothing will ever notify you that a
script finished. Never wait for one; run every command synchronously in the
foreground and read its output directly. No network calls (stub R2). Never
print a secret or a signed URL. Clean up every row your one-off script
creates (by its own ids) and confirm zero remain. Do not weaken any
assertion. Commit on dealroom3-work with message "dealroom3 fix 1:
share-unwatermarked 500, verify pause bool, discovery phrase".

=== TASK 1: REPRODUCE FAIL 2 === the real exception and its cause.
=== TASK 2: FIX THE ROUTE (and FAIL 3 if anything beyond the cascade) ===
=== TASK 3: FIX verify_dealroom3.py:951 (bool) and the discovery doc phrase ===
=== TASK 4: UPDATE PROJECT STATUS === one UPDATE line under the dealroom3
entry naming the cause of the 500.
