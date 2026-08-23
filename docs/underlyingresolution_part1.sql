-- ============================================================================
-- UNDERLYING RESOLUTION — Part 1 SQL
--
-- Turns portfolio.securities_global_relationships into a maker-checker surface:
-- a machine may PROPOSE a target, only a human may RESOLVE to one.
--
-- WHY THE PROPOSAL LIVES ON THE EDGE AND NOT IN A PROPOSALS TABLE
-- ---------------------------------------------------------------------------
-- The platform's existing "AI proposes, human confirms" table is
-- public.document_link_proposals. It is not reusable here and the reason is
-- structural, not stylistic: its shape is
--     document_id uuid NOT NULL REFERENCES documents(id)
--     org_id      uuid NOT NULL REFERENCES organizations(id)
-- and an unresolved underlying edge has NEITHER. These 97 edges hang off SEC
-- filings in portfolio.reference_filings — global public reference data with no
-- org_id anywhere in its lineage and no row in `documents`. Satisfying those two
-- NOT NULL FKs would mean inventing a document and attributing a public fact to
-- one tenant. So this migration reuses the PATTERN (pending -> reviewed_by /
-- reviewed_at, approve or reject) on the edge itself, exactly as the sprint's
-- stated fallback prescribes.
--
-- to_global_security_id IS NOT OVERLOADED. Its meaning is fixed by
-- sec_global_rel_resolved_has_target: it is "resolved's target". An unconfirmed
-- guess goes in proposed_global_security_id, a different column, and the edge
-- sits at link_state='ambiguous' — the third state the original design already
-- reserved for exactly this.
--
-- MAKER-CHECKER IS ENFORCED IN THE DATABASE, NOT ONLY IN PYTHON
-- ---------------------------------------------------------------------------
-- A CHECK constraint cannot express "who wrote this", so the gate is a BEFORE
-- trigger keyed to a transaction-local GUC (portfolio.sec_global_rel_confirm_gate
-- below). Any transition INTO link_state='resolved' is rejected unless
-- app.underlying_confirm = 'true' is set LOCAL in that transaction. Only the
-- Super-Admin-gated confirm endpoint sets it. The proposal pipeline never does,
-- so a proposal CANNOT resolve an edge even if application code is wrong,
-- refactored, or bypassed with raw SQL. This mirrors the app.is_super_admin
-- convention the RLS policies on this table already use.
--
-- Safe to apply against live data: all 97 current edges are 'unresolved' and the
-- only existing writer (services/note_terms_extraction.py) hardcodes
-- 'unresolved', so nothing in flight transitions to 'resolved'.
--
-- Idempotent: IF NOT EXISTS / DROP-then-CREATE throughout.
-- ============================================================================

BEGIN;

-- ── 1. Proposal columns on the edge ─────────────────────────────────────────

ALTER TABLE portfolio.securities_global_relationships
    -- The machine's guess. NULL until propose_resolution runs. Never read as a
    -- resolution: the queue and every consumer key off link_state.
    ADD COLUMN IF NOT EXISTS proposed_global_security_id uuid
        REFERENCES portfolio.securities_global(id),
    -- 'high'  -> a closed-set index registry hit
    -- 'needs_manual_match' -> the matcher looked and declined
    ADD COLUMN IF NOT EXISTS proposal_confidence text,
    -- Reviewer-facing classification of WHY it declined: single_name, fund_etf,
    -- decrement_candidate, unclassified. Metadata for the queue screen only —
    -- nothing branches on it.
    ADD COLUMN IF NOT EXISTS proposal_kind text,
    -- For single-name patterns: the extracted issuer name, as a lead for the
    -- reviewer. Explicitly NOT a ticker and explicitly not resolved.
    ADD COLUMN IF NOT EXISTS proposal_hint text,
    ADD COLUMN IF NOT EXISTS proposed_at timestamptz,
    -- The deterministic normalizer's output, persisted so the queue can group
    -- by it and so a reviewer can see what the matcher actually matched on.
    ADD COLUMN IF NOT EXISTS normalized_underlying_text text,
    -- Who confirmed, and when. uuid with no FK: the portfolio schema does not
    -- FK across into public (note_terms_stp_policy.granted_by is text for the
    -- same reason), and a cross-schema FK here would be the only one.
    ADD COLUMN IF NOT EXISTS resolved_by uuid,
    ADD COLUMN IF NOT EXISTS resolved_at timestamptz;

-- Vocabulary locked at the database, so a typo in Python is a constraint
-- violation and not a value nobody ever queries again.
ALTER TABLE portfolio.securities_global_relationships
    DROP CONSTRAINT IF EXISTS sec_global_rel_proposal_confidence_chk;
ALTER TABLE portfolio.securities_global_relationships
    ADD CONSTRAINT sec_global_rel_proposal_confidence_chk
    CHECK (proposal_confidence IS NULL
           OR proposal_confidence IN ('high', 'needs_manual_match'));

ALTER TABLE portfolio.securities_global_relationships
    DROP CONSTRAINT IF EXISTS sec_global_rel_proposal_kind_chk;
ALTER TABLE portfolio.securities_global_relationships
    ADD CONSTRAINT sec_global_rel_proposal_kind_chk
    CHECK (proposal_kind IS NULL
           OR proposal_kind IN ('known_index', 'single_name', 'fund_etf',
                                'decrement_candidate', 'unclassified'));

-- A 'high' proposal without a target is a proposal of nothing. Reject it here
-- rather than discovering an empty confirm dialog in the UI.
ALTER TABLE portfolio.securities_global_relationships
    DROP CONSTRAINT IF EXISTS sec_global_rel_high_needs_proposed_target;
ALTER TABLE portfolio.securities_global_relationships
    ADD CONSTRAINT sec_global_rel_high_needs_proposed_target
    CHECK (proposal_confidence IS DISTINCT FROM 'high'
           OR proposed_global_security_id IS NOT NULL);

-- link_state='ambiguous' is this sprint's "proposed, awaiting confirmation".
-- It must therefore carry something to confirm.
ALTER TABLE portfolio.securities_global_relationships
    DROP CONSTRAINT IF EXISTS sec_global_rel_ambiguous_has_proposal;
ALTER TABLE portfolio.securities_global_relationships
    ADD CONSTRAINT sec_global_rel_ambiguous_has_proposal
    CHECK (link_state <> 'ambiguous' OR proposed_global_security_id IS NOT NULL);

-- Resolution is an act by a person; an anonymous one is not auditable.
ALTER TABLE portfolio.securities_global_relationships
    DROP CONSTRAINT IF EXISTS sec_global_rel_resolved_is_attributed;
ALTER TABLE portfolio.securities_global_relationships
    ADD CONSTRAINT sec_global_rel_resolved_is_attributed
    CHECK (link_state <> 'resolved'
           OR (resolved_by IS NOT NULL AND resolved_at IS NOT NULL));

CREATE INDEX IF NOT EXISTS idx_sec_global_rel_proposed
    ON portfolio.securities_global_relationships (proposed_global_security_id)
    WHERE proposed_global_security_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_sec_global_rel_normalized
    ON portfolio.securities_global_relationships (normalized_underlying_text)
    WHERE link_state <> 'resolved';


-- ── 2. The 1e gap: nothing prevented a duplicate index row ──────────────────
--
-- portfolio.securities_global has NO unique constraint of any kind — only a
-- pkey, one FK and two value CHECKs. resolve_or_create_index_security must be
-- safe to call twice, and "safe" that depends on application code winning a
-- race is not safe. This index makes the second concurrent INSERT fail instead
-- of succeeding quietly.
--
-- Scoped to security_type='index' deliberately. The 54 existing structured_note
-- rows are prospectus-derived names ("Callable Contingent Income Securities due
-- March 17, 2028") that genuinely CAN repeat across issuers, so a table-wide
-- name index would reject legitimate data. Index names are proper nouns and do
-- not.
CREATE UNIQUE INDEX IF NOT EXISTS uq_sec_global_active_index_name
    ON portfolio.securities_global (lower(name))
    WHERE security_type = 'index' AND valid_to IS NULL AND system_to IS NULL;


-- ── 3. Database-level maker-checker on link_state='resolved' ────────────────

CREATE OR REPLACE FUNCTION portfolio.sec_global_rel_confirm_gate()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    -- Only transitions INTO 'resolved' are gated. An already-resolved row being
    -- updated for some other reason is not a new resolution and does not need a
    -- fresh confirm token.
    IF NEW.link_state = 'resolved'
       AND (TG_OP = 'INSERT' OR OLD.link_state IS DISTINCT FROM 'resolved') THEN

        -- NULLIF because a reset GUC reads back as '' rather than NULL — the
        -- same artifact every RLS policy in this codebase has to guard against.
        IF NULLIF(current_setting('app.underlying_confirm', true), '')
           IS DISTINCT FROM 'true' THEN
            RAISE EXCEPTION
                'link_state=''resolved'' may only be set by the human confirm '
                'path (app.underlying_confirm not set). Propose, then confirm.'
                USING ERRCODE = '42501';  -- insufficient_privilege
        END IF;

        -- Belt and braces with sec_global_rel_resolved_has_target: that CHECK
        -- fires too, but this message says what to do about it.
        IF NEW.to_global_security_id IS NULL THEN
            RAISE EXCEPTION 'resolved edge requires to_global_security_id'
                USING ERRCODE = '23514';
        END IF;
    END IF;

    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_sec_global_rel_confirm_gate
    ON portfolio.securities_global_relationships;
CREATE TRIGGER trg_sec_global_rel_confirm_gate
    BEFORE INSERT OR UPDATE ON portfolio.securities_global_relationships
    FOR EACH ROW
    EXECUTE FUNCTION portfolio.sec_global_rel_confirm_gate();


-- ── 4. RLS, re-asserted in the same migration ───────────────────────────────
--
-- The table already carries the right shape (global SELECT, Super-Admin-only
-- writes) and the new columns inherit it — a policy is per row, not per column.
-- Restated here anyway, DROP-then-CREATE, so this file is the whole story for
-- anyone reading it later and so a table created fresh from it is not open.

ALTER TABLE portfolio.securities_global_relationships ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS securities_global_relationships_global_read
    ON portfolio.securities_global_relationships;
CREATE POLICY securities_global_relationships_global_read
    ON portfolio.securities_global_relationships
    FOR SELECT USING (true);

DROP POLICY IF EXISTS securities_global_relationships_super_admin_insert
    ON portfolio.securities_global_relationships;
CREATE POLICY securities_global_relationships_super_admin_insert
    ON portfolio.securities_global_relationships
    FOR INSERT WITH CHECK (
        NULLIF(current_setting('app.is_super_admin', true), '') = 'true');

DROP POLICY IF EXISTS securities_global_relationships_super_admin_update
    ON portfolio.securities_global_relationships;
CREATE POLICY securities_global_relationships_super_admin_update
    ON portfolio.securities_global_relationships
    FOR UPDATE
    USING (NULLIF(current_setting('app.is_super_admin', true), '') = 'true')
    WITH CHECK (NULLIF(current_setting('app.is_super_admin', true), '') = 'true');

DROP POLICY IF EXISTS securities_global_relationships_super_admin_delete
    ON portfolio.securities_global_relationships;
CREATE POLICY securities_global_relationships_super_admin_delete
    ON portfolio.securities_global_relationships
    FOR DELETE
    USING (NULLIF(current_setting('app.is_super_admin', true), '') = 'true');

-- securities_global gets the same treatment: Task 3 INSERTs index rows into it.
ALTER TABLE portfolio.securities_global ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS securities_global_global_read ON portfolio.securities_global;
CREATE POLICY securities_global_global_read
    ON portfolio.securities_global FOR SELECT USING (true);

DROP POLICY IF EXISTS securities_global_super_admin_insert ON portfolio.securities_global;
CREATE POLICY securities_global_super_admin_insert
    ON portfolio.securities_global
    FOR INSERT WITH CHECK (
        NULLIF(current_setting('app.is_super_admin', true), '') = 'true');

DROP POLICY IF EXISTS securities_global_super_admin_update ON portfolio.securities_global;
CREATE POLICY securities_global_super_admin_update
    ON portfolio.securities_global
    FOR UPDATE
    USING (NULLIF(current_setting('app.is_super_admin', true), '') = 'true')
    WITH CHECK (NULLIF(current_setting('app.is_super_admin', true), '') = 'true');

DROP POLICY IF EXISTS securities_global_super_admin_delete ON portfolio.securities_global;
CREATE POLICY securities_global_super_admin_delete
    ON portfolio.securities_global
    FOR DELETE
    USING (NULLIF(current_setting('app.is_super_admin', true), '') = 'true');

COMMIT;
