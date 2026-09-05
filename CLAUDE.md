# 2nd Act Capital / Hollisworks — Claude Code Rules

## Stack
- Frontend: Next.js 16, App Router, Tailwind — deployed on Vercel
- Backend: FastAPI Python, asyncpg — deployed on Render
- Database: Supabase Postgres + PgBouncer (project `mmgwmcinimzuhargsazs`)
- Auth: Auth0 @auth0/nextjs-auth0 v4, proxy.js — TWO tenants (2nd Act's own,
  and a separate Hollisworks platform tenant for `admin.hollisworks.com` —
  selected per-request by `getAuthClientForHost(host)`, never assumed)
- Storage: Cloudflare R2 (boto3)
- AI: routed through a central LiteLLM proxy (self-hosted Render service,
  `hollisworks-litellm`) — see "AI Provider Abstraction" below, this is no
  longer a future migration, it is live
- Secrets: Doppler (`hollisworks` project, `prd`/`dev` configs) — see
  "Secrets — Doppler Is the Only Source of Truth" below
- Monorepo: apps/web and apps/api

## Secrets — Doppler Is the Only Source of Truth

**Never read a secret from a local `.env` file, `~/.bashrc`, or a hardcoded
value.** Every command that needs real credentials runs through Doppler:

```bash
doppler run -- python scripts/whatever.py
doppler run -- npm run dev
```

First-time setup in a fresh shell: `doppler login`, then `doppler setup`
(select project `hollisworks`, config `prd` or `dev`).

**A secret value drifting between Doppler and wherever it's actually used has
caused multiple real production incidents.** If a script reports
"password authentication failed" or similar, the FIRST question is whether
this specific service is actually reading from Doppler at all, or from a
stale local fallback — verify with:

```bash
doppler run -- python3 -c "import os; k=os.environ['SOME_VAR']; print(repr(k)); print(len(k))"
```

`repr()` catches hidden whitespace/newlines that look identical by eye.

**A Render service does not automatically sync with Doppler just because
another service in the same project does.** Each service needs its own,
explicit sync connection in Doppler's integration settings. Confirm a new
service actually has one before assuming it's receiving secrets.

**Secret referencing**: a rotatable value (e.g. a database password) should
be its own secret, referenced from other secrets via `${SECRET_NAME}` —
never hand-typed into multiple connection strings. Rotate once, everything
using the reference updates automatically.

**One value is permanently un-rotatable**: any encryption/salt key protecting
data-at-rest (e.g. `LITELLM_SALT_KEY`) must be backed up in a separate,
durable location outside Doppler at creation time. Losing it or changing it
after first use makes encrypted data permanently unrecoverable.

## Database Schema Namespacing — Read This Before Writing Any Query

**`portfolio` and `litellm` are real, separate Postgres schemas — neither is
on `app_service`'s (or any application role's) default `search_path`.**
This has caused repeated, real production bugs across this project. Always
schema-qualify: `portfolio.positions`, not `positions`.

If a NEW schema is ever introduced for a new subsystem, either schema-qualify
every reference to it in application code, OR set the search_path at the
ROLE level once (`ALTER ROLE some_role SET search_path = some_schema,
public;`) — the role-level fix is more robust, since it also covers any raw
SQL a third-party library issues internally without qualification (this
exact class of bug hit LiteLLM's own internal background jobs).

**Dedicated, least-privilege roles per subsystem, not the `postgres`
superuser.** `app_service` (main app), `litellm_service` (LiteLLM's own
schema only) are separate roles, each scoped to what they actually need.
Follow this pattern for any new subsystem needing its own database access.

## Design Tokens — Never Change
Navy #1B2B4B | Gold #C5A880 | Gold Light #E8D5A3
BG App #FAF9F6 | BG Sidebar #F5F1EB
BG Card #FFFFFF | Text #0F172A / #334155 / #64748B
Border #E2E8F0 | Error #9B2335 | Success #2D6A4F
Base font: 17px. Always light theme — no dark mode, ever, anywhere.

## Rule 1 — Never Hardcode Display Data
All labels (taxonomy, stages, statuses, dimensions) come from the config
table or the API's own response envelope. Never hardcode them in frontend or
backend. Config categories: asset_taxonomy, deal_scoring, deal_stages,
investment_stages, document_statuses.

**This extends to every permission-gated UI screen**: a grid's editable-field
list, sort/filter vocabulary, and permission flags (`can_read`/`can_write`)
must come from the server's own response — never a client-side default or
fallback list. See "Permission Envelope Pattern" below.

## Rule 2 — PgBouncer (CRITICAL)
Add `statement_cache_size=0` to EVERY asyncpg connection and pool — no
exceptions:

```python
conn = await asyncpg.connect(DATABASE_URL, statement_cache_size=0)
pool = await asyncpg.create_pool(
    DATABASE_URL, statement_cache_size=0, min_size=1, max_size=10
)
```

Missing this causes `DuplicatePreparedStatementError`.

**Same underlying issue, different tool**: if a third-party service (e.g.
Prisma, used internally by LiteLLM) connects through Supabase's transaction
pooler (port 6543), it also cannot use prepared statements. Fix there is
`?pgbouncer=true` on the connection string. Migrations specifically may
still fail against the transaction pooler even with this flag (advisory
locks need a stable session); if so, run the migration once against a
different, stable connection, then disable future migrations on that
service entirely rather than fighting the pooler repeatedly.

## Rule 3 — Bi-temporal Writes
Never update a row in place. Two real axes exist — use the right one:

**Valid-time restatement** (most tables): the row's own id changes on every
correction.
```sql
UPDATE table SET valid_to = now() WHERE [natural key] AND valid_to IS NULL;
INSERT INTO table (..., valid_from) VALUES (..., now());
```

**System-time archival** (tables with real, live foreign keys pointing at
their id — e.g. `portfolio.assets`, referenced by positions and valuations):
the row's id is KEPT stable; only `system_from`/`system_to` change. Using
valid-axis restatement here would orphan every FK reference. Check whether
anything references a table's `id` before choosing which axis applies.

## Rule 4 — Taxonomy Keys
Deals store taxonomy keys not labels. Resolve labels server-side at read
time. Key patterns:
```
taxonomy_sc_{n}            → super_class
taxonomy_mc_{sc}_{mc}       → major_class
taxonomy_sub_{sc}_{mc}_{n}  → sub_category
```

## Rule 5 — Auth Pattern
Server components: `auth0.getSession()` — but this must be **host-aware**.
There are two real Auth0 tenants; use `getAuthClientForHost(host)`, never a
single hardcoded client, for both login AND every subsequent session check
on every protected page. A page checking its session with the wrong tenant's
client produces an infinite redirect loop — this has happened in production.

Client components: call Next.js API routes which handle auth server-side.
Never call FastAPI directly from client components — always via Next.js API
routes, which then forward server-side with no `org_id` on the client's side
of that forward.

## Rule 6 — Org ID
Default org (2nd Act): `00000000-0000-0000-0000-000000000001`
Hollisworks platform org: `bb347258-8f28-4f49-8cc9-e29ccad82884`
All tables have `org_id`. All queries scope to it. **`org_id` is NEVER read
from a request body or a path parameter — only from the caller's own
verified session/JWT context (`get_org_id(request)`).** A request model that
even *declares* an `org_id` field is itself a bug; use `extra='forbid'` on
Pydantic models to make this mechanical, not just a code-review habit.

**Platform-level settings** (things Hollisworks itself configures, not any
one client org) use `owner_scope: 'platform'` with a NULL org_id, not the
default org id as a stand-in for "platform-wide."

## Permission Envelope Pattern (established across every UI sprint)

Every permission-gated API response publishes an envelope, not just data:
```json
{
  "rows": [...],
  "permissions": {
    "can_read": true, "can_write": false,
    "is_super_admin": false,
    "read_permission": "view_x", "write_permission": "manage_x"
  },
  "vocabularies": { "editable": [], "inline_editable": [] }
}
```

`editable`/`inline_editable` are **empty arrays** for a view-only caller —
never omitted, never a client-side default. The frontend's write controls
render ONLY inside a `permissions.can_write` (or equivalent) check, with
**no truthy fallback** (`|| DEFAULTS` is exactly the anti-pattern that
silently restores full write access if the envelope is ever missing for an
unrelated reason — a lost envelope must fail CLOSED).

Every write-gated feature needs BOTH proofs, independently:
1. Server-side: the API genuinely refuses the write (403, naming the missing
   permission) when attempted directly, bypassing the UI.
2. Client-side: the UI genuinely renders no control for it — checked by
   feeding the real envelope into the component's own render logic, not
   inferred from the server-side proof alone.

A hidden button behind an unprotected endpoint, and a protected endpoint
behind a visible button, are both real bugs — proving only one side proves
nothing about the other.

Super-admin bypass is checked FIRST, before any granular permission lookup,
via ONE shared helper (`is_super_admin` / `rbac.has_permission`) — never a
second, local re-implementation of the same check.

## Dual-Path Permission Resolution (most-restrictive-wins)

Established by the CRM UDF module (six sprints, 462 assertions, all merged)
for field-level security binding to BOTH `profile_permissions` and
`permission_set_permissions` simultaneously — a real, recurring shape
whenever access needs to resolve across two independent permission paths
rather than one.

The reusable functions: `resolve_field_access_bulk` / `resolve_tab_visibility`
(`services/portfolio_udf*.py`). The rule: when the two paths disagree,
the MORE RESTRICTIVE result always wins — a field marked `edit` on one path
and `hidden` on the other resolves to `hidden`, never split the difference
or let either path unilaterally grant access the other denies.

Reuse this pattern (or these functions directly, if the shape genuinely
matches) anywhere else in the platform that needs to bind one resource's
access to two independent, potentially-conflicting permission sources.
Do not write a second, bespoke dual-path resolver — check here first.

## AI Provider Abstraction

All AI calls route through the central `call_claude_text` / `call_claude_json`
/ `call_claude_with_tools` helpers, which call a **LiteLLM proxy**
(`hollisworks-litellm` on Render), not the Anthropic SDK directly. This
centralization is what made the LiteLLM migration a config change rather
than an application refactor — confirmed live: all production AI call sites
are a single chokepoint in `services/extraction.py`.

**A real rollback exists**: setting `LITELLM_ROUTING_DISABLED=1` reverts to
calling Anthropic directly, bypassing LiteLLM entirely, for use if the proxy
has a problem. This is separate from, and simpler than, the future
Hollis-admin-facing "force Anthropic" UI toggle (design-only, not yet built).

**Model selection**: per-org, per-task model assignment (which model handles
which real task) is a real, planned feature — NOT yet built. Do not assume
an org can currently choose its own models; this is still hardcoded via
`org_settings`' `ai.model.fallback_chain` key.

### User-facing privacy claims — HOLD
Do NOT surface any "nothing leaves 2nd Act", "fully private", "data never
leaves", or similar privacy claims in user-facing copy until the commercial
+ Zero Data Retention arrangement is signed and verified. Use neutral
language like "your private AI assistant."

## Schema Source of Truth
Before writing ANY SQL or query in a sprint, read `docs/schema_snapshot.sql`
— a live introspection of the deployed database, regenerated at the start of
each sprint. Use exact column names from it. Never infer column names from
the sprint prompt, from a dataclass's field names, or from memory. If a
table is not in the snapshot, it is not deployed yet. Column-name drift
between a prompt's assumption and the deployed schema has caused repeated
real bugs — the snapshot is the fix, and it must be re-read, not assumed
current from an earlier sprint in the same session.

## Standard Sprint Structure (four parts)

**Part 1 — Schema, applied directly.** If a sprint needs new tables/columns/
roles with a KNOWN shape, apply the SQL directly (e.g. via the Supabase MCP
tool) before the sprint runs, and VERIFY it actually landed with a real
follow-up query — never trust a "success" response alone. If the correct
shape genuinely depends on discovery the sprint itself needs to do (e.g. an
unclear join path), leave Part 1 empty deliberately and let the sprint's own
Task 1 inform the schema — do not guess and apply blind.

**Part 2 — Branch + schema refresh.** Fetch, checkout the working branch,
merge origin, refresh `docs/schema_snapshot.sql`.

**Part 3 — The sprint prompt itself**, structured as: DISCOVER (report real,
measured findings — not quoted from the prompt — then continue immediately,
never stop and wait, since there is no human available mid-sprint), BUILD,
PROVE (real proof, see below), UPDATE PROJECT STATUS.

**Part 4 — Merge.** `.lowrisk` sprints (discovery-only, or genuinely
low-stakes) can auto-merge. `.structural` sprints (schema changes,
permission changes, anything touching money/auth/tenant boundaries) are
HELD for manual review — read the verify log, confirm the reasoning holds,
then merge deliberately.

**If a sprint's wrapper reports "expected verify script not found"** — this
is often a wrapper mismatch (e.g. a discovery-only sprint with no code to
verify), not a sign the work was lost. Check `git log --oneline -3` and
`git status` before assuming anything needs re-running — the real work
frequently already committed successfully even when the wrapper's own
report looks like a failure.

## Verify Script Discipline

This is the actual, hard-won standard — read this before writing a new
`verify_*.py`, not just the mechanical rules below it.

**Real proof, never assumed behavior.** Don't just check that a function
returns without error — check that a WRITE actually persisted (re-read from
an independent connection), that a REFUSAL actually left data unchanged
(before/after comparison), that a FILTER actually narrows (non-empty AND a
strict subset AND matches the same predicate run directly in SQL) rather
than merely "didn't error."

**Prove the negative case as rigorously as the positive one.** A filter that
returns everything, and a filter that returns nothing, both pass a naive
"got some results" check. Prove both directions: inclusion AND exclusion,
on the same real dataset.

**Reproduce the bug before proving the fix.** When fixing a real, reported
issue, the verify script should first reproduce the ORIGINAL failure
(pinning whatever condition caused it), THEN show the fix resolves it. A
fix proven only in isolation, never shown to actually address the reported
symptom, isn't fully proven.

**Report discovery findings honestly, including "no gap found."** A
discovery task that's allowed to come back empty and genuinely finds nothing
should say so plainly — inventing speculative work to justify a sprint's
existence is worse than a small, honest result.

**Cross-org isolation must be tested under a genuinely non-bypassing role**,
never the application's own connection if that connection has RLS-bypass
privileges. Confirm the test role's `rolbypassrls=False` as its own explicit
assertion — otherwise every isolation check downstream proves nothing.

**Teardown is by-fixture-tag plus an exact before/after row count as the
backstop — never an unconditional TRUNCATE.** Most tables hold real
production data by the time a sprint touches them; a truncate is a
data-loss bug waiting to happen the moment that becomes true.

**A `[FIND]` is a real, worth-recording discovery distinct from PASS/FAIL**
— use it for things like "this assumption in the prompt turned out to be
wrong, here's what's actually true" or "this negative behavior is a
deliberate, correct design choice, not a bug." Don't bury a real finding
inside a bare PASS line.

**Seed a real test user before tests** (or the project's current equivalent
pattern — confirm against `docs/schema_snapshot.sql`, this convention
predates the multi-tenant/Doppler work and the exact seed shape may need
reconfirming for a new sprint).

## Schema Notes

### member_target_allocations — Partial Unique Index
The uniqueness constraint on (entity_id, taxonomy_key) is a PARTIAL unique
index covering only active rows:
```sql
CREATE UNIQUE INDEX member_target_allocations_active_unique
  ON member_target_allocations (entity_id, taxonomy_key)
  WHERE valid_to IS NULL;
```
Allows unlimited historical rows with the same pair — only one active row
per pair is enforced.

### entity_type enum
Extended with `household`, `account`, `spv` (each added deliberately, each
with its own real reason — `account` and `spv` are OPERATIONAL entity types
deliberately excluded from CRM-visibility surfaces by default).

## Reference Data
See `docs/reference.md` for seed entity UUIDs, role UUIDs, entity type enum
values, and sprint history. See `docs/PROJECT_STATUS.md` for current build
status across every major subsystem. See `docs/OUTSTANDING_TODO_LIST.md` for
the current, real list of unfinished work.

## Major Subsystem Design Docs
- `docs/PORTFOLIO_REPORTING_DESIGN_V6.md` — positions, transactions,
  securities, corporate actions, UDFs (built, phases A1–G, all merged)
- `docs/LITELLM_INTEGRATION_DESIGN_V1.md` — AI gateway (Phase A deployed,
  Phase B merged; see design doc §13.5 for real deployment gotchas before
  touching this service again)
- `docs/WORKFLOW_SCHEDULER_DESIGN_V1.md` — RRULE-based scheduling (built,
  all sprints merged)

## Brand System

Private membership platform for post-liquidity founders/operators. Premium
private club, not a fintech startup. Understated luxury, discretion, earned
trust. Light/cream UI only — no dark mode.

### Typography
- Display/headings: `'Spectral', Georgia, serif` (300–600 + italic)
- Body/UI: `'Hanken Grotesk', system-ui, sans-serif`, base 17px

### Patterns
App background is cream; cards are white with a `1px solid #ece8dd`
hairline and `6px` radius — no heavy shadows, no left-accent-border cards.
Copy voice: quiet, precise, no hype — *members / allocation / co-invest /
discretion*, never *users / unlock / supercharge*. No emoji.

### Avoid
Fintech aesthetics, gradients, heavy law-firm serifs, dollar-sign/bar-chart
iconography, dark mode.
