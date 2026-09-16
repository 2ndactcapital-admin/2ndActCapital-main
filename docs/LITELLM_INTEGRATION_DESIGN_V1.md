# LiteLLM Integration — Design (v1)

**Status:** design, grounded in a real discovery sprint (`docs/LITELLM_DISCOVERY_FINDINGS.md`, commit `db63028`). Ready to phase into sprints. Supersedes `LITELLM_INTEGRATION_CAPTURE.md`'s speculative assumptions where discovery corrected them — noted explicitly below, not silently.

---

## 0 · What discovery corrected

The pre-discovery capture assumed a scattered set of AI call sites and four active `org_settings` keys. Real findings:

- **16 production Claude call sites, all through one function, in one file** (`services/extraction.py`). The Anthropic SDK is imported in exactly one place. Zero hardcoded model strings anywhere. This is a **clean, already-centralized chokepoint** — routing everything through LiteLLM is a small, contained change, not a sprawling refactor.
- **`ai.model.provider` and `ai.model.fallback` are dead keys — zero consumers.** The real, live mechanism is `ai.model.fallback_chain`. The abstraction is **model-level, not provider-level** — worth designing the new system around that same shape rather than reintroducing a provider concept nothing currently uses.
- **Voyage embeddings bypass the router entirely** — a separate `ai.embedding.*` namespace, raw `httpx`, no fallback chain, no `ai_decision_log` row. Per Joe's direction, **Voyage is now assumed part of LiteLLM** (§6) — this gap is exactly what that change closes.
- **Zero voice call sites exist.** No Polly, no Transcribe, anywhere. Voice is a **build-fresh** section, not a migration.
- **`ai_decision_log`: 12 columns, RLS on, 1 writer, 1 reader, 0 endpoints, 259 live rows** including 15 real `member_brief` fallbacks — a real, working system, just with no UI ever built on top of it.
- **`render.yaml` declares 2 services; the web app is actually on Vercel.** `AWS_*` and `VOYAGE_API_KEY` are used in production but absent from the manifest — a real, pre-existing gap worth fixing regardless of this project.

---

## 1 · The core ask

**One single, unified API surface for all AI in the platform** — text, embeddings, and (eventually) voice — no exceptions, no parallel code paths.

Two real screens:

1. **Model selection (per org)**: a multi-select pick list narrowing LiteLLM's full catalog down to what an org wants access to, showing model name, provider, and LiteLLM's own cost/context heuristics, filterable.
2. **Task assignment (per org, and per Hollis for platform-level tasks)**: assign a specific model — from the org's own selected list — to each real task. Hollis-level tasks (the SPV tool) are managed platform-wide, using the same `owner_scope: platform | org` shape already proven for UDFs.

**Business rationale, worth keeping explicit**: this closes a real sales objection. RIAs are wary of model choice and want autonomy over cost and PII exposure — this gives them that, concretely, as a real feature rather than a promise.

**The gap this closes, confirmed directly**: there has never been a screen where an admin sees which model handles which task. The capability has existed only in `org_settings`, editable by direct database access.

---

## 2 · Deployment, confirmed

- **Self-hosted on Render** — not LiteLLM Cloud
- **Same Supabase Postgres instance, own schema** — following the exact `portfolio` precedent. No separate database, no ETL.
- **Schema-qualification discipline applies identically.** `app_service`'s `search_path` will not include LiteLLM's schema by default — write every query schema-qualified from the start. This exact class of bug has cost real time three separate times this session; there is no excuse for a fourth.
- **`render.yaml` needs the pre-existing `AWS_*`/`VOYAGE_API_KEY` gap fixed alongside adding the new LiteLLM service** — found by discovery, not introduced by this project, but this is the natural moment to close it.

---

## 3 · "Team," not "Organization" — and the real naming collision

LiteLLM's own docs: *"Teams and Virtual Keys are available in open source... Organizations and Org Admins... are an enterprise feature."*

**Decision: a Hollisworks client org = a LiteLLM Team**, given self-hosting open-source. Consequence: LiteLLM's native org→team budget *inheritance* is also Enterprise-only, so **the two-tier safe-model hierarchy (§7) must be enforced in Hollisworks' own application logic, not assumed from LiteLLM's own nesting.**

**The naming collision is real, confirmed by discovery**: `public.teams`/`team_members` is genuine, live, **intra-org staff grouping** (`org_id NOT NULL`), already used by UDFs' `owner_scope='team'`. A LiteLLM "Team" means something entirely different — a whole client org. **Resolution: always say "LiteLLM Team" explicitly, in code and UI, never bare "team," to avoid confusion with the existing staff-team concept.** This is not optional politeness — the two concepts really are different things that happen to share a word.

---

## 4 · Relationship to S27 TaskRouter, now grounded in real structure

**Confirmed shape**: `ai_decision_log` (12 columns), one writer, one reader, zero endpoints, real live data (259 rows). The real config key is `ai.model.fallback_chain` — model-level, ordered.

**Decision: TaskRouter owns policy — which model a task should use, and the fallback order. LiteLLM owns execution** — the actual call, the live fallback, the cost capture.

**Real, still-open design question the doc must resolve before Phase A**: does `ai_decision_log` get retired in favor of LiteLLM's own spend logs (with Hollisworks' reporting reading from there instead), or does it stay as a distinct *policy-decision* audit trail, separate from LiteLLM's *execution-level* log? Leaning toward **keep both, distinct purposes**: `ai_decision_log` answers "what did TaskRouter decide and why," LiteLLM's spend log answers "what actually happened and what did it cost" — but this is worth a deliberate decision, not a default.

---

## 5 · Every real call site, and what routing looks like

All 16 sites run through `services/extraction.py`, calling the Anthropic SDK from one place. The fix is narrow: **that one call point routes through LiteLLM's OpenAI-compatible endpoint instead of the Anthropic SDK directly**, with the model name resolved via `ai.model.fallback_chain` per task (already the live mechanism, now with LiteLLM executing the chain instead of raw provider calls).

**Zero hardcoded model strings exist today** — a real, confirmed advantage. Nothing needs hunting down and replacing; the abstraction point already exists cleanly.

---

## 6 · Voyage, now assumed part of LiteLLM

Per direction: Voyage is treated as any other LiteLLM-routed model, not a special case. This closes the real gap discovery found — no more separate `ai.embedding.*` namespace, no more raw `httpx`, no more invisibility in `ai_decision_log`.

**The real risk this creates — and the resolution, per direction, is friction, not a lock:**

Embeddings from different models are not comparable. Switching an org's embedding model without re-indexing produces **silently degraded search** — comparing vectors from two different spaces yields meaningless similarity scores, with no error, no crash, just quietly worse results.

**Mechanism: a real, specific confirmation dialog on embedding model change**, not a generic "are you sure":

- States plainly that changing the embedding model requires re-indexing every existing document, or search results for anything indexed under the old model will silently degrade
- States the real, concrete scope — document count, estimated re-indexing cost/time for **this org's actual corpus**, not a generic warning
- Requires an explicit, typed or checkbox confirmation, not a single click
- Triggers a real, tracked re-indexing job on confirmation — this is not just a warning, it's the trigger for the actual migration work

> **Phase C update (2026-09-14):** the first three bullets shipped, proven
> live — see §14.2. The fourth did **not**: no re-indexing job, script, or
> endpoint exists anywhere in this codebase, confirmed by direct search
> (Phase C's own discovery task). The shipped dialog says this plainly in its
> own copy (real corpus count + real cost estimate + "no automated
> re-indexing job exists yet, you will need to re-embed manually") rather
> than implying a migration will run that doesn't exist. Building the actual
> tracked re-indexing job is real, unstarted work — a natural Phase D/E-
> adjacent task, not yet scheduled.

**Also worth naming**: Voyage's own finance-specific quality matters here specifically. `voyage-context-4` was deliberately chosen over `voyage-finance-2` earlier this project based on Voyage's own benchmarks. The picker should surface this kind of task-fit signal where it exists (§9's recommender is the natural home for this), not just cost and context window.

---

## 7 · The two-tier safe-model hierarchy

Every task tries its assigned model first. On failure — deprecation, outage, **or budget exhaustion (§8)** — falls to:
1. The **org's own safe/default model** (org-admin-set)
2. If that also fails, or for a platform-level task with no org context (the SPV tool case): the **Hollis-level safe/default model** (Super-Admin-set only)

Same `owner_scope: platform | org` shape as UDFs.

**Model-change warnings, generalized from §6's specific mechanism**: any task reassignment should surface real consequences before confirming — cost delta at minimum, a DeepEval accuracy flag where the task has eval history (§9), and the embedding-specific re-indexing warning where applicable. Never a silent swap, for any task type.

---

## 8 · Budget-threshold UX — in scope now, not deferred to guardrails

A hard stop with no warning is a bad client experience and undermines the "full autonomy" pitch this whole project is meant to support.

- **A warning threshold** (e.g., 80%) notifying the org admin, Hollis staff, or both, before the cap
- **At-cap behavior: graceful degradation to the org's own safe/default model (§7), not a hard stop** — extending the existing safe-model concept to cover budget exhaustion as well as outages, a small natural extension rather than new machinery
- **A separate, platform-wide Hollis ceiling**, with its own distinct alert path when approached — protects the platform even if a single org's own budget is generous

**"Guardrails" (content filtering, prompt-injection defense, jailbreak detection) stays a separate, later topic**, explicitly deferred per direction.

---

## 9 · Model recommendation / decision-support tool

**Recommendation: calc-based, deterministic, explainable — not AI-based, at least initially.**

**Reasoning, stated directly**: the genuinely valuable signal available here isn't LiteLLM's own model metadata (cost, context window, provider) — it's **DeepEval's real, task-specific accuracy history**, which only Hollisworks has and LiteLLM cannot know. A deterministic score combining cost, context-window fit, and DeepEval accuracy where it exists — with adjustable weights and a visible "why this model" — is auditable in a way that matters on a platform where a model choice can have real compliance weight. An LLM-generated recommendation is a black box by comparison, and it's genuinely unclear what it would add once the real structured data is already in front of the person deciding.

**Honest limitation, worth stating rather than hiding**: for a brand-new task with no DeepEval history yet, the recommender can only offer cost/context-window guidance — a real, weaker signal than what it can offer once eval data exists. This is a **cold-start problem**, not a flaw to paper over; the tool should say so plainly rather than present a confident-looking recommendation built on thin data.

**Shape**:
- Inputs: LiteLLM's cost/context/provider metadata for the org's selected models, DeepEval's accuracy history for this specific task (where it exists), the org's own stated priority (a simple cost-vs-quality weighting)
- Output: a ranked short list with the reasoning shown per model — not a single black-box pick
- Revisit an AI-assisted version later, once real usage shows whether the deterministic version's explanations are actually sufficient — not a decision to make now

---

## 10 · Security — must shape the design, not be a footnote

- **CVE-2026-42208 (CVSS 9.3, critical)**: a pre-auth SQL injection in LiteLLM 1.81.16–1.83.6, targeting exactly the credential-storing tables, exploited in the wild within 36 hours of disclosure. Current stable is 1.90.0. **Version pinning and an ongoing patching discipline are required**, not optional — treat this service with the same posture as a secrets manager.
- **`LITELLM_SALT_KEY` cannot be rotated, ever, after first use** — it encrypts every stored provider credential; changing it makes every encrypted row permanently unrecoverable and the proxy won't start. **This one value needs deliberate, careful storage** (a real secrets manager), a different discipline than a normal Render env var, given this platform has already rotated database passwords twice this session for real reasons.
- **`LiteLLM_SpendLogs` stores request/response content in plaintext, unencrypted.** Given RIAs may pick models partly on PII grounds, this is a real compliance question — an org choosing a model for its data-handling properties would reasonably expect the same care downstream. **Recommendation: redact/disable full request-body logging by default**, keeping only cost/token/task metadata.
- **`LITELLM_MASTER_KEY` defaults to unset** — a known, common misconfiguration; must be deliberately set.

---

## 11 · Quality/eval risk

DeepEval already measures real accuracy for specific tasks. An org freely choosing a cheaper/weaker model for a high-stakes task (document extraction) could quietly degrade accuracy the org may not notice. **This connects directly to §9's recommender** — the same DeepEval data that powers the recommendation should also power an optional floor: certain tasks could require a minimum measured accuracy before a model is even selectable, rather than being fully open to any choice. Worth deciding whether this is a hard floor or just a strong warning — a concrete instance of the deferred "guardrails" conversation, named now so it isn't lost.

---

## 12 · Voice — build fresh, not migrate

Confirmed: zero existing call sites. This section is unconstrained by legacy integration shape. LiteLLM's real, current xAI voice support (real-time speech-to-speech via WebSocket, standalone STT/TTS) is a genuine, concrete option — xAI's STT specifically noted for financial/legal entity recognition and inverse text normalization, relevant to advisor call notes or voice data entry.

**Architectural note**: real-time speech-to-speech is a persistent WebSocket session, a genuinely different task shape than a single request/response call. The task-assignment screen needs to treat this as its own category from the start, not retrofit it later.

**Connects to existing backlog items**: live voice/Nova Sonic and mobile voice onboarding were both previously unbuilt, later-tier items — sequence together with this work rather than revisiting the same ground twice.

---

## 13 · Open questions before Phase A

1. **`ai_decision_log` vs. LiteLLM's spend log** — retire, or keep both for distinct purposes (§4)?
2. **RLS on LiteLLM's own schema** — add org-matching policies, or keep reporting `service_role`-only with org-scoping in application code?
3. **DeepEval floor for high-stakes tasks** — hard block or strong warning (§11)?
4. **Where `LITELLM_MASTER_KEY`/`LITELLM_SALT_KEY` actually live** — needs a real secrets-manager decision, not just "put it in Render," given the salt key's un-rotatable nature.

---

## 13.5 · Deployment gotchas — read this before touching this service again

**The B2 `PROXY_ADMIN` mystery, root-caused.** `LITELLM_MASTER_KEY` in Doppler
`prd` was always the *right value* — the proxy kept authenticating it as
`role=internal_user` anyway. Root cause: **a Doppler sync had silently
overwritten `hollisworks-litellm`'s `DATABASE_URL`** with the shared root
config's value on some prior sync/re-trigger, pointing the LiteLLM service at a
database session where its own bootstrap-created master key's role record
didn't match what Doppler's copy of the key claimed. This is exactly the
"Doppler sync can be destructive, not additive" hazard called out at the top
of this project's CLAUDE.md, confirmed for a **third** time on this project.
**Fix:** a dedicated Doppler **branch config** (`lite_llm`) syncing `DATABASE_URL`
*only* to `hollisworks-litellm`, so a future root-config sync can never
clobber it again. Anyone adding a fourth Render service to this project should
set up its own branch config for anything that must differ from the shared
root value, before the first sync — not after losing hours to this exact
symptom.

**A new, separate finding from the completion-proof sprint:** direct SQL
against `litellm.*` from `app_service` (the role `DATABASE_URL` has pointed at
since the RLS enforcement cutover) now fails
`InsufficientPrivilegeError: permission denied for schema litellm`. This is
the platform's own least-privilege design working as intended — `app_service`
and `litellm_service` are deliberately separate, scoped roles, and
`app_service` was never granted into LiteLLM's schema (§2). The *earlier*
Phase B verify script's direct queries against
`litellm."LiteLLM_SpendLogs"` only worked because they ran **before** that
cutover, when `DATABASE_URL` was still the `postgres` superuser. The obvious
workaround — connecting directly as `litellm_service` via the
`LITELLM_DATABASE_URL` secret — is **also currently blocked**: both its
embedded password and the separate `LITELLM_DB_PASSWORD` secret fail
`InvalidPasswordError` against Supabase's pooler, the same class of credential
drift previously documented for `DB_PASSWORD`. **Any code or script that needs
to read LiteLLM's own spend/usage data should use LiteLLM's own admin HTTP API
(`GET /spend/logs`, `GET /global/spend`) with `LITELLM_MASTER_KEY`, not a raw
SQL connection into its schema** — this is not a workaround so much as the
correct interface for an external caller, and it sidesteps the schema
separation entirely rather than punching a hole in it.

---

## 14 · Phasing

| Phase | Scope |
|---|---|
| **A** | ~~LiteLLM proxy deployed on Render, own Supabase schema, `render.yaml` gap fixed.~~ **DONE.** The proxy is live and the `litellm` schema is migrated (77 tables). **A real model deployment now exists** (`claude-sonnet` → `anthropic/claude-sonnet-4-6`) and the proxy has routed its first successful, billed call (2026-09-14) — see §14.1. The `render.yaml` service-adoption gap is still open. |
| **B** | ~~The 16 `extraction.py` call sites routed through LiteLLM instead of the Anthropic SDK directly. `ai.model.fallback_chain` now executes via LiteLLM.~~ **FULLY COMPLETE (2026-09-14)** — routing built 68/68 (2026-08-26), all 5 previously-BLOCKED assertions now PASS, 25/25, `verify_litellmphasebproof.py`. See §14.1. |
| **C** | ~~Voyage routed through LiteLLM (§6), including the re-indexing confirmation mechanism.~~ **DONE (2026-09-14)** — 34/34, 0 BLOCKED, `verify_litellmphasec.py`. See §14.2. |
| **D** | Discovery **DONE** (`docs/LITELLM_PHASE_D_DISCOVERY.md`). **D1a — per-org BYO provider credential storage: DONE (2026-09-15)** — 58/58, 0 FAIL, `verify_litellmphased1a.py`. See §14.3. **D1b — routing + spend attribution: DONE (2026-09-15)** — 54/54, 0 FAIL, `verify_litellmphased1b.py`. See §14.4. **D1c — credential-failure alerting: DONE (2026-09-15).** **D2 — the model pick-list UI: DONE (2026-09-16)** — 47/47 PASS, 0 FAIL, 4 FIND, `verify_litellmphased2.py`. See §14.5. **NEXT: Phase E** — per-task model assignment, building ON TOP of D2's curated/authorised lists rather than replacing them. |
| **E** | Task-assignment screen, including the two-tier safe-model hierarchy (§7) and change warnings. |
| **F** | Budget-threshold UX (§8) — warnings, graceful degradation, the Hollis-wide ceiling. |
| **G** | Reporting/billing surfaces, Hollis-level and org-level, reading LiteLLM's real spend data. |
| **H** | The recommendation tool (§9). |
| **I** | Voice, as its own build (§12). |
| **Later** | Guardrails proper (§8, §11) — content filtering, prompt-injection defense, DeepEval floors. |

---

## 14.1 · Phase B — what actually shipped (2026-08-26, completed 2026-09-14)

`apps/api/scripts/verify_litellmphaseb.py` — **68/68 PASS, 5 BLOCKED** (transport
layer, 2026-08-26). `apps/api/scripts/verify_litellmphasebproof.py` — **25/25
PASS, 1 FIND, 0 BLOCKED** (completion proof, 2026-09-14) — see the RESOLVED
section below and §13.5.

### The routing change

`services/extraction.py` routes through the live proxy. One function
(`_build_ai_client`) decides the transport; the chain walk, cost model,
`ai_decision_log` writes and error handling are untouched, and `ai_decision_log`
gained **no columns**.

**Endpoint, measured against the live instance rather than assumed.** All three
are mounted and answer:

| Route | Status |
|---|---|
| `POST /v1/chat/completions` | live (OpenAI shape) |
| `POST /chat/completions` | live, equivalent to the above |
| `POST /v1/messages` | live, **Anthropic shape** — the one Phase B uses |

**Decision: point the Anthropic SDK at LiteLLM rather than replace it.**
`AsyncAnthropic(api_key=<master key>, base_url=<LITELLM_BASE_URL>)` posts to
LiteLLM's `/v1/messages`, so responses stay genuine Anthropic objects and every
`extract()` closure, `_compute_cost`, and the tool-use `block.model_dump()` path
keep working with **zero** changes. §5 above said "OpenAI-compatible endpoint",
written before deployment; using it would mean hand-writing an OpenAI→Anthropic
response adapter (including `tool_calls`→`tool_use`) on the single most
load-bearing path in the platform. That is a rewrite, and §5's phrasing was
shorthand for "through the proxy", not a requirement to reshape every response.
Both auth schemes are sent (`x-api-key` via the SDK plus an explicit
`Authorization: Bearer`), so auth does not hinge on which one a proxy build
prefers.

### The rollback path — real, tested

`LITELLM_ROUTING_DISABLED=1` reverts every call to direct Anthropic, never
contacting LiteLLM. Proven both by the client's real `base_url` and by **zero
rows in LiteLLM's own spend log** after waiting the full flush window.

An **environment variable, not an `org_settings` key** — deliberate. It follows
the established external-service convention (`portfolio_altruist.py`,
`LITELLM_ENV_VARS` in `litellm_ops.py`), it is platform-wide rather than
per-org, and it must keep working when the database is the unhappy thing; an
`org_settings` read would need a working DB to report that the DB-independent
fallback is on. **This is not §7.5's `force_anthropic`** — that remains a future,
per-org, UI-driven, Hollis-admin-facing capability. Different audience, lifetime
and mechanism.

Deliberately absent: any "LiteLLM is configured but the call failed → quietly
retry against Anthropic" path. An *unconfigured* proxy degrades to direct
Anthropic with a loud printed reason (so deploying before the env var lands does
not brick AI); a *configured but broken* proxy fails loudly. Silently healing the
latter would let the platform stop routing through LiteLLM with nobody noticing.

### §13's open question 1, now answered by evidence

*"Retire `ai_decision_log` in favour of LiteLLM's spend log, or keep both?"* —
**keep both.** Phase B measured them side by side and they are genuinely
different records: `ai_decision_log` captured the policy decision (requested vs
used, `fallback_used`, the reason) while `LiteLLM_SpendLogs` captured each
execution attempt. The same call is correlatable across the two by model name and
ordering.

**A real operational property, measured:** LiteLLM writes `LiteLLM_SpendLogs`
**asynchronously**, seconds after answering the request. A before/after count
taken around a call sees no change — an earlier draft of the verify script
reported a false negative for exactly this reason. **Any assertion or reporting
query against that table — presence or absence — must tolerate the flush lag.**
This matters directly for Phase G's billing surfaces.

### RESOLVED (2026-09-14) — Phase B is now fully, genuinely complete

`apps/api/scripts/verify_litellmphasebproof.py` — **25/25 PASS, 1 FIND, 0
BLOCKED.** All three blockers below (and the 5 assertions they blocked in
`verify_litellmphaseb.py`) are closed:

1. **A real model deployment exists.** `GET /v1/models` returns `claude-sonnet`;
   `GET /model/info` confirms it persists as `litellm."LiteLLM_ProxyModelTable"`
   row `7fcd845c-0a47-413c-b77d-3da88d984425`, routing to
   `anthropic/claude-sonnet-4-6`. Survives restarts — a real DB row, not
   in-memory config.
2. **`LITELLM_MASTER_KEY` now authenticates as `PROXY_ADMIN`.** Root cause of
   the earlier `internal_user` failure, and the fix — see §13.5 below.
3. **`ANTHROPIC_API_KEY` exists in Doppler and is real** — proven by a minimal
   call made directly against `api.anthropic.com`, independent of LiteLLM.

**The one proof `verify_litellmphaseb.py` could not make, now made for real:**
a genuine `200` with real generated text through the full chain
(`call_claude_text` → LiteLLM → Anthropic); `ai_decision_log` recording real
`success=true`, non-zero `cost_usd`, non-zero `latency_ms`; LiteLLM's own spend
ledger recording the SAME call with non-zero spend (the first real, billed call
this proxy has ever routed); the two logs agreeing on outcome; the rollback path
(`LITELLM_ROUTING_DISABLED=1`) succeeding via direct Anthropic, proven by a
genuine ABSENCE of a new LiteLLM spend row after the full flush window; and the
fallback chain still walking correctly via LiteLLM under a forced first-model
failure.

**Real finding, not a regression:** the two logs don't correlate on model name
directly. `ai_decision_log.model_used` records the request-facing name we
called with (`claude-sonnet`); LiteLLM's spend log records the *resolved*
deployment string (`anthropic/claude-sonnet-4-6`). Any future reporting surface
(Phase G) that joins these two logs needs to correlate by time window +
deployment identity (via `/model/info`'s `model_name` → `litellm_params.model`
mapping), not by a naive string match on model name.

**Gap closed by this sprint:** `LITELLM_BASE_URL` was still absent from Doppler
`prd` and is now set to the live Render URL, verified by read-back.

---

## 14.2 · Phase C — what actually shipped (2026-09-14)

`apps/api/scripts/verify_litellmphasec.py` — **34/34 PASS, 0 BLOCKED.**

### The routing change

`services/document_embedding.py` — Chancery's semantic-embedding module —
now calls LiteLLM's `POST /v1/embeddings` instead of Voyage's API directly.
Both `/v1/embeddings` and bare `/embeddings` are live and answer identically;
`/v1/embeddings` is used to match the documented OpenAI-compatible surface.
The direct-Voyage call this module always made is now the explicit fallback
path (rollback engaged, or LiteLLM unconfigured), reached through the exact
same `resolve_transport()` text calls use — **one rollback switch,
`LITELLM_ROUTING_DISABLED`, now covers both text and embeddings.**

**Voyage registered as a real proxy deployment**, via `POST /model/new` with
the master key (now proven `PROXY_ADMIN` since Phase B's completion —
§14.1's blocker 2):

```json
{"model_name": "voyage-3.5",
 "litellm_params": {"model": "voyage/voyage-3.5",
                     "api_key": "os.environ/VOYAGE_API_KEY"}}
```

Persistence confirmed by a FRESH `GET /v1/models` / `GET /model/info` read
(`db_model=true`), not by trusting the registration POST's own response —
same discipline §14.1 used for `claude-sonnet`.

### The fallback chain — a separate key, on purpose

`ai.embedding.fallback_chain` (new org_settings key, default
`["voyage-3.5"]`) is **not** shared with `ai.model.fallback_chain`. A
text-model fallback just needs to answer; an embedding-model fallback that
silently returned a different vector width would corrupt vector search — so
`_execute_embedding_chain` gates every candidate on the org's configured
`ai.embedding.dimensions` before accepting it, treating a wrong-width
response as a failed attempt, never a silently-stored one.

### `ai_decision_log` — same table, same shape, new task_types

Every embedding call writes one row via the identical `_safe_log` /
`_write_ai_decision` helper `services/extraction.py` already uses —
`task_type='embedding_document'` (INDEX) or `'embedding_query'` (RETRIEVE).
No new columns, no parallel logging path. Proven: `success`, `model_used`,
`fallback_used`, `latency_ms`, `cost_usd` all populate correctly for a real
call, a forced-fallback call, and a rollback call.

**Real precision finding:** `ai_decision_log.cost_usd` is `numeric(10,6)`,
sized for Claude's per-call cost (cheapest realistic call > $0.000001).
Voyage's real live price ($0.06/1M input tokens) means a short embedding
call (a handful of tokens) silently rounds to `0.000000` at that scale — not
an error, a real precision floor. A migration
(`migrations/litellmphasec_cost_precision.sql`, `numeric(10,6)` →
`numeric(14,10)`) is written but **BLOCKED**: `DATABASE_URL` connects as
`app_service` post-RLS-cutover, which does not own the table (`postgres`
does) and cannot `ALTER` it, and no `postgres`-role credential exists in
this environment. LiteLLM's own spend log has no such limit and correctly
shows the real, non-zero cost regardless.

### The re-indexing friction dialog — shipped, honestly scoped

New endpoint `GET /orgs/{org_id}/settings/embedding-reindex-estimate?new_model=X`
(`services/document_embedding.reindex_estimate`) returns:

- `corpus_document_count` — `COUNT(*) FROM document_embeddings WHERE org_id=$1`, live, not cached
- `estimated_reindex_cost_usd` — computed from the corpus's real stored `content_chars` and the candidate model's LIVE LiteLLM price (`GET /model/info` → `input_cost_per_token`, never a local table)
- `reindex_mechanism_exists: false`, and a `note` saying so in plain language

`OrgSettingsEditor.jsx` intercepts a genuine `ai.embedding.model` change
(dirty, and different from the stored value) and blocks the save behind this
dialog — Cancel, or "Change model anyway." This is friction, not a lock, per
direction: a confirming admin always proceeds; nothing is enforced
server-side beyond the informational read.

**Honest scope note, per §6's update above:** this ships the confirmation
UX only. No re-indexing job exists to trigger on confirmation — building one
is real, unscheduled future work.

### RESOLVED / PROVEN (2026-09-14)

A real `embed_document()` call through the full app path (Chancery's own
INDEX entrypoint, not a bespoke test path) succeeds end-to-end through
LiteLLM: a genuine 1024-wide vector stored in `document_embeddings`,
LiteLLM's own spend log recording non-zero spend
(`call_type='aembedding'`), and `ai_decision_log` recording the same call in
its standard shape. The fallback chain walks on a forced-bogus primary
(LiteLLM's own "Invalid model name" error proves the request reached the
live proxy) and recovers via `voyage-3.5`. The rollback switch bypasses
LiteLLM for embeddings exactly as it does for text — proven by genuine
ABSENCE from LiteLLM's spend log after the full flush window, not merely a
successful direct-Voyage result. A raw, pre-existing `document_embeddings`
row (inserted directly, never touched by any Phase-C code path) survives
every maneuver above unchanged, at its original dimensionality — this
sprint did not silently invalidate the existing corpus.

**[FIND] — the live corpus was empty.** `document_embeddings` had zero rows,
across every org, at the start of this sprint: Chancery's semantic INDEX had
never successfully embedded a document in this environment before Phase C.
The verify script seeds real fixture rows (via the full `embed_document`
path, plus a raw pre-seeded row simulating genuinely pre-existing data) to
prove both the friction dialog's live-count claim and the
existing-embedding-survival claim against real, not synthetic-only, data.

**[FIND] — the Doppler `VOYAGE_API_KEY` is rate-limited.** Live error text:
"reduced rate limits of 3 RPM and 10K TPM" — no payment method on file. The
verify script paces its real Voyage-hitting calls (a 65s gap; 25s measurably
was not enough — reproduced live). Any real production volume through this
key will need a paid Voyage tier or app-side request pacing.

**[FIND] — `docs/schema_snapshot.sql` does not capture FOREIGN KEY
constraints.** Confirmed: zero `FOREIGN KEY` occurrences in the entire file.
`document_embeddings.document_id` has a real, live
`REFERENCES documents(id) ON DELETE CASCADE` the snapshot never recorded —
found building this sprint's verify fixtures. A generator gap worth fixing;
out of scope for this sprint.

---

## 14.3 · Phase D1a — per-org AI provider credential storage (2026-09-15)

`apps/api/scripts/verify_litellmphased1a.py` — **58/58 PASS, 0 FAIL, 3 FIND**,
re-run clean three consecutive times. Backend only. Scope was deliberately
narrow: can an org store its own provider key, and does a dedicated LiteLLM
deployment get created for it? **Not built here, on purpose: routing an
org's own AI calls to its own deployment, spend attribution, and
credential-failure alerting.** `ai.credential_source.{provider}` is written
and readable today but nothing in `services/extraction.py`'s resolution
path reads it yet — every call, for every org, still resolves to the
platform's `claude-sonnet` / `voyage-3.5` deployments exactly as before this
sprint. That wiring is the real remaining work for D1b+.

### 1a — THE finding every later phase depends on, proved live

`POST /model/new`'s `litellm_params.api_key` accepts a **literal** credential
value, not only `os.environ/<NAME>` indirection. Proved directly against the
live proxy: a deployment was created with a literal `ANTHROPIC_API_KEY`
value, a real call through it returned genuine model output (HTTP 200,
"OK"), and the deployment was deleted. LiteLLM encrypts the literal value at
rest with `LITELLM_SALT_KEY` (§10) and **never echoes `api_key` back through
`GET /model/info`** — confirmed for the two pre-existing platform
deployments (`claude-sonnet`, `voyage-3.5`) and for the literal-key probe
deployment alike, regardless of which mechanism supplied the credential.
This is what makes the whole feature safe to build from application code at
all: an org's own key crosses the wire to LiteLLM exactly once and is never
stored, logged, or readable back out of our own database or LiteLLM's own
admin API.

### The mechanism

`services/litellm_credentials.py` (new). `PROVIDER_PLATFORM_DEPLOYMENT`
(code constant, same "no owner_scope row mechanism exists" reasoning §4c
already established) maps `{"anthropic": "claude-sonnet", "voyage":
"voyage-3.5"}` — the platform deployment each provider's org-key deployment
mirrors. An org's own deployment gets a deterministic, purely INTERNAL name
(`org-{provider}-{org_id}`) — never the platform's logical name, because
same-`model_name` deployments load-balance rather than route deterministically
by owner (the structural consequence the Phase D discovery doc already
named). `provision_org_deployment` reads the platform deployment's *live*
`litellm_params.model` (never hardcoded) and re-registers it under the org's
own name with the org's own literal key; `deprovision_org_deployment`
deletes it. Both are idempotent — provisioning again (key rotation) deletes
any pre-existing deployment for that (provider, org) pair first, so a retry
or a rotation can never leave two same-owner deployments answering to the
same internal name.

**No new table.** The org↔deployment mapping is deliberately NOT persisted
in our own schema — `app_service` cannot read the `litellm` schema anyway
(CLAUDE.md, confirmed again live via `InsufficientPrivilegeError` if
attempted), so any code needing "does org X have a deployment for provider
Y" has to ask LiteLLM's own admin API regardless. Storing a second copy of
that fact in our DB would just be a second place for it to drift out of
sync with what the proxy actually has; the deterministic name plus
`GET /model/info` is the single source of truth instead.

`org_settings.ai.credential_source.{provider}` (`'org'` | `'platform'`,
`DEFAULT_SETTINGS` both `'platform'`) is the one new piece of state on our
side — no schema change, validated with the same enum-precedent shape
`ai.embedding.provider` already uses. `set_org_provider_credential` /
`clear_org_provider_credential` keep the flag and the real deployment in
lockstep, in a specific, deliberate order:

- **Set**: provision the real deployment FIRST, flip the flag to `'org'`
  SECOND — never claim `'org'` with no real deployment behind it.
- **Clear**: deprovision the real deployment FIRST, revert the flag to
  `'platform'` SECOND — the sprint's own named hazard ("a stale deployment
  holding a revoked key") means the flag must never claim `'platform'` while
  a still-keyed deployment could still be sitting on the proxy.

New routes, `apps/api/routers/org_settings.py`: `GET` / `PUT` / `DELETE
/orgs/{org_id}/settings/ai-credentials[/{provider}]`. Writes use the
identical `can_manage_org_settings` gate every other settings write already
uses (super_admin anywhere, org_admin at home); reads are open to any org
member, matching the rest of this router (`GET /orgs/{id}/settings`) — the
credential SOURCE is not itself sensitive, only the raw key value is, and
the raw key value is never stored or returned by anything on our side.

### Real proof, all against the live proxy and database

A test org's own key creates a genuinely distinct deployment — confirmed by
reading the proxy's own `GET /model/info` back, not by trusting the
creation call's own response — whose `litellm_params.model` mirrors the
live platform `claude-sonnet` string and whose `model_info.id` differs from
the platform deployment's own id. Removing it deletes that exact
deployment, confirmed the same way. Every response body across the full
lifecycle (`PUT`, `DELETE`, the final `GET`, and the general `GET
/orgs/{id}/settings?detail=true`) was grepped as raw text for the internal
deployment name — zero matches, every time; an org never sees one. Two
orgs' own deployments coexist independently, with distinct names and ids;
org B's admin is refused (403) on the identical read/write/delete requests
org A's own admin succeeds on; a real, role-holding (not zero-role)
non-admin member of org A is refused (403) on the identical write request
org A's admin succeeds on.

**[FIND]** `GET /model/info` showed brief eventual-consistency lag
immediately after a `POST /model/new` / `POST /model/delete` during ad hoc
repeated runs — the same class of propagation lag already documented for
`LiteLLM_SpendLogs` (§14.1), just metadata-only and on a much shorter
timescale (seconds, not the spend log's tens of seconds). The verify
script polls (up to 12s) on every existence-transition assertion rather
than trusting a single immediate read.

**[FIND]** A fixture user with zero `user_roles` grants default-ALLOWS every
permission check (`has_permission`'s documented single-admin bootstrap
posture — see the org_admin role reconciliation entries in
`docs/PROJECT_STATUS.md`). The non-admin fixture needed a real, granted role
that specifically excludes `manage_org_settings`, not merely an ungranted
user, for the refusal-path assertion to prove anything — the same lesson
`verify_orgadminrole.py` already learned building its own non-admin
fixture.

Teardown: zero leftover fixture rows and the live proxy confirmed back at
exactly its pre-sprint deployment set (`claude-sonnet`, `voyage-3.5` only)
— re-verified after every run, three consecutive clean runs.

---

## 14.4 · Phase D1b — routing + spend attribution (2026-09-15)

`apps/api/scripts/verify_litellmphased1b.py` — **54/54 PASS, 0 FAIL, 3
FIND.** D1a stopped at "can an org store its own key and get a deployment";
nothing read `ai.credential_source.*` at call time, and no call sent
LiteLLM any metadata at all — every real call landed in `LiteLLM_SpendLogs`
attributed to the master key with a null team. This sprint closes both
gaps: calls now actually use the right deployment, and spend is
attributable.

### Task 1 — discovery, re-probed live

**1a — where the resolver slots in.** `services/extraction.py`'s
`_execute_chain` and `services/document_embedding.py`'s
`_execute_embedding_chain` are the two real chain executors (unchanged
choke points from Phase B/C). D1b's per-org resolution slots into the SAME
point in both: right after the model/fallback chain is computed, right
before the outbound call is made, per attempt — never touching
`resolve_model`/`resolve_fallback_chain` (still the LOGICAL model name from
`org_settings`), only deciding which DEPLOYMENT actually answers that name.
Gated on `transport == TRANSPORT_LITELLM`, so the direct-Anthropic rollback
path is provably byte-for-byte unchanged.

**1b — real metadata fields, probed live, not assumed.** Sending
`metadata.tags` (a native Anthropic-SDK field the LiteLLM proxy reads
through) lands in `LiteLLM_SpendLogs.request_tags`. Sending a top-level
`user` field (not part of the SDK's typed surface — sent via `extra_body`)
lands in `LiteLLM_SpendLogs.end_user` — a DIFFERENT column from `user`,
which stays the fixed string `'default_user_id'` for a master-key call
regardless of request content. **[FIND]** `metadata.user_id` and
`metadata.spend_logs_metadata` — despite looking like the obvious
mechanism from LiteLLM's own field name — do NOT land anywhere readable
back via `GET /spend/logs` for a master-key-authenticated call; a real dead
end, probed and discarded rather than assumed to work.

**1c — text and embeddings need SEPARATE work, confirmed live.**
`metadata.tags` behaves identically on both `/v1/messages` and
`/v1/embeddings` (same field, same spend-log column — one mechanism,
shared). The top-level `user` field does NOT: Voyage's embeddings route
rejects it outright — `litellm.UnsupportedParamsError: voyage does not
support parameters: {'user': ...}`, HTTP 400, probed live — a genuine,
provider-specific incompatibility, not an oversight. This is why
`_embed_litellm` sends only `metadata.tags` while every `call_claude_*`
wrapper in `extraction.py` sends both `metadata.tags` and `user`.

### Task 2 — routing

`services/litellm_credentials.py` gained `resolve_deployment_model(model_id,
org_id, provider, credential_source)`: translates `model_id` to the org's
own deployment (`_org_deployment_name(provider, org_id)`, D1a's existing
naming scheme) ONLY when `model_id` is EXACTLY the platform deployment that
provider mirrors (`claude-sonnet` for anthropic, `voyage-3.5` for voyage)
AND `credential_source == 'org'`. Every other `model_id` — an
unregistered/dated string never registered as its own deployment, or a
provider still on `'platform'` — passes through completely unchanged. This
is the sprint's own stated requirement made concrete: the caller-facing
logical model name never changes. Proven live: `ai_decision_log.model_used`
/ `model_requested` record `'claude-sonnet'` for an org-routed call exactly
as for a platform-routed one, and the internal deployment name was grepped
out of both the DB log and every org-facing HTTP response — zero matches.

### Task 3 — attribution

`resolve_credential_source(org_id, provider)` (mirrors
`services.extraction.resolve_model`'s fail-to-`'platform'`-on-any-error
discipline — a lookup failure can never silently grant org-key routing) and
`build_attribution(org_id, provider, credential_source)` — returns `{tags,
end_user, usage}`. `usage` is one of THREE labels: `'org_owned_key'` (this
org supplied its own key), `'hollisworks_platform'` (the shared platform
key, used for Hollisworks' OWN org), or `'platform_on_behalf_of_org'` (the
shared platform key, used for any OTHER org). Three, not two, because
conflating Hollisworks' own usage with "platform key on someone else's
behalf" would make Hollisworks' own AI spend invisible in its own log —
exactly the distinguishability the sprint prompt asked for. Attached by all
three `call_claude_*` wrappers in `extraction.py` and by
`document_embedding.py`'s `_embed_litellm` — never a new `ai_decision_log`
column, per Phase B's own established "keep the two logs, don't conflate
them" precedent (§14.1).

### Task 4 — proof, all live, all by deployment IDENTITY

- **No regression:** 2nd Act's real call (`'platform'`) lands on the
  platform deployment's own `model_info.id`, exactly as before this
  sprint — the state every real, existing org is in today.
- **Before/after, in the same run:** an unattributed raw call (bypassing
  this sprint's code entirely) shows an empty `end_user` and no `org:` tag;
  the identical call shape through the real, fixed code path shows both,
  populated — the genuine null-to-populated proof, not just presence.
- **Hollisworks vs. on-behalf-of:** Hollisworks' own real call tags
  `usage:hollisworks_platform`; 2nd Act's tags
  `usage:platform_on_behalf_of_org` — same shared platform key, two
  genuinely different, queryable labels.
- **Org-routing + cross-org, real deployments, real key:** two fixture
  orgs each provisioned a REAL Anthropic deployment (D1a's own mechanism).
  Three real calls (platform, org A, org B) produced three mutually
  distinct `model_info.id` values in the spend log — org A's call never
  carries org B's or the platform's deployment id, and vice versa. This is
  cross-org isolation proven by the proxy's own record of which deployment
  executed the call, not inferred from which org_settings row says what.
- **Embeddings carry attribution too:** the same before/after proof,
  re-run on the embedding path (2nd Act, platform-routed), correctly
  missing `end_user` per Task 1c. Org-routing for embeddings is proven only
  at the function level (`resolve_deployment_model` correctly computes the
  org's deployment name for `voyage-3.5`) — a second live `'org'`-routed
  Voyage call was deliberately skipped to respect the documented free-tier
  pacing budget (3 req/min, 25s previously proven insufficient; this sprint
  paced 68s between its two real Voyage calls).

Teardown: zero leftover fixture rows (organizations/users/org_settings),
zero leftover `ai_decision_log` rows, and the live proxy back to exactly
its pre-run deployment set (`claude-sonnet`, `voyage-3.5` only).

**Next: D1c — credential-failure alerting** (an org's own key going bad
must surface somewhere; deliberately out of scope here, per the sprint
prompt).

## 14.5 · Phase D2 — the model pick-list UI (2026-09-16)

`47/47 PASS, 0 FAIL, 4 FIND` — `apps/api/scripts/verify_litellmphased2.py`.
D1a-c built the credential SOURCE flag ('org'/'platform') per provider; none
of it decided WHICH models exist at all, or which of them an org may use.
This sprint adds that layer, above D1's routing, not replacing it.

### Task 1 findings

- **1a — org_settings genuinely cannot hold a platform-scoped row.**
  Re-confirmed live (not assumed from the Phase D discovery doc): `org_id`
  is `NOT NULL` and there is no `owner_scope` column. Two new tables were
  built instead: `platform_model_catalog` (Hollisworks-curated, NO org_id
  column at all — every row is unconditionally platform-wide) and
  `org_model_selections` (which curated models one org has authorised, row
  PRESENCE = authorised, `UNIQUE(org_id, model_id)`). This mirrors the SAME
  live convention `public.reference_data`/`reference_data_lists` already use
  for their own global-vs-org split (`org_id IS NULL OR org_id = current_org
  OR is_super_admin`) — re-used, not reinvented. RLS on both new tables:
  reads open (`platform_model_catalog`) or org-scoped-or-super
  (`org_model_selections`); writes to `platform_model_catalog` restricted to
  `is_super_admin` at the RLS layer AND the app layer (defense in depth).
- **1b — `GET /model/info`, probed live.** Genuinely available per
  REGISTERED deployment: `model_info.max_input_tokens`/`max_output_tokens`
  (context window), `input_cost_per_token`/`output_cost_per_token`
  (pricing). Genuinely absent: no `provider` field (derived here from
  `litellm_params.model`'s `"provider/model"` prefix) and no broad
  catalogue — only the proxy's own registered deployments appear (2-3
  entries today), never a general model list. `services.model_catalog.
  enrich_with_live_info` attaches this opportunistically where a curated
  `model_id` happens to match a registered deployment's real upstream
  string; most curated models get no live enrichment, by design, not by
  omission.
- **1c — the real existing settings screen.** `OrgSettingsEditor.jsx`
  (`/admin/settings`)'s "AI Models" category already established the
  envelope shape ai-credentials (D1a) uses: reads open to any org member,
  writes gated on `manage_org_settings`. `OrgModelSelector.jsx` (a new,
  small checkbox-list component embedded in that same screen) reuses the
  IDENTICAL envelope shape — never a second one. The platform catalog is a
  DIFFERENT, higher-privilege screen (`/admin/model-catalog`, a new
  `ModelCatalogManager.jsx` built on `components/ui/DataGrid` + a
  right-pane, the same pattern the Triggers screen established), gated
  `super_admin` only, mirroring `/admin/platform`'s existing gate — not the
  org settings screen's gate.

### Task 2 — the curated platform list

`POST/DELETE /admin/model-catalog` (super_admin only, proven both ways: an
org_admin gets 403 on the identical request, and the refused write
genuinely left the row unchanged — before/after DB read, not just an HTTP
status). `platform_model_catalog` seeded with the three real, currently-
in-use model strings from `org_settings.DEFAULT_SETTINGS`
(`claude-sonnet-4-6`, `claude-haiku-4-5-20251001`, `voyage-3.5`) — never a
fictitious model.

### Task 3 — the org picker

`GET/PUT /orgs/{org_id}/settings/model-selections`. Reads open to any org
member (envelope always includes the full catalog for display, even when
`vocabularies.editable` is `[]` for a view-only caller); writes gated on
`manage_org_settings`, proven the same before/after way a plain member is
refused. **A real routing bug found and fixed while proving this**: the
generic `PUT /orgs/{org_id}/settings/{key}` route (registered earlier in
`routers/org_settings.py`) silently swallowed `PUT .../model-selections`
— Starlette matches routes in REGISTRATION order and `{key}` matches any
single path segment, including the literal string `"model-selections"`.
The new routes were moved ahead of the generic one; this is now called out
in the route's own docstring so it cannot regress silently.

### Task 4 — enforcement is real, at the call path

`services.model_catalog.resolve_authorized_models(org_id)` returns `None`
for "no explicit selection" (unrestricted — every existing org's real state
today) or the real authorised set otherwise.
`services.extraction._execute_chain` filters its resolved `attempts` list
against that set BEFORE any provider call; an empty result raises
`AIModelNotAuthorizedError` (a NEW exception, deliberately not converted to
`None` by any `call_claude_*` wrapper — including `call_claude_json`, whose
existing bare `except Exception` would otherwise have silently swallowed it
exactly the way it already protects `AIOrgCredentialError`/
`AILiteLLMAuthError`, a real bug caught and fixed while wiring this in).
Proven bidirectionally, live: an org authorised for a model its chain never
resolves to is refused BEFORE any provider call (zero new `ai_decision_log`
success rows); the identical org, once authorised for the model its chain
actually resolves to, gets a real, successful response. An org that never
touched model-selections is genuinely unaffected — proven for a fresh
fixture org AND read-only for both real production orgs (2nd Act,
Hollisworks — neither has ever explicitly selected anything, confirmed
live, nothing written to either).

**[FIND] — a real, pre-existing gap in a different layer, orthogonal to D2,
not fixed here.** Neither of `org_settings`' own real, currently-stored
default-chain values (`claude-sonnet-4-6`, `claude-haiku-4-5-20251001`) is
actually callable against the live proxy today — both return LiteLLM's own
"Invalid model name" HTTP 400. Only the proxy's REGISTERED `model_name`,
`claude-sonnet`, is callable. Re-checking prior sprints' own real
successful calls confirms this was already true: D1c's real call used an
explicit `model="claude-sonnet"` override, never the default resolution
chain. This is a D1b/model-resolution gap, not a D2 one — Task 4's own
proof above uses an explicit override for exactly this reason, and this
finding is recorded here rather than silently worked around.

Cross-org isolation on selections proven both directions (org A's write
does not touch org B's list and vice versa). No deployment name (the
`org-<provider>-<org_id>` shape) and no internal LiteLLM field name
(`model_name`, `litellm_params`) appears in any org-facing response body —
grepped raw response text. View-only proven two ways independently: the
server envelope (`can_write=false`, `editable=[]`) AND the frontend source
(`ModelCatalogManager.jsx`/`OrgModelSelector.jsx` both gate their write
control on `canWrite` with no truthy fallback). `npm run build` exits 0.

Teardown: zero leftover fixture rows across every touched table
(organizations/users/roles/`platform_model_catalog`/
`org_model_selections`/`ai_decision_log`), the three real seeded catalog
models still exactly present and untouched, and the live proxy's
deployment set byte-for-byte unchanged (D2 makes zero LiteLLM admin-API
calls of any kind).

**Next: Phase E — per-task model assignment**, building on D2's curated
list and org authorisation as the safe-model universe a task-level picker
selects from — not a replacement for either.
