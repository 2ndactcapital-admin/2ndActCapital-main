Approved: add a real `tags / create` permission row. Gate minting on it.

DB/bash access should now be available with correct allowedTools on this
resume - retry the blocked queries before assuming still blocked.

Apply the corrected Part 1 migration file, verify each object landed with
a real follow-up query (not just DDL success), then proceed into Tasks 2
and 3 and run the verify script for real. Report actual pass/fail per
assertion, not code written.
