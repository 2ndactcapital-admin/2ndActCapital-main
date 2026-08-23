-- ─────────────────────────────────────────────────────────────────────────
-- STP policy + note-terms routing — Part 1 SQL
-- ─────────────────────────────────────────────────────────────────────────
-- SCHEMA ONLY. No routing logic ships here; services/note_terms_routing.py
-- fills these columns. Nothing in this file touches the extraction or hazard
-- ensemble logic, and nothing here backfills existing data (see THE 54 below).
--
-- WHAT STP IS, AND WHAT IT IS NOT
--   Straight-through processing is a statement of trust in AGREEMENT between
--   the two extraction models for one (issuer, form type) pairing. It is NOT
--   a bypass of disagreement detection. The hazard ensemble still runs on
--   every row regardless of STP status, and its comparison result is recorded
--   identically whether the row is queued or straight-through. STP changes
--   only whether a HUMAN is asked to look — never what gets computed or
--   stored. A row with any hazard disagreement is queued even when an active
--   policy covers its pairing; that ordering is enforced in the service and
--   is not expressible as a constraint here.
--
-- WHY (cik, form_type) AND NOT A GLOBAL SWITCH
--   "Trust this issuer's 424B2s" is a defensible statement. "Trust
--   everything" is not — it collapses the distinction between an issuer whose
--   prospectus template we have read 40 times and one seen once. cik is the
--   stable identifier; reference_filings.filer_name is display-only and drifts
--   (the live corpus holds both 'JPMORGAN CHASE & CO' cik 19617 and
--   'JPMorgan Chase Financial Co. LLC' cik 1665650 — different issuers that a
--   name-based key would either merge or split arbitrarily).
--
-- THE 54 — NO BACKFILL, DELIBERATELY
--   54 note_terms rows exist from the extraction sprint, created before any
--   routing policy existed. Their routing_decision stays NULL. NULL here means
--   "no routing decision was ever made for this row", which is the truth. Any
--   backfilled value would be a fabrication: 'queued' would claim a human was
--   asked to look when none was, and 'stp' would claim trust that was never
--   granted. The 28 of them with extraction_confidence='needs_review' still
--   surface in the review queue through the confidence half of the queue
--   query, so nothing is lost by leaving the column honest.
--
-- This is GLOBAL public reference data derived from public SEC filings: NO
-- org_id column, and the same four-policy RLS shape as
-- portfolio.reference_filings / portfolio.securities_global_note_terms
-- (global read, super-admin write). Four separate policies, never one FOR ALL.
-- ─────────────────────────────────────────────────────────────────────────


-- ═══════════════════════════════════════════════════════════════════════════
-- TABLE — portfolio.note_terms_stp_policy
-- ═══════════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS portfolio.note_terms_stp_policy (
    id           uuid PRIMARY KEY DEFAULT uuid_generate_v4(),

    -- The stable issuer identifier from portfolio.reference_filings.cik.
    -- text, not bigint: EDGAR CIKs are zero-paddable identifiers, and the
    -- corpus stores them as text. Matching is exact string equality against
    -- reference_filings.cik, so a numeric type here would silently fail to
    -- join a zero-padded value.
    cik          text NOT NULL,
    form_type    text NOT NULL,

    -- The lifecycle flag. A revoke sets this false and stamps revoked_by /
    -- revoked_at ON THE SAME ROW — that row IS the revocation record. A later
    -- re-grant inserts a NEW row rather than flipping this one back, so the
    -- table reads as an append-mostly audit trail of grant/revoke episodes.
    enabled      boolean NOT NULL DEFAULT true,

    -- Who did this. text per spec; the value written is a users.id uuid
    -- rendered as text — the platform's existing "who did this" convention
    -- (document_field_corrections.corrected_by, restricted_access_audit) is a
    -- users.id. No FK: this table is global/org-less and users is org-scoped,
    -- so a FK would couple public reference data to tenant data.
    granted_by   text,
    granted_at   timestamptz NOT NULL DEFAULT now(),
    revoked_by   text,
    revoked_at   timestamptz,

    -- Free text: why this pairing was trusted. The grant is a human judgement
    -- and this is where the reasoning lives.
    notes        text,

    -- Only the two form types the corpus actually contains. Confirmed against
    -- live data: all 54 note-terms-linked filings are 424B2 or FWP.
    CONSTRAINT note_terms_stp_policy_form_type_chk
        CHECK (form_type IN ('424B2', 'FWP')),

    -- A revoked policy must carry its revocation stamps, and an active one
    -- must not. Without this a row could read enabled=false with no record of
    -- who revoked it or when, which destroys the audit trail the enabled flag
    -- exists to keep.
    CONSTRAINT note_terms_stp_policy_revocation_chk
        CHECK (
            (enabled = true  AND revoked_at IS NULL AND revoked_by IS NULL)
            OR
            (enabled = false AND revoked_at IS NOT NULL)
        )
);

-- Exactly one ACTIVE policy per (cik, form_type). PARTIAL, on enabled = true:
-- unlimited revoked history for a pairing may coexist with one live grant, so
-- the audit trail survives a grant → revoke → re-grant cycle intact. A full
-- unique constraint would force the second grant to mutate the first row and
-- erase who trusted this issuer the first time, and why.
CREATE UNIQUE INDEX IF NOT EXISTS note_terms_stp_policy_active_unique
    ON portfolio.note_terms_stp_policy (cik, form_type)
    WHERE enabled = true;

-- The routing lookup: one point-read per extracted row.
CREATE INDEX IF NOT EXISTS note_terms_stp_policy_lookup_idx
    ON portfolio.note_terms_stp_policy (cik, form_type)
    WHERE enabled = true;

-- ── RLS — four-policy global shape, copied verbatim from
--    portfolio.securities_global_note_terms. Global read, super-admin write.
--    Four separate policies, NOT one FOR ALL. ─────────────────────────────
ALTER TABLE portfolio.note_terms_stp_policy ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS note_terms_stp_policy_global_read
    ON portfolio.note_terms_stp_policy;
DROP POLICY IF EXISTS note_terms_stp_policy_super_admin_insert
    ON portfolio.note_terms_stp_policy;
DROP POLICY IF EXISTS note_terms_stp_policy_super_admin_update
    ON portfolio.note_terms_stp_policy;
DROP POLICY IF EXISTS note_terms_stp_policy_super_admin_delete
    ON portfolio.note_terms_stp_policy;

CREATE POLICY note_terms_stp_policy_global_read
    ON portfolio.note_terms_stp_policy
    FOR SELECT
    USING (true);

CREATE POLICY note_terms_stp_policy_super_admin_insert
    ON portfolio.note_terms_stp_policy
    FOR INSERT
    WITH CHECK (current_setting('app.is_super_admin', true) = 'true');

CREATE POLICY note_terms_stp_policy_super_admin_update
    ON portfolio.note_terms_stp_policy
    FOR UPDATE
    USING (current_setting('app.is_super_admin', true) = 'true')
    WITH CHECK (current_setting('app.is_super_admin', true) = 'true');

CREATE POLICY note_terms_stp_policy_super_admin_delete
    ON portfolio.note_terms_stp_policy
    FOR DELETE
    USING (current_setting('app.is_super_admin', true) = 'true');

GRANT SELECT, INSERT, UPDATE, DELETE
    ON portfolio.note_terms_stp_policy TO app_service;


-- ═══════════════════════════════════════════════════════════════════════════
-- ADDITIVE — routing decision on portfolio.securities_global_note_terms
-- ═══════════════════════════════════════════════════════════════════════════
-- Two nullable columns. No default, no backfill, no change to any existing
-- column, constraint, index or RLS policy on this table.
--
-- WHY THIS IS AN IN-PLACE COLUMN AND NOT A NEW BITEMPORAL VERSION (Rule 3)
--   Rule 3 governs business facts: a changed term is a new version of the
--   terms. routing_decision is not a term — it is metadata about the
--   extraction event that produced this row, stamped once, at the end of that
--   event. It is also not expressible as a new version: the partial unique
--   index sec_global_note_terms_current_unique forbids a second CURRENT row
--   for the same (global_security_id, terms_status, reference_filing_id), so
--   "close the old row and insert a new one" would either collide or require
--   retiring a perfectly good terms row to record a routing annotation. The
--   in-place UPDATE is deliberate and scoped to these two columns.

ALTER TABLE portfolio.securities_global_note_terms
    ADD COLUMN IF NOT EXISTS routing_decision text;

ALTER TABLE portfolio.securities_global_note_terms
    ADD COLUMN IF NOT EXISTS routed_at timestamptz;

-- NULL is a first-class value here and means "no routing decision was made"
-- — every row that predates this sprint. It is NOT 'unknown' and NOT a
-- pending state; see THE 54 at the top of this file.
ALTER TABLE portfolio.securities_global_note_terms
    DROP CONSTRAINT IF EXISTS securities_global_note_terms_routing_decision_chk;

ALTER TABLE portfolio.securities_global_note_terms
    ADD CONSTRAINT securities_global_note_terms_routing_decision_chk
    CHECK (routing_decision IN ('queued', 'stp') OR routing_decision IS NULL);

-- The queue read: current rows needing a human. Partial so the index stays
-- small as the straight-through population grows.
CREATE INDEX IF NOT EXISTS sec_global_note_terms_queue_idx
    ON portfolio.securities_global_note_terms (routing_decision)
    WHERE valid_to IS NULL AND system_to IS NULL;
