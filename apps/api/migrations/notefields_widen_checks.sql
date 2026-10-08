-- notefields.structural — part 1 of 2: widen three CHECK constraints.
--
-- Split from notefields_registry_v3.sql because it contains DROP CONSTRAINT
-- (the MCP migration tool has cancelled DROP-containing migrations before).
-- Nothing is removed: every value the old constraints accepted is still
-- accepted.
--
--   note_terms_field_registry.data_type   + 'json'     (the three list fields)
--   note_term_readings.source             + 'derived'  (tenor, max loss, the
--                                                       unstated estimated-value unit)
--   note_extraction_staged_fields.resolution + 'rules' (rules-only fields),
--                                              'derived'

ALTER TABLE portfolio.note_terms_field_registry
    DROP CONSTRAINT IF EXISTS note_terms_field_registry_data_type_chk;
ALTER TABLE portfolio.note_terms_field_registry
    ADD CONSTRAINT note_terms_field_registry_data_type_chk
    CHECK (data_type = ANY (ARRAY['numeric', 'text', 'boolean', 'date', 'json']));

ALTER TABLE portfolio.note_term_readings
    DROP CONSTRAINT IF EXISTS note_term_readings_source_check;
ALTER TABLE portfolio.note_term_readings
    ADD CONSTRAINT note_term_readings_source_check
    CHECK (source = ANY (ARRAY['rules', 'edgartools', 'model_1', 'model_2', 'jev',
                               'escalation', 'human', 'derived']));

ALTER TABLE portfolio.note_extraction_staged_fields
    DROP CONSTRAINT IF EXISTS note_extraction_staged_fields_resolution_check;
ALTER TABLE portfolio.note_extraction_staged_fields
    ADD CONSTRAINT note_extraction_staged_fields_resolution_check
    CHECK (resolution = ANY (ARRAY['verified_agreement', 'agreed_null', 'jev', 'escalation',
                                   'unresolved', 'rules', 'derived']));
