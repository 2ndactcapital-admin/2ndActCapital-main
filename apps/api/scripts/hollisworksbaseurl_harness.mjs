/**
 * Callback-base-URL hermetic harness — proves the ACTUAL `redirect_uri` value the
 * Auth0 SDK constructs for a real login, using the REAL deployed modules:
 *
 *   - apps/web/lib/authHostConfig.mjs        (our host→config resolver, the fix)
 *   - @auth0/nextjs-auth0 dist/utils/*.js    (the SDK's OWN resolveAppBaseUrl +
 *                                             createRouteUrl — the exact code the
 *                                             login route runs at runtime)
 *
 * It reproduces the SDK's login step verbatim (auth-client.js:335-336 +
 * client.js:808-813):
 *
 *   this.appBaseUrl = options.appBaseUrl ?? process.env.APP_BASE_URL
 *   const appBaseUrl = resolveAppBaseUrl(this.appBaseUrl, req)          // SDK fn
 *   const redirectUri = createRouteUrl("/auth/callback", appBaseUrl)... // SDK fn
 *
 * so the printed redirect_uri is what Auth0 would actually receive — not a
 * re-implementation. Prints one JSON line: { ok, checks:[{name,pass,detail}] }.
 * Exit 0 iff every check passes. No network.
 */
import { resolveAppBaseUrl } from "../../../node_modules/@auth0/nextjs-auth0/dist/utils/app-base-url.js";
import { createRouteUrl } from "../../../node_modules/@auth0/nextjs-auth0/dist/utils/pathUtils.js";
import {
  resolveAuthTenantForHost,
  hollisworksAppBaseUrl,
  HollisworksAuthConfigError,
  HOLLISWORKS_ADMIN_HOST,
} from "../../web/lib/authHostConfig.mjs";

// SDK default callback route (client.js:127). No NEXT_PUBLIC_BASE_PATH in this app.
const CALLBACK_ROUTE = "/auth/callback";

const TWOACT_BASE = "https://2ndactcapital.com"; // 2nd Act's APP_BASE_URL in prod
const HOLLIS_BASE = "https://admin.hollisworks.com";

// Production-like env: BOTH tenants configured; APP_BASE_URL points at 2nd Act
// (the shared default that USED to leak into the Hollisworks client).
const envFull = {
  APP_BASE_URL: TWOACT_BASE,
  AUTH0_DOMAIN: "dev-smmrfubsfscif3t1.us.auth0.com",
  AUTH0_CLIENT_ID: "twoact_client",
  AUTH0_CLIENT_SECRET: "twoact_secret",
  AUTH0_SECRET: "0".repeat(64),
  HOLLISWORKS_AUTH0_DOMAIN: "dev-gy85vzuf6mruzv3j.us.auth0.com",
  HOLLISWORKS_AUTH0_CLIENT_ID: "hollis_client",
  HOLLISWORKS_AUTH0_CLIENT_SECRET: "hollis_secret",
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
 * the Auth0Client was constructed with (undefined for 2nd Act's client, the
 * allow-list array for the Hollisworks client). `envAppBaseUrl` is
 * process.env.APP_BASE_URL. Throws exactly where the SDK would throw.
 */
function sdkRedirectUri(optionAppBaseUrl, envAppBaseUrl, req) {
  // client.js:808-813 — APP_BASE_URL may be a comma list; else a bare string.
  const envParsed =
    envAppBaseUrl && envAppBaseUrl.includes(",")
      ? envAppBaseUrl.split(",")
      : envAppBaseUrl;
  const thisAppBaseUrl = optionAppBaseUrl ?? envParsed; // options.appBaseUrl ?? env
  const appBaseUrl = resolveAppBaseUrl(thisAppBaseUrl, req); // SDK fn
  return createRouteUrl(CALLBACK_ROUTE, appBaseUrl).toString(); // SDK fn
}

const checks = [];
const add = (name, pass, detail) => checks.push({ name, pass: !!pass, detail });

// What auth0Hollisworks.js actually passes as appBaseUrl: [cfg.appBaseUrl].
const hollisOption = [resolveAuthTenantForHost(HOLLISWORKS_ADMIN_HOST, envFull).appBaseUrl];
// What auth0.js passes: nothing (relies on process.env.APP_BASE_URL).
const twoactOption = undefined;

// 1 — THE FIX: real request to admin.hollisworks.com/auth/login builds
//     redirect_uri = https://admin.hollisworks.com/auth/callback (EXACT value).
{
  const uri = sdkRedirectUri(hollisOption, envFull.APP_BASE_URL, mockReq(HOLLISWORKS_ADMIN_HOST));
  add(
    "admin.hollisworks.com/auth/login -> redirect_uri EXACTLY https://admin.hollisworks.com/auth/callback",
    uri === `${HOLLIS_BASE}${CALLBACK_ROUTE}` && uri.startsWith(HOLLIS_BASE),
    `redirect_uri=${uri}`
  );
}

// 2 — PRE-FIX CONTRAST: the OLD Hollisworks client passed no appBaseUrl, so the
//     SDK used APP_BASE_URL (2nd Act) verbatim — the exact bug Auth0 rejected.
{
  const uri = sdkRedirectUri(undefined, envFull.APP_BASE_URL, mockReq(HOLLISWORKS_ADMIN_HOST));
  add(
    "pre-fix (no appBaseUrl) WOULD have built the WRONG https://2ndactcapital.com/auth/callback",
    uri === `${TWOACT_BASE}${CALLBACK_ROUTE}`,
    `pre-fix redirect_uri=${uri} (this is what caused "Callback URL mismatch")`
  );
}

// 3 — REGRESSION: 2nd Act's own login-initiation is completely unaffected — it
//     still builds https://2ndactcapital.com/auth/callback (EXACT, unchanged).
{
  const uri = sdkRedirectUri(twoactOption, envFull.APP_BASE_URL, mockReq("2ndactcapital.com"));
  add(
    "2nd Act login-initiation -> redirect_uri EXACTLY https://2ndactcapital.com/auth/callback (unchanged)",
    uri === `${TWOACT_BASE}${CALLBACK_ROUTE}`,
    `redirect_uri=${uri}`
  );
}

// 4 — FAIL LOUD: with the Hollisworks allow-list, a request whose Host is NOT the
//     admin host THROWS rather than silently building 2nd Act's base again.
{
  let threw = false;
  let errName = "";
  let leaked2ndAct = false;
  try {
    const uri = sdkRedirectUri(hollisOption, envFull.APP_BASE_URL, mockReq("2ndactcapital.com"));
    leaked2ndAct = uri.startsWith(TWOACT_BASE);
  } catch (e) {
    threw = true;
    errName = e && e.constructor ? e.constructor.name : String(e);
  }
  add(
    "wrong Host against Hollisworks base -> throws, NEVER silently uses 2nd Act's base",
    threw && !leaked2ndAct,
    `threw=${threw} (${errName}) leaked2ndAct=${leaked2ndAct}`
  );
}

// 5 — FAIL LOUD (config layer): a malformed HOLLISWORKS_APP_BASE_URL override is
//     rejected up front instead of degrading to a wrong base.
{
  let threw = false;
  try {
    hollisworksAppBaseUrl({ HOLLISWORKS_APP_BASE_URL: "not-a-url" });
  } catch (e) {
    threw = e instanceof HollisworksAuthConfigError;
  }
  add(
    "malformed HOLLISWORKS_APP_BASE_URL override -> fail loud (HollisworksAuthConfigError)",
    threw,
    `threw=${threw}`
  );
}

// 6 — SANITY: the resolver derives the Hollisworks base from its OWN host, never
//     from APP_BASE_URL (2nd Act).
{
  const cfg = resolveAuthTenantForHost(HOLLISWORKS_ADMIN_HOST, envFull);
  add(
    "resolver derives Hollisworks appBaseUrl from its own host (not APP_BASE_URL)",
    cfg.appBaseUrl === HOLLIS_BASE && cfg.appBaseUrl !== TWOACT_BASE,
    `appBaseUrl=${cfg.appBaseUrl}`
  );
}

const ok = checks.every((c) => c.pass);
process.stdout.write(JSON.stringify({ ok, checks }) + "\n");
process.exit(ok ? 0 : 1);
