# BULKAPPROVE1 — Task 1 discovery (2026-11-05)

Joe: "account authority approval needs a 'bulk' screen — it's 5 clicks per to approve." The Olivo onboarding has
34 accounts, many with a non-default authority (each one a maker-checker draft), and ~25 held-away drafts.

## 1a. The single-approval paths

### Authority / custody (rep7b)

| Step | Where |
|---|---|
| Route | `POST /api/v1/authority/changes/{change_id}/approve` and `/reject` (`routers/authority.py`), gate `require_permission(approve_authority)`; body `{note}` (`extra='forbid'`) |
| Service | `services.authority.decide(pool, *, org_id, actor_id, change_id, outcome, note)`: outcome approve/reject; a reject needs a note; `HT.assert_human`; `load_change` (household_authority, then account_authority_settings, `org_id` = the caller's, `system_to IS NULL` — another org's id is `AuthorityNotFound` → 404); status must be `draft` (`AuthorityConflict` → 409); maker ≠ checker (`MakerCheckerViolation` → 403) |
| Human task | `HT.complete_task` on the change's `approval_task_id` (kind `approve_authority_change`, eligible permission `approve_authority`): task must be `open` (a withdrawn / cancelled task → `HumanTaskConflict` → 409); maker-checker again; `rbac.has_permission(actor, approve_authority)` per call; the precheck `authority._precheck` (still a draft; never managed + outside); the engine's `complete_user_task` (its own maker-checker, backed by the run step's `proposed_by`/`approved_by` CHECK) |
| Apply | `authority._apply` (on_complete): `FOR UPDATE`, maker-checker a third time, supersedes the prior approved row (`valid_to`), archives the draft (system axis, `archived_from_id`), sets status / `approved_by` / `decided_at` / `decision_note`, audit `authority.account_approved` / `household_approved` / `_rejected`, then `withdraw_forbidden` (open proposals the new authority forbids) and the domain event |
| DB | `household_authority_maker_checker_chk` / `account_authority_settings_maker_checker_chk`: `approved_by IS NULL OR approved_by <> maker_id`; the immutability trigger (rows never deleted; teardown only via `execution.teardown = 'fixture'`) |

### Held-away entries / imports (rep7, portentry1, portentry2)

| Step | Where |
|---|---|
| Route | `POST /api/v1/held-away/imports/{import_id}/approve` and `/reject` (`routers/held_away.py`), gate `require_permission(approve_held_away)` |
| Service | `services.held_away.decide_import(pool, *, org_id, actor_id, import_id, outcome, note)`: reject needs a note; `_import` (org-scoped; another org's id → `HeldAwayNotFound` → 404); status `draft` (else `HeldAwayConflict`); maker ≠ checker on **`entered_by`** (not `maker_id`); a refused file can only be rejected |
| Human task | `HT.complete_task` (kind `held_away_import_approval`, `approve_held_away`) + `_import_precheck` (refused file; data older than the account's) |
| Apply | `held_away._apply_import`: replaces the account's live positions as of the import's date, lots, transactions, provenance (`held_away_records`), source last as-of, confirmations expire, `_decide_row` (system axis), audit `held_away.import_approved`; then the family rollup (`portfolio_refresh.after_write`) |
| DB | `held_away_imports_maker_checker_chk`: `approved_by IS NULL OR approved_by <> entered_by` |

Permission keys: `view_authority` / `manage_authority` / `approve_authority`; `view_held_away` / `manage_held_away` /
`approve_held_away`. Super Admin first everywhere (`services.authz`; `rbac.has_permission` inside the task).

## 1b. Where pending items are listed today — the ~5 clicks

* **Task inbox** — the dashboard's "My tasks" (`components/dashboard/MyTasks.jsx`, `GET /human-tasks/mine`): one line
  per open task, linking to the subject's own screen. No decision there.
* **Authority** — Household → tab Authority, or the account page's Authority panel (`components/authority/AuthorityPanel.jsx`):
  the History table, a note box and Approve / Reject per row.
* **Held-away** — the account page's held-away panel (`components/heldaway/HeldAwayPanel.jsx`): the imports list, one
  import at a time.

Per item today: (1) open My tasks / the household, (2) click the task (navigates to the household or account),
(3) open the Authority tab (or scroll to the panel), (4) find the row in History, (5) Approve — then back to the inbox
for the next. There is no list action anywhere.

## 1c. Other maker-checker types that could reuse the screen later — LISTED ONLY, NONE BUILT

Every kind registered with `HT.register_kind(..., outcomes=("approve", "reject"), maker_checker=True)` has the same
shape (a subject row + an approval task + a single `decide` service), so each could become another tab by adding one
entry to `services.bulk_approvals.TYPES` that points at its own single-approval function:

* Model approval (`services/models.py`, `approve_models`) — **models**
* Rebalance proposal approval / review (`services/rebalance_proposals.py`) — **proposals**
* Allocation policy approval (`services/blocks.py`) — **policies**; also block approval and allocation corrections
* Hypothetical-performance policy approval (`services/hypothetical_policy.py`) — **policies**
* Integrated-account import approval (`services/integrated_import.py`) — **integrated import**
* Account model change (`services/account_models.py`)
* Execution approval (`services/execution.py`)
* Lot-method change (`services/lot_method.py`)
* Tax profile / loss carryforward approval (`services/tax_profiles.py`)
* Substitution set / denial approval (`services/substitutions.py`)
* Tilt set approval (`services/tilts.py`)
* Charitable gift approval (`services/gifts.py`)
* Note replica approval (`services/note_replication.py`); option package approval (`services/options_sleeve.py`)

Not candidates: kinds with `maker_checker=False` or a `done` outcome (held-away data confirmation, roll / expiry
reminders, new-issue alerts, terms reviews) — they are acknowledgements, not second-person approvals. Anything in a
custody-cliff class (trade execution, money movement, filing, GL posting) stays a person's decision on its own screen;
a bulk tab for execution approval would need its own design review first.

## The one-path rule (what BULKAPPROVE1 built)

`POST /api/v1/approvals/bulk {type, item_ids, decision, note}` calls **the same service function** as the single route
(`authority.decide` / `held_away.decide_import`) once per item, in its own transaction(s). Nothing new writes a
setting or a position. Every rule above applies unchanged, per item; one item's refusal never stops or undoes another.
Per item an `approvals.bulk_item` audit row (result + reason, refusals included); per request one `approvals.bulk`
row (who, how many, which ids). Cap: 200 ids per request (`services.bulk_approvals.MAX_ITEMS`).
