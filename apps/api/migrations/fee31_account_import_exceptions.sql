-- Sprint fee31 — ADDITIVE addendum to the Part 1 account-layer SQL.
--
-- Nothing here re-creates or alters a Part 1 table's existing columns. Two gaps
-- were found by introspecting the deployed schema (scripts/discover_fee31.py):
--
--  1. account_import_batches records unmatched_count (an integer) but has no
--     place to put the unmatched ROWS. The sprint requires that unmatched rows
--     "land in a visible exception list on the batch" and are never silently
--     dropped. A count alone cannot be reviewed, re-driven, or corrected — it
--     only tells an operator that something was lost. Hence
--     account_import_exceptions.
--
--  2. account_flows has a surrogate `id` primary key and NO unique constraint
--     of any kind, so re-importing the same file would append a second copy of
--     every flow. account_balances_daily is safe already — its PK is the
--     natural key (org_id, account_id, as_of_date, source_system) — but flows
--     had no idempotency key at all.
--
--     The naive fix, a unique index over (account, date, amount, type), is
--     WRONG: two genuinely distinct $500 deposits on the same day are real and
--     would be silently collapsed into one. Instead a nullable source_row_hash
--     column carries a per-occurrence fingerprint computed by the importer
--     (see services/custody/importer.flow_row_hash), which folds in the
--     occurrence index of that flow within its own (account, date, amount,
--     type) group. Two identical deposits get indexes 0 and 1 and both survive;
--     the same file imported twice reproduces 0 and 1 and dedupes.
--
--     Nullable + a partial index predicate on IS NOT NULL so that a flow
--     written by some future non-CSV path without a fingerprint is still
--     insertable rather than being rejected by an index it knows nothing about.

BEGIN;

-- ── 1. Flow idempotency fingerprint ───────────────────────────────────────
ALTER TABLE public.account_flows
    ADD COLUMN IF NOT EXISTS source_row_hash text;

CREATE UNIQUE INDEX IF NOT EXISTS account_flows_source_row_uq
    ON public.account_flows (org_id, account_id, source_system, source_row_hash)
    WHERE system_to IS NULL AND source_row_hash IS NOT NULL;

-- ── 2. The batch exception list ───────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.account_import_exceptions (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id        uuid NOT NULL REFERENCES public.organizations (id),
    batch_id      uuid NOT NULL REFERENCES public.account_import_batches (id),
    source_row    integer NOT NULL,
    record_kind   text NOT NULL,
    reason_code   text NOT NULL,
    reason        text NOT NULL,
    -- The offending row, ALREADY MASKED by the importer. The raw account number
    -- never reaches this column: services/custody/base.AccountNumber refuses to
    -- serialise itself unmasked, so an exception record cannot become the back
    -- door through which a full account number lands in the database.
    raw_row       jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at    timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT account_import_exceptions_record_kind_check
        CHECK (record_kind IN ('account', 'balance', 'flow')),
    CONSTRAINT account_import_exceptions_source_row_check
        CHECK (source_row >= 0)
);

CREATE INDEX IF NOT EXISTS account_import_exceptions_batch_idx
    ON public.account_import_exceptions (batch_id);

-- ── 3. RLS — the SAME policy shape as the five Part 1 tables ──────────────
-- Introspected from the deployed policies rather than written from memory:
-- one PERMISSIVE policy, cmd=ALL, role public, NULLIF-guarded org GUC plus the
-- platform's standard super-admin escape hatch. The NULLIF is load-bearing: on
-- a pooled backend a custom GUC reverts to '' rather than NULL, and a bare
-- ''::uuid cast would raise instead of default-denying.
ALTER TABLE public.account_import_exceptions ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS account_import_exceptions_org_isolation
    ON public.account_import_exceptions;

CREATE POLICY account_import_exceptions_org_isolation
    ON public.account_import_exceptions
    FOR ALL
    USING (
        org_id = (NULLIF(current_setting('app.current_org_id', true), ''))::uuid
        OR current_setting('app.is_super_admin', true) = 'true'
    )
    WITH CHECK (
        org_id = (NULLIF(current_setting('app.current_org_id', true), ''))::uuid
        OR current_setting('app.is_super_admin', true) = 'true'
    );

-- Matches the grants the Part 1 tables carry for the non-bypass application
-- role. Without this the app connects as app_service and gets a bare
-- permission-denied that looks exactly like an RLS denial.
GRANT SELECT, INSERT, UPDATE, DELETE
    ON public.account_import_exceptions TO app_service;

COMMIT;
