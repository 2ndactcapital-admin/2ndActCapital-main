-- Portfolio Phase B — Part 1 SQL.
--
-- The two constraint fixes this sprint was handed (external_references'
-- org-scoped UNIQUE, fx_rates' rate_type-scoped UNIQUE) were applied ahead of
-- the sprint and are NOT repeated here. Both were re-introspected against the
-- live database at the start of Phase B and confirmed present:
--
--   external_references_org_source_ext_type_key
--     UNIQUE (org_id, source_system, external_id, record_type)
--   fx_rates_pair_date_type_key
--     UNIQUE (base_ccy, quote_ccy, as_of_date, rate_type)
--
-- This file holds the ONE additional migration Phase B discovered on its own.
--
-- ---------------------------------------------------------------------------
-- positions_source_chk does not admit the file-import source
-- ---------------------------------------------------------------------------
-- The deployed CHECK, introspected (not inferred from the sprint prompt):
--
--   CHECK (source_system = ANY (ARRAY[
--     'reporting_tool_bd', 'reporting_tool_addepar', 'reporting_tool_orion',
--     'reporting_tool_apx', 'altruist', 'spv_subscriptions', 'chancery',
--     'manual']))
--
-- Every reporting-tool token names a SPECIFIC vendor. Phase B's file importer
-- deliberately does not claim to know which vendor produced a given export:
-- Black Diamond, Addepar, Orion and APX all emit broadly the same tabular
-- shape, and guessing the vendor from column headers would write a provenance
-- claim the file does not actually support. `reporting_tool_import` is the
-- honest token for "a reporting-tool export, vendor not asserted".
--
-- Additive only. No existing row can violate the widened constraint, so the
-- validating re-add is a formality rather than a risk — but it IS validated
-- rather than added NOT VALID, because a source_system nobody validates is
-- exactly the drift this CHECK exists to prevent.
--
-- `transactions` deliberately gets no equivalent change: it has no source CHECK
-- at all (A2 recorded this), so there is nothing to widen.

ALTER TABLE portfolio.positions
  DROP CONSTRAINT IF EXISTS positions_source_chk;

ALTER TABLE portfolio.positions
  ADD CONSTRAINT positions_source_chk CHECK (
    source_system = ANY (ARRAY[
      'reporting_tool_bd'::text,
      'reporting_tool_addepar'::text,
      'reporting_tool_orion'::text,
      'reporting_tool_apx'::text,
      'reporting_tool_import'::text,
      'altruist'::text,
      'spv_subscriptions'::text,
      'chancery'::text,
      'manual'::text
    ])
  );
