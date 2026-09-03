Proceed with (a) — build public.reference_data_lists as a real list header,
move owner_scope / owner_scope_id / is_extensible to it, keep value_set_id + FK.

Fix blockers 1 and 3 mechanically: mirror idx_udf_def_key_unique's shape
(org_id, owner_scope, owner_scope_id, applies_to) and include valid_to IS NULL.
Drop reference_data_org_list_code_uniq as well as the global constraint.

On the [FIND]: dual-write. Tag mints for applies_to='position' also write
value_text as today, so fee_run_inputs keeps working unchanged. Mark
# TODO(udf-1d): remove after fee_run_inputs migration. Add an assertion that a
tag minted through the new path is returned by the fee_run_inputs query verbatim.

Also widen DATA_TYPES, _normalize_options, and coerce_value alongside the CHECK —
widening the CHECK alone changes nothing.

Then apply corrected Part 1, verify each object landed with a follow-up query,
and continue into Tasks 2 and 3.
