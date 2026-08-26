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

## 14 · Phasing

| Phase | Scope |
|---|---|
| **A** | ~~LiteLLM proxy deployed on Render, own Supabase schema, `render.yaml` gap fixed.~~ **DONE, but only partly.** The proxy is live and the `litellm` schema is migrated (77 tables). **It has ZERO model deployments and has never successfully routed a call** — every row in its own spend log is `status=failure`. The `render.yaml` service-adoption gap is still open. |
| **B** | ~~The 16 `extraction.py` call sites routed through LiteLLM instead of the Anthropic SDK directly. `ai.model.fallback_chain` now executes via LiteLLM.~~ **COMPLETE (2026-08-26), 68/68, 5 BLOCKED** — `verify_litellmphaseb.py`. See §14.1. |
| **C** | Voyage routed through LiteLLM (§6), including the re-indexing confirmation mechanism. |
| **D** | Model pick-list screen (org-scoped, filterable, LiteLLM metadata-driven). |
| **E** | Task-assignment screen, including the two-tier safe-model hierarchy (§7) and change warnings. |
| **F** | Budget-threshold UX (§8) — warnings, graceful degradation, the Hollis-wide ceiling. |
| **G** | Reporting/billing surfaces, Hollis-level and org-level, reading LiteLLM's real spend data. |
| **H** | The recommendation tool (§9). |
| **I** | Voice, as its own build (§12). |
| **Later** | Guardrails proper (§8, §11) — content filtering, prompt-injection defense, DeepEval floors. |

---

## 14.1 · Phase B — what actually shipped (2026-08-26)

`apps/api/scripts/verify_litellmphaseb.py` — **68/68 PASS, 5 BLOCKED.**

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

### Blocked, and on what

Phase A is live but **not usable for traffic**. Three external blockers, none of
them code, all needing console access — full detail in
`docs/PROJECT_STATUS.md` item 00:

1. The proxy has **zero model deployments** (`/v1/models` → `{"data":[]}`).
2. Doppler's `LITELLM_MASTER_KEY` is an **`internal_user` virtual key, not
   PROXY_ADMIN** — so this sprint could not register one either. This also means
   `litellm.reload_model_cost_map` remains blocked despite the `LITELLM_BASE_URL`
   fix, contrary to `render.yaml`'s note.
3. **`ANTHROPIC_API_KEY` exists nowhere.** Bedrock is not an alternative — the
   existing `AWS_*` creds are Textract-only and Bedrock returns `AccessDenied`.

**Gap closed by this sprint:** `LITELLM_BASE_URL` was still absent from Doppler
`prd` and is now set to the live Render URL, verified by read-back.
