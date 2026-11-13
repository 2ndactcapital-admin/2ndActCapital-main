-- PRICES2 part 2 — plain indexes for five foreign keys onto portfolio.securities_global(id) that had none usable.
-- FIND (verify_prices2's teardown): deleting ONE security took ~15 s — each delete's FK check seq-scanned
-- securities_global_prices (~717k rows, 9.5 s; its only global_security_id index is PARTIAL, so the FK check cannot
-- use it), security_reference_attributes (2.3 s, partial too), index_levels (1.9 s), sec_13f_list (0.8 s) and
-- data_quality_events (0.2 s). Additive, reversible (DROP INDEX), built CONCURRENTLY (no write lock).
-- Applied by apps/api/scripts/_apply_prices2.py --fk-indexes, one statement at a time (CONCURRENTLY cannot run in a
-- transaction block). Idempotent.
CREATE INDEX CONCURRENTLY IF NOT EXISTS securities_global_prices_gsid_fk_ix ON portfolio.securities_global_prices (global_security_id);
CREATE INDEX CONCURRENTLY IF NOT EXISTS security_reference_attributes_gsid_fk_ix ON portfolio.security_reference_attributes (global_security_id);
CREATE INDEX CONCURRENTLY IF NOT EXISTS index_levels_gsid_fk_ix ON portfolio.index_levels (global_security_id);
CREATE INDEX CONCURRENTLY IF NOT EXISTS sec_13f_list_gsid_fk_ix ON portfolio.sec_13f_list (global_security_id);
CREATE INDEX CONCURRENTLY IF NOT EXISTS data_quality_events_gsid_fk_ix ON portfolio.data_quality_events (global_security_id);
