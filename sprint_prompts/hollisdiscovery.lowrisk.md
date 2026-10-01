HOLLIS — DISCOVERY ONLY. Is the first agent already built?

YOU ARE THE SPRINT. run_sprint.sh already invoked you — that is why
you have this prompt. Do this work yourself, now, in this session.
Do NOT run run_sprint.sh. Do NOT launch anything in the background.
Run every command synchronously in the foreground and read its
output. Nothing will ever notify you that something finished.

READ-ONLY. NO CODE CHANGES. NO SCHEMA CHANGES. The findings document
is the deliverable. There is no verify script by design —
run_sprint.sh will print "FATAL: expected verify script not found"
at Step 3. That is expected, not a failure.

WHY THIS EXISTS: Wave 2 turned out to be five merged sprints that no
status doc recorded. The same may be true of Hollis. The existing
member assistant already runs a tool-using LLM loop over the action
registry's read verbs — which describes Hollis almost exactly. Find
out what is real before anyone builds it again.

HOLLIS, AS DESIGNED (settled — do not re-litigate):
- Member-facing. READ-ONLY. Never executes a write.
- When a member asks for something that needs action, Hollis
  PROPOSES it via propose() — the proposal is owned by the member's
  advisor, not by Hollis. It never performs the action itself.
- One of seven agents. The agent boundary is the TOOL ALLOWLIST.

THE AGENT CONTRACT — six constraints every agent must meet:
  1. principal-as-user — runs AS the authenticated human, with that
     human's permissions and RLS scope, never as a system identity
  2. workflow-instance execution — runs as a workflow instance
  3. bounded loop — a hard cap on steps/tokens, escalating with a
     fixed escalation_reason when hit
  4. per-step decision log — every step recorded, not just the call
  5. idempotency — a retried step cannot double-act
  6. eval gate — measured against an eval before it is trusted

THE CUSTODY CLIFF (permanent): trade execution, money movement,
filing submission, and GL posting NEVER get an agent.

Facts that change decisions:
- The registry has 17 verbs; 10 reads are Tier 3. propose() exists
  and writes to agent_proposals (maker-checker enforced).
- spv.subscribe is Tier 1, gated on indicate_interest — a member-
  held permission. It commits capital.
- Reads execute inline in the LLM loop; writes go through
  POST /assistant/confirm (established by actionregistryfix).
- Tier gating (tiergating.structural) suspends Tier-1 steps in the
  WORKFLOW ENGINE. Whether the assistant's confirm path honours
  tiers at all is unknown — find out.
- RLS is scoped per ORG, not per member.
- Reads without RLS context set return None/0/[] SILENTLY. Set
  context on every query.

=== TASK 1: WHAT EXISTS ===
The real assistant: entry points, the loop, how tools are selected
for a given caller (filtered by required_permission? by tier? not at
all?), which model it resolves to, and who can use it (members,
staff, both).

=== TASK 2: THE CONTRACT, CONSTRAINT BY CONSTRAINT ===
For each of the six: MET, PARTIAL, or ABSENT — with the file and
line that proves it. If a constraint arguably does not fit an
interactive read-only agent (workflow-instance execution is the
likely one), report the tension plainly. Do not resolve it.

=== TASK 3: THE WRITE SURFACE ===
Every write verb the assistant can reach today, and what happens
when a member confirms one. Specifically: can a member commit
capital through the assistant via spv.subscribe, and does that path
go through any tier gate or maker-checker, or execute on the
member's own confirmation? State plainly how this differs from
Hollis-as-designed (read-only, propose-only).

=== TASK 4: MEMBER-TO-MEMBER ISOLATION ===
RLS isolates orgs, not members. For every read verb a member can
reach, does it return ONLY the calling member's data, or anything in
the org? Test at least one real case against live data. A
member-facing agent that can read another member's positions is the
most serious finding this sprint could produce — if found, lead the
report with it.

=== TASK 5: THE CUSTODY CLIFF ===
Any path from the assistant to trade execution, money movement,
filing submission, or GL posting. Name each one.

=== TASK 6: WRITE FINDINGS ONLY ===
Write docs/HOLLIS_DISCOVERY.md — facts and gaps, with file:line
evidence. No design, no roadmap, no proposed schema. Commit it. Prove
with git diff that no other file changed.

"This does not exist" and "this constraint is absent" are correct,
useful answers. Do not soften a gap into a partial.
