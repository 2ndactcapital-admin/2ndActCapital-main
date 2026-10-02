-- noteextractb1.structural — Decision 1: move the hazard-ensemble MODEL
-- DISAGREEMENTS out of document_field_corrections. Corrections hold only human
-- decisions; these 29 rows were two models disagreeing (original = the primary
-- reader, corrected = the secondary reader, corrected_by NULL).
--
-- Each moved row becomes TWO readings in portfolio.note_term_readings:
--   source 'model_1' = original_value, provider_model = notes.primary.model
--   source 'model_2' = corrected_value, provider_model = notes.secondary.model
-- legacy_correction_id keeps the moved row's id, metadata keeps its full notes
-- text verbatim, created_at keeps its corrected_at. deployment_name is NULL on
-- purpose: the rows recorded the provider's model id, not which proxy
-- deployment was asked, and that is not invented after the fact.
--
-- Atomic and re-runnable: one DO block; the delete runs only after every
-- selected row is proven to have both readings, and only rows matching the
-- exact disagreement shape (target_type note_terms, corrected_by NULL, notes
-- source hazard_ensemble_disagreement) are touched.
DO $$
DECLARE
    v_ids uuid[];
    v_n int;
    v_readings int;
BEGIN
    PERFORM set_config('app.is_super_admin', 'true', true);

    SELECT array_agg(id) INTO v_ids
    FROM public.document_field_corrections
    WHERE target_type = 'note_terms'
      AND corrected_by IS NULL
      AND notes LIKE '%hazard_ensemble_disagreement%';
    v_n := coalesce(array_length(v_ids, 1), 0);
    IF v_n = 0 THEN
        RAISE NOTICE 'no model disagreements left in document_field_corrections';
        RETURN;
    END IF;

    INSERT INTO portfolio.note_term_readings (
        reference_filing_id, note_terms_id, field_key, value, value_normalized,
        source, origin, status, provider_model, legacy_correction_id, metadata, created_at)
    SELECT nt.reference_filing_id, nt.id, c.field_name, v.val, lower(v.raw),
           v.source, 'migrated_correction', 'ok', v.model, c.id,
           jsonb_build_object(
               'moved_from', 'public.document_field_corrections',
               'legacy_side', v.side,
               'legacy_notes', c.notes,
               'legacy_corrected_at', c.corrected_at),
           c.corrected_at
    FROM public.document_field_corrections c
    JOIN portfolio.securities_global_note_terms nt ON nt.id = c.target_id
    LEFT JOIN portfolio.note_terms_field_registry r ON r.field_key = c.field_name
    CROSS JOIN LATERAL (
        VALUES
          ('model_1', 'original', c.original_value,
           substring(c.notes from 'primary\\?": \{\\?"model\\?": \\?"([^"\\]+)')),
          ('model_2', 'corrected', c.corrected_value,
           substring(c.notes from 'secondary\\?": \{\\?"model\\?": \\?"([^"\\]+)'))
    ) AS side(source, side, raw, model)
    CROSS JOIN LATERAL (
        SELECT side.source, side.side, side.raw, side.model,
               CASE
                 WHEN side.raw IS NULL THEN NULL
                 WHEN r.data_type = 'boolean' AND lower(side.raw) IN ('true', 'false')
                   THEN to_jsonb(lower(side.raw)::boolean)
                 WHEN r.data_type = 'numeric' AND side.raw ~ '^-?[0-9]+(\.[0-9]+)?$'
                   THEN to_jsonb(side.raw::numeric)
                 ELSE to_jsonb(side.raw)
               END AS val
    ) AS v
    WHERE c.id = ANY (v_ids)
    ON CONFLICT (legacy_correction_id, source) WHERE legacy_correction_id IS NOT NULL
    DO NOTHING;

    SELECT count(*) INTO v_readings
    FROM portfolio.note_term_readings
    WHERE legacy_correction_id = ANY (v_ids)
      AND source IN ('model_1', 'model_2')
      AND provider_model IS NOT NULL;
    IF v_readings <> 2 * v_n THEN
        RAISE EXCEPTION 'expected % readings (2 per moved row) with a recorded model, found % — nothing deleted',
            2 * v_n, v_readings;
    END IF;

    DELETE FROM public.document_field_corrections WHERE id = ANY (v_ids);
    RAISE NOTICE 'moved % model disagreements into % readings', v_n, v_readings;
END;
$$;
