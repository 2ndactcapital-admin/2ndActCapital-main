/**
 * VHK2 — the server gate behind a page, TRACED from the code, for pages the
 * navregroup hand table (navregroup_server_gates.mjs) does not list.
 *
 * Why: navregroup's SERVER_GATES was written out by hand on 2026-10-20 for the
 * pages that existed then. Every page a later sprint added (REP1 → REP10,
 * MARKETDATA1) was "unknown" to it, and `serverAllows` answers false for an
 * unknown href — so the 2026-11-02 regression run reported Super Admin
 * "refused" by twelve pages it is in fact served by. That was the table's
 * staleness, not a refusal.
 *
 * Rule (survives later sprints): a page's gate is
 *   1. its SERVER_GATES row when navregroup traced it by hand, else
 *   2. the gates of the FastAPI routes its page.js calls (`/api/v1/...`, or the
 *      browser proxy `/api/<module>/...` which forwards to `/api/v1/<module>/...`),
 *      read from docs/AUTHZ1_ROUTES.json — the route record verify_authz1 checks
 *      against the live gates — and the persona is served only if EVERY one of
 *      those GET routes admits it.
 * A page whose calls cannot be found, or whose route gate cannot be evaluated
 * from a /users/me payload (entity scope, deal-room scope), is UNTRACED: it
 * must then get a hand row in SERVER_GATES. Untraced never means "allowed".
 */
import { existsSync, readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { SERVER_GATES, serverAllows } from "./navregroup_server_gates.mjs";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const REPO = path.resolve(HERE, "..", "..", "..");
const APP = path.join(REPO, "apps", "web", "app");
const ROUTES = JSON.parse(readFileSync(path.join(REPO, "docs", "AUTHZ1_ROUTES.json"), "utf8")).routes;
const GET_ROUTES = ROUTES.filter((r) => (r.methods || []).includes("GET"));

const isSuper = (me) => me?.account_role === "super_admin";
const perms = (me) => (Array.isArray(me?.permissions) ? me.permissions : []);
const isStaff = (me) => isSuper(me) || me?.is_staff === true;

/** The page file for an href, or null. */
export function pageFile(href) {
  for (const name of ["page.js", "page.jsx", "page.tsx"]) {
    const f = path.join(APP, ...href.split("/").filter(Boolean), name);
    if (existsSync(f)) return f;
  }
  return null;
}

/** Every API path a page.js names, normalised to /api/v1/..., `${…}` → ":param". */
export function pageApiPaths(file) {
  const src = readFileSync(file, "utf8");
  const out = new Set();
  for (const m of src.matchAll(/["'`](\/api\/[^"'`\s?#]*)/g)) {
    let p = m[1].replace(/\$\{[^}]*\}/g, ":param").replace(/\/+$/, "");
    if (!p.startsWith("/api/v1/")) p = p.replace(/^\/api\//, "/api/v1/");
    out.add(p);
  }
  return [...out].sort();
}

const segs = (p) => p.split("/").filter(Boolean);
const isParam = (s) => s === ":param" || /^\{.*\}$/.test(s);

/** The GET route record a called path resolves to: an exact static match first, then a templated one. */
export function routeFor(apiPath) {
  const a = segs(apiPath);
  let best = null;
  let bestScore = -1;
  for (const r of GET_ROUTES) {
    const b = segs(r.path);
    if (a.length !== b.length) continue;
    let ok = true;
    let score = 0;
    for (let i = 0; i < a.length; i += 1) {
      if (a[i] === b[i]) score += 2;
      else if (isParam(a[i]) && isParam(b[i])) score += 1;
      else if (isParam(b[i]) && !isParam(a[i])) score += 0;
      else { ok = false; break; }
    }
    if (ok && score > bestScore) { best = r; bestScore = score; }
  }
  return best;
}

/** Evaluate one route record for a /users/me-shaped persona: true / false, or null when it cannot be. */
export function routeAdmits(me, rec) {
  const e = rec?.expect || {};
  switch (e.rule) {
    case "public":
    case "active":
    case "self":
      return true;
    case "super":
      return isSuper(me);
    case "perm":
      return isSuper(me) || (e.any_of || []).some((p) => perms(me).includes(p));
    case "org_admin":
      return isSuper(me) || perms(me).includes("manage_org_settings");
    case "staff":
      return /entity access/.test(rec.after || "") ? null : isStaff(me);
    case "mixed": {
      // e.g. "staff or authz manage_org_settings"; "authz a+b" = both. Anything else is a scope → untraced.
      const terms = String(rec.after || "").split(/\s+or\s+/);
      const vals = terms.map((t) => {
        t = t.trim();
        if (t === "staff") return isStaff(me);
        if (t === "super_admin" || t === "super admin") return isSuper(me);
        const m = t.match(/^authz ([\w+]+)$/);
        if (m) return isSuper(me) || m[1].split("+").every((p) => perms(me).includes(p));
        return null;
      });
      return vals.includes(null) ? null : vals.some(Boolean);
    }
    default:
      return null;    // scoped / workflow / unknown: needs a hand row
  }
}

/** { source, apis: [{path, route, gate}], untraced: [...] } for an href, or null when the page cannot be traced. */
export function tracedGate(href) {
  if (href in SERVER_GATES) return { source: "navregroup_server_gates", gate: SERVER_GATES[href], apis: [] };
  const file = pageFile(href);
  if (!file) return null;
  const apis = pageApiPaths(file).map((p) => {
    const r = routeFor(p);
    return { path: p, route: r ? r.path : null, gate: r ? r.after : null, rec: r };
  });
  if (apis.length === 0 || apis.some((a) => !a.rec)) return null;
  // A route whose gate cannot be evaluated from /users/me makes the page untraced.
  if (apis.some((a) => routeAdmits({ account_role: "member", permissions: [] }, a.rec) === null)) return null;
  return { source: "AUTHZ1_ROUTES.json", gate: apis.map((a) => `${a.route}: ${a.gate}`).join("; "), apis };
}

/** True when the page's traced server gate serves `me`; false when refused OR untraced (fail closed). */
export function serverAdmits(me, href) {
  if (href in SERVER_GATES) return serverAllows(me, href);
  const t = tracedGate(href);
  if (!t) return false;
  return t.apis.every((a) => routeAdmits(me, a.rec) === true);
}

/** True when the href has a traced gate (hand row or derived). */
export function isTraced(href) {
  return tracedGate(href) !== null;
}
