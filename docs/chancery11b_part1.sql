-- ─────────────────────────────────────────────────────────────────────────
-- Chancery Phase 11b — Part 1 SQL — semantic INDEX + RETRIEVE (Voyage + pgvector)
-- ─────────────────────────────────────────────────────────────────────────
-- One pgvector-typed, org-scoped table holding ONE embedding per document. RLS
-- is applied IN THIS SAME migration (org-isolation + super-admin bypass), the
-- identical shape used for every table this session (see docs/rls2_part1.sql).
--
-- Dimension is FIXED at 1024 — Voyage's real current output dimension (verified
-- live at Task 1: voyage-3.5 / voyage-law-2 / voyage-finance-2 all return 1024).
-- Voyage is the only functionally-enabled provider right now, so the column is
-- sized to it. Multi-provider dimension normalization is a real problem for
-- whenever a SECOND provider is actually enabled — deliberately not solved here.
--
-- pgvector is already enabled (extension `vector` v0.8.0, schema public) — this
-- migration does NOT re-create it; it only depends on it.
-- ─────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS public.document_embeddings (
    id             uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
    document_id    uuid NOT NULL REFERENCES public.documents(id) ON DELETE CASCADE,
    org_id         uuid NOT NULL,
    provider       text NOT NULL,             -- the org's configured provider (voyage today)
    model          text NOT NULL,             -- the concrete embedding model used
    dimensions     integer NOT NULL,          -- must equal vector length (1024)
    content_source text NOT NULL,             -- which text was embedded (narrative/template/extracted_text)
    content_chars  integer NOT NULL DEFAULT 0,
    embedding      vector(1024) NOT NULL,
    created_at     timestamptz NOT NULL DEFAULT now(),
    updated_at     timestamptz NOT NULL DEFAULT now(),
    -- One active embedding per document; re-embedding upserts on this key. NOT
    -- bitemporal (mirrors documents / org_settings — no valid_to here).
    CONSTRAINT document_embeddings_document_id_key UNIQUE (document_id)
);

CREATE INDEX IF NOT EXISTS document_embeddings_org_id_idx
    ON public.document_embeddings (org_id);

-- Approximate-NN index for cosine similarity. HNSW builds fine on an empty table
-- and the planner falls back to an exact scan on tiny tables, so recall is exact
-- for small orgs and scalable for large ones.
CREATE INDEX IF NOT EXISTS document_embeddings_embedding_hnsw
    ON public.document_embeddings
    USING hnsw (embedding vector_cosine_ops);

-- ── RLS — org isolation + super-admin bypass (same pattern as every table this
--    session; every GUC read is NULLIF(..., '')-guarded so a reused pooled
--    connection default-denies instead of erroring on ''::uuid). ─────────────
ALTER TABLE public.document_embeddings ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS document_embeddings_org_isolation ON public.document_embeddings;

CREATE POLICY document_embeddings_org_isolation
    ON public.document_embeddings
    FOR ALL
    USING (
        org_id = NULLIF(current_setting('app.current_org_id', true), '')::uuid
        OR current_setting('app.is_super_admin', true) = 'true'
    )
    WITH CHECK (
        org_id = NULLIF(current_setting('app.current_org_id', true), '')::uuid
        OR current_setting('app.is_super_admin', true) = 'true'
    );

-- The non-bypass application role used once the connection is switched off the
-- RLS-bypassing `postgres` role (a separate, manual step — not this sprint).
GRANT SELECT, INSERT, UPDATE, DELETE ON public.document_embeddings TO app_service;
