-- notefix1.structural — Task 2: four economics fields become ranges.
--
-- The first real gold pre-fill (run e6da8fcd-b4e7-4300-9bac-48dee6e54697,
-- batch gold-1) failed note 6380efa7-5a87-4202-8b6c-3bcaf34030aa: the model
-- correctly read "a structuring fee of up to 0.85%" for structuring_fee_pct,
-- a field the v3 registry still listed as 'scalar'. Because the whole
-- reading validated as ONE Pydantic model (schema.pydantic_model), that one
-- field's shape mismatch failed the ENTIRE note's reader call, and only the
-- 5 rules/derived values were saved. 1d confirmed real bound wording ("up to
-- $X", "up to X%") on structuring_fee_pct and other_fees_pct in the 138-final
-- corpus, and the task also widens total_fees_pct and proceeds_to_issuer_pct
-- to match (proceeds is commonly stated "at least" elsewhere in this corpus;
-- total fees is the sum of range-shaped components and must be able to carry
-- a range itself). price_to_public_pct stays scalar: the only "up to" wording
-- near it in the 138 finals describes a DEALER RESALE DISCOUNT, not the
-- field's own value (see GOLDSET_DISCOVERY.md / notefix1 task 1d).
--
-- NO DATA MIGRATION NEEDED. schema.as_range() already reads a bare scalar
-- number as {min: v, max: v, bound: 'exact'} — every existing reading,
-- staged field and (there are currently zero) gold value on these four keys
-- compares and self-checks correctly under the new range kind with no
-- rewrite. Readings are append-only; nothing here touches them.
--
-- Idempotent.

UPDATE portfolio.note_terms_field_registry
   SET value_shape = 'range'
 WHERE field_key IN ('structuring_fee_pct', 'other_fees_pct', 'total_fees_pct', 'proceeds_to_issuer_pct')
   AND retired_at IS NULL
   AND value_shape <> 'range';
