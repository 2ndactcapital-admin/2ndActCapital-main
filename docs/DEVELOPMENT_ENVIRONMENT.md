# Development Environment — Secrets & Local Setup

**Status as of 2026-08-25:** the local flow below (§3) is the intended fix and is
documented in full. **It is not yet active** — see §7 (Blocked) for exactly what
is missing and who has to do it. Until then §6 describes the interim workaround.

---

## 1. The problem this exists to solve

`APP_SERVICE_DATABASE_URL` in `apps/api/.env` went stale and **silently** broke
four sprint runs in a row (`portfolioux1`, `portfolioux2`, `portfolioux3`, and
this one). Measured directly, right now:

```
apps/api/.env DATABASE_URL             : CONNECT OK   role=postgres
apps/api/.env APP_SERVICE_DATABASE_URL : CONNECT FAILED
                                         InvalidPasswordError:
                                         password authentication failed
                                         for user "app_service"
```

The failure is invisible because every affected verify script is written to
degrade rather than stop:

```python
if APP_SERVICE_DATABASE_URL:
    conn = await _connect(APP_SERVICE_DATABASE_URL)
    ...
else:
    print("... set APP_SERVICE_DATABASE_URL to run")
```

The variable *is* set, so the guard passes; the **connection** is what fails, and
the fallback path (`SET LOCAL ROLE app_service`) still produces a green run. An
RLS test that was supposed to prove "a non-bypass role cannot read across orgs"
instead proved "a `postgres` connection wearing a role hat cannot" — a strictly
weaker claim, reported as a pass. Four times.

**This is a synchronisation problem, not a code problem.** The value lives in at
least four places (Render dashboard, Vercel dashboard, `apps/api/.env`,
`apps/web/.env.local`) with no mechanism keeping them equal. Rotating the
`app_service` password updates one of them.

---

## 2. Target architecture

One source of truth — a Doppler project with three configs — feeding every
consumer through a platform-native integration, so **no application code
changes**:

```
                    ┌──────────────────────────┐
                    │  Doppler project         │
                    │  ├── development         │
                    │  ├── staging  (unused)   │
                    │  └── production          │
                    └────┬──────────┬──────────┘
        native integration│          │native integration
                    ┌─────▼────┐ ┌───▼──────┐      ┌──────────────┐
                    │  Render  │ │  Vercel  │      │  local dev   │
                    │  (API)   │ │  (web)   │      │ doppler run  │
                    └──────────┘ └──────────┘      └──────────────┘
                     production    production        development
```

Rules:

- **No SDK calls from application code.** `apps/api` and `apps/web` keep reading
  `os.environ` / `process.env` exactly as they do today. Doppler injects; it is
  not a library dependency. This also means the abstraction can be removed
  without a refactor.
- **`staging` is not wired to anything.** It exists in the project and is left
  alone. Do not point a service at it.
- **Doppler is the single source, not a mirror.** Once a variable resolves
  through Doppler on a platform, the hand-set dashboard value for that variable
  is **deleted**. A duplicate that still exists is a duplicate that can drift —
  which is the entire failure being fixed.

---

## 3. Local development — the flow that replaces `apps/api/.env`

### One-time, per machine

```bash
# 1. Install the CLI (already verified working on this machine: v3.76.5)
curl -Ls https://cli.doppler.com/install.sh | sh
#    or, without the install script:
#    download doppler_<ver>_linux_amd64.tar.gz from
#    github.com/DopplerHQ/cli/releases and put `doppler` on PATH

# 2. Authenticate (opens a browser; one time per machine)
doppler login

# 3. Bind this repo to the project/config. Reads doppler.yaml at the repo root,
#    so there is nothing to type and nothing to get wrong.
cd /path/to/2ndActCapital
doppler setup
```

### Every command, from then on

Prefix with `doppler run --`. Nothing else changes.

```bash
# Run the API
doppler run -- uvicorn main:app --reload            # from apps/api

# Run a verify script
doppler run -- python3 apps/api/scripts/verify_sprintN.py

# Run the frontend
doppler run -- npm run dev                          # from apps/web

# Ad-hoc psql against the same credential production uses
doppler run -- psql "$DATABASE_URL"
```

`doppler run` fetches the `development` config at invocation time and injects it
into the child process's environment. There is no local copy to go stale,
because there is no local copy. When the `app_service` password is rotated in
Doppler, the *next* `doppler run --` already has the new one — no file to edit,
nothing to remember, no fourth silent failure.

### Deleting `apps/api/.env`

Do **not** delete it until `doppler run -- python3 apps/api/scripts/verify_doppler.py`
reports `PASS` on the local leg. Once it does:

```bash
git rm --cached apps/api/.env 2>/dev/null || true   # it is gitignored already
rm apps/api/.env apps/api/.env.broken.bak apps/web/.env.local
```

`.env.example` stays — it is the human-readable list of *names*, and it should
be kept in sync with the Doppler `development` config.

> `apps/api/.env.broken.bak` is worth noting on its own: it declares `R2_*`
> variables that the live `apps/api/.env` does **not**. Two local env files
> disagreeing about which variables exist is the same drift in miniature.

---

## 4. Complete secret inventory

Names only. No values appear in this file, and none should ever be added to it.

### API service (Render) — `apps/api`

| Name | Used by | In `render.yaml`? |
|---|---|---|
| `DATABASE_URL` | every asyncpg pool (PgBouncer, `statement_cache_size=0`) | yes |
| `AUTH0_DOMAIN` | `main.Settings.auth0_domain` | yes |
| `AUTH0_AUDIENCE` | `main.Settings.auth0_audience` | yes |
| `HOLLISWORKS_AUTH0_DOMAIN` | second Auth0 tenant; unset ⇒ every admin.hollisworks.com request 401s | yes |
| `HOLLISWORKS_AUTH0_AUDIENCE` | must be `https://api.hollisworks.com`, never 2nd Act's | yes |
| `ALLOWED_ORIGINS` | CORS | yes |
| `R2_ACCOUNT_ID` | Cloudflare R2 | yes |
| `R2_ACCESS_KEY_ID` | Cloudflare R2 | yes |
| `R2_SECRET_ACCESS_KEY` | Cloudflare R2 | yes |
| `R2_BUCKET_NAME` | Cloudflare R2 | yes |
| `ANTHROPIC_API_KEY` | central `call_claude_text` / `call_claude_json` | yes |
| `AWS_ACCESS_KEY_ID` | Textract OCR, via boto3 credential chain | **added 2026-08-25** |
| `AWS_SECRET_ACCESS_KEY` | Textract OCR, via boto3 credential chain | **added 2026-08-25** |
| `AWS_DEFAULT_REGION` | `services/textract.py` (falls back to `AWS_REGION`) | **added 2026-08-25** |
| `VOYAGE_API_KEY` | Chancery 11b embeddings, via `_VOYAGE_KEY_NAMES` tuple | **added 2026-08-25** |
| `EDGAR_USER_AGENT` | SEC EDGAR requires a declared UA | **added 2026-08-25** |

### Frontend (Vercel) — `apps/web`

| Name | Used by |
|---|---|
| `NEXT_PUBLIC_API_URL` | `lib/api.js`, `lib/apiForward.js` — **inlined at build time** |
| `AUTH0_SECRET` | `@auth0/nextjs-auth0` v4 session encryption |
| `AUTH0_DOMAIN` | v4 (not `AUTH0_ISSUER_BASE_URL` — that is a v3 name) |
| `APP_BASE_URL` | v4 (not `AUTH0_BASE_URL`); array allow-list per host |
| `AUTH0_CLIENT_ID` | 2nd Act tenant |
| `AUTH0_CLIENT_SECRET` | 2nd Act tenant |
| `HOLLISWORKS_AUTH0_DOMAIN` | Hollisworks tenant — `authHostConfig.mjs` is fail-loud |
| `HOLLISWORKS_AUTH0_CLIENT_ID` | Hollisworks tenant |
| `HOLLISWORKS_AUTH0_CLIENT_SECRET` | Hollisworks tenant |
| `HOLLISWORKS_AUTH0_SECRET` | Hollisworks tenant |
| `HOLLISWORKS_AUTH0_AUDIENCE` | must be `https://api.hollisworks.com` |

> **`NEXT_PUBLIC_*` is build-time, not runtime.** Doppler injects it into the
> *build*, and it is then baked into the client bundle. Changing it in Doppler
> requires a **redeploy**, not a restart. Any variable prefixed `NEXT_PUBLIC_`
> is public by definition — it ships to the browser. Never put a credential
> behind that prefix.

### Local / tooling only — never sent to Render or Vercel

| Name | Used by |
|---|---|
| `APP_SERVICE_DATABASE_URL` | `apps/api/scripts/verify_*.py` — the non-bypass `app_service` role. **The variable that keeps going stale.** |
| `SUPABASE_URL` | schema introspection |
| `SUPABASE_SERVICE_ROLE_KEY` | schema introspection (bypasses RLS) |
| `AUTH0_MGMT_CLIENT_ID` | Auth0 Management API, tooling scripts |
| `AUTH0_MGMT_CLIENT_SECRET` | Auth0 Management API, tooling scripts |
| `R2_SOURCE_BUCKET` | `verify_r2rename.py` only |

### Deliberately unset in production — do not add

| Name | Why |
|---|---|
| `APP_BASE_URL` / `WEB_BASE_URL` *(API service)* | Single shared value pointing at 2nd Act. A Hollisworks invite falling back to it gets 2nd Act's domain — the same cross-tenant inheritance bug as the three Auth0 failures. Invite URLs derive per-org from `organizations.enroll_url`. |
| `ALTRUIST_BASE_URL` / `ALTRUIST_CLIENT_ID` / `ALTRUIST_CLIENT_SECRET` | Integration is BLOCKED; no credentials have ever been issued. |

---

## 5. Wiring the platforms (procedure — not yet executed)

### Render

1. Doppler → project → `production` config → **Integrations** → **Render**.
2. Authorise, select the `2ndactcapital-api` service, sync scope = *service*.
3. Trigger a deploy so the injected values take effect.
4. **Then** delete the hand-set duplicates from Render → service →
   *Environment*. Leaving them is not harmless: Render's own values take
   precedence, so a stale duplicate silently wins and Doppler becomes
   decorative.

### Vercel

1. Doppler → project → `production` config → **Integrations** → **Vercel**.
2. Select the frontend project; target = **Production** (add Preview separately
   if wanted — do **not** point Preview at `staging`, which is unused).
3. **Redeploy** — `NEXT_PUBLIC_API_URL` is build-time (§4).
4. Then delete the hand-set duplicates from Vercel → *Settings* →
   *Environment Variables*.

Ordering matters in both cases: **verify, then delete.** Deleting first turns a
misconfigured integration into an outage instead of a no-op.

---

## 6. Interim workaround (until §5 is done)

`APP_SERVICE_DATABASE_URL` is currently **broken** — the password does not
authenticate. Any verify script relying on it is silently taking the
`SET LOCAL ROLE app_service` fallback and proving less than it claims.

Until Doppler is live, either:

- **Repair the value:** get the current `app_service` password and update
  `apps/api/.env`. Confirm with `python3 apps/api/scripts/verify_doppler.py`,
  which connects rather than merely checking that the variable is non-empty.
- **Or unset it:** with the variable absent, verify scripts print an explicit
  `SKIP` instead of a vacuous pass. A visible skip beats an invisible one.

Do not leave it set-but-broken. That is the exact state that produced four
consecutive false-green runs.

---

## 7. Blocked — what is missing

The wiring in §5 and the migration could not be executed. This is an access
gap, not a technical one:

| Requirement | State |
|---|---|
| Doppler CLI installable | **verified** — v3.76.5 installed and running |
| `api.doppler.com` reachable | **verified** — responds `401` (reachable, unauthenticated) |
| Doppler account / `DOPPLER_TOKEN` | **absent** — `doppler me` → `you must provide a token`; no `~/.doppler`, no configured scope |
| Doppler project + configs | **unverifiable** — cannot list without a token |
| Render API credential | **absent** — `api.render.com` → `401`; no `RENDER_API_KEY` |
| Vercel API credential | **absent** — `api.vercel.com` → `403`; no `VERCEL_TOKEN`, no `.vercel` link dir |

Creating a Doppler service token, and authorising the Render and Vercel
integrations, both require an interactive browser OAuth session against accounts
this environment holds no credentials for. No amount of scripting substitutes
for that.

**To unblock**, provide either:

- `DOPPLER_TOKEN` (a service token scoped to the `development` config is enough
  for the local leg), **or**
- an interactive session in which to run `doppler login`.

Plus, for the platform legs, `RENDER_API_KEY` and a Vercel token — or perform
the two dashboard integrations by hand per §5.

Then re-run:

```bash
python3 apps/api/scripts/verify_doppler.py
```

It gates each leg independently and reports `BLOCKED` (non-zero exit) rather
than passing vacuously, so the legs turn green one at a time as access arrives.

---

## 8. Never print a secret

Everything here — the verify script, this document, commit messages, sprint logs
— refers to secrets by **name only**. When a value must be checked, the check is
"does a real connection using it succeed", never "is the value equal to X".

This is also why §5's success criterion is a live request rather than a value
comparison: comparing values requires *reading* both, and a read is a leak
waiting for a log line.
