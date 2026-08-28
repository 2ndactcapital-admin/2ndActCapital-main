-- ============================================================
-- PATCH — fee_discounts PCT_OFF scale constraint (closes fee35 F2)
-- ============================================================
-- fee35's golden-case suite found this live: fee_discounts.value has no
-- scale and no CHECK. A PCT_OFF of 20 (meaning 20%) and 0.20 (meaning the
-- same 20%, entered as a fraction) differ by 100x with nothing in the
-- schema to catch the mistake. The engine already reads PCT_OFF as a
-- percent in [0,100] on its own side; this constraint makes that the
-- durable, DB-enforced truth instead of an assumption living only in
-- application code.
--
-- Scoped to discount_type='PCT_OFF' only -- BPS_OFF, DOLLAR_CREDIT,
-- FEE_HOLIDAY, and SCHEDULE_OVERRIDE all use `value` differently (or not
-- at all) and must not be constrained by a percent range.
--
-- Applied live 2026-08-28, confirmed via pg_get_constraintdef.

ALTER TABLE fee_discounts
  ADD CONSTRAINT fee_discounts_pct_off_scale_check
  CHECK (discount_type != 'PCT_OFF' OR (value >= 0 AND value <= 100));
