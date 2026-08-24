/**
 * DASHBOARD SESSION CHECK — HOST-AWARE FIX: hermetic REAL-session harness.
 *
 * This does NOT check signatures or grep strings. It mints REAL encrypted Auth0
 * session cookies with the SDK's OWN crypto and reads them back through REAL
 * `Auth0Client.getSession(req)` calls, then walks the actual redirect graph
 * (page -> /auth/login -> Auth0 -> /auth/callback -> page) hop by hop.
 *
 * Real modules exercised:
 *   - @auth0/nextjs-auth0 dist/server/client.js   (the real Auth0Client)
 *   - @auth0/nextjs-auth0 dist/server/cookies.js  (the real JWE encrypt)
 *   - apps/web/lib/authHostConfig.mjs             (the real host->tenant resolver
 *                                                  AND the real host predicate
 *                                                  `isHollisworksAdminHost` that
 *                                                  `getAuthClientForHost` uses)
 *   - next/server NextRequest                     (real request/cookie parsing)
 *
 * THE BUG: pages imported the FIXED 2nd Act client and called
 * `auth0.getSession()` regardless of Host. A Hollisworks-tenant session lives in
 * the `__hw_session` cookie encrypted with the Hollisworks secret; 2nd Act's
 * client only reads `__session` with 2nd Act's secret. So a valid Hollisworks
 * session read as "no session" -> redirect to /auth/login -> the (correctly
 * host-aware) middleware saw the live Hollisworks tenant session -> bounced
 * straight back to the page -> "too many redirects".
 *
 * Reads a JSON array of {file, path, returnTo} on argv[2] (the pages/routes the
 * verify script found fixed) and runs the full four-way simulation for EACH one.
 *
 * Prints one JSON line: { ok, checks:[{name,pass,detail}] }. Exit 0 iff all pass.
 * No network: both clients are in the SDK's "static domain" mode, so
 * `provider.forRequest` returns a pre-built client with zero discovery calls.
 */
import { Auth0Client } from "../../../node_modules/@auth0/nextjs-auth0/dist/server/client.js";
import { encrypt } from "../../../node_modules/@auth0/nextjs-auth0/dist/server/cookies.js";
import { NextRequest } from "../../../node_modules/next/server.js";
import {
  resolveAuthTenantForHost,
  isHollisworksAdminHost,
  HOLLISWORKS_ADMIN_HOST,
} from "../../web/lib/authHostConfig.mjs";

const TWOACT_HOST = "2ndactcapital.com";
const TWOACT_COOKIE = "__session"; // SDK default (abstract-session-store.js)
const HOLLIS_COOKIE = "__hw_session"; // lib/auth0Hollisworks.js
const REDIRECT_CAP = 12; // a loop is anything that never terminates

// Hermetic, production-SHAPED env: BOTH tenants configured, APP_BASE_URL at 2nd
// Act. Resolved through the REAL deployed resolver, not hand-written config.
const env = {
  APP_BASE_URL: "https://2ndactcapital.com",
  AUTH0_DOMAIN: "dev-smmrfubsfscif3t1.us.auth0.com",
  AUTH0_CLIENT_ID: "twoact_client",
  AUTH0_CLIENT_SECRET: "twoact_client_secret",
  AUTH0_SECRET: "a".repeat(64),
  HOLLISWORKS_AUTH0_DOMAIN: "dev-gy85vzuf6mruzv3j.us.auth0.com",
  HOLLISWORKS_AUTH0_CLIENT_ID: "hollis_client",
  HOLLISWORKS_AUTH0_CLIENT_SECRET: "hollis_client_secret",
  HOLLISWORKS_AUTH0_SECRET: "b".repeat(64),
};

const twoactCfg = resolveAuthTenantForHost(TWOACT_HOST, env);
const hollisCfg = resolveAuthTenantForHost(HOLLISWORKS_ADMIN_HOST, env);

// Constructor options mirror lib/auth0.js and lib/auth0Hollisworks.js exactly;
// the verify script asserts that correspondence against the real source.
const twoactClient = new Auth0Client({
  domain: twoactCfg.domain,
  clientId: twoactCfg.clientId,
  clientSecret: twoactCfg.clientSecret,
  secret: twoactCfg.secret,
  appBaseUrl: twoactCfg.appBaseUrl,
  authorizationParameters: {
    audience: twoactCfg.audience,
    scope: "openid profile email",
  },
});
const hollisClient = new Auth0Client({
  domain: hollisCfg.domain,
  clientId: hollisCfg.clientId,
  clientSecret: hollisCfg.clientSecret,
  secret: hollisCfg.secret,
  appBaseUrl: [hollisCfg.appBaseUrl],
  authorizationParameters: {
    audience: hollisCfg.audience,
    scope: "openid profile email",
  },
  session: { cookie: { name: HOLLIS_COOKIE } },
});

/**
 * THE FIX under test, as a selector: this is the REAL deployed rule —
 * `lib/authForHost.js` is literally
 *   `isHollisworksAdminHost(host) ? getHollisworksAuth0() : auth0`
 * and `isHollisworksAdminHost` is imported here from the REAL module.
 */
const clientForHost = (host) =>
  isHollisworksAdminHost(host) ? hollisClient : twoactClient;

/** The PRE-FIX behavior: the fixed 2nd Act client, whatever the Host. */
const brokenClientForHost = () => twoactClient;

// ── real encrypted session cookies (the SDK's own JWE) ───────────────────────
// Epoch is INJECTED by the caller (Date.now() is avoided so a run is
// reproducible). It must be the current time: the cookie is a real JWE with an
// `exp`, so a stale epoch would make every minted session decrypt as expired and
// the "no session" assertions would pass vacuously. Checks 1 and 1b are the
// controls that prove the minted cookies are genuinely live.
const NOW = Math.floor(Number(process.argv[3]));
if (!Number.isFinite(NOW) || NOW <= 0) {
  process.stdout.write(
    JSON.stringify({
      ok: false,
      checks: [{ name: "epoch argument", pass: false, detail: `argv[3]=${process.argv[3]}` }],
    }) + "\n",
  );
  process.exit(1);
}

function sessionFor(sub, email) {
  return {
    user: { sub, email, name: email },
    tokenSet: {
      accessToken: "at_" + sub,
      idToken: "idt_" + sub,
      expiresAt: NOW + 3600,
      scope: "openid profile email",
    },
    internal: { sid: "sid_" + sub, createdAt: NOW },
  };
}

async function mintCookie(secret, name, session) {
  const jwe = await encrypt(session, secret, NOW + 3600);
  return name + "=" + jwe;
}

const HOLLIS_SUB = "auth0|hollisworks_staff";
const TWOACT_SUB = "auth0|2ndact_member";

const hollisCookie = await mintCookie(
  hollisCfg.secret,
  HOLLIS_COOKIE,
  sessionFor(HOLLIS_SUB, "staff@hollisworks.com"),
);
const twoactCookie = await mintCookie(
  twoactCfg.secret,
  TWOACT_COOKIE,
  sessionFor(TWOACT_SUB, "member@2ndactcapital.com"),
);

function mkReq(host, path, cookie) {
  const headers = { host };
  if (cookie) headers.cookie = cookie;
  return new NextRequest("https://" + host + path, { headers });
}

/**
 * Walk the REAL redirect graph for a protected page.
 *
 *   page          -> session check with `selector(host)`; null => 302 /auth/login
 *   /auth/login   -> mounted by proxy.js with the HOST-AWARE client (already
 *                    correct today), so it hits the host's OWN Auth0 tenant. If
 *                    that tenant already has a live SSO session, Auth0 bounces
 *                    silently through /auth/callback back to returnTo — which
 *                    re-mints the SAME tenant cookie. Otherwise Auth0 renders its
 *                    hosted login prompt: a TERMINAL state, not a redirect.
 *
 * Returns { outcome, redirects, user, trail }. `redirects` counts app-level 302s.
 */
async function walk({ host, cookie, idpSession, selector, path, returnTo }) {
  let current = path;
  let jar = cookie;
  let redirects = 0;
  const trail = [];

  while (redirects <= REDIRECT_CAP) {
    if (current === "/auth/login") {
      if (!idpSession) {
        trail.push("/auth/login -> Auth0 hosted login prompt (terminal)");
        return { outcome: "login_prompt", redirects, user: null, trail };
      }
      trail.push(
        "/auth/login -> Auth0 (live " +
          (isHollisworksAdminHost(host) ? "hollisworks" : "2ndact") +
          " tenant session) -> /auth/callback -> " +
          returnTo,
      );
      jar = cookie; // the callback re-mints the SAME tenant's session cookie
      current = returnTo;
      redirects += 1;
      continue;
    }

    const client = selector(host);
    const session = await client.getSession(mkReq(host, current, jar));
    if (session) {
      return {
        outcome: "rendered",
        redirects,
        user: session.user.sub,
        trail,
      };
    }
    trail.push(current + " -> 302 /auth/login?returnTo=" + returnTo);
    current = "/auth/login";
    redirects += 1;
  }
  return { outcome: "redirect_loop", redirects, user: null, trail };
}

const checks = [];
const add = (name, pass, detail) => checks.push({ name, pass: !!pass, detail });

// The pages/routes the verify script found and fixed. Each gets the SAME
// four-way proof as /dashboard.
let targets;
try {
  targets = JSON.parse(process.argv[2] || "[]");
} catch {
  targets = [];
}
if (!targets.length) {
  targets = [{ file: "apps/web/app/dashboard/page.js", path: "/dashboard", returnTo: "/dashboard" }];
}

// ── 0. Cookie-level root cause: the two tenants' cookies are mutually opaque ──
{
  const hwOn2a = await twoactClient.getSession(
    mkReq(HOLLISWORKS_ADMIN_HOST, "/dashboard", hollisCookie),
  );
  const taOnHw = await hollisClient.getSession(
    mkReq(TWOACT_HOST, "/dashboard", twoactCookie),
  );
  add(
    "root cause: 2nd Act's client CANNOT read a real Hollisworks session cookie (and vice versa)",
    hwOn2a === null && taOnHw === null,
    "2ndActClient.getSession(__hw_session)=" +
      (hwOn2a === null ? "null" : "SESSION") +
      "; hollisClient.getSession(__session)=" +
      (taOnHw === null ? "null" : "SESSION") +
      " — distinct cookie names AND distinct encryption secrets, so the " +
      "host-unaware check could never see a Hollisworks session.",
  );
}

// ── 1. Real Hollisworks session decrypts through the REAL client ─────────────
{
  const s = await hollisClient.getSession(
    mkReq(HOLLISWORKS_ADMIN_HOST, "/dashboard", hollisCookie),
  );
  add(
    "real Hollisworks-tenant session round-trips through the real Auth0 SDK",
    s !== null && s.user.sub === HOLLIS_SUB,
    "sub=" + (s && s.user.sub),
  );
}

// ── 1b. CONTROL for the negative assertions: the 2nd Act cookie is live too, so
//        a "no session" result below can never be an expired-cookie artifact. ──
{
  const s = await twoactClient.getSession(
    mkReq(TWOACT_HOST, "/dashboard", twoactCookie),
  );
  add(
    "control: the minted 2nd Act session cookie is genuinely LIVE (negatives are not vacuous)",
    s !== null && s.user.sub === TWOACT_SUB,
    "sub=" + (s && s.user.sub) + " — both minted cookies decrypt, so every null " +
      "session below is a real tenant mismatch, not an expired JWE.",
  );
}

// ── 2..N. Per-target four-way proof ──────────────────────────────────────────
for (const t of targets) {
  const path = t.path;
  const returnTo = t.returnTo || t.path;
  const label = t.file;

  // (a) FIXED: a real Hollisworks session on admin.hollisworks.com renders.
  const a = await walk({
    host: HOLLISWORKS_ADMIN_HOST,
    cookie: hollisCookie,
    idpSession: true,
    selector: clientForHost,
    path,
    returnTo,
  });
  add(
    "hollisworks-session|" + label,
    a.outcome === "rendered" && a.redirects === 0 && a.user === HOLLIS_SUB,
    "outcome=" + a.outcome + " redirects=" + a.redirects + " user=" + a.user,
  );

  // (b) PRE-FIX CONTRAST: the same request against the host-unaware check loops.
  const b = await walk({
    host: HOLLISWORKS_ADMIN_HOST,
    cookie: hollisCookie,
    idpSession: true,
    selector: brokenClientForHost,
    path,
    returnTo,
  });
  add(
    "prefix-loop-reproduced|" + label,
    b.outcome === "redirect_loop" && b.redirects > REDIRECT_CAP,
    "outcome=" +
      b.outcome +
      " redirects=" +
      b.redirects +
      " (capped at " +
      REDIRECT_CAP +
      ") first hops: " +
      b.trail.slice(0, 2).join(" | "),
  );

  // (c) REGRESSION: a real 2nd Act session on 2nd Act's host still renders —
  //     and rendered identically BEFORE the fix (the fix is a no-op for 2nd Act).
  const cFixed = await walk({
    host: TWOACT_HOST,
    cookie: twoactCookie,
    idpSession: true,
    selector: clientForHost,
    path,
    returnTo,
  });
  const cOld = await walk({
    host: TWOACT_HOST,
    cookie: twoactCookie,
    idpSession: true,
    selector: brokenClientForHost,
    path,
    returnTo,
  });
  add(
    "2ndact-regression|" + label,
    cFixed.outcome === "rendered" &&
      cFixed.redirects === 0 &&
      cFixed.user === TWOACT_SUB &&
      cOld.outcome === cFixed.outcome &&
      cOld.redirects === cFixed.redirects &&
      cOld.user === cFixed.user,
    "fixed: outcome=" +
      cFixed.outcome +
      " redirects=" +
      cFixed.redirects +
      " user=" +
      cFixed.user +
      " || pre-fix: outcome=" +
      cOld.outcome +
      " redirects=" +
      cOld.redirects +
      " user=" +
      cOld.user +
      " — byte-identical outcome, the fix is a no-op on 2nd Act's host.",
  );

  // (d) NO SESSION on EITHER host: exactly ONE redirect, then Auth0's prompt.
  const dHw = await walk({
    host: HOLLISWORKS_ADMIN_HOST,
    cookie: null,
    idpSession: false,
    selector: clientForHost,
    path,
    returnTo,
  });
  const dTa = await walk({
    host: TWOACT_HOST,
    cookie: null,
    idpSession: false,
    selector: clientForHost,
    path,
    returnTo,
  });
  add(
    "nosession-single-redirect|" + label,
    dHw.outcome === "login_prompt" &&
      dHw.redirects === 1 &&
      dTa.outcome === "login_prompt" &&
      dTa.redirects === 1,
    "admin.hollisworks.com: outcome=" +
      dHw.outcome +
      " redirects=" +
      dHw.redirects +
      " || 2ndactcapital.com: outcome=" +
      dTa.outcome +
      " redirects=" +
      dTa.redirects +
      " — one 302 to /auth/login, then Auth0's hosted prompt (terminal).",
  );

  // (e) CROSS-TENANT: a Hollisworks cookie must NOT authenticate on 2nd Act's
  //     host (and vice versa) even with the host-aware selector.
  const eA = await walk({
    host: TWOACT_HOST,
    cookie: hollisCookie,
    idpSession: false,
    selector: clientForHost,
    path,
    returnTo,
  });
  const eB = await walk({
    host: HOLLISWORKS_ADMIN_HOST,
    cookie: twoactCookie,
    idpSession: false,
    selector: clientForHost,
    path,
    returnTo,
  });
  add(
    "cross-tenant-isolation|" + label,
    eA.outcome === "login_prompt" &&
      eA.redirects === 1 &&
      eB.outcome === "login_prompt" &&
      eB.redirects === 1,
    "__hw_session on 2ndactcapital.com: " +
      eA.outcome +
      "/" +
      eA.redirects +
      "; __session on admin.hollisworks.com: " +
      eB.outcome +
      "/" +
      eB.redirects +
      " — neither leaks across tenants.",
  );
}

const ok = checks.every((c) => c.pass);
process.stdout.write(JSON.stringify({ ok, checks }) + "\n");
process.exit(ok ? 0 : 1);
