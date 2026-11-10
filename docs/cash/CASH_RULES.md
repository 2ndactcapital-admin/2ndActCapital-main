# Cash rules — operator notes (CASH06, stage A: rules DRAFT payment instructions for approval)

A cash rule keeps a family's cash where the policy wants it. When it runs it **prepares** payment instructions between
the family's OWN accounts (cash07) and submits each for a person's approval. A rule never approves, sends or moves
money: stage B (sending through the custodian) needs Altruist write access; stage C (no person per occurrence) needs
S30 + S29b + counsel. Design and decisions: docs/cash/CASH06_NOTES.md. Payments runbook: docs/cash/CASH_PAYMENTS.md.

Screen: Household → Treasury → **Rules** (below Payments, in the existing Treasury tab). API:
`/api/v1/cash/households/{id}/rules…` (routers/cash_rules.py). Service: `services/cash_rules.py`.

## Rule types

| Type | What it drafts | Schedule |
|---|---|---|
| Distribute across accounts | to 1–5 same-owner destinations: a % of the source's balance, a fixed amount each, or a % of each incoming credit | daily / business days / weekly / monthly — or each incoming credit (for % of incoming) |
| Maintain a target balance | BELOW the target: the gap, from the supporting account into the account. ABOVE: the excess, from the account to the supporting account | daily / business days / weekly / monthly |
| Scheduled recurring transfer | a fixed amount to one destination | daily / business days / weekly / monthly |
| Redirect incoming funds | a % of each incoming credit to one destination | each incoming credit |

Scheduled rules run at **10:00 Eastern**. Money moves on business days: a daily or business-day rule does nothing on a
weekend or holiday; a weekly or monthly rule that falls on one drafts for the next business day and says so in the
instruction's purpose. An **incoming credit** is a new feed transaction INTO the account's cash (dividend, interest,
a buy of the cash position — `cash_activity.TYPE_MAP` sign +1). It fires the rule once per transaction, ever.

## Amounts, holds and caps

- Percentages round DOWN to the cent; a target draw rounds UP (it reaches the target); a sweep rounds DOWN (it never
  goes below the target). Under $0.01 nothing is drafted.
- **Shortfall:** when the paying account does not hold enough for everything it would pay, NOTHING is drafted from it
  (no partial overdraft). The firing is held with the reason.
- **Stale balance:** a cash balance more than one business day old (or none) holds the firing.
- **Caps:** each rule has a cap per run ($) and a limit of instructions per day. Neither may exceed the firm's
  ceilings, the org settings `cash.rules.max_amount_per_firing` (default 250,000.00) and
  `cash.rules.max_instructions_per_day` (default 5). The excess is held.
- Every hold alerts the rule's owner and every org admin (a to-do) and shows in the rule's run history.
- A held or skipped period is **not retried**. The next period is evaluated fresh. A firing left "started, not
  finished" (the process stopped mid-way) is never retried; look at it.

## What a firing does

1. The trigger fires (schedule or incoming credit) → the engine starts a run of the rule's own workflow.
2. S29b decides the step (below). Only when it may run unattended does the rule evaluate.
3. The firing is CLAIMED once for its period (day / ISO week / month / the credit's transaction).
   The same period again drafts nothing.
4. The evaluator computes the drafts and holds from the live balances.
5. Each draft goes through `payments.draft_and_submit` **as the rule's owner**. It is checked like a person's draft
   (same owner, a declared rail, a business-day value date, the owner's trading and account authority) and lands
   **pending approval** with its approval task.
6. The run history links the engine run to the instructions it drafted (each opens in Payments).

## Who approves

- **The rule** (create, edit, activate, resume): a holder of `approve_payment` who is not one of its makers. The rule's
  OWNER is a maker of every version, so the owner never approves their own rule (S30 floor item `cash_rule`, Super
  Admin included). An edit is a NEW version (draft); the active version keeps running until the new one is approved,
  then it is replaced.
- **Each instruction it drafts:** the owner is its maker, so any OTHER `approve_payment` holder approves it. The owner
  never does (the cash07 floor, recorded in `sod_decisions`). At 2nd Act, with one checker today, an instruction drafted
  by a rule that checker owns waits for a second person (docs/cash/CASH_PAYMENTS.md).
- **Pause** and **retire** apply at once (anyone with `initiate_payment` in scope). **Resume** needs a different
  person's approval.

## Before a rule can run unattended (S29b — unchanged)

A rule's firing is an unattended write, so S29b's switches apply exactly as for every workflow:

1. The org switch `workflows.autonomy` is ON (Admin → Workflows → Autonomy; a second person decides it).
2. The rule's workflow version (`system:cash_rule:<rule id>`) is opted in after its shadow runs: set it to `shadow`,
   let it fire (each shadow run records what it would do and drafts nothing), have the OWNER review the shadow runs as
   clean, then ask for `on`. A different person decides; the owner cannot.
3. Until both, each firing SUSPENDS for a person. A suspended run is unfinished, so that rule's later firings are
   skipped as "overlap" until it is resolved or cancelled.

## How to pause everything (kill switch)

- **One rule:** Treasury → Rules → the rule → Pause (with a reason). Its trigger turns off; it never fires while paused.
- **Every rule in the firm:** Admin → Workflows → Autonomy → the organization **kill switch** (org admin). It is
  immediate, and every unattended step suspends for a person.
- **Every rule on the platform:** the platform kill switch (Super Admin).
- After a kill switch is cleared, cancel the rule runs it suspended (Admin → Workflows → runs). Until then those rules
  are skipped as "overlap".

## Never

- A rule approving, sending or reconciling a payment; an agent or the assistant firing a rule (`cash.rule_fire` is
  workflow-only: never listed to the assistant, refused by `/assistant/confirm`, refused outside its own engine run).
- A rule paying a third party: every destination and supporting account has the source's owner (service + database).
- A rule created for the demo family by script; a full account number anywhere (last four only).
