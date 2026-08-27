FEE MODULE — SPRINT fee32 (position linkage + household precedence).
3 tasks + verification. Extends fee31's account layer to connect with
the already-shipped Portfolio Reporting Layer, per the RFC exchange
with the Portfolio Reporting Layer thread. Part 1 SQL (account_id on
portfolio.positions, portfolio_precedence_household_overrides) is
already applied by Joe directly via Supabase MCP — confirm it live
before writing any code, do not re-create it.

CRITICAL CONTEXT FROM THE RFC EXCHANGE — do not re-derive these,
they are settled:
- account_id is OPTIONAL on positions. Directly-held assets (real
  estate, direct PE) and SPV interests (identified via
  portfolio.assets.internal_spv_id) stay NULL. Do not backfill or
  require it.
- Consistency between a position's account_id and its owner_entity_id
  (must the entity be one of that account's account_owners?) is
  validated in APPLICATION CODE at write time, matching how
  ownership_basis is already validated on this table. Do NOT add a
  DB trigger or CHECK constraint for this.
- The existing precedence mechanism at
  apps/api/services/portfolio_precedence.py (function
  resolve_precedence(conn, org_id, ids, apply)) is NOT being
  replaced. It is being extended with one more, more-specific level
  ahead of its existing org_settings['portfolio.precedence.source_order']
  lookup and DEFAULT_SETTINGS fallback. Read that file's actual
  current signature and docstring before touching it — this prompt's
  description of it may already be slightly stale relative to the
  real file.
- Joint-custody positions being split into multiple owner rows on
  import is a KNOWN, CONFIRMED GAP (per the Portfolio Reporting Layer
  thread) and is explicitly OUT OF SCOPE for this sprint.

STANDING RULES: org_id never from request bodies. Decimal for money.
No interactive prompts in scripts. Additive-first migrations only.
`portfolio` schema is not on the default search path — schema-qualify
every reference to it explicitly, since this has been a real,
recurring bug source in this codebase per the Portfolio Reporting
Layer thread's own note.

=== TASK 1: Discover, don't assume ===
Read the REAL, current versions of:
  apps/api/services/portfolio_precedence.py (full function signature,
    DEFAULT_SETTINGS shape, exact org_settings key name, how it
    currently derives which rows are "the same holding" to resolve
    between)
  apps/api/services/portfolio_import.py (the resolve_precedence_after
    call site, around line 596 — confirm the current line number and
    call shape, prompt line numbers may have drifted)
  apps/api/routers/portfolio_ingest.py (resolve_precedence_endpoint)
Also query live: portfolio.positions columns (confirm account_id is
present per Part 1), public.accounts / public.account_owners /
public.households columns, and the new
portfolio_precedence_household_overrides table's actual deployed
shape. Report all of this before writing any code. If anything here
conflicts with this prompt's description, the live code and schema
win — flag the conflict and proceed from what's actually there.

=== TASK 2: Position <-> account linkage, validated in application code ===
Wherever positions get written or updated with an account_id set
(likely the reporting-tool import path in portfolio_import.py, and
any admin/manual position-edit path), add a validation step: if
account_id is set, the position's owner_entity_id must appear in
that account's active account_owners. This is a WARNING/exception
path, not a hard rejection — write the position, but surface the
mismatch (reuse the account_import_exceptions pattern from fee31 if
there's a natural fit, or a comparable exception record if not) so
it's reviewable rather than silently accepted or silently blocking a
real import. Do not add a database trigger or CHECK for this.

=== TASK 3: Household-level precedence override ===
Extend resolve_precedence() (or wrap it, whichever is the smaller,
more honest change once you've read the real function in Task 1) so
that resolution order is:
  1. An active row in portfolio_precedence_household_overrides for
     the household derived from the position(s) being resolved
     (prefer account_id -> accounts.household_id when account_id is
     set; else owner_entity_id -> entities.primary_household_id)
  2. The existing org_settings['portfolio.precedence.source_order']
     lookup, unchanged
  3. The existing DEFAULT_SETTINGS fallback, unchanged
Preserve the existing non-destructive superseded_by_source behavior
exactly — a household override changing the winner should correctly
flip superseded_by_source on the old winner and clear it on the new
one, same as changing the org-level order already does today.
A household with no override row must resolve identically to how it
resolves today — this task must not change behavior for any household
that doesn't have an override.

=== VERIFICATION ===
Write scripts/verify_fee32.py — pass/fail only, no interactive
prompts. Use app_service, not the MCP bypass connection, for any RLS
check. Follow this project's teardown discipline: prove each
assertion with real fixture writes, then restore the exact
before/after row count on every table touched.
Assert:
  1. portfolio.positions.account_id exists, nullable, FK to
     public.accounts, and a position with account_id=NULL still
     inserts and resolves precedence with no error (directly-held
     asset case).
  2. A position with account_id set to an account whose owners do
     NOT include that position's owner_entity_id is written (not
     rejected) but produces a reviewable exception record.
  3. A position with account_id set to an account whose owners DO
     include the owner_entity_id produces no exception.
  4. A household WITH an active override in
     portfolio_precedence_household_overrides resolves to the
     override's source_order, correctly overriding what the org-level
     setting would otherwise pick — proven with two conflicting
     source_system rows on the same (owner_entity_id/account_id,
     asset_id, as_of_date) where org order and household order
     disagree on the winner.
  5. A household WITHOUT an override resolves identically to the
     pre-existing org-level/DEFAULT_SETTINGS behavior — prove this
     with a before/after comparison against the same fixture run
     without this sprint's changes, not just an assertion that it
     "looks right."
  6. superseded_by_source correctly flips when a household override
     is added, changed, and removed (three states, not just one).
  7. Cross-org isolation on portfolio_precedence_household_overrides
     via app_service, same pattern as fee31 check 5.
  8. No table's row count differs from its pre-test count after the
     script exits (teardown proof).
Report actual results, then stop. Do not proceed further in this run.
