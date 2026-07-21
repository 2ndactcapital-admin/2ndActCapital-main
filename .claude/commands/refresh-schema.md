---
description: Regenerate docs/schema_snapshot.sql from the live dev DB and push it before running any sprint Part 3 prompt.
---

Run this before touching any sprint code:

1. Run `python scripts/refresh_schema.py` from repo root.
2. Diff the resulting `docs/schema_snapshot.sql` against the last committed version. Summarize what changed (new/dropped columns, tables, constraints) in plain language — call out anything that affects `journal_lines`, `journal_entries`, or `reference_data` specifically, since those have known drift-prone fields (`entry_id` vs `journal_entry_id`, `ledger_basis`, `line_no NOT NULL`).
3. If the Supabase MCP server (`supabase-2ndact-dev`) is connected, cross-check the diff against the live schema directly (list tables / describe columns) rather than trusting the script output alone — catches cases where the script itself is stale.
4. Commit the refreshed snapshot to the current feature branch with message `chore: refresh schema snapshot`.
5. Stop and report the diff summary. Do not proceed into Part 3 code generation until the diff has been confirmed as expected — an unexpected change means the schema drifted from what the sprint plan assumed, and the sprint's SQL (Part 1) needs to be re-checked against it first.
