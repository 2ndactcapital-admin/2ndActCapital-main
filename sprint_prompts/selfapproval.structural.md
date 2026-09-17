MAKER-CHECKER — THE EMPTY-CHECKER CASE.

YOU ARE THE SPRINT. run_sprint.sh already invoked you — that is why
you have this prompt. Do this work yourself, now, in this session.
Do NOT run run_sprint.sh. Do NOT launch anything in the background.
Run every command synchronously in the foreground and read its
output. Nothing will ever notify you that something finished.

WRITE THE VERIFY SCRIPT BUT DO NOT RUN IT. The operator runs it.
Stop once it is written and your other tasks are complete.

THE PROBLEM: agenticmakerchecker.structural built maker-checker as
a computed rule — a user may check a proposal if they hold
review_agent_proposals AND are not its maker — enforced both in
Python and by a database CHECK constraint
(agent_proposals_maker_checker_chk) that refuses
reviewed_by = proposed_by even on a raw UPDATE.

That is correct, and it makes a single-advisor org UNABLE TO EVER
APPROVE ANYTHING. 2nd Act today has one advisor. Excluding the
maker leaves an empty eligible set, and the proposal can never be
routed. This is a real fiduciary question, not an edge case.

THE DECISION, ALREADY MADE — DO NOT RE-LITIGATE:

ALLOW WITH DISCLOSURE. When the eligible-checker set is genuinely
empty after excluding the maker, permit the self-approval and
RECORD IT AS a self-approval.

Escalating to org_admin was considered and REJECTED: in a lean
organisation that is frequently the same human wearing a second
hat, which looks like separation of duties while providing none.
Honest recording beats theatrical separation. Blocking outright
was rejected as unusable for a single-advisor tenant.

THE DESIGN, ALREADY DECIDED — DO NOT SUBSTITUTE YOUR OWN:

Do NOT drop or weaken agent_proposals_maker_checker_chk into
nothing. Make it CONDITIONAL, so accidental self-approval stays
structurally impossible while a deliberate, disclosed one is
permitted. Shape:

  reviewed_by IS NULL
  OR reviewed_by <> proposed_by
  OR (self_approved = true AND self_approval_reason IS NOT NULL)

So a raw UPDATE setting reviewed_by = proposed_by STILL fails
unless the row also carries the flag and a recorded reason. The
guarantee survives; only the deliberate case opens.

AND THE CRITICAL GATE: self-approval is permitted ONLY when the
eligible-checker set is genuinely empty for that org. It is NOT a
flag a maker can set to bypass an available reviewer. The
application must compute emptiness and refuse the self-approval
path when any other eligible checker exists. Prove this — it is
the difference between a fiduciary accommodation and a hole.

Facts that change decisions:
- Eligibility is: holds review_agent_proposals AND is not the
  maker. The permission is granted to advisor, compliance,
  fund_finance, investment_committee, org_admin, support_staff.
- EVERY ONE OF THOSE ROLES CURRENTLY HAS ZERO HOLDERS except
  org_admin (1) — so in the real 2nd Act org today, essentially
  every proposal would hit the empty-checker path. That makes this
  the NORMAL case at present, not the exception. Build accordingly
  and say so in the status update.
- agent_proposals already records proposed_by as a USER id, never
  an agent name. An agent runs AS a human principal, so the
  exclusion fires correctly for agent output. Do not change this;
  confirm it still holds.
- RLS policies are PER-OPERATION and context must be set. Both
  failure modes surface as "row not found", never "permission
  denied".
- NEVER let code assert a cause it cannot know.
- TEARDOWN: audit_log is a CHILD of users. Any fixture user that
  performs an audited action leaves audit_log rows that block the
  user delete. Clear audit_log (and assistant_activities, and
  agent_proposals) BEFORE deleting fixture users. This cost three
  failed runs on the previous sprint.

=== TASK 1: DISCOVER ===
Report findings, then continue immediately.
  1a. The real current eligibility function and the exact CHECK
      constraint definition, so the conditional version is a
      genuine edit rather than a guess.
  1b. Whether any real org today has two or more holders of
      review_agent_proposals — i.e. whether the non-empty path is
      currently reachable at all in production data.
  1c. Confirm proposed_by still holds a user id, not an agent
      identifier.

=== TASK 2: THE CONDITIONAL CONSTRAINT ===
Replace agent_proposals_maker_checker_chk with the conditional
form above. Add self_approved (boolean, default false) and
self_approval_reason (text, nullable).

=== TASK 3: THE EMPTINESS GATE ===
The application permits the self-approval path ONLY when no other
eligible checker exists in that org. A maker with an available
reviewer is refused exactly as today. Compute emptiness; do not
trust a caller-supplied flag.

=== TASK 4: THE DOC NOTE ===
Add to docs/SPRINT_WORKFLOW_STANDARD.md, in the teardown guidance:
audit_log is a child of users. Any fixture user performing an
audited action leaves rows that block the user delete, and the
failure surfaces as a ForeignKeyViolationError at teardown, often
only on the SECOND run. Clear audit_log, assistant_activities and
agent_proposals before deleting fixture users. Keep it short.

=== TASK 5: PROOF (write the script; the operator runs it) ===
  - Report Task 1's three findings.
  - A maker WITH another eligible checker available is still
    refused on their own proposal — unchanged, and this is the
    assertion that proves self-approval is not a bypass.
  - A maker with NO other eligible checker CAN self-approve, and
    the row records self_approved = true with a reason.
  - A raw UPDATE setting reviewed_by = proposed_by WITHOUT the
    flag and reason still fails the CHECK constraint — the
    guarantee survives.
  - A raw UPDATE setting the flag but NO reason also fails.
  - A non-holder is still refused regardless of emptiness.
  - Cross-org: emptiness is computed per org, so org A having no
    reviewers does not let org B's maker self-approve.
  - No regression: the normal two-party path still works
    unchanged.
  - Teardown: zero leftover rows, clearing audit_log and
    assistant_activities before users.

=== TASK 6: STATUS ===
Update docs/PROJECT_STATUS.md and
docs/CROSS_PROJECT_STATUS_CONSOLIDATED.md. Record that
self-approval is permitted only on genuine emptiness, that it is
disclosed rather than silent, and that with today's zero-holder
reviewer roles this is currently the NORMAL path rather than an
exception — which is a staffing fact, not a design intent.

=== VERIFY: apps/api/scripts/verify_selfapproval.py ===
WRITE IT. DO NOT RUN IT. Pass/fail only. MUST print a
'TOTAL: N PASS, M FAIL' line and exit non-zero on failure. Must
hydrate its own secrets from Doppler over HTTPS at startup
(verify_actionregistryfix.py is the pattern). Set RLS context on
every connection touching an RLS-protected table. Fixture emails
must derive from the full user UUID, never a shared prefix slice —
two fixture UUIDs sharing their first 8 characters collided on
users_email_key last sprint.

  [Y] Task 1's three findings reported
  [Y] A maker WITH an available checker is still refused
  [Y] A maker with NO available checker can self-approve, recorded
  [Y] Raw UPDATE without flag+reason still fails the constraint
  [Y] Raw UPDATE with flag but no reason also fails
  [Y] A non-holder is refused regardless of emptiness
  [Y] Cross-org: emptiness computed per org
  [Y] No regression on the normal two-party path
  [Y] Teardown: zero leftover rows
