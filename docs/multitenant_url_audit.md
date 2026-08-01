# Multi-Tenant URL / Org Resolution — Discovery Audit

**Date:** 2026-08-01
**Scope:** Pure audit. No code, schema, or config was changed. This report
describes the *current* state of how a request is mapped to an organization,
so that white-label / multi-tenant design can be scoped in a **separate, later**
conversation. No fix or design is proposed here.

---

## Bottom line

**Yes — org resolution is based purely on the authenticated user's own
`users.org_id` (surfaced as a JWT claim). Nothing in the URL — subdomain,
custom domain, path segment, or query parameter — distinguishes tenants on any
request path that a logged-in user actually hits.**

There is exactly **one** vestige of host-based tenant resolution: the
`/theme/public` endpoint accepts an optional `slug` query param "when the
deployment is host-mapped." That parameter is **never populated by the
frontend** and there is **no host→slug mapping anywhere** in the repo, so it is
inert. It is a design stub, not working infrastructure.

---

## (a) The real `get_org_id(request)` — how org is determined

Defined once, in `apps/api/routers/entities.py:82`, and imported everywhere
(including the RLS middleware in `main.py`):

```python
DEFAULT_ORG_ID = "00000000-0000-0000-0000-000000000001"

ORG_ID_CLAIMS = (
    "org_id",
    "https://2ndactcapital.com/org_id",
    "https://api.2ndactcapital.com/org_id",
)

def get_org_id(request: Request) -> str:
    """Resolve the caller's org_id from JWT claims, or the default org."""
    claims = getattr(request.state, "user", None) or {}
    for key in ORG_ID_CLAIMS:
        value = claims.get(key)
        if value:
            return value
    return DEFAULT_ORG_ID
```

**Evidence it is purely user/JWT-driven, with zero URL logic:**

- The only input is `request.state.user` — the verified JWT claims. It reads
  three claim keys and otherwise returns the hardcoded default org.
- It never touches `request.url`, `request.headers` (Host / X-Forwarded-Host),
  `request.query_params`, or any path segment.
- `request.state.user` is populated solely from the `Authorization: Bearer`
  token: `main.py:250` reads the `Authorization` header and `main.py:260` sets
  `request.state.user = verify_token(token)`. No host/subdomain is consulted.
- The RLS middleware (`main.py:180–227`) that stamps the per-request org GUC
  calls this same `get_org_id(request)` — "nothing reinvented" (its own
  comment, `main.py:185`). So the *entire* request lifecycle derives org from
  the token, never the URL.

Note on the JWT claim itself: whether `org_id` is present in the token is an
Auth0-config matter outside this repo. In the current single-org deployment the
claim resolves to — or falls back to — the one `DEFAULT_ORG_ID`. The code path
is capable of honoring a per-user `org_id` claim, but that claim originates from
the user's identity, **not** from the URL they arrived on.

## (d) How `org_id` is set in the first place (first login)

`ensure_user` in `apps/api/services/users.py:36` creates the `users` row on
first sight. The org it stamps comes from the same `get_org_id(request)`:

```python
org_id = get_org_id(request)      # users.py:51
...
INSERT INTO users (id, org_id, email, full_name, auth0_sub, role)
VALUES (uuid_generate_v4(), $1, $2, $3, $4, 'member')   # $1 = org_id
```

So at signup the org is assigned from the **JWT claim, else the hardcoded
default org** — never from anything in the URL or request host. This closes the
loop with (a): a user's org is fixed at first login from their token/identity,
and every subsequent request re-derives that same org from the token. The URL
plays no part at any point.

## (b) Frontend routing config

- **`next.config.mjs`** (`apps/web/next.config.mjs`): only a Turbopack
  `root` pin. **No `rewrites`, no `redirects`, no domain logic.**
- **Middleware:** There is **no source middleware** (`middleware.js`/`.ts`) in
  `apps/web`. The only matches are compiled build artifacts under `.next/`.
  Request interception is instead done by **`apps/web/proxy.js`**, which is a
  thin pass-through to Auth0:

  ```js
  export async function proxy(request) {
    const authResponse = await auth0.middleware(request);
    return authResponse;   // forwards to app routes; no tenant routing
  }
  export const config = { matcher: [ "/((?!_next/static|...).*)" ] };
  ```

  It does **nothing** with host, subdomain, or tenant — it only runs the Auth0
  session middleware.
- **`vercel.json`:** **Does not exist** anywhere in the repo — not at the app
  level, not at the true repo root, nowhere. Re-confirmed by filesystem search.

## (c) Real current domain setup

- No committed Vercel project config exists (no `vercel.json`), so the repo does
  not declare or hint at any subdomain / custom-domain wiring.
- The only domain literals in the codebase are `2ndactcapital.com` /
  `api.2ndactcapital.com`: the CORS default `allowed_origins`
  (`main.py:80` → `"http://localhost:3000,https://2ndactcapital.com"`) and the
  namespaced JWT claim keys. Both point at the single production domain.
- **Conclusion:** as far as anything inspectable from the repo shows,
  `2ndactcapital.com` is the sole domain the app is built to respond to. No
  additional domains or subdomain support are configured or documented in-repo.
  (Anything configured directly in the Vercel dashboard is not visible here and
  would not be reflected in code.)

## (e) Any subdomain / custom-domain / tenant-domain references (even partial)

A broad search for `subdomain`, `custom_domain`, `tenant_domain`,
`custom-domain`, and `tenant` found:

- **`subdomain`, `custom_domain`, `tenant_domain`, `custom-domain`:** **zero
  hits** in application code or docs. This concept was never even sketched.
- **`tenant`:** all hits are **Sprint 24 white-label branding**, meaning
  "per-organization," never "per-URL." Examples: `lib/theme.js` ("values live
  in `org_settings` per tenant"), `ThemeProvider.jsx`, `Sidebar.jsx`
  ("no logo configured for this tenant"), `app/manifest.js` (per-tenant PWA
  manifest), `DataGrid.jsx`. None read the host or URL; every one resolves the
  org **after authentication** from the user's own org via `org_settings`.
- **The one real host-based stub** — `/theme/public` in
  `apps/api/routers/org_settings.py:183`:

  ```python
  @router.get("/theme/public")
  async def read_public_theme(slug: str | None = None):
      """... The org is resolved by slug when the deployment is host-mapped;
      otherwise it falls back to the default org."""
      if slug:
          org = ... WHERE slug = $1
      else:
          org = ... WHERE id = DEFAULT_ORG_ID
  ```

  This is the **only** place in the codebase that anticipates resolving an org
  from something other than the logged-in user. But:
  - The frontend caller (`lib/theme.js:122`) fetches `/api/v1/theme/public`
    with **no `slug` query param** and no host-derived value. So the branch is
    never exercised — it always falls back to `DEFAULT_ORG_ID`.
  - There is **no host→slug resolver** anywhere (no middleware, no rewrite, no
    `headers()`/`window.location` host parsing feeding a slug).
  - The `organizations` table (`docs/schema_snapshot.sql:983`) has `id`,
    `name`, `slug` (UNIQUE), `created_at` — **and no `domain` / `host` column.**
    So even the data model has no place to map a domain to a tenant today.

  Interpretation: host-based tenant branding was **designed for but not
  finished** — a slug hook and a UNIQUE org slug exist, but the wiring
  (host→slug, a domain column, a frontend that passes the slug) was never
  built.

---

## What this means for the white-label vision

**If a second org (a real licensee RIA) existed today, its users could NOT reach
a distinctly-branded URL.** Concretely:

- Every user — regardless of org — would log in at the exact same
  **`2ndactcapital.com`**. There is no subdomain, custom domain, or path prefix
  that would route a licensee's members to a tenant-specific entry point.
- After authentication, the app *would* correctly show that user's own org
  branding: the theme, logo, brand name, PWA manifest, and vocabulary all
  resolve from `org_settings` keyed on the authenticated user's `org_id`
  (`GET /theme` → `principal.org_id`). So **branding is already per-org; the
  URL is not.**
- The login screen itself (`/theme/public`, unauthenticated) would render the
  **default org's** branding for everyone, because no slug/host is passed — a
  licensee's members would see 2nd Act Capital's brand until the moment they
  authenticate.

So the current architecture is **"single URL, org-branding-after-login,"** not
**"branded URL per tenant."** The org identity is carried by the *user's token*,
established at first login and re-derived from the token on every request — the
URL never distinguishes tenants. The only pre-existing scaffolding toward
URL-distinguished tenants is the inert `slug` param on `/theme/public` plus a
UNIQUE `organizations.slug`; there is no domain column, no host→org resolver,
and no frontend or platform routing to make it real.

*(Design and remediation are intentionally out of scope for this audit and are
deferred to a separate conversation.)*
