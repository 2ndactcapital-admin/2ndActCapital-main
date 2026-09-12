# Sprint Workflow Standard — read this before drafting any sprint in this project

This is the single, canonical process. Every chat in this Project should follow it exactly — do not improvise a variant.

---

## When to use this at all

A "sprint" is for real, structural work: schema changes, new endpoints, new UI screens, permission changes, anything touching money/auth/tenant boundaries. Not every task needs one — a quick question, a one-line fix, or a discovery-only check can just be answered directly.

## The four-part structure

**Part 1 — Schema, applied directly, ONLY if the shape is already known.**
If a sprint needs new tables/columns/roles with a genuinely *known* shape, apply the SQL directly (e.g. via the Supabase MCP tool) before the sprint runs, and VERIFY it landed with a real follow-up query — never trust a bare "success" response.

If the correct shape depends on discovery the sprint itself hasn't done yet — leave Part 1 empty, deliberately. Let the sprint's own Task 1 (Discover) inform the schema, and build it in a later Task (usually Task 2). Do not guess DDL blind.

**Part 2 — Branch confirm.**
```bash
cd /mnt/c/Users/Joe/2ndActCapital
git fetch origin
git checkout claude/inspiring-turing-v8hf1w   # or main, or a feature branch
git merge origin/claude/inspiring-turing-v8hf1w
claude -p "/refresh-schema" --permission-mode acceptEdits --allowedTools "Read,Write,Bash(cd*),Bash(python*),Bash(npm*),Bash(git*),mcp__supabase-2ndact-dev" --output-format json &
```

**Part 3 — The sprint prompt itself.** This is the artifact drafted in-chat, saved via `nano`, then executed. Structure below.

**Part 4 — Merge.**
- `.lowrisk` sprints (discovery-only, or genuinely low-stakes) can auto-merge.
- `.structural` sprints (schema, permissions, money, auth, tenant boundaries) are HELD for manual review — read the verify log, confirm the reasoning holds, then merge deliberately.

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

=== TASK N+1: UPDATE PROJECT STATUS ===
Update docs/PROJECT_STATUS.md [and any relevant design doc].

=== VERIFICATION: apps/api/scripts/verify_<name>.py ===
Pass/fail only. No interactive prompts.
Assertions: [Y] for each real proof above, explicitly listed.
```

**Why "THERE IS NO HUMAN AVAILABLE" matters**: without it, Claude Code sometimes stops mid-task waiting for confirmation that will never come. This line is load-bearing, not decoration.

**Why "CONFIRMED REAL FACTS" matters**: prompts drafted in-chat are frequently *wrong* about the exact API shape, file location, or existing behavior — this project's own history includes many sprints correcting the prompt's own false premise. Marking a fact "confirmed" (from a prior sprint's real verify output) versus leaving it for Task 1 to discover keeps the sprint from re-deriving settled things while still catching genuinely wrong assumptions.

---

## Commands, exactly

```bash
nano sprint_prompts/<name>.<tier>.md
```
Paste the Part 3 prompt. Save and exit (`Ctrl+O`, `Enter`, `Ctrl+X`).

```bash
./scripts/run_sprint.sh <name>.<tier>
```

Watch for the leg-completion summary and the `--- Step 3: verify ---` output. If it says `FATAL: expected verify script not found` — this is often a wrapper mismatch (e.g. a discovery-only sprint with no code to verify), not lost work. Check before assuming failure:
```bash
git log --oneline -3
git status
```

**Merge, once verify passes:**
```bash
git checkout main
git pull origin main
git merge <branch-or-name> --no-edit
git push origin main
```

---

## Running two sprints in parallel — use a worktree, never the same directory

Two Claude Code processes writing files and running git commands in the *same* working directory can interleave badly — one sprint's uncommitted edits can get swept into the other's commit, or a checkout/merge from one can pull the filesystem out from under the other mid-write.

```bash
cd /mnt/c/Users/Joe/2ndActCapital
git worktree add ../2ndActCapital-<name> <branch>
cd ../2ndActCapital-<name>
```

This gives a second, independent working directory sharing history but with separate files. Each worktree needs its own venv (`python3 -m venv venv && source venv/bin/activate && pip install -r requirements.txt --break-system-packages`) and its own Doppler scope (`doppler setup`, confirm project `hollisworks`, correct config — check `doppler.yaml` for the current real config name before assuming).

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

---

## What "good" looks like in a verify log

- Every assertion explains *why* it matters, not just what it checked.
- A `[FIND]` marks a genuine discovery (a wrong assumption in the prompt corrected against live code, or a real design decision worth recording) — distinct from a plain PASS.
- Negative cases are proven as rigorously as positive ones (a refusal that actually leaves data unchanged; a filter that excludes what it should, not just includes what it should).
- Cross-org isolation is proven in both directions on the same test, not one-sided.
- Teardown restores an exact before/after row count, never an unconditional TRUNCATE — any table may hold real production data by the time a sprint touches it.
