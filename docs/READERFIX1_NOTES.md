# READERFIX1 — both AI readers answer within their token allowances

Branch `readerfix1-work`. Sprint prompt: `sprint_prompts/readerfix1.structural.md`.

## Task 1 — discovery (read-only; live DB read 2026-11-12 ~21:30 UTC, code at 73b54e3)

### How each reader builds its request
- **Fund terms** (`services/replica_terms.py`): `read_one` sent `{"model", "messages", "max_tokens": READER_MAX_TOKENS (3000), response_format, metadata}` — no `reasoning_effort`. `read_pair` reserved both calls together with `ai_budget.pair([(m1, chars, 3000), (m2, chars, 3000)])`. Its own request code — it did not use `note_extraction.readers.ReaderConfig`.
- **Note terms** (`services/note_extraction/readers.py`): `ReaderConfig(slot, deployment, reasoning_effort=None, max_tokens=4000)`; `request_body` adds `reasoning_effort` only when set. The daily run (`ai_daily_runs._note_read`) builds both slots with `runner.reader_config(slot, dep)` → no effort, 4,000 tokens. `prefill.MIN_MAX_TOKENS = 32000` is applied only by the gold prefill, never by the daily runner.

### Why nano "stopped after one reading" — NOT a code stop [FIND]
One reader call reads the WHOLE document and answers every field in one JSON object. A successful call is stored as one row per field (gpt-5-mini's 13 rows for IVV = 13 fields of ONE call, `extraction_run_id 3c09e402…`, all 7,408 in / 2,406 out). A failed call is stored as ONE `field_key = '__call__'` row with the call's status — nano's single `invalid` / `empty response` row (7,408 in / 3,000 out) is that marker. So nano did not stop on field 1; its one call for all fields came back empty. Per-field failure handling already exists (`schema.parse_reader_output` drops only the field that fails its shape; notefix1). The fix is therefore the empty answer itself, and the verify proves a field-level failure on field 1 keeps fields 2..n.

### The "only one reader found a value" rule
`replica_terms.compose_values` (line ~761): a field with a value from exactly one model reader gets `needs_review` + reason "only one reader found a value"; acceptance needs both readers to agree. UNCHANGED by this sprint.

### Why the retry / re-read never happened for IVV [FIND]
`run_extraction` REUSES any prior run for the same document + prompt + the same two models, and `_replica_queue` skips any fund that already has a model reading for those models. A run where one reader failed counts as "read", so IVV would never be read again. Task 3 changes that, once.

### Budget reservation per call
`ai_budget.estimate_tokens = ceil(prompt_chars / 4) + max_tokens`, reserved under a row lock against `ai_daily_usage` before the call, then SETTLED to the provider-reported usage after it. A larger `max_tokens` therefore only holds more budget WHILE a call is in flight; what counts toward the day afterwards is the real usage. A call made outside a pre-made pair reservation (e.g. a retry) takes its own reservation in `proxy.chat → scope.before_call`, and raises `BudgetReached` without calling when it would cross the budget.

### Evidence for the numbers (live readings, read-only)
| | input tokens | output tokens (total, incl. reasoning) | visible answer (value + quote JSON, chars/4) |
|---|---|---|---|
| IVV, gpt-5-mini, default (medium) effort | 7,408 | 2,406 | ~513 |
| IVV, gpt-5-nano, default effort | 7,408 | 3,000 = the whole allowance, no text | — |
| 99 note calls, gpt-5-mini, default effort, 32k allowance (gold prefill, Oct 8–14) | avg 16,919 | p50 8,266 · p90 12,249 · max 15,426 | median ~1,858 · max ~4,808 |

So: at default effort the reasoning tokens are 75–90 % of the output; a note answer alone can exceed the daily runner's 4,000 tokens (max ~4,808) — even gpt-5-mini would come back empty or truncated on notes at 4,000.

### The nightly note runs have read ZERO notes [FIND]
`ai_daily_runs` note_terms_extraction: `stopped_error` every night 2026-11-06 … 2026-11-12 with the numeric(12,6) overflow that dailyops1 fixed (in this branch's history, not yet deployed). `portfolio.note_term_readings` holds NO `model_2` row at all and no gpt-5-nano reading; every `model_1` reading is from the Oct 8–14 gold prefill. **Notes affected (Model 1 value + empty/invalid Model 2): 0.** The Model-2-only re-read is still built (Task 3) — it costs nothing when there is nothing to re-read, and it covers anything read between the dailyops1 deploy and this one.

The replica run of 2026-11-12 finished `queue_empty` with SPY failed ("no SEC series id") — waits for edgarops1. IVV was read by the Add-on-Replication path, not the nightly.

## Task 2 — the fix (code)

**One shared setting** — `services/note_extraction/readers.py`:
- `reader_settings(slot, deployment, *, answer_tokens, catalog, effort=None, max_tokens=None) -> ReaderConfig` — the ONE per-deployment reader setting. The note reader uses it through `runner.reader_config` (answer allowance `NOTE_ANSWER_TOKENS = 4000`); the fund reader through `replica_terms.reader_cfg` (answer allowance `READER_MAX_TOKENS = 3000`). It is the same function at call time (the verify swaps in a spy and sees both readers call it).
- A **reasoning model** (`is_reasoning_model`: the proxy's `supports_reasoning` flag, or a gpt-5 / o1 / o3 / o4 name on the deployment or its upstream) gets `reasoning_effort = "low"` and `max_tokens = answer allowance + REASONING_HEADROOM_TOKENS (12,000)`: **funds 15,000, notes 16,000**. Retry allowance = 2× (**30,000 / 32,000**). Any other model: no effort, allowance unchanged (3,000 / 4,000).
- Why these numbers: at the DEFAULT (medium) effort gpt-5-mini needed 2,406 tokens on IVV (answer ~513) and p50 8,266 / p90 12,249 / max 15,426 on notes (answer up to ~4,808). The 12,000 headroom is at least mini's medium-effort p90 on notes, and LOW effort needs less reasoning than that. 16,000 is above mini's medium-effort maximum on notes. The 32,000 note retry is the gold prefill's proven floor (`prefill.MIN_MAX_TOKENS`). An explicit operator override (pilot / eval CLI `--effort`, `--max-tokens`) still wins. The pilot's `--max-tokens` default is now None, meaning the shared setting.
- `readers.send(cfg, body, dep, before_retry=…)` makes the call. An answer that comes back empty **because of the allowance** (empty content AND either `finish_reason = 'length'` or output tokens ≥ the allowance) is retried **once** with `retry_max_tokens`. Both attempts go through `proxy.chat`, so each is reserved and settled against the daily budget. Their tokens and cost are SUMMED on the call. If the retry is also empty, the call is `invalid` with the error "**the model used its whole output allowance** (retried once with N tokens)". If the budget or dollar cap refuses the retry, it is not made: the call is `invalid` with the same reason plus "retry not made: BudgetReached …". An empty answer that is not from the allowance (finish 'stop', under the limit) is not retried. It stays "empty response".
- Recorded: the call's `reasoning_effort`. Reading `metadata.allowance_retry` holds `{made, first_max_tokens, first_output_tokens, first_finish_reason, retry_max_tokens, retry_output_tokens | reason}`. On fund readings, `metadata` also carries `reasoning_effort` and `max_tokens` (the column existed and was never written before). The key is `allowance_retry`, not `retry`, because the note cascade already writes `retry: 'fuller_text'` into the same metadata. The verify caught that clash.
- `proxy.Deployment.supports_reasoning` is read from the proxy's `/model/info` when it reports one.
- Field-level: one call answers all fields. A field that fails its own shape is dropped (notefix1) and the other fields are kept. Proven again for the fund reader (S5).

**Budget — still honest, configs untouched.** Per call, the reservation is ceil(prompt chars / 4) + max_tokens, and it is SETTLED to the real usage after the call. A retry takes its own reservation, and the budget may refuse it. Proven on the real `ai_daily_usage` ledger (section B): with a Model 2 budget of exactly first + retry, the retry is made and the ledger records both attempts. One token less, and the retry is refused before it is made. Used ≤ budget in both cases, and nothing is left reserved.

Expected documents per day at the new allowance (Model 2 = gpt-5-nano is the binding budget in both tasks):
- **Funds** (250,000 tokens/day each): IVV's prompt ≈ 7,868 tokens (est; provider counted 7,408). The reservation is 22,868 per call (was 10,868), so ≥ 10 funds can be in flight against a fresh day. Once settled, a low-effort call should use roughly 7,400 in + 1,000–3,000 out, so about **20–25 funds/day ≈ 260–325 fund fields/day** (13 model fields each). That falls to ≈ **6/day** if every call needs the 30,000 retry. Today's real queue is 2 funds (IVV, SPY).
- **Notes** (nano 500,000/day; mini 1,500,000/day): the trimmed prompt is ≈ 17,000 tokens. The reservation is 33,000 per call (Model 2's pair half). Settled at low effort, expect roughly 17,000 in + 3,000–8,000 out, so about **20–25 notes/day** on nano's budget, or ≈ 7/day if every call needs the 32,000 retry. Before this fix, the 4,000-token allowance would have reserved about 21,000 per call. But the answers would have come back empty for BOTH models: a note answer alone runs up to ~4,800 tokens.

## Task 3 — re-read once (no manual step)

- **Funds** (`replica_terms.reread_due_sql`, one SQL fragment used by both `run_extraction` and the nightly `_replica_queue`): a fund is due when its LATEST model run (same prompt + same two models) has a model `__call__` row whose status is not 'ok', and no reading of the fund carries `metadata.reread`. The nightly queue then selects it again. `run_extraction` skips reuse and reads the pair (both readers) again, marking every reading `metadata.reread = true`. It happens once, whatever the re-read returns. A Replication Studio "extract" before the nightly does the same re-read (same rule). The nightly only writes readings. A fund's open draft picks them up at the next extract, which reuses the re-read run at no model cost. **IVV is due now** (live check L). **SPY** stays blocked on "no SEC series id" until edgarops1.
- **Notes** (`ai_daily_runs.NOTE_REREAD_SQL` / `note_reread_due`): a note is due when it has a Model 2 call that is not 'ok' in a run where Model 1's call IS ok, Model 2 has never succeeded on it since, and no reading carries `metadata.reread_model_2`. Due notes come FIRST in the nightly queue, newest first, with `reread_model_2: true`. `_note_read` rebuilds Model 1's call from its stored readings (`cascade.prior_model_1_call`: the call row plus its field rows by `call_id`, cost 0). `run_note(prior_model_1=…)` then reserves and calls **Model 2 only** — also for the fuller-text retry. It does not write Model 1's readings again. It compares as usual and stages the note under the new run. Model 2's call row records `reread_model_2` and `model_1_call_id`. It happens once, inside the daily budgets (the same budget scope).
- **Notes affected today: 0** (live read-only count, section L). The nightly note runs never read a note, because every night failed on the numeric overflow fixed by dailyops1 (see Task 1).

## Task 4 — verify

`apps/api/scripts/verify_readerfix1.py` (added to `scripts/regression_set.txt`). Sections: S self-checks (pure) · B the real ledger · R eligibility inside a ROLLED-BACK transaction (funds and notes, including the real nightly queues pointed at that transaction) · N the real note cascade's Model-2-only re-read (stub transport) · L READ-ONLY live checks · X fast regressions. The stub transport returns the real response shape (content + finish_reason + usage incl. `completion_tokens_details.reasoning_tokens`). No model call, no network.

One-offs (each `timeout 600`, 2026-11-12):
| run | result |
|---|---|
| `--self-checks-only` | 26 PASS / 0 FAIL |
| `--section b` (real ledger) | 37 / 0 |
| `--section r` (rolled back) | 39 / 0 |
| `--section n` (real cascade) | 34 / 0 |
| `--section l` (read-only live) | 32 / 0 |
| full, `VERIFY_NESTED=1` (all DB sections, regressions run separately) | **55 / 0** |
| `--cleanup-only` | 27 / 0 |
| verify_dailyops1 `--self-checks-only` | 28 / 0 |
| verify_aibudget1 `--self-checks-only` | 5 / 0 |
| verify_rep1 `--self-checks-only` | 5 / 0 |
| verify_rep3 `--self-checks-only` | 7 / 0 |
| verify_dailyops1 `--section d` (real `_note_read`, the queue + reader this sprint changed) | 44 / 2 — see FIND |
| verify_dailyops1 `--section e` (replica job) | 30 / 2 — see FIND |

[FIND] Both dailyops1 section FAIL pairs are "portfolio.securities_global at its pre-test count / real securities unchanged". The real (non-fixture) security count grew during each run, 16,047 → 16,094, 16,119 → 16,169. It then grew again from 16,169 → 16,194 between two plain reads with no verify running. A live writer outside this sprint is adding securities (possibly track1's lists / gap fill). This sprint writes no security. Every functional check in both sections passed.

[FIND] The note-extraction verifies (`verify_noteextractb1`, `verify_notefix1/2`, `verify_notefields`, `verify_notetermsextraction`) have no `--self-checks-only` mode, so they cannot run as fast nested regressions. They were not run (the rule is not to run another sprint's full verify).

[FIND] The fix is proven against recorded response shapes, not a live model. Whether gpt-5-nano at LOW effort answers inside 15,000 / 16,000 tokens will show on the first real run. Either way it is bounded: one retry at 30,000 / 32,000, then invalid with the reason. It never loops.
