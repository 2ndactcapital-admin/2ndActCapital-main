-- Applied live via MCP 2026-10-04. Inventory v2 groups concepts by the model's
-- suggested field key when AI grouping is skipped; without this value the
-- grouping step's write would be rejected.
ALTER TABLE portfolio.edgar_inventory_concepts
  DROP CONSTRAINT edgar_inventory_concepts_grouping_method_check,
  ADD CONSTRAINT edgar_inventory_concepts_grouping_method_check
    CHECK (grouping_method IN ('normalised_label', 'field_key', 'model'));
