BULKFETCH UI 2 — a "Run on your laptop" instructions dialog: when the operator
queues a run with target Laptop on the Bulk download tab, show a dialog with
the exact steps to start (or check) the laptop worker, with copy buttons and a
live worker-status check. UI-only plus one small API field. 4 tasks +
verification.

Context: bulkfetchui (merged to main) added the Bulk download tab on the EDGAR
Pipeline page. Queuing a Laptop run when no laptop worker is heartbeating
leaves the run 'queued' and shows the start command inline. Joe wants a proper
dialog with the full instructions every time he queues a Laptop run, so he
does not have to remember tmux, the folder, the Doppler config or the
keep-awake setting.

CONFIRMED REAL FACTS, DO NOT RE-DERIVE (checked 2026-10-20):
- POST /api/v1/admin/edgar/bulk/runs queues a run; with no laptop worker
  heartbeat in the last 2 minutes it returns worker_online=false and a start
  command. GET /api/v1/admin/edgar/bulk/workers returns the last heartbeat per
  target and host. Both are super-admin only.
- The start command returned today is
  "doppler run -- python3 apps/api/scripts/run_bulk_fetch.py --worker --target laptop",
  which is NOT what works on Joe's laptop. The command that works is:
    cd /mnt/c/Users/Joe/2ndActCapital && doppler run -p hollisworks -c prd -- venv/bin/python -u apps/api/scripts/run_bulk_fetch.py --worker --target laptop
- The worker is run inside tmux: tmux new -s bulkworker → run the command →
  detach with Ctrl-B then D; reattach with tmux attach -t bulkworker; stop with
  Ctrl-C inside it. The verify scripts refuse to run while a real worker is
  heartbeating.
- The laptop must stay awake for the run (Windows: Settings → System → Power →
  Sleep "Never" when plugged in). A stopped or crashed run can be requeued and
  continues where it stopped.
- Render target is refused (409) until a Render worker is set up; this sprint
  does not change Render behaviour.
- Baselines: verify_bulkfetchui 106/106 (with fix commits 4ecb14c, 9bae801),
  verify_edgarpipelinea 102/102 (~10 min).

THERE IS NO HUMAN AVAILABLE. Report findings, then continue immediately in the
same response. If uncertain, continue.

STANDING RULES: no interactive prompts. There is NO background-process
notification mechanism — never wait for one, never poll in a loop for a file,
never start a script in the background; run every command synchronously in the
foreground. No real SEC, R2 or Render calls. Write the verify script but DO NOT
RUN IT. Never print a secret value. UI: light theme, whites and creams,
matching the existing admin screens. No AI model anywhere.

=== TASK 1: DISCOVER ===
Report findings, then continue immediately.
  1a. Where the Bulk download tab handles the Queue run confirm and the queue
      response (component and functions), and how worker status is fetched.
  1b. Where the start command string is built on the server, and whether the
      app has an existing dialog/modal component to reuse.

=== TASK 2: SERVER — ONE SOURCE FOR THE INSTRUCTIONS ===
  - Make the laptop start command configurable: setting
    EDGAR_BULK_LAPTOP_WORKER_COMMAND (read like other settings), defaulting to
    the working command in CONFIRMED FACTS. GET workers and POST runs both
    return it as laptop_start_command.
  - GET workers also returns laptop_instructions: an ordered list of steps
    {title, detail, command?} built from that command (open the Ubuntu/WSL
    terminal; tmux new -s bulkworker; run the command; detach Ctrl-B then D;
    keep the laptop awake; to check later: tmux attach -t bulkworker; to stop:
    Ctrl-C inside tmux — and stop it before running verify scripts; a stopped
    run can be requeued). The dialog renders this list; no instruction text is
    hard-coded only in the front end.

=== TASK 3: THE DIALOG ===
When the operator confirms Queue run with Run on = Laptop:
  - The run is queued as today, then a dialog opens titled "Run on your
    laptop".
  - Top: worker status, re-checked every 5 s while the dialog is open —
    green "Laptop worker running on <host> — your run will start
    automatically" or amber "No laptop worker running — your run is queued and
    will start when you start one".
  - Below: the numbered steps from laptop_instructions; each step with a
    command shows it in a monospace box with a Copy button (copied state shown
    briefly). When the worker is already running, the start steps collapse
    under "How to start the worker" and the keep-awake / check / stop steps stay
    visible.
  - Buttons: "I've started it — check again" (immediate re-check), "View run"
    (closes the dialog and highlights the run's row), "Close".
  - A "Show these steps" link beside the Laptop radio opens the same dialog
    without queuing anything.
  - "Don't show this again when the worker is running" checkbox, stored per
    browser (localStorage, wrapped in try/catch); the dialog always shows when
    the worker is NOT running.
  - Render choice: unchanged (inline refusal, no dialog).
  - Accessible: focus moves into the dialog, Esc closes it, focus returns to
    the Queue button.

=== TASK 4: UPDATE PROJECT STATUS ===
docs/PROJECT_STATUS.md: the dialog, the setting name and its default.

=== VERIFICATION: apps/api/scripts/verify_bulkfetchui2.py ===
WRITE IT. DO NOT RUN IT. Pass/fail only, no prompts. MUST print
'TOTAL: N PASS, M FAIL' and exit non-zero on failure. Hydrate secrets from
Doppler over HTTPS. Mock SEC, R2 and Render. Wrap every section so an exception
is a [FAIL], never a crash; teardown at start and end, then re-read every
touched table for its exact pre-test count. Fixture users created explicitly
with the auth0_sub the mocked token carries.

Verify-script rules — each one broke a real script:
  - check(passed, label, detail): condition FIRST; isinstance(passed, bool).
  - Fixture values satisfy live CHECK and FK constraints (pg_constraint,
    contype ::text). Never derive a unique value from a fixture-UUID slice.
  - RLS context on every read. An empty list never passes a check.
  - Never spawn a background process. Nested regression only after teardown,
    1200 s timeout, and only when no real worker is heartbeating.
  - Never touch real note_gold_values or real edgar_index_filings rows.

Assertions:
  [Y] Task 1 findings reported
  [Y] GET workers and POST runs return laptop_start_command equal to the
      default working command when the setting is unset, and to the setting's
      value when set (monkeypatched)
  [Y] laptop_instructions is a non-empty ordered list containing the tmux new,
      the start command, the Ctrl-B D detach, keep-awake, tmux attach and the
      stop-before-verify step
  [Y] Queue on Laptop with no worker: run queued (independent re-read) and
      worker_online false; with a mocked fresh heartbeat: worker_online true
      and the host returned
  [Y] Render still refused 409 with nothing queued (count before/after)
  [Y] Front end (node tests on the dialog logic): dialog opens after a Laptop
      queue; amber state when offline, green with host when online; start steps
      collapsed only when online; every step with a command renders a Copy
      button; "Show these steps" opens without a POST; the don't-show option
      suppresses the dialog only when online; no dialog for Render
  [Y] Front end source: the dialog renders steps from laptop_instructions (no
      hard-coded copy of the command in the component); npm run build exits 0
  [Y] Permission gate unchanged: org admin refused, super-admin admitted on
      GET workers and POST runs, identical request
  [Y] Regression: verify_bulkfetchui.py runs ONCE as a flat subprocess AFTER
      teardown; 0 FAIL
  [Y] Teardown leaves zero fixture rows; every touched table at its pre-test count
