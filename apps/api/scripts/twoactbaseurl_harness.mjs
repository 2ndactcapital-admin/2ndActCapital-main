/**
 * 2nd Act host-derived appBaseUrl — hermetic harness.
 *
 * Proves the ACTUAL `redirect_uri` 2nd Act's Auth0 client builds, using the REAL
 * deployed modules — never a re-implementation:
 *
 *   - apps/web/lib/authHostConfig.mjs         (twoActAppBaseUrls — the fix)
 *   - @auth0/nextjs-auth0 dist/utils/*.js     (the SDK's OWN resolveAppBaseUrl +
 *                                              createRouteUrl, i.e. the exact
 *                                              code /auth/login runs at runtime)
 *
 * It reproduces the SDK's login step verbatim (client.js:808-813 +
 * auth-client.js:335-336):
 *
 *   const envAppBaseUrl = APP_BASE_URL.includes(",") ? split : APP_BASE_URL
 *   this.appBaseUrl     = options.appBaseUrl ?? envAppBaseUrl
 *   const appBaseUrl    = resolveAppBaseUrl(this.appBaseUrl, req)      // SDK fn
 *   const redirectUri   = createRouteUrl("/auth/callback", appBaseUrl) // SDK fn
 *
 * so a printed redirect_uri is what Auth0 would actually receive.
 *
 * Prints one JSON line: { ok, checks:[{name,pass,detail}] }. Exit 0 iff every
 * check passes. No network, no database, no writes.
 */
import { resolveAppBaseUrl } from "../../../node_modules/@auth0/nextjs-auth0/dist/utils/app-base-url.js";
import { createRouteUrl } from "../../../node_modules/@auth0/nextjs-auth0/dist/utils/pathUtils.js";
import {
  twoActAppBaseUrls,
  TwoActAuthConfigError,
  TWOACT_PRIMARY_BASE_URL,
  TWOACT_WWW_BASE_URL,
  TWOACT_TENANT_BASE_URL,
  resolveAuthTenantForHost,
  HOLLISWORKS_ADMIN_HOST,
} from "../../web/lib/authHostConfig.mjs";

// SDK default callback route (client.js:127). No NEXT_PUBLIC_BASE_PATH in this app.
const CALLBACK_ROUTE = "/auth/callback";

const BARE_HOST = "2ndactcapital.com";
const TENANT_HOST = "2ndactcapital.hollisworks.com";
const WWW_HOST = "www.2ndactcapital.com";
const MARKETING_HOST = "hollisworks.com";

const BARE_CALLBACK = `${TWOACT_PRIMARY_BASE_URL}${CALLBACK_ROUTE}`;
const TENANT_CALLBACK = `${TWOACT_TENANT_BASE_URL}${CALLBACK_ROUTE}`;
const WWW_CALLBACK = `${TWOACT_WWW_BASE_URL}${CALLBACK_ROUTE}`;

// Production-like env: exactly what Vercel holds today. APP_BASE_URL is the
// STATIC bare domain — the value that caused the bug.
const envProd = {
  NODE_ENV: "production",
  APP_BASE_URL: TWOACT_PRIMARY_BASE_URL,
  AUTH0_DOMAIN: "dev-smmrfubsfscif3t1.us.auth0.com",
  AUTH0_CLIENT_ID: "twoact_client",
  AUTH0_CLIENT_SECRET: "twoact_secret",
  AUTH0_SECRET: "0".repeat(64),
};

/** Minimal NextRequest stand-in that inferBaseUrlFromRequest(req) understands. */
function mockReq(host, proto = "https") {
  const h = new Map([
    ["host", host],
    ["x-forwarded-proto", proto],
    ["x-forwarded-host", host],
  ]);
  return {
    headers: { get: (k) => h.get(String(k).toLowerCase()) ?? null },
    nextUrl: { host, protocol: proto + ":" },
  };
}

/**
 * Reproduce the SDK's exact redirect_uri construction. `optionAppBaseUrl` is what
 * the Auth0Client was constructed with — `undefined` for the PRE-FIX 2nd Act
 * client, the allow-list array POST-FIX. Throws exactly where the SDK would.
 */
function sdkRedirectUri(optionAppBaseUrl, envAppBaseUrl, req) {
  const envParsed =
    envAppBaseUrl && envAppBaseUrl.includes(",")
      ? envAppBaseUrl.split(",").map((u) => u.trim()).filter(Boolean)
      : envAppBaseUrl;
  const thisAppBaseUrl = optionAppBaseUrl ?? envParsed; // options.appBaseUrl ?? env
  const appBaseUrl = resolveAppBaseUrl(thisAppBaseUrl, req); // SDK fn
  return createRouteUrl(CALLBACK_ROUTE, appBaseUrl).toString(); // SDK fn
}

/** Reproduce client.js:95-104 — are session/transaction cookies marked secure? */
function cookiesSecure(appBaseUrl) {
  if (!appBaseUrl) return process.env.NODE_ENV === "production";
  return Array.isArray(appBaseUrl)
    ? appBaseUrl.every((u) => new URL(u).protocol === "https:")
    : new URL(appBaseUrl).protocol === "https:";
}

const checks = [];
const add = (name, pass, detail) => checks.push({ name, pass: !!pass, detail });

// What lib/auth0.js NOW passes, and what it passed BEFORE the fix.
const postFix = twoActAppBaseUrls(envProd);
const preFix = undefined;

// ---------------------------------------------------------------------------
// 1 — REGRESSION PROOF: the bare domain is byte-for-byte unchanged.
// ---------------------------------------------------------------------------
{
  const before = sdkRedirectUri(preFix, envProd.APP_BASE_URL, mockReq(BARE_HOST));
  const after = sdkRedirectUri(postFix, envProd.APP_BASE_URL, mockReq(BARE_HOST));
  add(
    "REGRESSION: 2ndactcapital.com -> redirect_uri EXACTLY https://2ndactcapital.com/auth/callback (byte-identical to pre-fix)",
    after === BARE_CALLBACK && after === before,
    `pre-fix=${before} post-fix=${after} identical=${after === before}`
  );
}

// ---------------------------------------------------------------------------
// 2 — THE FIX: the tenant subdomain now stays on its own host.
// ---------------------------------------------------------------------------
{
  const uri = sdkRedirectUri(postFix, envProd.APP_BASE_URL, mockReq(TENANT_HOST));
  add(
    "FIX: 2ndactcapital.hollisworks.com -> redirect_uri EXACTLY https://2ndactcapital.hollisworks.com/auth/callback",
    uri === TENANT_CALLBACK && new URL(uri).host === TENANT_HOST,
    `redirect_uri=${uri}`
  );
}

// ---------------------------------------------------------------------------
// 3 — PRE-FIX CONTRAST: reproduce the EXACT observed production bug.
//     A signup FROM 2ndactcapital.hollisworks.com built a callback on the BARE
//     domain, so the __txn_ cookie written on the subdomain was invisible at the
//     callback -> "the state parameter is invalid".
// ---------------------------------------------------------------------------
{
  const uri = sdkRedirectUri(preFix, envProd.APP_BASE_URL, mockReq(TENANT_HOST));
  add(
    'PRE-FIX CONTRAST: static APP_BASE_URL sent a .hollisworks.com signup to https://2ndactcapital.com/auth/callback ("the state parameter is invalid")',
    uri === BARE_CALLBACK && new URL(uri).host === BARE_HOST && uri !== TENANT_CALLBACK,
    `pre-fix redirect_uri=${uri} (WRONG host — expected ${TENANT_CALLBACK})`
  );
}

// ---------------------------------------------------------------------------
// 4 — The pre-fix value ignored the request entirely (the root cause).
//     resolveAppBaseUrl short-circuits on a string, so EVERY host got the same
//     answer. Proven by giving it three different hosts.
// ---------------------------------------------------------------------------
{
  const hosts = [BARE_HOST, TENANT_HOST, WWW_HOST];
  const uris = hosts.map((h) => sdkRedirectUri(preFix, envProd.APP_BASE_URL, mockReq(h)));
  const allSame = uris.every((u) => u === BARE_CALLBACK);
  add(
    "ROOT CAUSE: pre-fix, a STATIC APP_BASE_URL string ignored the request Host — all three hosts produced the same bare-domain callback",
    allSame,
    hosts.map((h, i) => `${h} -> ${uris[i]}`).join(" | ")
  );
}

// ---------------------------------------------------------------------------
// 5 — Post-fix, each real host gets its OWN callback (no cross-host bleed).
// ---------------------------------------------------------------------------
{
  const pairs = [
    [BARE_HOST, BARE_CALLBACK],
    [WWW_HOST, WWW_CALLBACK],
    [TENANT_HOST, TENANT_CALLBACK],
  ];
  const results = pairs.map(([h, want]) => {
    const got = sdkRedirectUri(postFix, envProd.APP_BASE_URL, mockReq(h));
    return { h, want, got, ok: got === want };
  });
  add(
    "every real 2nd Act host resolves to its OWN /auth/callback",
    results.every((r) => r.ok),
    results.map((r) => `${r.h} -> ${r.got}${r.ok ? "" : ` (WANT ${r.want})`}`).join(" | ")
  );
}

// ---------------------------------------------------------------------------
// 6 — FAIL LOUD: an unlisted host throws instead of silently borrowing another
//     host's domain (what the marketing apex does today by accident).
// ---------------------------------------------------------------------------
{
  let threw = false;
  let leaked = "";
  try {
    leaked = sdkRedirectUri(postFix, envProd.APP_BASE_URL, mockReq(MARKETING_HOST));
  } catch {
    threw = true;
  }
  add(
    "unlisted host (hollisworks.com marketing apex) -> throws, never silently builds 2nd Act's callback",
    threw && !leaked,
    `threw=${threw} leaked=${leaked || "(none)"}`
  );
}

// ---------------------------------------------------------------------------
// 7 — SECURE COOKIES unchanged in production (client.js:95-104). The array must
//     be all-https or the SDK silently stops marking cookies secure.
// ---------------------------------------------------------------------------
{
  const before = cookiesSecure(envProd.APP_BASE_URL);
  const after = cookiesSecure(postFix);
  add(
    "production cookie `secure` flag unchanged (all allow-list entries are https)",
    after === true && after === before,
    `pre-fix secure=${before} post-fix secure=${after} entries=${postFix.join(",")}`
  );
}

// ---------------------------------------------------------------------------
// 8 — DEV unchanged: http://localhost:3000 still resolves, and still yields
//     non-secure cookies exactly as the http string did.
// ---------------------------------------------------------------------------
{
  const envDev = { NODE_ENV: "development", APP_BASE_URL: "http://localhost:3000" };
  const list = twoActAppBaseUrls(envDev);
  const uri = sdkRedirectUri(list, envDev.APP_BASE_URL, mockReq("localhost:3000", "http"));
  const before = cookiesSecure(envDev.APP_BASE_URL);
  const after = cookiesSecure(list);
  add(
    "dev: localhost:3000 still builds http://localhost:3000/auth/callback with the same cookie-secure behavior",
    uri === "http://localhost:3000/auth/callback" && after === before && after === false,
    `redirect_uri=${uri} pre-fix secure=${before} post-fix secure=${after}`
  );
}

// ---------------------------------------------------------------------------
// 9 — FAIL LOUD (config layer): malformed / unsafe entries are rejected up front.
// ---------------------------------------------------------------------------
{
  let malformed = false;
  try {
    twoActAppBaseUrls({ NODE_ENV: "production", APP_BASE_URL: "not-a-url" });
  } catch (e) {
    malformed = e instanceof TwoActAuthConfigError;
  }
  let insecure = false;
  try {
    twoActAppBaseUrls({
      NODE_ENV: "production",
      TWOACT_EXTRA_APP_BASE_URLS: "http://evil.example.com",
    });
  } catch (e) {
    insecure = e instanceof TwoActAuthConfigError;
  }
  add(
    "malformed APP_BASE_URL and non-loopback http entries both fail loud (TwoActAuthConfigError)",
    malformed && insecure,
    `malformed=${malformed} nonLoopbackHttp=${insecure}`
  );
}

// ---------------------------------------------------------------------------
// 10 — The escape hatch works: a preview / future tenant origin added via env
//      resolves without a code change.
// ---------------------------------------------------------------------------
{
  const env = {
    ...envProd,
    TWOACT_EXTRA_APP_BASE_URLS: "https://preview-2ndact.vercel.app",
  };
  const list = twoActAppBaseUrls(env);
  const uri = sdkRedirectUri(list, env.APP_BASE_URL, mockReq("preview-2ndact.vercel.app"));
  add(
    "TWOACT_EXTRA_APP_BASE_URLS adds an origin (previews / future tenants) with no code change",
    uri === "https://preview-2ndact.vercel.app/auth/callback",
    `redirect_uri=${uri}`
  );
}

// ---------------------------------------------------------------------------
// 11 — NO CROSS-TENANT REGRESSION: the Hollisworks admin client is untouched and
//      still builds its own callback. (This sprint edits a shared module.)
// ---------------------------------------------------------------------------
{
  const envBoth = {
    ...envProd,
    HOLLISWORKS_AUTH0_DOMAIN: "dev-gy85vzuf6mruzv3j.us.auth0.com",
    HOLLISWORKS_AUTH0_CLIENT_ID: "hollis_client",
    HOLLISWORKS_AUTH0_CLIENT_SECRET: "hollis_secret",
  };
  const cfg = resolveAuthTenantForHost(HOLLISWORKS_ADMIN_HOST, envBoth);
  const uri = sdkRedirectUri([cfg.appBaseUrl], envBoth.APP_BASE_URL, mockReq(HOLLISWORKS_ADMIN_HOST));
  add(
    "Hollisworks admin client unchanged -> https://admin.hollisworks.com/auth/callback",
    uri === "https://admin.hollisworks.com/auth/callback" && cfg.tenant === "hollisworks",
    `tenant=${cfg.tenant} redirect_uri=${uri}`
  );
}

// ---------------------------------------------------------------------------
// 12 — The 2nd Act branch of resolveAuthTenantForHost now carries the allow-list
//      (and still the 2nd Act tenant/audience) for every non-admin host.
// ---------------------------------------------------------------------------
{
  const cfg = resolveAuthTenantForHost(TENANT_HOST, envProd);
  const ok =
    cfg.tenant === "2ndact" &&
    Array.isArray(cfg.appBaseUrl) &&
    cfg.appBaseUrl.includes(TWOACT_TENANT_BASE_URL) &&
    cfg.appBaseUrl.includes(TWOACT_PRIMARY_BASE_URL) &&
    cfg.audience === "https://api.2ndactcapital.com";
  add(
    "resolveAuthTenantForHost(2ndactcapital.hollisworks.com) -> 2nd Act tenant with the host-derived allow-list",
    ok,
    `tenant=${cfg.tenant} audience=${cfg.audience} appBaseUrl=${JSON.stringify(cfg.appBaseUrl)}`
  );
}

const ok = checks.every((c) => c.pass);
process.stdout.write(JSON.stringify({ ok, checks }) + "\n");
process.exit(ok ? 0 : 1);
