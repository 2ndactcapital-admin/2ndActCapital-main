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
