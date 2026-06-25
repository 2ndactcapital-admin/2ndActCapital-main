# 2nd Act Capital — Claude Code Rules

## Stack
- Frontend: Next.js 16, App Router, Tailwind
- Backend: FastAPI Python, asyncpg
- Database: Supabase Postgres + PgBouncer
- Auth: Auth0 @auth0/nextjs-auth0 v4, proxy.js
- Storage: Cloudflare R2 (boto3)
- AI: Anthropic API (claude-haiku-4-5-20251001)
- Deploy: Vercel (web), Render (api)
- Monorepo: apps/web and apps/api

## Environment Variables
These are available in the shell environment:

SUPABASE_URL — Supabase project URL
SUPABASE_SERVICE_ROLE_KEY — service role key
  for schema introspection (bypasses RLS)
DATABASE_URL — direct Postgres connection
  with PgBouncer (always use 
  statement_cache_size=0)
ANTHROPIC_API_KEY — set per session when 
  needed for AI features

Before writing any new endpoint or verify 
script, introspect the actual table schema:

  import asyncpg, os
  conn = await asyncpg.connect(
      os.environ['DATABASE_URL'],
      statement_cache_size=0
  )
  rows = await conn.fetch("""
      SELECT column_name, data_type
      FROM information_schema.columns
      WHERE table_name = $1
      AND table_schema = 'public'
      ORDER BY ordinal_position
  """, 'your_table_name')
  for r in rows:
      print(f"{r['column_name']} ({r['data_type']})")

## Design Tokens — Never Change
Navy #1B2B4B | Gold #C5A880 | Gold Light #E8D5A3
BG App #FAF9F6 | BG Sidebar #F5F1EB
BG Card #FFFFFF | Text #0F172A / #334155 / #64748B
Border #E2E8F0 | Error #9B2335 | Success #2D6A4F
Base font: 17px

## Rule 1 — Never Hardcode Display Data
All labels (taxonomy, stages, statuses, 
dimensions) come from the config table via API.
Never hardcode them in frontend or backend.
Config categories:
  asset_taxonomy, deal_scoring, deal_stages,
  investment_stages, document_statuses

## Rule 2 — PgBouncer (CRITICAL)
Add statement_cache_size=0 to EVERY asyncpg
connection and pool — no exceptions:

  conn = await asyncpg.connect(
      DATABASE_URL,
      statement_cache_size=0
  )
  pool = await asyncpg.create_pool(
      DATABASE_URL,
      statement_cache_size=0,
      min_size=1, max_size=10
  )

Missing this causes DuplicatePreparedStatementError.

## Rule 3 — Bi-temporal Writes
Never update a row in place. Always:
  Step 1: Close old row
    UPDATE table SET valid_to = now()
    WHERE [natural key] AND valid_to IS NULL;
  Step 2: Insert new row
    INSERT INTO table (..., valid_from)
    VALUES (..., now());

## Rule 4 — Taxonomy Keys
Deals store taxonomy keys not labels.
Resolve labels server-side at read time.
Key patterns:
  taxonomy_sc_{n}           → super_class
  taxonomy_mc_{sc}_{mc}     → major_class
  taxonomy_sub_{sc}_{mc}_{n} → sub_category

## Rule 5 — Auth Pattern
Server components: auth0.getSession()
Client components: call Next.js API routes
  which handle auth server-side.
Never call FastAPI directly from client
components — always via Next.js API routes.

## Rule 6 — Org ID
Default org: 00000000-0000-0000-0000-000000000001
All tables have org_id. All queries scope to it.

## Verify Script Standards
Every sprint needs apps/api/scripts/
verify_sprint{N}.py following these rules:

1. statement_cache_size=0 on all connections
2. Seed test user before tests:
     id: 99000000-0000-0000-0000-000000000001
     auth0_sub: 'auth0|test_verify_user'
     ON CONFLICT (auth0_sub) DO NOTHING
3. Teardown in try/finally, FK-safe order
   (delete child tables before parents —
   see docs/reference.md for full order)
4. ON CONFLICT DO NOTHING on test inserts
5. Bi-temporal test: close old row first,
   then insert new (Rule 3 above)
6. Skip gracefully when env vars missing:
     if not os.environ.get('ANTHROPIC_API_KEY'):
         print('[N] SKIP — key not set')

## Schema Introspection Before Writing Code
Before writing any endpoint or verify script
that touches a new or modified table, run
this to confirm actual column names:

  SELECT column_name, data_type
  FROM information_schema.columns
  WHERE table_name = 'your_table'
  AND table_schema = 'public'
  ORDER BY ordinal_position;

Use asyncpg with statement_cache_size=0.
Fix code to match schema — never guess.

## Schema Notes

### member_target_allocations — Partial Unique Index
The uniqueness constraint on (entity_id, taxonomy_key)
is a PARTIAL unique index covering only active rows:

  CREATE UNIQUE INDEX
    member_target_allocations_active_unique
  ON member_target_allocations (entity_id, taxonomy_key)
  WHERE valid_to IS NULL;

This replaced the earlier full constraint:
  member_target_allocations_entity_taxonomy_unique

The partial index allows unlimited historical rows with
the same (entity_id, taxonomy_key) — only one active
row (valid_to IS NULL) per pair is enforced.
Migration: Sprint 8 (applied 2026-06-25).

### entity_type enum
The Postgres entity_type enum was extended with:
  ALTER TYPE entity_type ADD VALUE IF NOT EXISTS 'household';
Migration: Sprint 8 (applied 2026-06-25).

## Reference Data
See docs/reference.md for:
- Seed entity UUIDs
- Role UUIDs
- Entity type enum values
- Sprint history
