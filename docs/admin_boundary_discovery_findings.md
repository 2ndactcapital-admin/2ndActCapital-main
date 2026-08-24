# Admin Surface Tenant Boundary — Discovery Findings

**Sprint:** `adminboundary.structural` — DISCOVERY ONLY
**Date:** 2026-08-24
**Scope:** read-only. No schema changes, no code changes, no RLS changes, no
Auth0 changes. **No verify script** — there is nothing to assert pass/fail
against; this report is the entire deliverable.
**Status:** STOP CONDITION TRIGGERED at 1d/1g.

---

## 0. Lead finding — the STOP condition

**Two `public.users` rows carry `role = 'super_admin'`, and BOTH sit in
2nd Act Capital's org (`00000000-0000-0000-0000-000000000001`). Zero users
sit in the Hollisworks org. The API's super-admin check is org-blind by
design, so both of these accounts pass every structured-notes admin gate
today.**

| `users.id` | email | `auth0_sub` | role | org_id | org | created | updated |
|---|---|---|---|---|---|---|---|
| `06fe93ed-bf7e-51d1-adc3-3023e959e382` | `jlarizza@culmina.io` | `auth0\|06fe93ed-bf7e-51d1-adc3-3023e959e382` | `super_admin` | `…0001` | **2nd Act Capital** | 2026-06-26 | 2026-07-01 |
| `f46eb620-f03c-49ab-b946-eefa621022f7` | `auth0\|6a3af4c9a1c6aeb8baddf3eb@placeholder.local` | `auth0\|6a3af4c9a1c6aeb8baddf3eb` | `super_admin` | `…0001` | **2nd Act Capital** | 2026-07-02 | 2026-07-02 |

The other 9 rows are `role = 'member'` (8 are `verify_chancery*@test.local`
fixtures, all also in 2nd Act's org).

**Both rows are Joe's own accounts, and this is already a documented item.**
`docs/PROJECT_STATUS.md:1096`:

> | Stray duplicate user identity for jlarizza@culmina.io | Two user rows exist
> (normal, Jun 26; dormant, Jul 2 — promoted to super_admin as a cutover
> unblock). Root cause not fully diagnosed. **Explicit decision: leave as-is** …

So this is **not** an unknown third party holding elevated access. But the
structural fact the sprint was checking for is confirmed, and it is worse than
"a stray duplicate row":

* Both rows predate the Hollisworks Auth0 tenant by five weeks — that tenant
  landed **2026-08-11** (`0cfe70e sprint: hollisworksauth.structural — second
  Auth0 tenant for admin.hollisworks.com`). On 2026-06-26 and 2026-07-02,
  `HOLLISWORKS_AUTH0_DOMAIN` did not exist, so `is_hollisworks_claims()` could
  not have returned `True`. **These are 2nd Act Auth0 tenant identities that
  were promoted by hand**, not Hollisworks staff identities.
* `auth0|6a3af4c9a1c6aeb8baddf3eb` is the real Auth0 database-connection sub
  shape (`auth0|` + 24 hex). It is a live, loginable identity on the 2nd Act
  tenant. `auth0|06fe93ed-bf7e-51d1-adc3-3023e959e382` is `auth0|` + a UUIDv5 —
  the shape `services.permissions.get_user_id()` *derives*, not a shape Auth0
  issues, so that row looks seeded/synthetic.
* The API gate reads the **persisted** `users.role`, not the token issuer. Once
  a row says `super_admin`, no Hollisworks token is needed at request time —
  logging in through 2nd Act's own tenant on 2nd Act's own domain is enough.

That is the answer to the question this sprint was written to ask: **yes, a
2nd-Act-tenant login can hold super-admin today, and one does.**

---

## 1. Can a 2nd Act (or other tenant) user reach the structured-notes admin surfaces TODAY?

**Yes — for the two accounts above, and only for them.** There is no second
condition to satisfy. Specifically:

1. **No network/deployment wall.** `/admin/pricing/note-terms-queue` and
   `/admin/pricing/surface` are routes in the *same* Next.js app that serves
   every tenant host (1e below). `2ndactcapital.hollisworks.com/admin/pricing/...`
   resolves.
2. **No entitlement layer.** `org_settings` has no `features.*` key and no
   module gate of any kind (1h below).
3. **No org scoping in the check.** `services.rbac.is_super_admin` deliberately
   ignores `org_id`.
4. **RLS is inert.** The app connects as `postgres`, which is
   `rolbypassrls = true` (1f below). Every `app.is_super_admin` RLS policy is a
   no-op for the running application.

So the *entire* boundary is one Python expression comparing a text column to a
string literal.

For a **hypothetical third-party tenant member** the answer is *not currently* —
`ensure_user` only ever writes `'member'` or `'super_admin'`, and it writes
`'super_admin'` only on the Hollisworks issuer. But the reason is "no such row
exists yet", not "the architecture prevents it." Any path that sets
`users.role = 'super_admin'` on a tenant row — a manual SQL promotion exactly
like the two above, or a future admin UI — grants the full global admin surface
with nothing else standing in the way.

---

## 2. The ACTUAL gating mechanism (1a, 1b) — real code

### 1a. Where `app.is_super_admin` is SET

`apps/api/services/database.py:140-163` — `_apply_rls_settings()`, called as the
first statement of every wrapped transaction:

```python
org_id = _org_id_var.get() or ""
is_super = "true" if _is_super_admin_var.get() else "false"
auth0_sub = _auth0_sub_var.get() or ""
await conn.execute(
    "SELECT set_config('app.current_org_id', $1, true),"
    "       set_config('app.is_super_admin', $2, true),"
    "       set_config('app.current_auth0_sub', $3, true)",
    org_id,
    is_super,
    auth0_sub,
)
```

The ContextVar is populated once per request by
`apps/api/main.py:289-343` — `rls_context_middleware`:

```python
        try:
            # Reads users by auth0_sub — now permitted by the bootstrap leg,
            # since app.current_auth0_sub is already set (step 1 above).
            is_super = await _resolve_is_super_admin(request)
        except Exception as exc:
            print(f"[rls] is_super_admin resolution failed (default False): {exc}")
            is_super = False

    # 2. Now that identity is resolved, set org_id/is_super_admin for the
    #    remainder of the request's queries.
    tokens = set_rls_context(org_id, is_super)
```

**The condition** is `apps/api/main.py:257-282` — `_resolve_is_super_admin()`.
Verbatim, the whole decision:

```python
    claims = getattr(request.state, "user", None) or {}
    sub = claims.get("sub")
    if not sub:
        return False
    # Hollisworks-tenant identity IS platform staff — recognized directly from the
    # validated token issuer, before (and independent of) any users-row read, so a
    # first request establishes Super Admin context without a write race.
    if is_hollisworks_claims(claims):
        return True
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT role FROM users WHERE auth0_sub = $1", sub)
    return is_super_admin(dict(row)) if row else False
```

There is exactly **one other** place the GUC is set — a route-scoped override
in `apps/api/routers/pricing_admin.py:421-422`, inside the note-terms field
resolve handler, after `_require_super_admin` has already passed:

```python
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
```

### 1b. Two disjunct conditions, traced to source

**Condition A — token issuer.** `apps/api/main.py:197-208`:

```python
def is_hollisworks_claims(claims: dict | None) -> bool:
    """True when a validated token was issued by the Hollisworks tenant.
    ...
    """
    settings = get_settings()
    if not settings.hollisworks_enabled or not claims:
        return False
    return claims.get("iss") == settings.hollisworks_issuer
```

`hollisworks_issuer` is `f"https://{self.hollisworks_auth0_domain}/"`
(`main.py:130-132`); `hollisworks_enabled` is
`bool(self.hollisworks_auth0_domain)`. The claim is trustworthy — `verify_token`
(`main.py:211-244`) validates signature/audience/issuer against that tenant's
own JWKS before any of this runs. This condition is sound: it cannot be forged
by a 2nd Act token.

**Condition B — the persisted `users.role` column.**
`apps/api/services/rbac.py:122` and `137-143`:

```python
SUPER_ADMIN_ROLE = "super_admin"
...
def is_super_admin(user) -> bool:
    """True when the user is Ripasso platform staff.

    Deliberately ignores org_id — a super_admin sits in the Ripasso platform
    org yet administers every tenant.
    """
    return _field(user, "role") == SUPER_ADMIN_ROLE
```

`users.role` is `text NOT NULL` with **no CHECK constraint** — deliberate, per
the comment at `rbac.py:106-120`. Live `DISTINCT role` (1b's query):

| role | count |
|---|---|
| `member` | 9 |
| `super_admin` | 2 |

`'org_admin'` is defined in code (`rbac.py:123`) but held by nobody yet.

**This is where the boundary leaks.** Condition B is a database value with no
constraint, no org qualification, and no link back to Condition A. It is the
condition the two rows in §0 satisfy.

The only *application* writer of `super_admin` is
`apps/api/services/users.py:56-79`, and it is issuer-gated:

```python
        is_staff = is_hollisworks_claims(claims)
    except Exception:
        is_staff = False
    role = "super_admin" if is_staff else "member"
...
            if is_staff and by_sub["role"] != "super_admin":
                await conn.execute(
                    "UPDATE users SET role = 'super_admin' WHERE id = $1",
                    by_sub["id"],
                )
```

Note it **never demotes** (`Never demotes and never touches non-staff rows`),
and the INSERT's `ON CONFLICT` clause has the same ratchet:
`role = CASE WHEN EXCLUDED.role = 'super_admin' THEN 'super_admin' ELSE users.role END`.
So a hand-promoted tenant row is permanent as far as the app is concerned —
nothing in the login path will ever reverse it. That is precisely why the two
2026-06/07 rows are still `super_admin` today.

No admin endpoint writes `users.role`. Grepping every router and service for
`UPDATE users` / `role =` finds exactly one hit: `services/users.py:77` above.
`routers/admin.py`, `routers/invites.py` and `routers/users.py` only *read* it,
or write `user_roles` (the separate RBAC grant table, which
`load_principal` deliberately does not consult —
`rbac.py:158-163`: *"Read from `users.role` rather than `user_roles` because the
platform/tenant admin distinction is a property of the account itself"*).
**So the only way those two rows became `super_admin` is direct SQL** — matching
PROJECT_STATUS's "promoted … as a cutover unblock."

### `auth.users` — resolved: vestigial

`SELECT count(*) FROM auth.users` → **0 rows**. The Supabase-native auth schema
is empty, so its `is_super_admin` boolean gates nothing and is referenced
nowhere in `apps/api`. This question is closed: it is dead weight, not a second
authorization path.

---

## 3. (1c) Is the issuing Auth0 tenant recorded anywhere? **No.**

Reporting this explicitly as a **finding**, per the sprint instruction.

* **`public.users` has no issuer/tenant column.** Live introspection of all 18
  columns: `id, org_id, email, full_name, avatar_url, auth0_sub, role,
  created_at, updated_at, assistant_panel_posture, nav_pinned, profile_id,
  manager_id, invite_token, invite_status, invited_by, invited_at,
  invite_expires_at`. Nothing named `iss`, `tenant`, `auth0_tenant`,
  `connection`, or equivalent.
* **`auth0_sub` cannot distinguish tenants.** It stores the bare `sub` string
  (`auth0|6a3af4c9a1c6aeb8baddf3eb`). Auth0 `sub` values are unique *within* a
  tenant, not across tenants, and the string carries no tenant identifier. The
  column has a UNIQUE constraint, so **the same `sub` string issued by both
  tenants would collide onto one row** — and per §1b the role ratchets upward
  and never down. A Hollisworks-issued sub landing on an existing 2nd Act row
  would silently promote that 2nd Act row to `super_admin` permanently. This is
  improbable (Auth0 generates random 24-hex ids) but it is *not prevented*, and
  Auth0 user-import lets a `user_id` be specified explicitly.
* **The `iss` claim IS read, but only in-request and never persisted.**
  `is_hollisworks_claims` reads `claims["iss"]` and the result is thrown away
  after the request; only its *effect* (`role = 'super_admin'`) survives, and
  that effect is indistinguishable from a manual promotion. There is no audit
  trail of which tenant caused a promotion.
* **Repo-visible Auth0 config confirms two separate clients but nothing more.**
  `apps/web/lib/authForHost.js`, `apps/web/lib/auth0Hollisworks.js`,
  `apps/web/lib/authHostConfig.mjs`, and the backend `hollisworks_*` settings
  in `main.py`. All are host→client selection; none record provenance.
* **Related:** `ORG_ID_CLAIMS` is 2nd-Act-namespaced only —
  `("org_id", "https://2ndactcapital.com/org_id", "https://api.2ndactcapital.com/org_id")`
  (`routers/entities.py:57-61`, duplicated in `routers/documents.py:39-43` and
  `routers/entity_documents.py:22-26`), falling back to
  `DEFAULT_ORG_ID = "00000000-0000-0000-0000-000000000001"`. A Hollisworks token
  will never carry a claim in that list, so **`get_org_id` puts every
  Hollisworks staff session into 2nd Act Capital's org**, and `ensure_user`
  writes that `org_id` onto the staff row. Going forward, real staff logins will
  keep manufacturing `super_admin` rows *inside the tenant org* — the same shape
  as the two in §0. The Hollisworks org row exists
  (`bb347258-8f28-4f49-8cc9-e29ccad82884`, slug `hollisworks`, created
  2026-07-22) and has **zero users**.

---

## 4. (1e) Deployment/domain separation: **NONE. It is (ii).**

`/admin/*` is a route group inside the single tenant-facing Next.js app.

* **No `vercel.json` anywhere in the repo.** `render.yaml` declares exactly
  **one** web service (`2ndactcapital-web`, `rootDir: .`,
  `cd apps/web && npx next start`) plus one API service. One build, one origin,
  all hosts.
* **`apps/web/proxy.js` is the only middleware, and it gates by host for
  *authentication only* — it never blocks a path.** Full body:

```js
export async function proxy(request) {
  const authClient = getAuthClientForHost(request.headers.get("host"));
  const authResponse = await authClient.middleware(request);
  return authResponse;
}
export const config = {
  matcher: ["/((?!_next/static|_next/image|favicon.ico|sitemap.xml|robots.txt).*)"],
};
```

  Its own comment names the gap: *"If you need to block requests, do it before
  calling `authClient.middleware()`"* — nothing does.
  `getAuthClientForHost` (`lib/authForHost.js:34-36`) is a one-line ternary
  selecting an Auth0 client; it has no notion of routes.
* **21 pages under `apps/web/app/admin/`**, including
  `admin/pricing/note-terms-queue/page.js` and `admin/pricing/surface/page.js`.
  All are served from every host.
* **Frontend gating is cosmetic and says so.**
  `admin/pricing/note-terms-queue/page.js:10-13`:

```js
// Super Admin is enforced SERVER-SIDE by FastAPI — the nav entry is gated too,
// but a hidden link is not a permission. A non-super-admin who types the URL
// gets a 403 from the API and the "not permitted" panel below.
```

  The page checks only `if (!session) redirect(...)`. `Sidebar.jsx:368`
  (`role === "super_admin"`) hides the link; `app/admin/page.js:19+` filters the
  section list the same way. Both read `/users/me`, which reads `users.role`
  (`routers/users.py:38-69`) — the same column, so no independent check.

**Conclusion:** the role check is the *only* thing between a tenant user and
these screens. There is no second wall.

---

## 5. (1f) Are the three sprints' gates consistent? **Yes — with three caveats.**

All three features live in just two routers, both mounted at `/api/v1`
(`main.py:438-439`), and both resolve through Condition B of §1b.

| Sprint | Endpoints | Router | Gate |
|---|---|---|---|
| notetermsrouting | `GET /admin/pricing/note-terms/queue`, `POST …/resolve`, `GET/POST/DELETE …/stp-policies` | `pricing_admin.py` | `_require_super_admin` (L144) |
| underlyingresolution | `GET /admin/pricing/underlying/queue`, `POST …/confirm`, `POST …/reject` | `pricing_admin.py` (same router, deliberately — L587) | `_require_super_admin` (L144) |
| S31 (SSVI surface) | `POST /admin/pricing/surface` | `pricing_surface.py` | `_require_super_admin` (L108) |

`pricing_admin.py:144-158`:

```python
async def _require_super_admin(request: Request) -> str:
    pool = await get_pool()
    async with pool.acquire() as conn:
        actor_id = await ensure_user(conn, request)
        principal = await load_principal(conn, actor_id)
    if not is_super_admin(principal):
        raise HTTPException(status_code=403, detail="Super Admin access required")
    return actor_id
```

`pricing_surface.py:108-116` — same check, differing only in returning
`(actor_id, org_id)`:

```python
async def _require_super_admin(request: Request) -> tuple[str, str]:
    org_id = get_org_id(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        actor_id = await ensure_user(conn, request)
        principal = await load_principal(conn, actor_id)
    if not is_super_admin(principal):
        raise HTTPException(status_code=403, detail="Super Admin access required")
    return actor_id, org_id
```

No third router touches this data — grepping
`note_terms|underlying|reference_filings|securities_global` across
`apps/api/routers/` hits only `pricing_admin.py` (plus `document_links.py` and
`ownership_tree.py`, which match on the unrelated `link_role`/`entitlement`
words). So the gating is **consistent**, and it is *duplicated, not shared* —
two near-identical copies of the same helper. Caveats:

1. **The duplication is a drift hazard, not a bug today.** Two copies of a
   security check will eventually diverge; the `tuple` return already differs.
2. **The gate calls `ensure_user` before checking.** A caller who fails the
   check has, by then, had a `users` row created for them (as `'member'`). Not
   an escalation, but the 403 path is not read-only.
3. **RLS adds nothing in production.** 30+ policies gate on
   `current_setting('app.is_super_admin', true) = 'true'` — including
   `securities_global_super_admin_update/delete`,
   `reference_filings_super_admin_update/delete`,
   `securities_global_note_terms_super_admin_update/delete`,
   `note_terms_stp_policy_super_admin_*`, `note_terms_field_registry_super_admin_*`.
   **All are inert.** `DATABASE_URL`'s user is `postgres.mmgwmcinimzuhargsazs`,
   and `pg_roles` says `postgres` has `rolbypassrls = true` (`app_service` is
   `false`, and `APP_SERVICE_DATABASE_URL` is *not* what `get_pool()` reads —
   `database.py:290` reads `DATABASE_URL` only). `database.py:68-74` states this
   plainly:

   > NOTE — this sprint does NOT change the live connection. DATABASE_URL still
   > points at the original (RLS-bypassing) `postgres` role in every deployed
   > environment. The wrapping below sets the GUCs on every connection, but they
   > are inert while connected as a bypass role.

   So the "defence in depth" the three sprints verified is, in production, a
   single layer: the Python `if`.

---

## 6. (1h) Is there any entitlement layer? **No.**

`org_settings` is `(id, org_id, setting_key, setting_value jsonb, category,
is_public, updated_at, updated_by, created_at)`. Live contents — **29 rows,
all for 2nd Act Capital, zero for Hollisworks**, in four categories only:

* `ai.*` (6): `model.assistant`, `model.default`, `model.document_classifier`,
  `model.fallback`, `model.fallback_chain`, `model.provider`
* `brand.*` (17), `footer.*` (3), `locale.base_currency`, `naming.*` (2)

There is **no `features.*` key, no `modules.*`, no per-org gate of any kind** —
confirming the earlier finding that `features.*` was proposed and never built.
Grepping `apps/` for `features\.`, `entitlement`, `module_enabled`,
`feature_flag` returns nothing functional (one unrelated docstring in
`services/ownership_tree.py:285`).

**Role-check is the entire boundary today.** There is no layer that could say
"this module is not sold to this org" even if the role check were correct.

---

## 7. What Joe must check OUTSIDE the repo

This sprint cannot see any of these. Each is a genuine unknown, not a formality.

1. **Auth0 dashboard — which tenant issued `auth0|6a3af4c9a1c6aeb8baddf3eb`?**
   Search both tenants' user lists for that `user_id`. The repo evidence
   (created 2026-07-02, five weeks before the Hollisworks tenant existed) says
   2nd Act, but only the dashboard proves it. Also: is that identity still
   loginable, and does it have a password/social connection, or is it orphaned?
   Same question for `auth0|06fe93ed-bf7e-51d1-adc3-3023e959e382`, whose sub
   shape suggests it was never an Auth0 identity at all.
2. **Auth0 dashboard — does the 2nd Act tenant emit any `org_id` claim?** All
   11 rows have `org_id = …0001`, which is consistent with *no* claim being
   emitted and `DEFAULT_ORG_ID` always winning. If so, org assignment is
   currently a hardcoded constant, not an identity property — which matters a
   lot for the second tenant.
3. **Render/Vercel — is `HOLLISWORKS_AUTH0_DOMAIN` actually set on the API
   service?** `render.yaml` does not declare it (only `AUTH0_DOMAIN` /
   `AUTH0_AUDIENCE`, both `sync: false`). If unset in the API's env,
   `hollisworks_enabled` is `False`, so `is_hollisworks_claims` returns `False`
   for everyone and **Condition A never fires** — real staff would then get
   `role = 'member'` and be locked out, while the two legacy tenant rows remain
   the only super-admins. A prior sprint already flagged the *frontend*
   `HOLLISWORKS_AUTH0_*` vars as still needed on Vercel; the API side needs the
   same confirmation.
4. **Which platform actually serves the web app, and does it have host-level
   routing rules?** `render.yaml` declares a Render web service; CLAUDE.md says
   Vercel. If Vercel, check whether `admin.hollisworks.com` and
   `*.hollisworks.com` are domains on the *same* project (they must be, given
   one codebase and no `vercel.json`) and whether any Firewall/WAF rule
   restricts `/admin/*` by host. Nothing in the repo suggests one exists.
5. **Was the 2026-07-01/07-02 promotion the only manual `users.role` write?**
   Check Supabase's SQL editor history / audit logs. The app has no code path
   that could have done it, so someone ran SQL; knowing whether that was a
   one-off matters for whether other rows were touched.

---

## 8. Recommendation — for the NEXT sprint, not this one

Nothing was changed and nothing should be changed on this report alone. The fix
is a security-boundary decision and needs Joe's call. Framing the choice:

**The root cause is that Condition A (trustworthy: validated token issuer) is
converted into Condition B (untrustworthy: an unconstrained text column) and
then only Condition B is ever checked.** The persisted role outlives, and is
indistinguishable from, its cause.

Three options, in what I'd consider descending order of soundness:

1. **Check the issuer at request time, not the stored role** — make
   `_require_super_admin` require `is_hollisworks_claims(request.state.user)`
   rather than `users.role`. The gate then reads the signed token directly and
   no DB value can grant platform admin. Strongest, and it makes the two legacy
   rows harmless without touching them. Cost: it hard-couples platform admin to
   one Auth0 tenant, and it must fail *closed* if `HOLLISWORKS_AUTH0_DOMAIN` is
   unset (see check #3 — today an unset var would silently lock out all real
   staff).
2. **Keep the role check but add an org predicate** — require
   `role == 'super_admin' AND org_id == HOLLISWORKS_ORG_ID`
   (`bb347258-8f28-4f49-8cc9-e29ccad82884`). Small, surgical, mirrors the
   existing `is_org_admin` shape. But it **breaks immediately** unless `get_org_id`
   stops putting Hollisworks sessions into 2nd Act's org (§3) — that must be
   fixed in the same sprint, and it would revoke Joe's own current access,
   so sequencing matters.
3. **Deployment-level separation** — serve `/admin/pricing/*` only from
   `admin.hollisworks.com`, enforced in `proxy.js` before
   `authClient.middleware()`. Genuine defence in depth and the comment in
   `proxy.js` already anticipates it, but it is a second wall, not a fix: the
   role check would still be wrong underneath.

Two things worth doing regardless of which is chosen:

* **De-duplicate `_require_super_admin` into one shared dependency** (§5 caveat
  1) so the next change lands in one place. Today a fix must be applied twice
  identically or the gates diverge.
* **Decide whether the RLS layer is meant to be live.** Right now the app
  connects as a bypass role, so every `app.is_super_admin` policy those three
  sprints verified is decoration (§5 caveat 3). Either switch the app to
  `app_service` — which needs the `users` carve-out that
  `main.py:264-268` already anticipates — or stop counting RLS as a layer in
  security reasoning. Believing in a layer that isn't running is worse than not
  having it.

---

## Appendix — what this sprint changed

Nothing in `apps/`, nothing in the database, nothing in Auth0. The only new
file is this report. **Part 4 is "nothing to merge"** beyond committing this
document. **There is no `verify_adminboundary.py`** — a discovery sprint has no
pass/fail assertions to make, and writing a checklist that only re-asserts what
the queries above already show would be theatre.
