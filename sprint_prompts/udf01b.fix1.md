Sprint udf01b came back 99 PASS, 0 FAIL, 3 FIND. Two FINDs are registered debt,
no action needed. One is a real functional gap to fix now, in this branch,
before merge:

FIND 6 — no remove_section function. Task 2d shipped add_section but no
remove_section (and no route). A tab that has ever had a section added can
never clear get_tab_references to zero, so it can never be soft-deleted again
through the API. This is a one-way door and needs closing.

Add:
1. remove_section(section_id) in apps/api/services/portfolio_udf_layouts.py,
   following the same pattern as remove_item. It should fail clearly if the
   section still contains items (require removing items first, same
   reference-blocking principle already used for soft-delete), not cascade
   silently.
2. DELETE /udf/layouts/{tab_id}/sections/{section_id} in apps/api/routers/udf.py,
   same permission gate (manage_portfolio) as the other layout write endpoints.
3. Add these assertions to apps/api/scripts/verify_udf01b.py:
   - remove_section is rejected when the section still has items (reference count reported)
   - remove_section succeeds once the section is empty
   - after removing all sections, a previously-blocked tab soft-delete now succeeds
   - the new DELETE endpoint: 403 without manage_portfolio, 200 with it
4. Run verify_udf01a.py AND verify_udf01b.py, in that order, for real, via
   `doppler run -- python3 apps/api/scripts/verify_udf0Xy.py`. Paste the full
   literal stdout of both in your final response — every line, not a summary.
   Do not end this turn until both have actually executed and you have their
   real output. Do not report a background process, a pending notification,
   or any other form of deferred completion — if you cannot finish executing
   both scripts in this turn, say so explicitly and stop, rather than
   describing future work as done.

Do not touch anything outside portfolio_udf_layouts.py, routers/udf.py, and
verify_udf01b.py. Do not commit or push - leave the working tree as-is for
manual review.
