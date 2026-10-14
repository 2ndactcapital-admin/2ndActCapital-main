-- notefix2.structural — Task 8: second-reader findings in the review screen.
--
-- GLOBAL (no org_id), like every other note-extraction table — the note
-- corpus is public SEC reference data. Four policies, same pattern as
-- noteextractb1_readings_staging_gold.sql: global read, super-admin writes.
--
-- A note's field can carry MORE THAN ONE second-reader finding (confirmed in
-- docs/gold/gold1_second_reader_v1.json: 3 of the 50 notes flag the same
-- field_key twice with two distinct issues, e.g. 'underlyings' once for a
-- junk entry and once for control characters in a kept name) — so the
-- idempotency key is (reference_filing_id, field_key, seq), ``seq`` being the
-- 0-based position of that finding among findings sharing the same field
-- within the same note's source file, NOT an arbitrary generated id. This
-- table never writes note_gold_values; it is read-only reference annotation
-- for the review screen.

CREATE TABLE IF NOT EXISTS portfolio.note_gold_second_reader (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    reference_filing_id uuid NOT NULL REFERENCES portfolio.reference_filings(id),
    field_key           text NOT NULL CHECK (btrim(field_key) <> ''),
    seq                 integer NOT NULL DEFAULT 0,
    prefill             jsonb,
    suggested           jsonb,
    evidence            text,
    severity            text NOT NULL CHECK (severity = ANY (ARRAY[
                            'critical', 'economics', 'minor', 'uncertain'])),
    kind                text NOT NULL CHECK (kind = ANY (ARRAY[
                            'wrong', 'missing', 'junk_extra', 'uncertain'])),
    source              text NOT NULL DEFAULT 'gold1_second_reader_v1',
    loaded_at           timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS note_gold_second_reader_key_uq
    ON portfolio.note_gold_second_reader (reference_filing_id, field_key, seq, source);
CREATE INDEX IF NOT EXISTS note_gold_second_reader_filing_idx
    ON portfolio.note_gold_second_reader (reference_filing_id);

DO $$
BEGIN
    ALTER TABLE portfolio.note_gold_second_reader ENABLE ROW LEVEL SECURITY;
    IF NOT EXISTS (SELECT 1 FROM pg_policies
                    WHERE schemaname = 'portfolio' AND tablename = 'note_gold_second_reader') THEN
        CREATE POLICY note_gold_second_reader_global_read ON portfolio.note_gold_second_reader
            FOR SELECT USING (true);
        CREATE POLICY note_gold_second_reader_super_admin_insert ON portfolio.note_gold_second_reader
            FOR INSERT WITH CHECK (current_setting('app.is_super_admin', true) = 'true');
        CREATE POLICY note_gold_second_reader_super_admin_update ON portfolio.note_gold_second_reader
            FOR UPDATE USING (current_setting('app.is_super_admin', true) = 'true')
            WITH CHECK (current_setting('app.is_super_admin', true) = 'true');
        CREATE POLICY note_gold_second_reader_super_admin_delete ON portfolio.note_gold_second_reader
            FOR DELETE USING (current_setting('app.is_super_admin', true) = 'true');
        GRANT SELECT, INSERT, UPDATE, DELETE ON portfolio.note_gold_second_reader TO app_service;
    END IF;
END;
$$;
