# Hollis Fix — closing the cross-member gap

Companion to `HOLLIS_DISCOVERY.md`. That sprint was read-only and found the
gap; this one closes the part of it that has one correct answer, and records
the part that does not.

Verify: `python3 apps/api/scripts/verify_hollisfix.py` (Doppler `prd`, real
non-bypassing `app_service` role — the script asserts `rolbypassrls = false`
before it claims anything).

## What the discovery sprint got right

Re-checked against the working tree, not taken on trust:

- `portfolio.show_allocation`, `portfolio.find_my_investment` and
  `entity_graph.show_hierarchy` scoped by `org_id` and never consulted the
  caller. Confirmed by reading all three handlers.
- `required_permission=None` on all three. Confirmed.
- The schema drift on `member_investments` is real, and provable without
  touching the database: `routers/marketplace.py`'s `MEMBER_INVESTMENT_SELECT`,
  `routers/portfolio.py:159,185` and `queries.py:177,190` all use
  `investment_stage` / `amount_committed` / `user_id`. Only
  `assistant_actions/portfolio.py` used `status` / `current_stage` /
  `committed_amount` / `currency`, and `currency` exists on no version of that
  table anywhere in the repo.
- `list_for_user` filters on `required_permission` only; `tier` is never read.
  `confirm_action` checks `access_type` and `required_permission` and nothing
  else. Both confirmed.
- No `agent_key` / `hollis` string anywhere in the loop or the registry.
  Confirmed — there is one generic registry and one generic loop.

## What it understated

**`confirm_action` trusts the entire client-supplied `proposed_action`.**
`ConfirmBody.proposed_action` is a raw dict from the request body, and
`action_key` and `params` are both read straight out of it. The LLM is not in
this path at all — a member can hand-roll `POST /assistant/confirm` with any
action key and any parameters. For `crm.draft_note`, which carries
`required_permission=None`, that was an unauthenticated-by-anything write of
arbitrary text onto an arbitrary `entity_id`. The discovery doc treats the write
surface as something the model has to be talked into; it is not.

**`spv.subscribe`'s amend path supersedes other members' commitments.**
`_execute_subscribe` closes any existing active subscription for
`spv_id + entity_id` (`SET valid_to = now()`) before inserting. Unscoped, that
is not merely a spurious commitment — it silently retires someone else's real
one. Note the lookup and the UPDATE carry no `org_id` predicate either;
cross-org is held by RLS, cross-member was held by nothing.

**`is_staff` defaults to True.** `services/permissions.py:68-76` returns True
when the token carries no roles claim. Every gate in the platform that branches
on it — including the ones added here — takes the staff branch for a rolesless
caller. This is a platform-wide auth default, untouched by this sprint, and it
caps how much any of these gates are worth.

## What changed

New `services/assistant_actions/_visibility.py` — the composition that was
already correct in `queries.py`, lifted out of that module's private scope so
the rest of the package can reach it, plus an entity-level
`assert_entity_visible`. The engines are unchanged: staff →
`get_staff_visible_entity_ids`, member → `get_delegate_visible_entity_ids`,
both wrapped by `filter_restricted`. Refusal semantics follow
`services/ownership_tree.py:member_tree` — refuse, never widen — and the refusal
text is identical for "not yours" and "no such entity" so the gate is not an
org-membership oracle.

Gated against the caller's visible set (every one of these takes a
caller-supplied `entity_id`):

| handler | path | note |
|---|---|---|
| `portfolio._show_allocation` | read | gates the selector root; `aggregate_allocation` still takes `org_id` only |
| `entity_graph._show_hierarchy_handler` | read | |
| `entity_graph._link_ownership_draft` | draft | the preview leaked `display_name` |
| `entity_graph._link_ownership_handler` | write | both endpoints of the edge |
| `crm._draft_note_preview` | draft | |
| `crm._save_note` | **write** | the load-bearing one — see `confirm_action` above |
| `spv._preview_subscribe` | draft | |
| `spv._execute_subscribe` | **write** | capital commitment |

`portfolio._find_investment` is scoped by `member_investments.user_id` for every
caller, staff included: the action is "find *my* investment", and `user_id` is
the same key `routers/portfolio.py` and `routers/marketplace.py` already use.
Its SQL now names the columns that exist. The output keys are unchanged so
`InvestmentCard`'s props keep their shape; `currency` reports `None` rather than
inventing a value.

`routers/assistant.py:confirm_action` now threads `is_staff` into the confirm
handler — the message loop already did this for reads, the confirm path did not,
so every write-side gate would otherwise have taken the staff branch — and maps
`PermissionError` to 403 rather than letting it surface as a 500.

`queries.py` re-exports the shared helper under its old private name; behaviour
is byte-for-byte what it was.

## Expect members to see less, immediately

`delegate_grants` had **zero rows** for org 1 at discovery time. Under the
correct engine every real member's visible set is empty, so these actions will
refuse for members until grants exist. That is the engine reporting the truth —
`entities.count` and `investments.count` have behaved this way since they
shipped. The prior behaviour, handing any member all 34 org entities, was the
bug, not the baseline. **If the assistant is meant to be usable by members
before grants are backfilled, the fix is to backfill `delegate_grants`, not to
loosen the gate.**

## Deliberately not fixed

Each of these is a design decision with more than one defensible answer, not a
bug:

1. **No maker-checker on the write surface.** `spv.subscribe`,
   `spv.record_transaction`, `entity.link_ownership` and `crm.draft_note` still
   execute on the member's own single confirm. `propose()` exists and works, but
   nothing routes the other six writes through it. Deciding that "a member
   caller may only reach `propose()`" is an agent-boundary decision — and there
   is no agent boundary in the code to hang it on (no `agent_key`, no per-agent
   allowlist, `tier` stored but never read).
2. **`confirm_action` trusts `proposed_action` wholesale.** The right fix is
   probably a signed or server-held proposal rather than a client round-trip,
   which changes the frontend contract.
3. **No idempotency key.** A retried confirm still double-posts
   `spv.record_transaction`.
4. **Loop-cap exhaustion is silent.** `range(10)` falls through to a plain
   return with no signal distinguishing "finished" from "cut off", and
   `escalation_reason` lives only on `agent_proposals`.
5. **No eval gate.** Nothing measures this loop; it reached real members first.
6. `services/document_embedding.py:817` holds a fourth copy of the visibility
   composition. It is correct, so it was left alone, but it should fold into
   `_visibility.py` on the next pass through that file.

## Suggested order

1. Backfill `delegate_grants` — without it the gate is a wall for everyone.
2. Run `verify_hollisfix.py` against `prd`.
3. Decide the agent boundary (item 1 above). It is the one that makes the other
   write-path items well-posed.
