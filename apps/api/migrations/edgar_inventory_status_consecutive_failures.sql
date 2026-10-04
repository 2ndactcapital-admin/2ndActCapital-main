-- Applied live via MCP 2026-10-03. Allows the inventory runner's new
-- consecutive-failure stop status; without it the stop itself would be rejected.
ALTER TABLE portfolio.edgar_inventory_runs
  DROP CONSTRAINT edgar_inventory_runs_status_check,
  ADD CONSTRAINT edgar_inventory_runs_status_check
    CHECK (status IN ('running','completed','stopped_spend_cap','stopped_consecutive_failures','failed','blocked'));
