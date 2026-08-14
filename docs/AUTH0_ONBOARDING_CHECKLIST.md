# Auth0 Tenant Onboarding Checklist

**Purpose:** every configuration step required to bring a new client RIA onto Hollisworks, in the order that avoids rework. Written from a real six-issue debugging chain that took hours to resolve — each item below exists because something silently failed without it.

**Two distinct kinds of work appear here.** *Dashboard config* happens in Auth0's UI and takes minutes. *Code* means the application already handles it correctly — listed so you know it's covered, not because you need to do anything. Where an item is code, it's noted.

---

## Part A · Standing architecture (already true — context, not tasks)

- **Two Auth0 tenants exist.** The **Hollisworks tenant** (`dev-gy85vzuf6mruzv3j.us.auth0.com`) is the central broker — the only tenant the application ever talks to directly. Each **client RIA's own tenant/IdP** federates *into* it as an Enterprise Connection.
- **The application never implements raw SAML.** Auth0 handles the protocol and returns a JWT the existing `verify_token()` already validates. Adding a client requires **no application code change**.
- **Auth0's free tier includes exactly one Enterprise Connection.** The second real client triggers a genuine pricing decision ($5,000–$34,000+/year per additional connection per multiple sources). Know this before promising a second client a timeline.
- **Explicit URL listing, never wildcards.** Auth0's own docs caution against wildcards in production, and independent reports describe real bugs with wildcard support for "Allowed Web Origins" specifically. One line per real subdomain.

---

## Part B · Onboarding a new client RIA

### B1 · DNS and hosting

- [ ] Add `<slug>.hollisworks.com` as a custom domain in the **Vercel** project.
- [ ] Add the matching **CNAME** record in Cloudflare (`hollisworks.com` zone), value as shown by Vercel, **Proxy status: DNS only (grey cloud)** — Cloudflare's orange-cloud proxying interferes with Vercel's SSL issuance.
- [ ] Confirm Vercel shows **"Valid Configuration"** before proceeding. Nothing downstream works until this is green.

> **Why not a wildcard:** `hollisworks.com` is registered with Cloudflare Registrar, which cannot point nameservers to a third party — a hard rule confirmed directly with Cloudflare, not a UI limitation. Vercel requires nameserver control for wildcard SSL. Revisit on/after **Oct 1, 2026** (past the 60-day ICANN transfer lock) if the per-client step becomes burdensome.

### B2 · The org record

- [ ] Create the organization with a **DNS-safe slug** — lowercase letters, numbers, hyphens only. Reserved slugs (`admin`, `www`, `api`, `app`, `mail`) are rejected by validation.
- [ ] Populate **`login_url`** and **`enroll_url`** explicitly. The firm-search interstitial reads these stored values rather than constructing them by convention — which is what makes a future custom domain (`portal.clientfirm.com`) a data change rather than a code change.

### B3 · The client's own tenant — configure it as an IdP

Done in **the client's** Auth0 tenant (or whatever IdP they use — Okta, Azure AD, Google Workspace all work; the steps below are Auth0-specific).

- [ ] Create a **dedicated Application** for this federation. Do not reuse an application their real users already log into directly — keep the federation config cleanly separated.
- [ ] **Addons** tab → enable **SAML2 Web App**.
- [ ] Set **Application Callback URL** to `https://dev-gy85vzuf6mruzv3j.us.auth0.com/login/callback` (the Hollisworks tenant's SP-side handler).
- [ ] From the addon's **Usage** tab, copy the **Identity Provider Login URL** and download the **X.509 certificate**.

### B4 · The Hollisworks tenant — consume it

- [ ] **Connections → Enterprise → SAML → Create Connection.**
- [ ] Name it clearly and memorably — this value belongs in `organizations.saml_connection_name`.
- [ ] **Sign In URL** = the Identity Provider Login URL from B3.
- [ ] Upload the **certificate** from B3.
- [ ] Enable the connection for the Hollisworks Application.
- [ ] Use Auth0's built-in **"Try"** button to confirm a real round-trip before involving the client.

### B5 · Application URLs — the `/auth/` prefix matters

In the Hollisworks tenant's Application settings, **append** (never replace) these three:

- [ ] **Allowed Callback URLs**: `https://<slug>.hollisworks.com/auth/callback`
- [ ] **Allowed Logout URLs**: `https://<slug>.hollisworks.com/auth/logout`
- [ ] **Allowed Web Origins**: `https://<slug>.hollisworks.com`

> **The `/auth/` prefix is not optional.** The app's `proxy.js` mounts auth routes there. Omitting it produces *"Callback URL mismatch"* — and the error text names the exact expected value, so read it rather than guessing.

### B6 · API authorization — two separate steps, both required

Only needed when introducing a **new API identifier**. Both were missed originally, each producing a different, confusing error.

- [ ] The audience value (e.g. `https://api.hollisworks.com`) must exist as a **registered API** in the tenant: **Applications → APIs → Create API**. The identifier must match the configured audience **character for character**. Missing → *"Service not found: <audience>"*.
- [ ] The Application must be explicitly authorized for that API: **Application → APIs tab → the API's row → User-delegated Access.** Missing → *"Client ... is not authorized to access resource server ..."*.

> **User-delegated Access and Client Access are different axes.** Browser-based human login uses **User-delegated** (Authorization Code flow). Client/M2M is for machine-to-machine and is **not** what a login flow uses. Configuring the wrong one looks correct and fails anyway.
>
> Creating an API also auto-generates a throwaway **"Test Application"** with its own Client ID. Ignore it — authorize the *real* Application.

### B7 · Environment variables — two separate places

- [ ] **Render** (backend) — anything the FastAPI service reads.
- [ ] **Vercel** (frontend) — anything Next.js server-side code reads (`proxy.js`, `lib/auth0*.js`, login/callback routes).

> A variable set in Render but not Vercel silently fails *in a way that looks like a code bug*: the Auth0 SDK's `domain ?? process.env.AUTH0_DOMAIN` pattern falls back to another tenant's value rather than erroring. The application now **fails loud** on missing Hollisworks-specific config specifically to prevent this — but the variable still has to exist in both places.
>
> **Vercel preview deployments do not inherit production environment variables.** Preview-branch errors about missing Auth0 config are expected and are *not* production issues.

- [ ] Trigger a **redeploy** after adding variables — saving them alone does not apply them.

### B8 · Users

- [ ] Obtain the client's user list: **email + role** per person.
- [ ] Create **pending user records** — `auth0_sub` stays NULL until first login.
- [ ] The client separately enrolls those same people in **their own IdP**, on their own timeline. Hollisworks does not send the invite; the client tells their own staff to log in.

> **Matching is by exact email** (SAML NameID, `emailAddress` format — every IdP sends this by default, no client-side configuration needed). **No matching pending record = hard reject.** The client's IdP authenticating someone proves who they are; it does not prove they should have Hollisworks access.

### B9 · Verify end to end

- [ ] Fresh **incognito** window → `https://<slug>.hollisworks.com/auth/login`.
- [ ] Confirm the login screen shows the **client's own IdP**, not Hollisworks' or another tenant's.
- [ ] Complete a real login and confirm it lands in the app with a working session.
- [ ] Confirm an **existing** client's login (e.g. 2nd Act) still works — regression check.

---

## Part C · Debugging — where to look

| Symptom | Where the real answer is |
|---|---|
| Any Auth0 error page | Click **TECHNICAL DETAILS** — it names the exact wrong value being sent. The summary message alone is nearly useless. |
| *"Callback URL mismatch"* | The detail text quotes the actual `redirect_uri` sent. Compare against Allowed Callback URLs — usually a missing `/auth/` prefix or a wrong base domain. |
| *"Service not found: <url>"* | The audience isn't a registered API in that tenant (B6, first item). |
| *"Client ... not authorized to access resource server"* | The API exists but the Application isn't authorized for **User-delegated** access (B6, second item). |
| *"The state parameter is invalid"* | The flow started with one tenant's config and returned to another's. Also caused by stale cookies across many test attempts — close **every** incognito window and retry cleanly before assuming a code bug. |
| *"An error occurred during the authorization flow"* | Generic wrapper. The real error is in **Vercel Runtime Logs** — expand the individual request; bulk log exports often omit the detail. |
| Backend errors generally | **Render's** logs. Browser and Vercel logs show only a generic forwarded error; the real Python traceback is in Render. |

---

## Part D · What is NOT built yet

- **Password "back door"** for clients without SAML infrastructure — deferred. Whether it should be a per-org toggle or universally available is also undecided.
- **Per-tenant SAML setup automation** (Auth0 Management API) — deliberately manual. Not worth automating until there is real, recurring multi-client demand.
- **SCIM provisioning** (the client's IdP pushing user create/update/deactivate events automatically, replacing B8's manual list) — the standard eventual answer, not needed yet.
