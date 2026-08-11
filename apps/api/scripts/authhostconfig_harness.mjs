/**
 * Bug 2 hermetic harness — exercises the REAL deployed host→tenant resolution
 * (apps/web/lib/authHostConfig.mjs), the exact module the login route + auth
 * middleware use to pick which Auth0 tenant a Host authenticates against.
 *
 * This is what the isolated sprint test failed to do: it SET the HOLLISWORKS_*
 * env vars and only grepped source strings, so it never observed the Auth0 SDK's
 * silent `domain ?? AUTH0_DOMAIN` fallback that made admin.hollisworks.com log in
 * against 2nd Act's tenant when those vars were absent in production.
 *
 * Prints a single JSON line to stdout: { ok: bool, checks: [{name, pass, detail}] }.
 * Exit 0 iff every check passes. No network, no node_modules — pure logic.
 */
import {
  resolveAuthTenantForHost,
  isHollisworksAdminHost,
  HollisworksAuthConfigError,
  TWOACT_ISSUER_HINT,
  HOLLISWORKS_ISSUER_HINT,
} from "../../web/lib/authHostConfig.mjs";

const TWOACT_DOMAIN = "dev-smmrfubsfscif3t1.us.auth0.com"; // 2nd Act tenant
const HOLLIS_DOMAIN = "dev-gy85vzuf6mruzv3j.us.auth0.com"; // Hollisworks tenant

// Production-like env: BOTH tenants configured (this is the required prod state).
const envFull = {
  AUTH0_DOMAIN: TWOACT_DOMAIN,
  AUTH0_CLIENT_ID: "twoact_client",
  AUTH0_CLIENT_SECRET: "twoact_secret",
  AUTH0_SECRET: "0".repeat(64),
  HOLLISWORKS_AUTH0_DOMAIN: HOLLIS_DOMAIN,
  HOLLISWORKS_AUTH0_CLIENT_ID: "hollis_client",
  HOLLISWORKS_AUTH0_CLIENT_SECRET: "hollis_secret",
};

// Broken prod state that caused Bug 2: Hollisworks vars ABSENT, 2nd Act present.
const envMissingHollis = {
  AUTH0_DOMAIN: TWOACT_DOMAIN,
  AUTH0_CLIENT_ID: "twoact_client",
  AUTH0_CLIENT_SECRET: "twoact_secret",
  AUTH0_SECRET: "0".repeat(64),
};

const checks = [];
const add = (name, pass, detail) => checks.push({ name, pass: !!pass, detail });

// 1 — admin.hollisworks.com initiates login against the HOLLISWORKS tenant.
{
  const c = resolveAuthTenantForHost("admin.hollisworks.com", envFull);
  add(
    "admin.hollisworks.com -> Hollisworks tenant (dev-gy85vzuf6mruzv3j...)",
    c.tenant === "hollisworks" &&
      c.domain === HOLLIS_DOMAIN &&
      c.domain.includes(HOLLISWORKS_ISSUER_HINT) &&
      !c.domain.includes(TWOACT_ISSUER_HINT),
    `tenant=${c.tenant} domain=${c.domain}`
  );
}

// 2 — 2nd Act tenant subdomain still logs in against 2nd Act.
{
  const c = resolveAuthTenantForHost("2ndactcapital.hollisworks.com", envFull);
  add(
    "2ndactcapital.hollisworks.com -> 2nd Act tenant (dev-smmrfubsfscif3t1...)",
    c.tenant === "2ndact" &&
      c.domain === TWOACT_DOMAIN &&
      c.domain.includes(TWOACT_ISSUER_HINT),
    `tenant=${c.tenant} domain=${c.domain}`
  );
}

// 3 — 2nd Act's OWN root domain login also stays on 2nd Act's tenant.
{
  const c = resolveAuthTenantForHost("2ndactcapital.com", envFull);
  add(
    "2ndactcapital.com (2nd Act root) -> 2nd Act tenant",
    c.tenant === "2ndact" && c.domain === TWOACT_DOMAIN,
    `tenant=${c.tenant} domain=${c.domain}`
  );
}

// 4 — THE FIX: when the Hollisworks tenant is unconfigured, the admin host FAILS
//     LOUD instead of silently falling back to 2nd Act's tenant. We also show the
//     pre-fix silent-fallback value for contrast (what the SDK would have used).
{
  const preFixSilentDomain =
    envMissingHollis.HOLLISWORKS_AUTH0_DOMAIN ?? envMissingHollis.AUTH0_DOMAIN;
  let threw = false;
  let leaked2ndAct = false;
  try {
    const c = resolveAuthTenantForHost("admin.hollisworks.com", envMissingHollis);
    // If it did NOT throw, ensure it at least did not silently point at 2nd Act.
    leaked2ndAct = c.domain === TWOACT_DOMAIN;
  } catch (e) {
    threw = e instanceof HollisworksAuthConfigError;
  }
  add(
    "admin host w/ missing Hollisworks env -> throws, NEVER silent 2nd Act fallback",
    threw && !leaked2ndAct,
    `threw=${threw} pre-fix_would_have_used=${preFixSilentDomain} (==2ndAct=${
      preFixSilentDomain === TWOACT_DOMAIN
    })`
  );
}

// 5 — REGRESSION GUARD: a Hollisworks misconfig must NOT affect 2nd Act's own
//     login path — it still resolves to 2nd Act's tenant, no throw.
{
  let ok = false;
  let detail = "";
  try {
    const c = resolveAuthTenantForHost("2ndactcapital.com", envMissingHollis);
    ok = c.tenant === "2ndact" && c.domain === TWOACT_DOMAIN;
    detail = `tenant=${c.tenant} domain=${c.domain}`;
  } catch (e) {
    detail = `unexpected throw: ${e.message}`;
  }
  add(
    "2nd Act login path unaffected by Hollisworks misconfig (no regression)",
    ok,
    detail
  );
}

// 6 — host predicate sanity: only the exact admin host diverges.
add(
  "isHollisworksAdminHost: exact admin host only",
  isHollisworksAdminHost("admin.hollisworks.com") &&
    isHollisworksAdminHost("ADMIN.Hollisworks.com:443") &&
    !isHollisworksAdminHost("2ndactcapital.hollisworks.com") &&
    !isHollisworksAdminHost("hollisworks.com") &&
    !isHollisworksAdminHost("2ndactcapital.com"),
  "admin-only divergence"
);

const ok = checks.every((c) => c.pass);
process.stdout.write(JSON.stringify({ ok, checks }) + "\n");
process.exit(ok ? 0 : 1);
