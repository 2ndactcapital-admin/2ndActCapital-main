-- Sprint fee32 — ADDITIVE addendum to the Part 1 position/household SQL.
--
-- Part 1 (applied directly by Joe, confirmed live by scripts/discover_fee32.py)
-- added portfolio.positions.account_id and
-- public.portfolio_precedence_household_overrides. Nothing here re-creates or
-- alters either of them.
--
-- WHY A NEW TABLE RATHER THAN fee31's account_import_exceptions
-- ─────────────────────────────────────────────────────────────────────────────
-- The RFC settles that a position whose account_id names an account the
-- position's owner_entity_id does not own is WRITTEN, then surfaced for review
-- — never rejected, never silently accepted. fee31 already has an exception
-- list with exactly that intent, and reusing it was the first choice. It does
-- not fit, for three reasons that are structural rather than cosmetic:
--
--   * account_import_exceptions.batch_id is NOT NULL and REFERENCES
--     account_import_batches(id). A position written through the reporting-tool
--     importer, the API, or a manual grid edit has no custody batch to point
--     at, and inventing one would put a fake row in the batch list that an
--     operator would try to re-drive.
--   * record_kind CHECK admits only ('account','balance','flow'). Admitting
--     'position' means widening a CHECK on a table fee31 just shipped.
--   * source_row is NOT NULL and means "line N of the uploaded file". A manual
--     single-position edit has no line number, and 0 would be a lie rather than
--     an absence.
--
-- Making that table fit would mean dropping a NOT NULL, widening a CHECK, and
-- leaving a column that is meaningless for two of the three write paths. A
-- separate, purpose-shaped table is the smaller and more honest change. It
-- carries the SAME review shape (reason_code + human reason + a jsonb detail
-- blob + an unreviewed/reviewed axis) so a future combined exceptions screen
-- can union the two without either side needing to move.

BEGIN;

CREATE TABLE IF NOT EXISTS public.position_account_exceptions (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id          uuid NOT NULL REFERENCES public.organizations (id),

    -- The position that WAS written. Not nullable: an exception with no
    -- position is not reviewable, and "we refused the write" is precisely the
    -- outcome the RFC rules out. positions is bitemporal on the valid axis, so
    -- an edit mints a new id and leaves this one in place — the FK holds and
    -- the exception keeps pointing at the row it was actually raised on.
    position_id     uuid NOT NULL REFERENCES portfolio.positions (id),

    account_id      uuid NOT NULL REFERENCES public.accounts (id),
    owner_entity_id uuid NOT NULL REFERENCES public.entities (id),

    reason_code     text NOT NULL,
    reason          text NOT NULL,

    -- Which feed wrote the position, copied off the position at write time so
    -- the review list can be triaged by source without a join back to a row
    -- that may since have been restated.
    source_system   text,

    -- The evidence: the account's active owner entity ids at the moment of the
    -- write, and the account's masked number. NEVER the unmasked number —
    -- public.accounts stores only account_number_masked/_hash, and this column
    -- must not become the place a full number reappears.
    detail          jsonb NOT NULL DEFAULT '{}'::jsonb,

    created_at      timestamptz NOT NULL DEFAULT now(),
    reviewed_at     timestamptz,
    reviewed_by     uuid,

    -- A closed vocabulary, mirrored in services/portfolio_account_link.py. The
    -- two codes are genuinely different findings and go to different fixes:
    -- an account with owners that simply do not include this entity is a
    -- mis-mapped import; an account with NO active owners at all is an
    -- incomplete account record, and merging them into one code would hide the
    -- second inside the first.
    CONSTRAINT position_account_exceptions_reason_code_check
        CHECK (reason_code IN ('owner_not_account_owner', 'account_has_no_owners')),

    -- reviewed_by without reviewed_at (or the reverse) is a half-written
    -- review; the pair moves together or not at all.
    CONSTRAINT position_account_exceptions_reviewed_pair_check
        CHECK ((reviewed_at IS NULL) = (reviewed_by IS NULL))
);

-- Idempotency for the OPEN set only. Re-validating the same position against
-- the same account must not append a second identical row — but once an
-- exception has been reviewed and closed, a later write that re-raises the
-- same mismatch is a NEW finding and must be recordable again.
CREATE UNIQUE INDEX IF NOT EXISTS position_account_exceptions_open_uq
    ON public.position_account_exceptions
       (org_id, position_id, account_id, reason_code)
    WHERE reviewed_at IS NULL;

-- The review list's own query: open exceptions for one org, newest first.
CREATE INDEX IF NOT EXISTS position_account_exceptions_open_idx
    ON public.position_account_exceptions (org_id, created_at DESC)
    WHERE reviewed_at IS NULL;

CREATE INDEX IF NOT EXISTS position_account_exceptions_position_idx
    ON public.position_account_exceptions (position_id);

-- ── RLS — the same policy shape as positions and the Part 1 tables ─────────
-- Introspected from the deployed positions / account_owners /
-- portfolio_precedence_household_overrides policies rather than written from
-- memory. The NULLIF is load-bearing: on a pooled backend a custom GUC reverts
-- to '' rather than NULL, and a bare ''::uuid cast raises instead of
-- default-denying.
ALTER TABLE public.position_account_exceptions ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS position_account_exceptions_org_isolation
    ON public.position_account_exceptions;

CREATE POLICY position_account_exceptions_org_isolation
    ON public.position_account_exceptions
    FOR ALL
    USING (
        org_id = (NULLIF(current_setting('app.current_org_id', true), ''))::uuid
        OR current_setting('app.is_super_admin', true) = 'true'
    )
    WITH CHECK (
        org_id = (NULLIF(current_setting('app.current_org_id', true), ''))::uuid
        OR current_setting('app.is_super_admin', true) = 'true'
    );

-- Without this the app connects as app_service and gets a bare
-- permission-denied that looks exactly like an RLS denial.
GRANT SELECT, INSERT, UPDATE, DELETE
    ON public.position_account_exceptions TO app_service;

COMMIT;
