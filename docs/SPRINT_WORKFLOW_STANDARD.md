# Sprint Workflow Standard — read this before drafting any sprint in this project

This is the single, canonical process. Every chat in this Project should follow it exactly — do not improvise a variant.

---

## When to use this at all

A "sprint" is for real, structural work: schema changes, new endpoints, new UI screens, permission changes, anything touching money/auth/tenant boundaries. Not every task needs one — a quick question, a one-line fix, or a discovery-only check can just be answered directly.

## The non-negotiable completion rule

**A sprint is not complete until its own verify script has actually executed, against the real database, with real output.** Not "the code is written." Not "the schema snapshot says this should work." Executed, with output.

This exists because it failed three times consecutively in one thread: an agent issued "pushed, sprint complete" reports based on re-reading `docs/schema_snapshot.sql` rather than running anything — because Claude Code had no `DATABASE_URL` in its environment and could not execute SQL at all. The reports were confident, plausible, and entirely unverified; the code contained column-name drift that would have failed on first execution.

**If the agent has no database access, it must say so explicitly and stop — not report completion.** This is a structural gap, not a prompt-wording problem: an environment without credentials cannot prove anything, and a completion claim from that environment means only that text was written.

---

## The four-part structure

**Part 1 — Schema, applied directly, ONLY if the shape is already known.**
If a sprint needs new tables/columns/roles with a genuinely *known* shape, apply the SQL directly (e.g. via the Supabase MCP tool — available both inside a running Claude Code sprint and directly in this chat interface) before the sprint runs, and VERIFY it landed with a real follow-up query — never trust a bare "success" response. ALWAYS apply Part 1 SQL and confirm it live BEFORE handing over the sprint's run command — never hand over the run command first and apply schema after.

If the correct shape depends on discovery the sprint itself hasn't done yet — leave Part 1 empty, deliberately. Let the sprint's own Task 1 (Discover) inform the schema, and build it in a later Task (usually Task 2). Do not guess DDL blind.

**Beware Part 1 SQL breaking existing objects.** A sprint's own Part 1 migration has broken a live trigger function in production (a `NOT NULL` column added with no default, which `fn_reverse_journal_entry` then couldn't satisfy). Adding a column is not automatically safe — check what existing functions, triggers, and views touch the table before applying.

**Part 2 — Branch confirm.**
```bash
cd /mnt/c/Users/Joe/2ndActCapital
git fetch origin
git status                                     # see below — divergence check
git checkout claude/inspiring-turing-v8hf1w   # or main, or a feature branch
git merge origin/claude/inspiring-turing-v8hf1w
claude -p "/refresh-schema" --permission-mode acceptEdits --allowedTools "Read,Write,Bash(cd*),Bash(python*),Bash(npm*),Bash(git*),mcp__supabase-2ndact-dev" --output-format json &
```

**`run_sprint.sh` does NOT create the sprint's branch — it pushes whatever branch is currently checked out.** This is why Part 2 exists, and skipping it has caused a real incident: a sprint landed directly on `main` because the operator was on `main` when the script ran, bypassing the entire review-before-merge convention. If a sprint needs its own branch, `git checkout -b <name>` before invoking the runner. Nothing in the script enforces this.

**Check for local/remote divergence before handing off, not just checkout/merge.** Claude Code once read a schema snapshot from a stale remote branch tip missing 8+ sprints' worth of tables, because local and remote had diverged silently. `git status`'s ahead/behind line is the cheap check; a snapshot read from the wrong tip produces confidently wrong column names throughout a sprint.

`/refresh-schema`'s own timeout was bumped from 300s to 600s after repeated transient exit-124 failures. If it still crashes with exit 124, just retry — it's usually transient, not a real problem. Note also: `/refresh-schema` tracks table/column/constraint structure only — it does NOT detect RLS policy changes. Always cross-check schema against the LIVE database when a sprint's premise depends on RLS behavior, not just the snapshot.

**What `schema_snapshot.sql` genuinely cannot represent** (confirmed general, affects every sprint): CHECK constraint bodies, RLS policies, and seed data. Anyone rebuilding an environment from the snapshot alone would silently be missing every correctness guarantee built into this platform. Treat the snapshot as authoritative for *column names and types only* — never for constraints, policies, or reference data.

`sprint_prompts/logs/decision_log.jsonl` and `*.done` files are gitignored on purpose — untracked, they used to constantly show as locally-modified and silently block `git checkout`/`git merge` between branches.

**Part 3 — The sprint prompt itself.** This is the artifact drafted in-chat, then saved to disk and executed. In practice this has consistently been done with a heredoc rather than an interactive editor — more reliable for pasting a long generated prompt over a terminal session:
```bash
cat > sprint_prompts/<name>.<tier>.md << 'EOF'
<paste the full prompt>
EOF
```
(`nano sprint_prompts/<name>.<tier>.md` works too if you'd rather paste interactively — save and exit with `Ctrl+O`, `Enter`, `Ctrl+X` — but the heredoc is the form actually used run after run in this project.)

**Confirm the file contains what you think it does before running it.** A sprint can silently execute a *different* sprint than the one named on the command line, if the wrong content was pasted into that filename's file. The runner trusts the filename, not the content — and the resulting run "succeeds" with a plausible cost/turn count giving no indication anything is wrong. This has happened twice: a stale Python file saved at a `.structural.md` path, and a duplicate-content collision between two prompts saved a minute apart. `head -5 sprint_prompts/<name>.<tier>.md` before invoking is enough.

Structure below.

**Part 4 — Merge.**
- `.lowrisk` sprints (discovery-only, or genuinely low-stakes) can auto-merge.
- `.structural` sprints (schema, permissions, money, auth, tenant boundaries) are HELD for manual review — read the verify log, confirm the reasoning holds, then merge deliberately.
- Don't assume there's always a separate feature branch to merge from. A leg can land its real work as plain uncommitted changes directly on local `main` (seen concretely in Altruist Sprint 6's third leg, which reused a prior leg's files and made a one-line fix rather than rebuilding). When that happens there's no `git merge` step at all — confirm the diff is proportionate to the sprint's actual scope first (`git diff --stat`, then read the diff on any file that touches routing/auth/public-path registration), then `git add` the real files, commit with the standard message, and `git push origin main` directly.
- Two other situations require a FULLY manual Part 4, bypassing `run_sprint.sh`'s own Step 4 entirely: (a) a leg recovered via `claude --resume <session_id>` (see the resume gotcha below) never goes through the wrapper's merge step at all; (b) an overall verify FAIL caused by something outside the sprint's own code (e.g. the stuck-transaction artifact described below) can leave genuinely good, fully-proven work unmerged — once the real cause is confirmed external, review and commit it manually rather than discarding real work over a false failure.

---

## The sprint prompt's real internal shape

Every prompt follows this skeleton, in this order:

```
<SPRINT NAME> — <ONE-LINE DESCRIPTION>. N tasks + verification.
<Context: what's already confirmed real, what prior sprint this
builds on, what the real problem is.>

CONFIRMED REAL FACTS, DO NOT RE-DERIVE:
- <anything already proven in a prior sprint — cite it>

THERE IS NO HUMAN AVAILABLE. Report findings, then continue
immediately in the same response. If uncertain, continue.

STANDING RULES: no interactive prompts; [any task-specific rules].

=== TASK 1: DISCOVER ===
Report findings, THEN CONTINUE IMMEDIATELY in the same response.
  1a. <specific, real thing to confirm against the live system —
      never assume from the prompt's own wording>
  1b. ...

=== TASK 2-N: BUILD ===
<the actual work, informed by Task 1's real findings>

=== TASK N: REAL PROOF ===
  - <proof that reproduces a bug BEFORE showing the fix, where
    applicable>
  - <proof of the negative case, not just the positive one>
  - <proof of persistence — an independent re-read, not just
    "the write didn't error">
  - <cross-org isolation, proven both directions>
  - <permission gate proven both ways: refuses the wrong caller,
    admits the right one, on the identical request>
  - Regression check: re-run every PRIOR sprint's own verify script
    and confirm they all still report clean. Every real sprint
    prompt from Sprint 4 onward has included this, and it matters —
    it's caught real cross-sprint breakage more than once. Each
    prior script is invoked as its own subprocess with a real
    timeout (180s has been the observed default); as the sprint
    count grows, so does this chain's total runtime and its
    exposure to the stuck-transaction failure mode described below
    — a FAIL here is not automatically a real regression, see below
    before assuming the worst.
    IMPORTANT: invoke each prior script EXACTLY ONCE, flat — never
    let a predecessor's own regression chain re-run inside yours
    (see "regression-chain nesting" below).

=== TASK N+1: UPDATE PROJECT STATUS ===
Update docs/PROJECT_STATUS.md [and any relevant design doc].

=== VERIFICATION: apps/api/scripts/verify_<name>.py ===
Pass/fail only. No interactive prompts.
Assertions: [Y] for each real proof above, explicitly listed.
Teardown must delete rows in strict FK child-before-parent order —
confirmed against information_schema, never assumed from memory of
a table's shape — and independently re-read every table the script
touched afterward to confirm it's back at the real pre-test count,
not just that teardown ran without error.
If the sprint posts a journal entry (or writes to any table with an
immutability trigger), teardown must disable/re-enable that trigger
around its own scoped delete — see "teardown vs. immutability
triggers" below.
```

**Why "THERE IS NO HUMAN AVAILABLE" matters**: without it, Claude Code sometimes stops mid-task waiting for confirmation that will never come. This line is load-bearing, not decoration.

**One real, correct exception**: a sprint that would make a hard-to-reverse change to shared production state (a live connection-role cutover, a destructive migration) may legitimately stop and ask despite this line — and has. That is the system working, not a bug to prompt around. If a sprint stops for that reason, confirm the specific step deliberately rather than forcing it through.

**Why "CONFIRMED REAL FACTS" matters**: prompts drafted in-chat are frequently *wrong* about the exact API shape, file location, or existing behavior — this project's own history includes many sprints correcting the prompt's own false premise. Marking a fact "confirmed" (from a prior sprint's real verify output) versus leaving it for Task 1 to discover keeps the sprint from re-deriving settled things while still catching genuinely wrong assumptions.

**Why the teardown FK-ordering rule matters**: a verify script that deletes a parent row before its FK-child row doesn't just fail its own teardown — the resulting `ForeignKeyViolationError` crash leaves orphaned rows in the shared live database that can cascade into false regression failures for every OTHER sprint's verify script run afterward in the same session (their scripts assert exact pre-test row counts). This happened for real in Altruist Sprint 6's second leg. See "Recovering from a live-database contamination" below for the actual recovery procedure.

---

## Commands, exactly

```bash
cat > sprint_prompts/<name>.<tier>.md << 'EOF'
<paste the Part 3 prompt>
EOF
```

```bash
./scripts/run_sprint.sh <name>.<tier>
```

Watch for the leg-completion summary and the `--- Step 3: verify ---` output. Step 3's output goes directly to the terminal only — it is NOT captured in `sprint_prompts/logs/<name>.<tier>.sprint.log` (that file only captures Step 2, the agent's own Part 3 execution). If you need to review verify detail after the fact, it has to have been captured from the terminal at the time; there's no separate log file to grep for it later.

**The runner's own `tail -f $SPRINT_LOG` suggestion is misleading.** `SPRINT_LOG` only ever receives `2>>` (stderr) — the real transcript goes to a per-leg JSON file on stdout that nothing in the runner's printed guidance mentions. Following the script's own advice during a live run shows nothing useful, and an empty log is NOT evidence the sprint did nothing. Read `sprint_prompts/logs/<name>.sprint.json` instead.

If it says `FATAL: expected verify script not found` — this is often a wrapper mismatch (e.g. a discovery-only sprint with no code to verify, or the agent's own edits landing in an isolated `.claude/worktrees/agent-<hash>/` directory on its own branch rather than at $REPO_ROOT), not lost work. Check before assuming failure:
```bash
ls -la .claude/worktrees/
git log --oneline -3
git status
```
Read the real leg result from `sprint_prompts/logs/<name>.sprint.json` (clean JSON, has `.result`/`.session_id`) — NOT `<name>.sprint.log` (raw appended stderr, often reads as empty or malformed).

**The runner has no way to represent "no verify script by design."** A discovery-only sprint that correctly declares "no verify script — the report is the deliverable" still gets a hard `FATAL`, even when Step 2 succeeded and committed cleanly. Treat this specific FATAL on a discovery sprint as expected noise, and confirm the real outcome from git.

**Merge, once verify passes (when there IS a separate branch):**
```bash
git checkout main
git pull origin main
git merge <branch-or-name> --no-edit
git push origin main
```
If instead the real work is sitting as uncommitted changes directly on local `main` (no separate branch — see Part 4 above), skip the merge step and go straight to `git add` / `git commit` / `git push`.

---

## Known operational gotchas — learned the hard way

- **`run_sprint.sh`'s `claude -p` calls need `--dangerously-skip-permissions`, not just `--permission-mode acceptEdits`.** `acceptEdits` alone still gates any Bash command with a live-DB footprint behind an interactive approval prompt that does not exist in headless `-p` mode — so the sprint hangs to its full turn/time budget and then reports it was blocked, often with an empty log and an implausibly short turn count. Fixed by adding the flag to all three call sites in the script. If a sprint reports success in under ~40 turns with no meaningful output, check this first: run the same prompt manually in the foreground and look at `permission_denials` in the JSON result.
- **Connection-drop mid-leg** (seen three times): signature is `API Error: Connection lost mid-response` followed by `FATAL: sprint leg reported an error (success)`. The working-tree state afterward has ranged from "nothing but discovery scratch files" to **2000+ lines of real, uncommitted service-layer changes touching already-shipped files**. Recovery sequence, in order: (1) `git status` / `git log` immediately, before anything else; (2) commit the WIP explicitly with an `UNVERIFIED` marker in the message so it can't be mistaken for verified work later; (3) hand-review any diff to files a *previous* sprint already verified — this is where real damage hides; (4) only then re-run the same `run_sprint.sh` invocation.
- **Worktree isolation**: when `claude -p` runs detached with `--dangerously-skip-permissions`, it can silently sandbox its own edits/commits into `.claude/worktrees/agent-<hash>/` on its OWN branch. The wrapper's Step 3 verify check looks for the verify script at $REPO_ROOT on whatever branch is checked out there — so `FATAL: expected verify script not found` can mean the real work landed correctly on the sprint's own branch inside that agent-worktree, not that it's lost. Clean up `.claude/worktrees/agent-*` directories once a sprint's work is fully merged, so they stop getting swept into later pushes.
- **Step 4 can auto-push stray content, even on `.structural` tier**: the agent's commit destination is inconsistent run-to-run — sometimes an isolated agent-worktree, sometimes committed directly onto local `main`. Step 4 (merge) then runs its own `git add`/commit/push at $REPO_ROOT regardless of tier, and this can sweep up unrelated stray content sitting there (a leftover untracked prompt file from a prior sprint, a stale already-merged worktree directory added as a broken embedded-repo gitlink) and push it straight to `origin/main` with a generic wrapper-written commit message — even on `.structural` tier, which is supposed to HOLD for manual review, not auto-push. This is a real gap in the tooling, not expected behavior. Fix: confirm the real sprint commit's contents via `git show --stat <sha>`, then `git revert <bad-wrapper-commit> --no-edit` + push to cleanly remove just the stray additions. After every run, check `git log --oneline -3` and `git show --stat HEAD` on main before trusting a push — don't assume Step 4 only committed what the agent actually produced.
- **A "fresh run" leg is not guaranteed to actually be fresh**: a leg labeled "fresh run" has come back having inherited a much older, unrelated conversation's context (evidenced by an implausibly high cached-token count for a small prompt, and responses referencing instructions never given this session). Don't re-run blind — resume that exact leg's session directly with an explicit, unambiguous instruction to proceed: `claude --resume <session_id> --permission-mode acceptEdits --dangerously-skip-permissions -p "THERE IS NO HUMAN AVAILABLE. Proceed immediately into Task 1 of <sprint description> and continue through Task 5 without stopping again." --output-format json > <file>.json 2>&1`. Since this bypasses `run_sprint.sh` entirely, Part 4 (merge) must then be done fully manually — there's no Step 4 auto-push to rely on or distrust.
- **A `-p` session can fabricate an "I'll wait for the background process and report back" completion.** No such notification mechanism exists in this tool. Seen at least three times, always on commands that genuinely take several minutes. Mitigate with explicit prompt language forbidding it, and by running long verify executions directly in the terminal rather than trusting a self-reporting session.
- **Regression-chain nesting compounds multiplicatively, not linearly.** When a verify script re-runs its predecessor's *full* script as its own regression gate, and that predecessor does the same to *its* predecessor, the earliest script re-executes once per depth level — 4× in a 4-deep chain, turning a ~5 minute verify into ~45. The durable fix is to state a predecessor's baseline as a CONFIRMED REAL FACT in the next prompt rather than instructing the sprint to re-run it.
- **`ANTHROPIC_API_KEY` and Claude Code's OAuth session actively conflict, and the failure is silent-then-expensive.** The key takes precedence, breaks `/refresh-schema` (which needs the OAuth session), and — discovered the hard way — routes an entire sprint's ordinary `claude -p` work through paid API billing instead of the Max plan, with no warning until credits run out mid-run. If `/refresh-schema` starts failing for no apparent reason, check whether `ANTHROPIC_API_KEY` is set in the shell.
- **venv/PATH cross-contamination**: `pip`/`python` on PATH can silently resolve to a DIFFERENT worktree's venv if one was activated earlier in the same shell and never deactivated — the `(venv)` prompt prefix doesn't say which one. Verify with `which pip`/`which python`, or just call `./venv/bin/pip` / `./venv/bin/python` directly by relative path to sidestep PATH entirely.
- **A stray untracked file at the merge target silently aborts `git merge`**: no ambiguity in the error, but if `git push` runs right after without checking, it can succeed by pushing whatever commits were already local — giving a false impression the merge happened. Always confirm `git log --oneline -3` shows a real merge/sprint commit before pushing.
- **Teardown vs. immutability triggers is a pattern, not a one-off.** A verify script that posts a journal entry cannot delete its own posted lines — the immutability trigger correctly blocks it, and the script leaks test rows into the live database on every run. Any GL-adjacent sprint needs the disable/re-enable-trigger teardown from the start: disable the trigger, scoped-delete every entry ID including any reversal, re-enable, then fail loudly (`SystemExit(2)`) rather than silently leaking if anything remains.
- **Chained regression subprocesses can leave a stuck Postgres transaction**: when Task N's regression check re-runs multiple prior sprints' verify scripts as subprocesses, a crash or a subprocess-timeout kill in one of them does not guarantee its Postgres session gets cleanly closed server-side. A session can be left `idle in transaction`, holding real locks — a later sub-check in the same chain can then hang against that lock until ITS OWN subprocess timeout kills it too, producing a FAIL that has nothing to do with that sprint's actual code (observed concretely in Altruist Sprint 6's third leg: Sprint 4's regression crashed early, Sprint 5's regression then hung for exactly its 180s timeout). Diagnostic: query `pg_stat_activity` for a session in `idle in transaction` state with an old `query_start` and `wait_event_type = 'Client'`; `select pg_terminate_backend(<pid>)` clears it (this can also retroactively roll back any uncommitted fixture rows that session had inserted, cleaning them up automatically). Then re-run the falsely-failed script(s) standalone — if they now pass cleanly and match their known-good baseline, the failure was purely the lock artifact, not a real regression, and the underlying sprint's work can be trusted and merged.

---

## When a later sprint finds a defect in an earlier, already-merged one

This happens regularly — it is normal, not exceptional. Real examples: a later sprint corrected an earlier one's idempotency gap; another found a missing scale constraint on a shipped column; another found and fixed a live RLS bypass in two unrelated pre-existing views.

In practice these get fixed via small out-of-band SQL patches applied directly, each tracked only in a commit message and a `PROJECT_STATUS.md` note. **There is no single index of "patches applied after the fact to sprint N"** — which means the full, real state of any given sprint's schema is reconstructable only by reading commit history. Worth building that index; until it exists, search commit messages before assuming a merged sprint's schema is exactly what its own prompt described.

**`PROJECT_STATUS.md` correction convention**: when a later sprint invalidates an earlier entry's prediction or finding, append `UPDATE <date>` under the original entry rather than writing a new, separate entry or editing the original in place. This emerged organically and works well — the original claim and its correction stay adjacent and both remain readable.

---

## Recovering from a live-database contamination

A verify script's own teardown can fail (most commonly: an FK-child row deleted after, rather than before, its FK-parent — see the teardown rule above) and leave orphaned rows in the shared live database. This doesn't just fail that one sprint — every OTHER sprint's regression check that runs afterward in the same session can fail too, because their own verify scripts assert exact pre-test row counts. When this happens (happened for real in Altruist Sprint 6):

1. Query the live tables directly (e.g. via the Supabase MCP connector) to see the actual current state of every table the failing teardown touched.
2. Confirm the suspect rows are genuinely same-run test fixtures before touching anything — matching timestamps within the crashed run's narrow window, synthetic/test-shaped values (`status='DRY_RUN'`, hand-picked test UUIDs), and org_ids matching the sprint's known test orgs. Never assume; a live production-adjacent database can hold real data by the time a sprint touches it.
3. Check `information_schema` for FK relationships so cleanup deletes children before parents.
4. Delete, then independently re-query every affected table to confirm it's back at 0/baseline before re-running or merging anything.
5. Fix the actual teardown bug in the sprint's re-run prompt as a standing rule (delete in strict FK child-before-parent order, confirmed against `information_schema`, never assumed) so it doesn't recur.
6. If a subsequent regression run in the same session produces failures that don't match a real code issue, check for the stuck-transaction pattern above before assuming a real regression.

---

## Running two sprints in parallel — use a worktree, never the same directory

Two Claude Code processes writing files and running git commands in the *same* working directory can interleave badly — one sprint's uncommitted edits can get swept into the other's commit, or a checkout/merge from one can pull the filesystem out from under the other mid-write.

```bash
cd /mnt/c/Users/Joe/2ndActCapital
git worktree add ../2ndActCapital-<name> <branch>
cd ../2ndActCapital-<name>
```

This gives a second, independent working directory sharing history but with separate files. Each worktree needs its own venv (`python3 -m venv venv && source venv/bin/activate && pip install -r requirements.txt --break-system-packages`) and its own Doppler scope (`doppler setup`, confirm project `hollisworks`, correct config — check `doppler.yaml` for the current real config name before assuming).

**A fresh worktree has no venv and no Doppler scope at all** — `git worktree add` only checks out tracked files, so anything untracked and local (the venv, any `.env`) simply doesn't exist there. A verify step failing immediately in a new worktree with `python: command not found` or `Doppler Error: You must specify a project` is this, not a real problem with the sprint.

**When merging two parallel sprints' work back to `main`, expect real conflicts if they touched the same file** (e.g. both adding a key to `org_settings.DEFAULT_SETTINGS`, or both registering a router in `main.py`). These are almost always "keep both sides" resolutions — resolve with:
```bash
sed -i '/^<<<<<<< HEAD$/d; /^=======$/d; /^>>>>>>> <branch-name>$/d' <file>
```
then manually verify the result reads correctly before `git add`ing it. Do not blindly pick one side — read what each side actually added first.

---

## Local execution — always through Doppler

Never read a secret from a local `.env` file or hardcode one. Every local Python invocation:
```bash
doppler run -- venv/bin/python apps/api/scripts/verify_<name>.py
```
If a fresh shell/worktree says `Doppler Error: You must specify a project`, run `doppler setup` and confirm it against `doppler.yaml`'s actual current values (project `hollisworks`; config name has been `prd` at various points — verify directly with `doppler secrets --only-names` rather than assume).

A standalone re-run of one verify script this way is also the fastest way to distinguish a real regression from an artifact of a chained run (see the stuck-transaction gotcha above) — if it passes clean standalone at its known baseline count, trust that over a FAIL seen only inside a longer chained run.

**Never let a secret's value reach terminal output.** A Doppler command's own confirmation table has printed a live credential into a session transcript — filtering the command's output for the *variable name* is not enough, because the tool echoes the value in its own formatting. Pipe through a redaction step, or use commands that report only names (`doppler secrets --only-names`). If a value does leak, rotate it immediately rather than assessing how much of it was visible.

---

## What "good" looks like in a verify log

- Every assertion explains *why* it matters, not just what it checked.
- A `[FIND]` marks a genuine discovery (a wrong assumption in the prompt corrected against live code, or a real design decision worth recording) — distinct from a plain PASS.
- Negative cases are proven as rigorously as positive ones (a refusal that actually leaves data unchanged; a filter that excludes what it should, not just includes what it should).
- Cross-org isolation is proven in both directions on the same test, not one-sided.
- A permission gate is proven on the **identical request** — refused for the wrong caller, admitted for the right one. A gate that refuses everyone passes a one-sided negative test trivially.
- Cross-org isolation is tested under a genuinely non-bypassing role. Assert `rolbypassrls = false` on the test role explicitly — if the connection bypasses RLS, every isolation check downstream proves nothing.
- Teardown restores an exact before/after row count, never an unconditional TRUNCATE — any table may hold real production data by the time a sprint touches it.
