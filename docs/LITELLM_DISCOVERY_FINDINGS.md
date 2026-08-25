# LiteLLM Integration — Discovery Findings

**Date:** 2026-08-25
**Type:** Discovery only. No code, schema, or configuration was changed.
**Scope:** Tasks 1–7 of the LiteLLM discovery brief.

This is a **record of facts**, not a design document. Every statement below is
sourced to a file/line, a live database read, or a deploy manifest. Where a fact
could not be established, it is marked **NOT ESTABLISHED** rather than guessed.

Method: static grep/read across `apps/api` and `apps/web` (excluding
`apps/web/.next`, which is build output and pollutes greps), plus one read-only
`asyncpg` query against the live dev database (`DATABASE_URL`,
`statement_cache_size=0`) for row counts, RLS state, and actual `org_settings`
rows.

---

## Task 1 — Every real AI call site

### 1.1 The choke point

All Claude access goes through **`apps/api/services/extraction.py`**, which
defines exactly three public helpers plus one private chain executor:

| Symbol | Line | Role |
|---|---|---|
| `call_claude_json` | `extraction.py:299` | JSON-returning call |
| `call_claude_text` | `extraction.py:341` | Text-returning call |
| `call_claude_with_tools` | `extraction.py:377` | Tool-use call |
| `_execute_chain` | `extraction.py:213` | Private: walks the fallback chain, times, prices, and logs every call |

**The Anthropic SDK is imported in exactly one place in the entire application:**

```python
# apps/api/services/extraction.py:231-233
import anthropic as _anthropic
client = _anthropic.AsyncAnthropic(api_key=api_key)
```

Confirmed by grep for `import anthropic` / `from anthropic` / `Anthropic(`
across `apps/api`: the only other hits are *comments* in
`apps/api/scripts/verify_s27.py:73-74`. There is **no** direct SDK call from any
router or service. The AI-provider-abstraction rule in `CLAUDE.md` is genuinely
held today.

`apps/web` contains **zero** AI SDK usage. The only web-side hit for
`anthropic|openai|@ai-sdk|litellm` in source (non-`.next`) is
`apps/web/components/admin/OrgSettingsEditor.jsx`, and that is a settings-key
string, not a client.

### 1.2 The four known keys vs. what is actually in the code

The brief named four keys. The real picture:

| Key | Real? | Consumed by | Notes |
|---|---|---|---|
| `ai.model.default` | ✅ | `extraction.DEFAULT_MODEL_KEY` (`extraction.py:37`) | Default `model_key` of all three helpers |
| `ai.model.assistant` | ✅ | `extraction.ASSISTANT_MODEL_KEY` (`extraction.py:38`) | Default for `call_claude_with_tools` |
| `ai.model.document_classifier` | ✅ | `extraction.DOCUMENT_CLASSIFIER_MODEL_KEY` (`extraction.py:44`), resolved by `document_classifier.resolve_classifier_model` (`document_classifier.py:46`) | Explicit-row lookup, so "unset" falls through to `ai.model.default` rather than to this key's own default |
| `ai.model.fallback` | ⚠️ **DEAD** | **Nothing.** | Defined at `org_settings.py:92` and present as a real row in the DB, but grep finds **zero** consumers. The comment at `org_settings.py:93-95` says so explicitly: *"Replaces the single, never-consumed `ai.model.fallback` above."* |

**The key the brief did not name, and which is the one that actually matters:**

| Key | Real? | Consumed by |
|---|---|---|
| `ai.model.fallback_chain` | ✅ | `extraction.FALLBACK_CHAIN_KEY` (`extraction.py:47`) → `resolve_fallback_chain` (`extraction.py:90`) → `_execute_chain` |

Plus a second, separate namespace the brief did not name:

| Key | Real? | Consumed by |
|---|---|---|
| `ai.embedding.provider` | ✅ | `document_embedding.py:62`, `org_settings._validate_setting:226` |
| `ai.embedding.model` | ✅ | `document_embedding.py:63` |
| `ai.embedding.dimensions` | ✅ | `document_embedding.py:64` |
| `ai.model.provider` | ⚠️ **DEAD** | **Nothing.** Defined `org_settings.py:91` (`"anthropic"`), zero consumers. |

`ai.model.provider` being decorative is the single most consequential Task-1
finding: **the abstraction is model-level, not provider-level.** An org can
choose *which Claude model*, but the provider is hardcoded by the
`import anthropic` at `extraction.py:231`. Setting `ai.model.provider` to
anything else today changes nothing.

### 1.3 Every production Claude call site

Sixteen call sites, all routed through the choke point. `org_id` column records
whether the call carries tenant context.

| # | File:line | Enclosing function | What it really does | Model key resolved | `task_type` | `org_id` |
|---|---|---|---|---|---|---|
| 1 | `routers/assistant.py:98` | `_run_loop` | The member-facing AI assistant's tool-use loop (up to 10 iterations) | `ai.model.assistant` (helper default) | `assistant` | ✅ real |
| 2 | `routers/dashboard.py:110` | member-brief narration | Writes the one-paragraph "advisor voice" narration on the member dashboard | `ai.model.assistant` (explicit) | `member_brief` | ✅ real |
| 3 | `routers/investment_profile.py:437` | Foundation conversation turn | Generates the conversational reply in the 10-question Foundation interview | `ai.model.default` | `foundation_reply` | ✅ real |
| 4 | `routers/investment_profile.py:730` | client-brief generation | Writes the advisor's long-form client brief from CRM notes + Foundation answers | `ai.model.default` | `client_brief` | ✅ real |
| 5 | `routers/investment_profile.py:741` | brief themes | Second structured pass over the brief → key_themes / risk_profile / decision_style | `ai.model.default` | `brief_themes` | ✅ real |
| 6 | `routers/marketplace.py:1644` | deal AI summary | Analyst-style deal summary + strengths/risks/market context for a marketplace deal | `ai.model.default` | `deal_summary` | ✅ real |
| 7 | `services/assistant_actions/crm.py:15` | `_draft_note_preview` | Drafts a CRM meeting note for advisor confirmation | `ai.model.default` | `crm_draft_note` | ✅ real |
| 8 | `services/document_classifier.py:176` | `classify_document` | Open-set document-type classification (Chancery SORT), with few-shot correction history | `ai.model.document_classifier` → falls through to `ai.model.default` | `document_classifier` | ✅ real |
| 9 | `services/extraction.py:456` | `extract_from_answer` | Pulls structured investment-profile fields out of one Foundation answer | `ai.model.default` | `profile_extraction` | ✅ real |
| 10 | `services/extraction.py:552` | `extract_from_note` | Pulls CRM field updates out of an advisor's meeting note | `ai.model.default` | `crm_extraction` | ✅ real |
| 11 | `services/narrative_extraction.py:183` | Chancery 11a narrative extraction | Summary / provisions / dates / parties from a narrative document | `ai.model.default` | `narrative_extraction` | ✅ real |
| 12 | `services/note_terms_extraction.py:657` | primary note-terms extraction | Structured-note term extraction from an SEC 424B2/FWP filing | `ai.model.default` | `note_terms_extraction` | ❌ `org_id=None` |
| 13 | `services/note_terms_extraction.py:745` | hazard ensemble | Independent second read of six hazard fields, deliberately on a *different* model | `ai.model.assistant` (+ explicit `model=`) | `note_terms_hazard_ensemble` | ❌ `org_id=None` |
| 14 | `services/note_terms_extraction.py:1081` | underlying mentions | Extracts verbatim underlying-asset reference strings from a filing | `ai.model.default` | `note_terms_underlyings` | ❌ `org_id=None` |
| 15 | `services/vdr_analysis.py:277` | VDR drop analysis | Reads a dropped document set → proposes deal fields with a confidence bar | `ai.model.default` | `vdr_analysis` | ✅ real |
| 16 | `services/workflow_nl_generator.py:178` | `_generate_once` | Natural-language description → BPMN XML workflow | `ai.model.default` | `workflow_generation` | ✅ real |

**Every one of the 16 maps to a real `ai.model.*` key.** There are no hardcoded
Claude model strings at any call site. The only model-string literals in
application code are in `org_settings.DEFAULT_SETTINGS` (lines 90–105), which is
where the module docstring says they belong.

### 1.4 Genuine gaps — call sites that do NOT map to an `ai.model.*` key

**Gap A — Voyage embeddings bypass the router entirely.**
`services/document_embedding.py:150` makes a raw `httpx.AsyncClient().post()` to
`https://api.voyageai.com/v1/embeddings`. It is configured by
`ai.embedding.provider|model|dimensions` — a *parallel* mechanism — and it:
- does **not** go through `call_claude_*` or `_execute_chain`;
- has **no fallback chain**;
- writes **no `ai_decision_log` row** (no spend, latency, or success record);
- has its own retry/backoff loop (`_VOYAGE_MAX_ATTEMPTS = 6`, honors
  `Retry-After`, `document_embedding.py:148-170`).

**Gap B — AWS Textract has no config key at all.**
`services/textract.py` constructs a boto3 client (`textract.py:75-80`) with
region/creds straight from the environment. There is no `ai.*` setting, no
per-org override, no decision-log row.

**Gap C — three org-blind call sites.**
Sites 12–14 pass `org_id=None`. `resolve_model(None)` returns the
`DEFAULT_SETTINGS` literal without ever touching `org_settings`
(`extraction.py:76-77`), so a per-org model override **cannot** apply to the
note-terms pipeline. This is intentional — that corpus is global
(`portfolio.reference_filings` has no `org_id`) — but it means those calls are
attributed to the default org purely for logging (`extraction.py:50-51`,
`DEFAULT_ORG_ID`).

**Gap D — `ai.model.provider` and `ai.model.fallback` are inert.** See §1.2.

**Not a gap, but worth recording:** `deepeval` is a declared dependency
(`requirements.txt`). Its built-in LLM-judge metrics default to OpenAI.
`services/eval_metrics.py:1-31` documents this trap at length and the one
implemented metric (`DocumentTypeSortAccuracy`) is a **no-judge**, pure
string-comparison metric — so **no OpenAI call exists anywhere in the
codebase**. Grep confirms zero OpenAI/Gemini/Mistral/Cohere/Ollama/LangChain
client usage in source.

---

## Task 2 — The "teams" naming collision

### 2.1 Verdict

`public.teams` / `public.team_members` is a **real, distinct, actively-used
intra-org staff-grouping concept**. It is not an org, not a tenant, and not a
billing boundary. A LiteLLM "Team" (which in the open-source tier is the unit
that carries budget, key scoping, and model access) would represent **a whole
client org** in this system — i.e. a `public.organizations` row. The two
concepts sit at different levels of the hierarchy and would collide by name only.

### 2.2 What `public.teams` actually is (live schema)

```
-- ===== teams =====
--   id           uuid NOT NULL DEFAULT uuid_generate_v4()
--   org_id       uuid NOT NULL
--   name         text NOT NULL
--   description  text
--   created_at   timestamptz NOT NULL DEFAULT now()
--   UNIQUE teams_org_id_name_key: (org_id, name)

-- ===== team_members =====
--   team_id    uuid NOT NULL
--   user_id    uuid NOT NULL
--   added_at   timestamptz NOT NULL DEFAULT now()
--   added_by   uuid
--   PRIMARY KEY team_members_pkey: (team_id, user_id)
```

Decisive detail: **`teams.org_id` is NOT NULL and uniqueness is scoped
`(org_id, name)`** — a team lives *inside* an org. `team_members` carries **no
`org_id` of its own**; its RLS policy reaches the org through an EXISTS on
`teams` (documented at `services/portfolio_udf.py:63-66`).

RLS is enabled on both, with policies `teams_org_isolation` and
`team_members_org_isolation` (both `cmd=ALL`), confirmed live via `pg_policies`.

### 2.3 Every real reference in the codebase

**Backend — CRUD / API surface**
- `apps/api/routers/staff_assignments.py` — the entire team API:
  - `GET /admin/staff/teams` (`:99`, joins `teams` ← `team_members` at `:108-109`)
  - `POST /admin/staff/teams` (`:134`, dedupe on `(org_id, name)` at `:143`, insert `:149`, audited `:155`)
  - `POST /admin/staff/teams/{team_id}/members` (`:162`, org-ownership check `:168`, insert `:180`)
  - `DELETE /admin/staff/teams/{team_id}/members/{user_id}` (`:189`, `:195`, `:200`)
  - assignment listing joins `teams` (`:223`) and name lookup (`:270`)

**Backend — visibility enforcement**
- `apps/api/services/staff_visibility.py`
  - module docstring `:9` — "`assigned_to_team_id` is a team this user belongs to"
  - `get_team_ids_for_users` (`:85`) — `team_members tm JOIN teams t ON t.id = tm.team_id` (`:94-96`), constrained by org
  - `:139` resolves team ids, `:150` filters `assigned_to_team_id = ANY($3::uuid[])`

**Backend — UDF ownership scope**
- `apps/api/services/portfolio_udf.py`
  - `TABLE_TEAMS = "public.teams"` (`:114`), `TABLE_TEAM_MEMBERS = "public.team_members"` (`:115`)
  - `OWNER_SCOPES = frozenset({"platform", "org", "team", "user"})` (`:119`)
  - docstring `:50-66` — every membership predicate JOINs `public.teams` to reach `org_id`
  - `owner_scope_id` is **polymorphic with no FK** (`:81`, `:182`, `:534`) — it holds a team id when `owner_scope='team'`

**Backend — referenced in passing**
- `apps/api/services/ownership_tree.py:15` — "(hierarchy + teams + assignment)"

**Frontend**
- `apps/web/app/admin/staff-visibility/page.js`
- `apps/web/components/admin/StaffVisibilityManager.jsx`
- `apps/web/app/admin/page.js` (admin index links)

**Related schema**
- `staff_assignments.assigned_to_team_id uuid` (nullable) — the join point that
  makes a team a unit of entity visibility.

### 2.4 Live usage volume

| Table | Rows |
|---|---|
| `teams` | 1 |
| `team_members` | 1 |
| `staff_assignments` with a non-null `assigned_to_team_id` | 2 |

The concept is real and wired end-to-end but very lightly populated today.

---

## Task 3 — Voyage / embedding call sites (Chancery Phase 11b)

### 3.1 How provider selection works today, exactly

`apps/api/services/document_embedding.py` is the single embedding choke point.
Its docstring (`:1-32`) states it deliberately "mirrors the provider-abstraction
discipline of `services/extraction.py`" — the "Mini-Bedrock pattern extended to a
new `ai.embedding.*` namespace."

**Registry** (`:46-58`):
```python
EMBEDDING_PROVIDERS = ["voyage", "openai", "google", "cohere"]
EMBEDDING_PROVIDER_LABELS = {...}
ENABLED_EMBEDDING_PROVIDERS = frozenset({"voyage"})
EMBEDDING_PROVIDER_DISABLED_MSG = "Voyage is the only model enabled right now"
```

**What determines Voyage is used** — three layers, in this order:

1. **Default.** `org_settings.DEFAULT_SETTINGS` (`org_settings.py:112-114`) sets
   `ai.embedding.provider = "voyage"`, `ai.embedding.model = "voyage-3.5"`,
   `ai.embedding.dimensions = 1024`. An org with no row gets Voyage.
2. **Per-org resolution.** `resolve_embedding_config(conn, org_id)`
   (`document_embedding.py:201-215`) reads all three keys via the *same*
   `get_setting` resolver used for `ai.model.*`, defaulting each independently.
3. **Dispatch.** `embed_texts` (`:181-197`) lowercases the provider; `"voyage"`
   → `_embed_voyage`; any other name in the registry → `_embed_stub`; an unknown
   name → immediate raise.

**How "other providers listed but rejected" is implemented** — two independent
enforcement points, and the code names which is authoritative:

- **Write-time (authoritative).** `org_settings._validate_setting`
  (`org_settings.py:216-234`) special-cases `ai.embedding.provider`: any value
  not in `ENABLED_EMBEDDING_PROVIDERS` raises `SettingsValidationError`, which
  the router maps to **HTTP 400** with the exact string
  `"Voyage is the only model enabled right now"`. It uses a lazy import to avoid
  a module cycle (`document_embedding` imports `get_setting` from `org_settings`).
  Clearing the key (`value is None`) resets to the default and is allowed.
- **Call-time (second line).** `_embed_stub` (`:173-178`) raises
  `EmbeddingProviderNotEnabled` **without touching the network**. The docstring
  states the reason plainly: "a misconfiguration can never leak data to an
  unintended vendor."
- **UI.** `apps/web/components/admin/OrgSettingsEditor.jsx:51-56, 223, 253`
  renders `ai.embedding.provider` as a dropdown of all four, and surfaces the
  backend 400 as the error message.

**The actual Voyage call** (`_embed_voyage`, `:135-170`): raw
`httpx.AsyncClient(timeout=60.0).post(VOYAGE_ENDPOINT, json=payload,
headers={"Authorization": f"Bearer {key}"})`. Payload is
`{"input": texts, "model": model}` plus an optional
`input_type` of `"document"` or `"query"`. Retries up to 6 attempts on
429/500/502/503/504, honoring `Retry-After`, capped at 60 s backoff.

**Credential** (`_voyage_api_key`, `:94-119`): tries `VOYAGE_API_KEY`,
`VOYAGEAI_API_KEY`, `VOYAGE_KEY` from the environment, then falls back to
hand-parsing `apps/api/.env`. Never logged.

**Storage.** `document_embeddings` (live schema): `provider text NOT NULL`,
`model text NOT NULL`, `dimensions integer NOT NULL`, `embedding` (pgvector
`vector(1024)`), `UNIQUE (document_id)`. RLS enabled. The module asserts the
returned vector width matches before storing (`:19-25`) so a differently-sized
provider fails loudly rather than corrupting the column.

### 3.2 Structural facts relevant to whether this could be absorbed

Recorded as facts, not as a recommendation:

- The embedding path is a **separate mechanism** from the Claude path: separate
  settings namespace (`ai.embedding.*` vs `ai.model.*`), separate module,
  separate credential env var, separate retry logic.
- It has **no fallback chain** — there is one enabled provider, so there is
  nothing to fall back to.
- It writes **no `ai_decision_log` row**. Embedding spend and latency are not
  captured anywhere.
- The `input_type` parameter (`"document"` vs `"query"`) is a
  **Voyage-specific asymmetric-embedding feature** used at both index and
  retrieve time.
- The stored vector width is pinned at the **column level** (`vector(1024)`),
  so provider substitution is a schema concern, not only a config concern.
- Consumers: `_maybe_embed_document` (the Chancery SORT hook) and
  `apps/api/routers/semantic_search.py`.

---

## Task 4 — Voice call sites (AWS Polly / AWS Transcribe)

### Finding: there are none. Zero.

This is a negative result and it is unambiguous.

**Searched:** `apps/api`, `apps/web/app`, `apps/web/components`, `apps/web/lib`
for, case-insensitively: `polly`, `synthesize_speech`, `transcribe_`,
`start_transcription`, `amazon.transcribe`, `speechSynthesis`,
`SpeechRecognition`, `MediaRecorder`, `getUserMedia`, `whisper`, `realtime`,
`text_to_speech`, `tts`, `stt`, `voice`.

**Total hits in source: 2, both irrelevant** —
- `apps/api/routers/dashboard.py:29` — the *prompt string* "You are the trusted advisor **voice** of {brand_name}"
- `apps/api/services/assistant_actions/crm.py:7` — the *prompt string* "Write in first-person advisor **voice**"

(An earlier search returned apparent hits only because `apps/web/.next` build
output was included; re-running against source directories only returns the two
above. `apps/api/services/portfolio_spv.py:45` contains the English word
"transcribed" in a docstring.)

**Corroborating evidence:**
- `apps/api/requirements.txt` has no speech library. `boto3` is present but for
  R2, Textract, and (aspirationally) SES.
- `apps/web/package.json` dependencies: Auth0, bpmn-js, dnd-kit, tabler-icons,
  tanstack-table, next, react. No audio, no recorder, no speech library.
- Every `boto3.client(...)` construction in non-script source code:
  `services/storage.py:20` (`"s3"` → Cloudflare R2 endpoint) and
  `services/textract.py:80` (`"textract"`). That is the complete list.
  SES appears only in `apps/api/scripts/verify_multitenant2.py:105,118`, which
  *checks send quota* — there is no SES send path in application code.

**Consequence for the brief's question:** the question "would swapping to a
LiteLLM-routed voice provider be a config change or a larger undertaking?" has no
factual answer from the code, because **there is nothing to swap**. Any voice
capability would be new construction, not a migration. No Polly- or
Transcribe-specific feature is relied upon, because neither service is called.

---

## Task 5 — Real Render service topology

### 5.1 What the repo declares

`render.yaml` (repo root, the only deploy manifest in the repository) declares
**two services**:

| # | `name` | `type` | `runtime` | `rootDir` | Start command |
|---|---|---|---|---|---|
| 1 | `2ndactcapital-web` | `web` | `node` | `.` | `cd apps/web && npx next start -p $PORT` |
| 2 | `2ndactcapital-api` | `web` | `python` | `apps/api` | `uvicorn main:app --host 0.0.0.0 --port $PORT` |

There are **no** `worker`, `cron`, `pserv` (private service), or `redis`
declarations. No `databases:` block — Postgres is Supabase, not Render.

### 5.2 What is actually deployed where

The **API service on Render is confirmed live** by multiple independent
references in `docs/PROJECT_STATUS.md`: Render's `DATABASE_URL` cutover to the
non-bypass `app_service` role (`:55`), a real production Render log used to
diagnose the `uuid_generate_v4` bug (`:1113`), the Textract "local-vs-Render env"
troubleshooting (`:101`), and the standing action item to set
`HOLLISWORKS_AUTH0_DOMAIN` "in the **Render API service** environment"
(`:1079`, `:1186`).

The **web tier is on Vercel, not Render.** `CLAUDE.md` states "Deploy: Vercel
(web), Render (api)", and `PROJECT_STATUS.md:1031` records four custom domains
live and "Valid Configuration" **in Vercel**
(`hollisworks.com`, `www.hollisworks.com`, `admin.hollisworks.com`,
`2ndactcapital.hollisworks.com`), with `:1196` noting Vercel preview-deployment
env behavior.

The `2ndactcapital-web` entry in `render.yaml` dates to commit `e8bd6db`
("Fix Render deployment: add web service + env-driven CORS"), an early-project
commit; `render.yaml` has been touched four times total, most recently by
`d9a6f5c` (the superadminmenu sprint). The manifest entry appears to predate the
Vercel move and was not removed.

**NOT ESTABLISHED:** whether the `2ndactcapital-web` Render service still exists
and runs in the Render account. There is no Render CLI, no `RENDER_API_KEY`, and
no Render MCP connector available in this environment, so the live Render
dashboard could not be queried. The repo manifest and the deployment
documentation disagree, and only the Render dashboard can settle it.

### 5.3 Shape relevant to adding a service later

- Render config is **manifest-driven and in-repo** (`render.yaml`), so a new
  service is a declared block plus dashboard-set secrets.
- **Every single env var in `render.yaml` is `sync: false`** — i.e. declared but
  not populated by the manifest. Declaring a variable in the file does *not* set
  it; this is called out explicitly at `PROJECT_STATUS.md:1079`.
- There is currently **no private-service / internal-networking pattern in use**
  — both declared services are `type: web` (public).
- There is **no Redis and no Render Postgres** in the account per this manifest;
  a LiteLLM proxy's own state store would be new infrastructure.

---

## Task 6 — Real current secrets-handling pattern

### 6.1 Where each credential family lives

| Credential | Declared in `render.yaml`? | Present in `apps/api/.env`? | Read by |
|---|---|---|---|
| `DATABASE_URL` | ✅ API service, `sync:false` | ✅ | `services/database.py`, every script |
| `APP_SERVICE_DATABASE_URL` | ❌ **not declared** | ✅ | non-bypass-role connections |
| `ANTHROPIC_API_KEY` | ✅ API service, `sync:false` | ❌ **absent** | `extraction._execute_chain:227` |
| `AUTH0_DOMAIN`, `AUTH0_AUDIENCE` | ✅ API service | ❌ | `main.Settings` |
| `HOLLISWORKS_AUTH0_DOMAIN`, `HOLLISWORKS_AUTH0_AUDIENCE` | ✅ API service | ❌ | `main.Settings:120-121` |
| `ALLOWED_ORIGINS` | ✅ API service | ❌ | `main.Settings:124` |
| `R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `R2_BUCKET_NAME` | ✅ API service | ❌ (present in `.env.broken.bak`) | `services/storage.py:16-24` |
| `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_DEFAULT_REGION` | ❌ **not declared** | ✅ | `services/textract.py:66-80` |
| `VOYAGE_API_KEY` | ❌ **not declared** | ✅ | `document_embedding._voyage_api_key:94-119` |
| `EDGAR_USER_AGENT` | ❌ | ✅ | `services/edgar_fetch.py` |
| `AUTH0_SECRET`, `AUTH0_BASE_URL`, `AUTH0_ISSUER_BASE_URL`, `AUTH0_CLIENT_ID`, `AUTH0_CLIENT_SECRET` | ✅ **web service** (the Render node block) | n/a | Next.js / Auth0 SDK — but the web tier deploys on **Vercel**, so the operative copies are Vercel env vars |

(Key **names** only were read; no secret values were opened or recorded.)

### 6.2 The confirmed live pattern

1. **Plain process environment variables.** There is no secret manager, no
   Vault, no Render "secret file", no Doppler/1Password integration anywhere in
   the repo. Every credential is `os.environ.get(...)` at the point of use.
2. **`sync: false` everywhere in `render.yaml`.** The manifest declares the
   variable's *existence* for documentation and for Render's env UI; the value
   is typed into the Render dashboard by hand. Declaring is not setting — a
   distinction that has already caused one real production outage
   (`PROJECT_STATUS.md:1079`, the `HOLLISWORKS_AUTH0_DOMAIN` 401s).
3. **A dual-home pattern for local/verify runs.** `apps/api/.env` is loaded by
   `main.Settings` (`SettingsConfigDict(env_file=".env", extra="ignore")`,
   `main.py:101`). `document_embedding._voyage_api_key` goes further and
   *hand-parses* `apps/api/.env` when the env var is absent, so verify scripts
   work outside the FastAPI process.
4. **Defensive pre-checks before use.** `textract.textract_configured()`
   (`textract.py:59-72`) rejects whitespace-containing values as placeholders
   and requires a region; `document_embedding.voyage_configured()` (`:122-124`)
   is the analogous cheap check. Both exist so unattended runs degrade cleanly
   instead of throwing per-document.
5. **Never logged.** Both `textract.py:13-15` and
   `document_embedding.py:30-31` state in their docstrings that the key is never
   printed or logged, and grep confirms no credential is interpolated into any
   log line.

### 6.3 The real gap this exposes

**`AWS_*` and `VOYAGE_API_KEY` are not declared in `render.yaml` at all.** They
exist only in `apps/api/.env` (a local, git-ignored file). Whether they are set
in the Render API service's dashboard environment is **NOT ESTABLISHED** from
the repo — but Textract is documented as working in production
(`PROJECT_STATUS.md:101` describes resolving a "local-vs-Render env" problem),
which implies the AWS values *are* set in Render dashboard-side without being
declared in the manifest. `render.yaml` is therefore **not a complete inventory**
of the API service's environment.

Also on record: a live `app_service` database password was once pasted into a
chat and had to be rotated via Supabase with a Render redeploy
(`PROJECT_STATUS.md:1167`).

---

## Task 7 — S27 TaskRouter's real current shape

### 7.1 There is no `services/task_router.py`

The S27 TaskRouter is **not a separate module**. It was built *into*
`services/extraction.py`, which its own docstring (`:10-21`) describes as "the
single choke point for every AI call in the platform." Grep for any file named
`*task_router*` returns nothing.

### 7.2 `ai_decision_log` — exact real columns

From `docs/schema_snapshot.sql` (live introspection), verbatim:

```
-- ===== ai_decision_log =====
--   id               uuid        NOT NULL DEFAULT uuid_generate_v4()
--   org_id           uuid        NOT NULL
--   task_type        text        NOT NULL
--   model_requested  text        NOT NULL
--   model_used       text        NOT NULL
--   fallback_used    boolean     NOT NULL DEFAULT false
--   fallback_reason  text
--   cost_usd         numeric
--   latency_ms       integer
--   success          boolean     NOT NULL
--   error_detail     text
--   created_at       timestamptz NOT NULL DEFAULT now()
--   PRIMARY KEY ai_decision_log_pkey: (id)
```

Twelve columns. **No `user_id`, no `input_tokens`/`output_tokens`, no
`request_id`, no prompt/response body, no `entity_id` or other correlation key.**
Cost is stored as a computed dollar figure; the raw token counts that produced it
are **not** persisted.

RLS is **enabled**, with one policy `ai_decision_log_org_isolation` (`cmd=ALL`),
confirmed live via `pg_policies`. The snapshot file records columns and
constraints only — it does not render policies — so RLS state was read directly
from the database.

`org_id` is `NOT NULL`, which is why platform-level calls made with
`org_id=None` are attributed to
`DEFAULT_ORG_ID = "00000000-0000-0000-0000-000000000001"` at write time
(`extraction.py:49-51`, `:189`).

### 7.3 How the fallback chain is read from `org_settings`

`resolve_fallback_chain(org_id, *, primary_key=DEFAULT_MODEL_KEY)`
(`extraction.py:90-120`):

1. Baseline is `DEFAULT_SETTINGS["ai.model.fallback_chain"]`, i.e.
   `["claude-haiku-4-5-20251001"]` (`org_settings.py:99`).
2. If `org_id` is not None, opens a pooled connection and calls
   `get_setting(conn, org_id, "ai.model.fallback_chain")`; a truthy value wins.
3. **Any exception falls back to the default and prints** — a DB outage degrades
   the chain rather than failing the call.
4. A mis-stored scalar string is tolerated and wrapped into a one-item list
   (`:118-119`).
5. Returns only non-empty strings.

`primary_key` is accepted but, per the docstring (`:98-100`), **a single global
chain is used today** — the same chain backs default / assistant / classifier
calls. There is no per-task chain.

**Execution** — `_execute_chain` (`extraction.py:213-283`):

```
primary  = model_override or await resolve_model(org_id, key=model_key)
chain    = await resolve_fallback_chain(org_id, primary_key=model_key)
attempts = _dedupe([primary, *chain])
```

`_dedupe` (`:123-131`) is order-preserving, so when the chain names the primary
the two collapse to one attempt — which is exactly the current default-org case
(primary haiku + chain `[haiku]` → one call). It then loops `attempts`, catching
**any** exception per model, printing `[ai_router] model '{id}' failed for task
'{task}'`, and continuing. On first success it stamps latency, computes cost, and
writes one log row with `success=true`. On exhaustion it writes one row with
`success=false` and raises `AIChainExhausted` (`:54-62`), which each public
helper catches and converts back to `None` to preserve the long-standing
graceful-degradation contract every call site already branches on.

**Cost model** (`_MODEL_PRICING`, `extraction.py:140-167`): a **hardcoded**
USD-per-1M-token table keyed by model-family prefix
(`claude-opus` 15/75, `claude-sonnet` 3/15, `claude-haiku` 1/5,
`claude-fable` 1/5), defaulting to 1/5 for anything unmatched. Cost is computed
from `message.usage.input_tokens|output_tokens`, in `Decimal`, quantized to 8
decimal places. Token counts are used and discarded.

**Logging is non-blocking by construction.** `_safe_log` (`:195-207`) wraps
`_write_ai_decision` and swallows every exception into a printed warning. It is
`await`ed rather than fire-and-forget specifically so the row is durably written
before the call returns — the docstring notes this is for deterministic
readers/verification.

### 7.4 Exact real invocation points

**Writers.** Exactly one: `_write_ai_decision` (`extraction.py:173-192`),
reached only via `_safe_log`, reached only from `_execute_chain`. Since all 16
call sites in §1.3 route through `_execute_chain`, every Claude call in the
platform produces exactly one row. **Nothing else in the codebase inserts into
`ai_decision_log`.**

**Readers.** Exactly one in application code:
`note_terms_extraction._last_ensemble_model_used` (`:935-958`):

```sql
SELECT model_used FROM ai_decision_log
WHERE task_type = 'note_terms_hazard_ensemble'
ORDER BY created_at DESC LIMIT 1
```

Its docstring (`:938-949`) records why it exists: the platform chain is
`["claude-haiku-4-5-20251001"]`, the *same* model as the primary, so if Sonnet is
unreachable the "two-model" hazard ensemble silently collapses into one model
agreeing with itself and would report 100% agreement while having checked
nothing. Reading `model_used` back is the only way to detect that. It notes the
query assumes a sequential runner and would break under concurrency.

**There is no admin UI, no endpoint, and no dashboard over `ai_decision_log`.**
Grep finds no router referencing the table. Spend is recorded and currently
unreadable except by direct SQL.

### 7.5 Live data — what the log actually contains

Read from the live database on 2026-08-25:

- **259 rows**, spanning **2026-07-29 15:53 UTC → 2026-08-25 07:51 UTC**
- **6 distinct `task_type` values** (out of the 16 declared in §1.3 — ten task
  types have never fired in this environment)

| `task_type` | `model_used` | rows | ever fell back |
|---|---|---|---|
| `document_classifier` | `claude-haiku-4-5-20251001` | 92 | no |
| `note_terms_extraction` | `claude-haiku-4-5-20251001` | 54 | no |
| `note_terms_hazard_ensemble` | `claude-sonnet-4-6` | 54 | no |
| `member_brief` | `claude-sonnet-4-6` | 24 | no |
| `workflow_generation` | `claude-haiku-4-5-20251001` | 17 | no |
| `member_brief` | `claude-haiku-4-5-20251001` | **15** | **yes** |
| `assistant` | `claude-sonnet-4-6` | 3 | no |

The 15 `member_brief` rows with `fallback_used = true` are **real evidence the
chain fires in practice**: the Sonnet primary failed and Haiku served the
request. This is not a theoretical mechanism.

### 7.6 Live `org_settings` `ai.*` rows

Only the **default org** (`00000000-0000-0000-0000-000000000001`) has any:

| `setting_key` | `setting_value` |
|---|---|
| `ai.model.assistant` | `"claude-sonnet-4-6"` |
| `ai.model.default` | `"claude-haiku-4-5-20251001"` |
| `ai.model.document_classifier` | `"claude-haiku-4-5-20251001"` |
| `ai.model.fallback` | `"claude-haiku-4-5-20251001"` (dead key — no consumer) |
| `ai.model.fallback_chain` | `["claude-haiku-4-5-20251001"]` |
| `ai.model.provider` | `"anthropic"` (dead key — no consumer) |

**The Hollisworks org has zero `ai.*` rows** and therefore runs entirely on
`DEFAULT_SETTINGS`. No org has an `ai.embedding.*` row — every org is on the
Voyage defaults. No org has configured a multi-model chain: the one chain in
existence is a single-element list naming the same model as the primary, which
`_dedupe` collapses to one attempt.

---

## Facts inventory — summary table

| Question | Answer |
|---|---|
| Number of production Claude call sites | 16 |
| Number that hardcode a model string | 0 |
| Number that map to a real `ai.model.*` key | 16 |
| Places the Anthropic SDK is imported | 1 (`extraction.py:231`) |
| Non-Claude AI providers called | 1 (Voyage, via raw httpx) |
| OpenAI / Gemini / Cohere / Mistral / Ollama calls | 0 |
| Polly call sites | 0 |
| Transcribe call sites | 0 |
| Any voice/audio capability in the codebase | none |
| `ai.model.*` keys with zero consumers | 2 (`ai.model.provider`, `ai.model.fallback`) |
| Modules writing `ai_decision_log` | 1 |
| Modules reading `ai_decision_log` | 1 |
| Endpoints exposing `ai_decision_log` | 0 |
| `ai_decision_log` rows in the live dev DB | 259, 6 task types, 15 real fallbacks |
| Services declared in `render.yaml` | 2 (`web` node, `web` python) |
| `render.yaml` env vars with `sync: false` | all of them |
| Credentials used in prod but absent from `render.yaml` | `AWS_*`, `VOYAGE_API_KEY`, `APP_SERVICE_DATABASE_URL` |
| `public.teams` rows / `team_members` rows | 1 / 1 |

## Explicitly NOT ESTABLISHED

1. Whether the `2ndactcapital-web` service still exists in the live Render
   account, or whether it was superseded by Vercel and left in the manifest.
   No Render CLI, API key, or MCP connector is available here.
2. Whether `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_DEFAULT_REGION`,
   and `VOYAGE_API_KEY` are actually set in the Render API service's dashboard
   environment. Circumstantial evidence (documented working Textract in
   production) says yes for the AWS trio; nothing in the repo confirms it, and
   nothing at all speaks to Voyage in Render.
3. The production values of any `sync: false` variable — by design, they are not
   in the repository.
