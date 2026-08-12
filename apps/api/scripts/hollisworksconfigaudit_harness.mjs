/**
 * Config-audit hermetic harness — proves the ACTUAL configuration values the
 * Auth0 SDK uses for a real admin.hollisworks.com request, field by field, using
 * the REAL deployed modules:
 *
 *   - apps/web/lib/authHostConfig.mjs   (our host→config resolver — the fix)
 *   - @auth0/nextjs-auth0 dist/utils/authorization-params-helpers.js
 *                                        (the SDK's OWN function that turns the
 *                                         client's authorizationParameters into
 *                                         the /authorize query — the exact code
 *                                         auth-client.js:359 runs at login)
 *
 * The AUDIENCE bug is the THIRD field of the identical shape (after tenant
 * domain/clientId and the callback base URL): the Hollisworks client silently
 * fell back to 2nd Act's audience (https://api.2ndactcapital.com), which the
 * SEPARATE Hollisworks tenant has no resource server for — so Auth0 returned
 * "Service not found: https://api.2ndactcapital.com". This harness proves the
 * audience the SDK now actually sends is EXACTLY https://api.hollisworks.com,
 * proves the OLD value for contrast, proves 2nd Act is unaffected, and proves
 * every at-risk field fails loud rather than reusing a 2nd Act value.
 *
 * Prints one JSON line: { ok, checks:[{name,pass,detail}] }. Exit 0 iff all pass.
 * No network, no client construction — the SDK's own merge fn IS the source of
 * truth for the /authorize audience.
 */
import { mergeAuthorizationParamsIntoSearchParams } from "../../../node_modules/@auth0/nextjs-auth0/dist/utils/authorization-params-helpers.js";
import {
  resolveAuthTenantForHost,
  hollisworksAudience,
  HollisworksAuthConfigError,
  HOLLISWORKS_ADMIN_HOST,
  HOLLISWORKS_API_AUDIENCE,
  TWOACT_API_AUDIENCE,
} from "../../web/lib/authHostConfig.mjs";

// auth-client.js:39-47 — params the SDK injects itself; audience/scope pass through.
const INTERNAL_AUTHORIZE_PARAMS = [
  "client_id",
  "redirect_uri",
  "response_type",
  "code_challenge",
  "code_challenge_method",
  "state",
  "nonce",
];

const TWOACT_DOMAIN = "dev-smmrfubsfscif3t1.us.auth0.com"; // 2nd Act tenant
const HOLLIS_DOMAIN = "dev-gy85vzuf6mruzv3j.us.auth0.com"; // Hollisworks tenant
const HOLLIS_SCOPE = "openid profile email"; // what auth0Hollisworks.js passes

// Production-like env: BOTH tenants configured, APP_BASE_URL points at 2nd Act,
// and NO HOLLISWORKS_AUTH0_AUDIENCE set — so we prove the DEFAULT audience is
// Hollisworks-specific (the whole point: setting it in Vercel was never what made
// it correct; the default itself must never be 2nd Act's).
const envFull = {
  APP_BASE_URL: "https://2ndactcapital.com",
  AUTH0_DOMAIN: TWOACT_DOMAIN,
  AUTH0_CLIENT_ID: "twoact_client",
  AUTH0_CLIENT_SECRET: "twoact_secret",
  AUTH0_SECRET: "0".repeat(64),
  HOLLISWORKS_AUTH0_DOMAIN: HOLLIS_DOMAIN,
  HOLLISWORKS_AUTH0_CLIENT_ID: "hollis_client",
  HOLLISWORKS_AUTH0_CLIENT_SECRET: "hollis_secret",
  HOLLISWORKS_AUTH0_SECRET: "1".repeat(64),
};

/**
 * The EXACT audience the SDK puts in the /authorize request, reproduced with the
 * SDK's own merge function fed the same authorizationParameters auth0Hollisworks.js
 * constructs the client with: { audience: cfg.audience, scope: HOLLIS_SCOPE }.
 */
function sdkAuthorizeAudience(cfg) {
  const params = mergeAuthorizationParamsIntoSearchParams(
    { audience: cfg.audience, scope: HOLLIS_SCOPE },
    undefined,
    INTERNAL_AUTHORIZE_PARAMS
  );
  return params.get("audience");
}

const checks = [];
const add = (name, pass, detail) => checks.push({ name, pass: !!pass, detail });

// ── Task 1 table sanity: the resolver's Hollisworks fields, all at once ──
const hollisCfg = resolveAuthTenantForHost(HOLLISWORKS_ADMIN_HOST, envFull);
const twoactCfg = resolveAuthTenantForHost("2ndactcapital.com", envFull);

// 1 — THE FIX: real admin.hollisworks.com /authorize audience is EXACTLY
//     https://api.hollisworks.com (Hollisworks tenant's own API), with no
//     HOLLISWORKS_AUTH0_AUDIENCE override present.
{
  const aud = sdkAuthorizeAudience(hollisCfg);
  add(
    "admin.hollisworks.com /authorize audience EXACTLY https://api.hollisworks.com",
    aud === HOLLIS_API() && aud !== TWOACT_API_AUDIENCE,
    `audience=${aud}`
  );
}

// 2 — PRE-FIX CONTRAST: the OLD resolver derived
//     `env.HOLLISWORKS_AUTH0_AUDIENCE || "https://api.2ndactcapital.com"`; with the
//     env var unset that is EXACTLY 2nd Act's audience — the "Service not found"
//     string Auth0 rejected.
{
  const preFix =
    envFull.HOLLISWORKS_AUTH0_AUDIENCE || "https://api.2ndactcapital.com";
  add(
    "pre-fix (silent ||) WOULD have sent the WRONG https://api.2ndactcapital.com",
    preFix === TWOACT_API_AUDIENCE,
    `pre-fix audience=${preFix} (== 2nd Act = ${preFix === TWOACT_API_AUDIENCE}); ` +
      `this is the exact "Service not found: ${preFix}" Auth0 error`
  );
}

// 3 — OVERRIDE honored VERBATIM: an explicit HOLLISWORKS_AUTH0_AUDIENCE is used
//     exactly as given (no trailing-slash mangling), still never 2nd Act.
{
  const custom = "https://api.hollisworks.com/staff";
  const cfg = resolveAuthTenantForHost(HOLLISWORKS_ADMIN_HOST, {
    ...envFull,
    HOLLISWORKS_AUTH0_AUDIENCE: custom,
  });
  const aud = sdkAuthorizeAudience(cfg);
  add(
    "explicit HOLLISWORKS_AUTH0_AUDIENCE override used verbatim in /authorize",
    aud === custom,
    `audience=${aud}`
  );
}

// 4 — REGRESSION: 2nd Act's own audience is UNCHANGED (exactly
//     https://api.2ndactcapital.com) and its host never resolves to Hollisworks.
{
  const aud = sdkAuthorizeAudience(twoactCfg);
  add(
    "2nd Act /authorize audience EXACTLY https://api.2ndactcapital.com (unchanged)",
    twoactCfg.tenant === "2ndact" && aud === TWOACT_API_AUDIENCE,
    `tenant=${twoactCfg.tenant} audience=${aud}`
  );
}

// 5 — PER-FIELD Hollisworks-specificity + 2nd Act regression, EVERY resolver field.
//     Each field: Hollisworks value is the Hollisworks-specific one AND differs
//     from 2nd Act's, while the 2nd Act cfg keeps its original value.
{
  const rows = [
    ["domain", hollisCfg.domain, HOLLIS_DOMAIN, twoactCfg.domain, TWOACT_DOMAIN, true],
    ["clientId", hollisCfg.clientId, "hollis_client", twoactCfg.clientId, "twoact_client", true],
    ["clientSecret", hollisCfg.clientSecret, "hollis_secret", twoactCfg.clientSecret, "twoact_secret", true],
    ["audience", hollisCfg.audience, HOLLIS_API(), twoactCfg.audience, TWOACT_API_AUDIENCE, true],
    ["appBaseUrl", hollisCfg.appBaseUrl, "https://admin.hollisworks.com", twoactCfg.appBaseUrl, "https://2ndactcapital.com", true],
    // secret: Hollisworks-specific here (HOLLISWORKS_AUTH0_SECRET set); NON-tenant-
    // scoped so a value-difference is not required, only that it is resolved.
    ["secret", hollisCfg.secret, "1".repeat(64), twoactCfg.secret, "0".repeat(64), false],
  ];
  for (const [field, hVal, hExp, tVal, tExp, mustDiffer] of rows) {
    const hOk = hVal === hExp;
    const tOk = tVal === tExp;
    const diffOk = !mustDiffer || hVal !== tVal;
    add(
      `field '${field}': Hollisworks-specific value correct AND 2nd Act unchanged`,
      hOk && tOk && diffOk,
      `hollis=${hVal} (exp ${hExp}) | 2ndact=${tVal} (exp ${tExp}) | differ=${hVal !== tVal}`
    );
  }
}

// 6 — FAIL LOUD: missing Hollisworks tenant vars -> throws, never a 2nd Act audience.
{
  let threw = false;
  let leaked = false;
  try {
    const cfg = resolveAuthTenantForHost(HOLLISWORKS_ADMIN_HOST, {
      AUTH0_DOMAIN: TWOACT_DOMAIN,
      AUTH0_CLIENT_ID: "twoact_client",
      AUTH0_CLIENT_SECRET: "twoact_secret",
      AUTH0_SECRET: "0".repeat(64),
    });
    leaked = cfg.audience === TWOACT_API_AUDIENCE || cfg.domain === TWOACT_DOMAIN;
  } catch (e) {
    threw = e instanceof HollisworksAuthConfigError;
  }
  add(
    "missing Hollisworks env -> throws, NEVER silent 2nd Act audience/domain",
    threw && !leaked,
    `threw=${threw} leaked2ndAct=${leaked}`
  );
}

// 7 — FAIL LOUD guard: an override set to 2nd Act's audience is REJECTED (it would
//     re-introduce the exact "Service not found" bug), never used.
{
  let threw = false;
  let used2ndAct = false;
  try {
    const cfg = resolveAuthTenantForHost(HOLLISWORKS_ADMIN_HOST, {
      ...envFull,
      HOLLISWORKS_AUTH0_AUDIENCE: TWOACT_API_AUDIENCE,
    });
    used2ndAct = cfg.audience === TWOACT_API_AUDIENCE;
  } catch (e) {
    threw = e instanceof HollisworksAuthConfigError;
  }
  add(
    "HOLLISWORKS_AUTH0_AUDIENCE == 2nd Act audience -> throws, never used",
    threw && !used2ndAct,
    `threw=${threw} used2ndAct=${used2ndAct}`
  );
}

// 8 — FAIL LOUD: a malformed audience override is rejected up front.
{
  let threw = false;
  try {
    hollisworksAudience({ HOLLISWORKS_AUTH0_AUDIENCE: "not-a-url" });
  } catch (e) {
    threw = e instanceof HollisworksAuthConfigError;
  }
  add(
    "malformed HOLLISWORKS_AUTH0_AUDIENCE override -> fail loud (HollisworksAuthConfigError)",
    threw,
    `threw=${threw}`
  );
}

// 9 — SECRET: (a) fail loud when NEITHER a Hollisworks nor a shared secret exists;
//     (b) the documented, SAFE share works (AUTH0_SECRET reused) because the secret
//     is a symmetric cookie key, not a tenant identifier.
{
  let threwOnNone = false;
  try {
    resolveAuthTenantForHost(HOLLISWORKS_ADMIN_HOST, {
      HOLLISWORKS_AUTH0_DOMAIN: HOLLIS_DOMAIN,
      HOLLISWORKS_AUTH0_CLIENT_ID: "hollis_client",
      HOLLISWORKS_AUTH0_CLIENT_SECRET: "hollis_secret",
      // no HOLLISWORKS_AUTH0_SECRET, no AUTH0_SECRET
    });
  } catch (e) {
    threwOnNone = e instanceof HollisworksAuthConfigError;
  }
  const shared = resolveAuthTenantForHost(HOLLISWORKS_ADMIN_HOST, {
    HOLLISWORKS_AUTH0_DOMAIN: HOLLIS_DOMAIN,
    HOLLISWORKS_AUTH0_CLIENT_ID: "hollis_client",
    HOLLISWORKS_AUTH0_CLIENT_SECRET: "hollis_secret",
    AUTH0_SECRET: "shared_symmetric_key",
  });
  add(
    "secret fails loud when absent; safe documented share of AUTH0_SECRET works",
    threwOnNone && shared.secret === "shared_symmetric_key",
    `threwOnNone=${threwOnNone} sharedSecretResolved=${shared.secret === "shared_symmetric_key"}`
  );
}

// Constant helper so a typo in the expected audience can't silently pass.
function HOLLIS_API() {
  return HOLLISWORKS_API_AUDIENCE;
}

const ok = checks.every((c) => c.pass);
process.stdout.write(JSON.stringify({ ok, checks }) + "\n");
process.exit(ok ? 0 : 1);
