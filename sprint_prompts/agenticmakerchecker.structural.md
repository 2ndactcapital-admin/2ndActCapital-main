AGENTIC SUBSTRATE — MAKER-CHECKER + ESCALATION ENUM.

YOU ARE THE SPRINT. run_sprint.sh already invoked you — that is why
you have this prompt. Do this work yourself, now, in this session.
Do NOT run run_sprint.sh. Do NOT launch anything in the background.
Run every command synchronously in the foreground and read its
output. Nothing will ever notify you that something finished.

WRITE THE VERIFY SCRIPT BUT DO NOT RUN IT. The operator runs it.
Stop once it is written and your other tasks are complete.

Context: docs/CROSS_PROJECT_STATUS_CONSOLIDATED.md has the 15-item
agentic design. This sprint builds the two items that are genuinely
unblocked. Item #3 (capability annotation) is NOT in scope — its
vocabulary was never defined and the assumption that SOC's would
serve has been disproven.

DECISIONS ALREADY MADE, DO NOT RE-LITIGATE:

- SEVEN agents, not eight. Chancery and Custodial Ops are MERGED
  into one "Document & Custodial Ops" agent. They shared a reviewer
  and adjacent verbs; two agents proposing to the same reviewer is
  one agent with a broader allowlist.
- THE BOUNDARY RULE IS CORRECTED. The original design said agent
  boundary = tool allowlist x reviewer role. Reviewer is NOT fixed
  per agent (see below), so it defines nothing. The boundary is the
  TOOL ALLOWLIST alone. Do not encode reviewer as a boundary.
- MAKER-CHECKER IS A RULE, NOT AN ASSIGNMENT. There is NO
  review_role column. A proposal stores WHO MADE IT; eligibility to
  check is COMPUTED as: holds the review permission AND is not the
  maker. This is a lean-organisation model — an advisor may check
  compliance work, support staff's work may be checked by
  fund_finance. Pinning one reviewer per agent would block work
  when that person is unavailable or push people to review their
  own proposals.
- ELIGIBILITY IS A PERMISSION. Create a real permission (name it
  following the existing catalog's convention — check it, do not
  invent a style). Any role holding it may check any proposal,
  except the one whose holder made it.
- compliance_sr AND compliance_jr COLLAPSE INTO ONE `compliance`
  ROLE. Verified live: both have ZERO holders and ZERO permissions,
  so this is free. Do not preserve the split.
- REAL REVIEWER ROLES, all confirmed to exist:
  Document & Custodial Ops -> support_staff
  Portfolio & Suitability  -> advisor
  Deal & SPV               -> investment_committee
  Fund Admin & Billing     -> fund_finance
  Compliance Analyst       -> compliance
  Authoring                -> org_admin
  Hollis                   -> none, read-only, never proposes
  These are ROUTING DEFAULTS for display only — never a constraint
  on who may actually check.

Facts that change decisions:
- escalation_reason ALREADY EXISTS as a live Postgres enum
  ('budget','max_steps','tool_error','low_confidence','refused',
  'ambiguous'), created earlier and verified. NO column uses it yet
  — deliberately, because no agent-run table existed. If this
  sprint creates one, use this type; do NOT create a second enum.
- NO AGENT RUNS EXIST YET. Workflow Manager Wave 2 is unbuilt, so
  agents have no workflow instance to execute as. This sprint
  builds SUBSTRATE — the tables and the rule — not a running agent.
  Say so plainly in the status update rather than implying more.
- THE REVIEWER ROLES ARE EMPTY. advisor (11 perms), support_staff
  (7), org_admin (1) have grants; investment_committee,
  fund_finance, compliance_* have ZERO permissions and ZERO
  holders across the board. So this mechanism is UNEXERCISED until
  staffing catches up — every proof will be against fixtures, not
  real usage. State that honestly.
- RLS: policies are PER-OPERATION and context must be set. Both
  failure modes cost real time this session and BOTH surface as
  "row not found", never "permission denied". Any new table needs a
  policy for every operation it will ever take.
- NEVER let code assert a cause it cannot know. Zero affected rows
  does not distinguish a missing row from an RLS-blocked write.

=== TASK 1: DISCOVER ===
Report findings, then continue immediately.
  1a. The real permissions catalog's naming convention, and
      whether a suitable review permission already exists before
      creating one.
  1b. The real proposed-state mechanism today — is there an
      existing proposal/pending table an agent's output would land
      in, or does one need creating? Report what genuinely exists.
  1c. Every real consumer of compliance_sr / compliance_jr
      anywhere in code or data, so the consolidation's blast
      radius is known before renaming.

=== TASK 2: CONSOLIDATE COMPLIANCE ===
Per 1c: collapse compliance_sr and compliance_jr into a single
`compliance` role. Both are empty, so this is additive-then-remove
rather than a migration. Report anything in code that referenced
the old names.

=== TASK 3: THE REVIEW PERMISSION ===
Per 1a: create the review permission. Grant it to the seven real
reviewer roles above (not Hollis, which never proposes). This is
what makes a role eligible to check.

=== TASK 4: THE MAKER-CHECKER RULE ===
Implement eligibility as a computed function, NOT a stored column:
a user may check a proposal if they hold the review permission AND
are not its maker. Proposals must record their maker. If 1b found
no proposal table, create one — minimal, with a maker column, and
a policy for every operation.

=== TASK 5: WIRE THE ESCALATION ENUM, IF THERE IS SOMEWHERE TO
WIRE IT ===
If Task 4 creates a proposal or run table where an escalation
reason genuinely belongs, use the EXISTING escalation_reason type.
If there is genuinely nowhere for it yet, say so and leave it
unwired rather than inventing a table to justify it.

=== TASK 6: PROOF (write the script; the operator runs it) ===
  - Report Task 1's three findings.
  - compliance_sr and compliance_jr are gone; `compliance` exists
    and nothing in code references the old names.
  - The review permission exists and is held by exactly the
    intended roles.
  - A user holding the review permission who is NOT the maker is
    eligible — proven by the real function.
  - The MAKER is refused on their own proposal even though they
    hold the permission — this is the whole rule, prove it
    explicitly.
  - A user without the permission is refused regardless.
  - Cross-org: a reviewer in org A cannot check org B's proposal.
  - If a table was created: a policy exists for every operation,
    and an UPDATE genuinely works with context set.
  - No regression: existing permission checks are unaffected.

=== TASK 7: STATUS ===
Update docs/PROJECT_STATUS.md and
docs/CROSS_PROJECT_STATUS_CONSOLIDATED.md. Record explicitly: the
boundary-rule correction (allowlist alone, not allowlist x
reviewer), the seven-agent merge, that maker-checker is computed
rather than stored, and that the mechanism is UNEXERCISED until
the reviewer roles have real holders. Note that #3 (capability
annotation) remains blocked on an undefined vocabulary.

=== VERIFY: apps/api/scripts/verify_agenticmakerchecker.py ===
WRITE IT. DO NOT RUN IT. Pass/fail only. MUST print a
'TOTAL: N PASS, M FAIL' line and exit non-zero on failure. Must
hydrate its own secrets from Doppler over HTTPS at startup
(verify_litellmphaseg.py is the pattern). Set RLS context on every
connection touching an RLS-protected table.

  [Y] Task 1's three findings reported
  [Y] compliance consolidated; no stale references
  [Y] The review permission exists, held by the intended roles
  [Y] A permission-holding non-maker is eligible
  [Y] THE MAKER IS REFUSED on their own proposal despite holding
      the permission
  [Y] A non-holder is refused regardless
  [Y] Cross-org isolation on eligibility
  [Y] If a table was created: policy per operation, UPDATE works
  [Y] No regression on existing permission checks
  [Y] Teardown: zero leftover rows
