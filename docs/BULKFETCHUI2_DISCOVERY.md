# BULKFETCH UI 2 — Task 1 discovery (2026-10-20)

Read from the code on `bulkfetchui-work` at 5b1f868 (bulkfetchui + fix commits
4ecb14c, 9bae801). No database or network access was needed for these two
questions.

## 1a. Where the tab confirms Queue run, handles the response, and fetches worker status

All in `apps/web/components/admin/EdgarBulkDownload.jsx`, default export
`BulkDownloadTab`, with the pure logic in `apps/web/lib/edgarBulkDownload.mjs`
(node tests in `apps/web/tests/edgar/`):

| Step | Where |
|---|---|
| "Queue run" button | `BulkDownloadTab` render, inside `{writable && …}`. Its onClick is `setConfirming(true)` |
| confirm dialog | `ConfirmDialog({ preview, targetLabel, onCancel, onConfirm, pending })`. Its "Queue run" button calls `onConfirm` = `queue()` |
| the POST | `queue()` → `queueBulkRunAction({ params, target })` (server action, `lib/edgarPipelineActions.js` → `POST /api/v1/admin/edgar/bulk/runs`) |
| the response | `queue()`: `setConfirming(false)`, `setOutcome(queueOutcome(res))`, then `refreshRuns(0)`, `setRunsPage(0)`, `refreshWorkers()`. `queueOutcome` (lib) turns `worker_online: false` into the inline "Queued. No laptop worker is running — start one with:" plus `tmux_command ?? start_command` |
| worker status | `refreshWorkers()` → `loadBulkWorkersAction()` → `GET /api/v1/admin/edgar/bulk/workers`, stored in `workers`. It loads on mount, and re-loads every 10 s **only while a run is queued or running** (`runsActive`). `laptopStatus(workers)` / `renderStatus(workers)` (lib) give the Run-on dots and text |

**FIND:** the 10 s worker refresh only runs while a run is active. Once a run is
queued, that holds. "Show these steps" opens without queuing, though, so the
dialog needs its own 5 s re-check that does not depend on `runsActive`.

**FIND:** the inline status prefers `tmux_command`, which today is
`tmux new -s bulkworker 'doppler run -- python3 …'`. That wraps the same
non-working command, so it is also wrong on Joe's laptop.

## 1b. Where the start command is built, and whether a dialog component exists

**Server.** `apps/api/services/edgar_bulk_queue.py` holds module constants
`LAPTOP_COMMAND = "doppler run -- python3 apps/api/scripts/run_bulk_fetch.py --worker --target laptop"`
and `LAPTOP_TMUX_COMMAND = f"tmux new -s bulkworker '{LAPTOP_COMMAND}'"`. Two
places return them:

- `workers_state(conn)` (GET workers) → `laptop.start_command`, `laptop.tmux_command`;
- `_check_target(conn, target)` (POST runs and POST runs/refresh-index, via
  `queue_bulk_run` / `queue_refresh_index`) → `start_command` and `tmux_command`
  when no laptop worker heartbeated within `WORKER_LOST_SECONDS` = 120. It
  returns **no host** when the worker IS online (`{"worker_online": True}` only).

Other settings in this module are read with `(os.environ.get(NAME) or "").strip()`
(`RENDER_WORKER_ENV`, `RENDER_API_KEY`), and Doppler populates the environment.
Nothing caches them at import, so a monkeypatched environment takes effect on the
next request.

The only other consumer is `verify_bulkfetchui.py:768`, which asserts
`start_command == q.LAPTOP_COMMAND`. This sprint keeps `LAPTOP_COMMAND` as the
default and changes that assertion to `q.laptop_start_command()`, so the
regression still holds if the Doppler setting is ever set.

**Dialog component.** None to reuse. `components/ui/` holds only `DataGrid.jsx`.
The two existing modals (`ConfirmDialog` in EdgarBulkDownload.jsx and one in
`OrgSettingsEditor.jsx`) are inline `role="dialog" aria-modal="true"` divs,
with no focus move, no Esc and no focus return. The new dialog follows their
visual shell (fixed overlay, white card with hairline border, 6 px radius) and
adds the accessibility handling locally.

**Row highlight.** `DataGrid` already takes `selectedRowId` (a `bg-[var(--2a-bg)]`
row tint) and `getRowStyle`, so "View run" can highlight its row without touching
the shared grid.
