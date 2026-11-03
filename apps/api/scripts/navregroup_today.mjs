/**
 * VHK2 — navregroup's rules evaluated over TODAY's tree (not a frozen list).
 *
 *   node apps/api/scripts/navregroup_today.mjs [extra-personas.json]
 *
 * Prints one JSON object:
 *   pages     every page.js under app/admin/** and app/portfolio/**, classified
 *             nav / landing / redirect / sub (has a nav ancestor below the group
 *             root) / other, with its traced server gate (or null).
 *   personas  for the six navregroup fixtures PLUS any extra /users/me-shaped
 *             personas in the optional JSON file ({name: me}) — e.g. every real
 *             role's live permission set, which verify_navregroup reads from
 *             the database — the visible hrefs, the ones whose server gate is
 *             untraced, and the ones the traced gate refuses.
 *
 * The rules verify_navregroup asserts on it:
 *   - every page is nav / landing / redirect / sub, or carries a stated
 *     non-nav reason (NON_NAV_REASONS in verify_navregroup.py);
 *   - every nav and sub page has a traced gate;
 *   - every visible href is admitted by its traced gate, except the mismatches
 *     navregroup recorded as pre-existing [FIND]s (refused at the baseline too).
 */
import { readdirSync, readFileSync, statSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { GROUPS, MENU_ITEMS, visibleAdminGroups, visibleMenuItems } from "../../web/lib/menuVisibility.mjs";
import { PERSONAS } from "./navregroup_personas.mjs";
import { isTraced, serverAdmits, tracedGate } from "./navregroup_traced_gates.mjs";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const APP = path.resolve(HERE, "..", "..", "web", "app");

function walk(dir, out = []) {
  for (const n of readdirSync(dir)) {
    const f = path.join(dir, n);
    if (statSync(f).isDirectory()) walk(f, out);
    else if (/^page\.(js|jsx|tsx)$/.test(n)) out.push(f);
  }
  return out;
}

const navHrefs = new Set(MENU_ITEMS.map((i) => i.href));
const landings = new Set(GROUPS.map((g) => g.landing));
const ROOTS = new Set(["/admin", "/portfolio"]);

function navAncestor(href) {
  const parts = href.split("/").filter(Boolean);
  for (let n = parts.length - 1; n >= 2; n -= 1) {
    const anc = `/${parts.slice(0, n).join("/")}`;
    if (navHrefs.has(anc) && !ROOTS.has(anc)) return anc;
  }
  return null;
}

const pages = [];
for (const base of ["admin", "portfolio"]) {
  for (const f of walk(path.join(APP, base)).sort()) {
    const href = `/${path.relative(APP, path.dirname(f)).split(path.sep).join("/")}`;
    const src = readFileSync(f, "utf8");
    let kind;
    if (navHrefs.has(href)) kind = "nav";
    else if (src.includes("GroupLanding") && landings.has(href)) kind = "landing";
    else if (/\bredirect\(/.test(src) && !/fetchAPI|["'`]\/api\//.test(src)) kind = "redirect";
    else if (navAncestor(href)) kind = "sub";
    else kind = "other";
    const t = tracedGate(href);
    pages.push({ href, kind, nav_ancestor: navAncestor(href), traced: t ? t.gate : null });
  }
}

const extra = process.argv[2] ? JSON.parse(readFileSync(process.argv[2], "utf8")) : {};
const all = { ...PERSONAS, ...extra };
const personas = Object.entries(all).map(([name, me]) => {
  const visible = [...new Set([
    ...visibleMenuItems(me).map((i) => i.href),
    ...visibleAdminGroups(me).flatMap((g) => g.tabs.map((t) => t.href)),
  ])].sort();
  return {
    name,
    fixture: name in PERSONAS,
    visible,
    untraced: visible.filter((h) => !isTraced(h)),
    refused: visible.filter((h) => !serverAdmits(me, h)),
  };
});

process.stdout.write(JSON.stringify({ pages, personas }, null, 2));
