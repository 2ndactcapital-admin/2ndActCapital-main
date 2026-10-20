/**
 * navregroup Task 1b — the BASELINE: exactly which navigation hrefs each persona
 * could see BEFORE the regroup, computed from the pre-sprint CODE, not
 * transcribed by hand.
 *
 * Both menus are read from a pinned git ref (default e581844, the commit this
 * sprint started from) so re-running it after the sprint commits still measures
 * the old world:
 *   - lib/menuVisibility.mjs at that ref is written to a temp file and imported,
 *     so canPerm / canAccess / visibleMenuItems / visibleAdminSections are the
 *     real old functions;
 *   - components/Sidebar.jsx at that ref is parsed: NAV_ITEMS (always shown),
 *     every *_ITEM constant, and each gated JSX block ({can("perm") && …} /
 *     {canAccess(me, GATE_X) && …}) with the item constants inside it. Each
 *     block's gate is evaluated with the OLD module (can() is canPerm, per
 *     lib/usePermissions.js).
 *
 * `reachable` = sidebar ∪ /admin index cards — every link a persona could click.
 *
 *   node apps/api/scripts/navregroup_baseline.mjs [ref] [--write]
 * --write stores docs/NAVREGROUP_BASELINE.json; without it the JSON goes to stdout.
 */
import { execFileSync } from "node:child_process";
import { mkdtempSync, writeFileSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

import { PERSONAS } from "./navregroup_personas.mjs";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const REPO = path.resolve(HERE, "..", "..", "..");
export const BASELINE_REF = "e581844";

const args = process.argv.slice(2);
const ref = args.find((a) => !a.startsWith("--")) || BASELINE_REF;
const git = (p) => execFileSync("git", ["show", `${ref}:${p}`], { cwd: REPO, encoding: "utf8" });

const tmp = mkdtempSync(path.join(os.tmpdir(), "navregroup-baseline-"));
const modPath = path.join(tmp, "menuVisibility.mjs");
writeFileSync(modPath, git("apps/web/lib/menuVisibility.mjs"));
const old = await import(pathToFileURL(modPath).href);

const src = git("apps/web/components/Sidebar.jsx");

const navBlock = src.match(/const NAV_ITEMS = \[([\s\S]*?)\n\];/)[1];
const navHrefs = [...navBlock.matchAll(/href: "([^"]+)"/g)].map((m) => m[1]);
const consts = Object.fromEntries(
  [...src.matchAll(/const (\w+_ITEM) = \{[^}]*?href: "([^"]+)"/g)].map((m) => [m[1], m[2]]),
);
const navJsx = src.slice(src.indexOf("<nav"), src.indexOf("</nav>"));
const gateRe = /\{(can\("(\w+)"\)|canAccess\(me, (\w+)\)) && \(/g;
const gates = [...navJsx.matchAll(gateRe)].map((m) => ({
  at: m.index,
  perm: m[2] || null,
  gateName: m[3] || null,
}));
const blocks = gates.map((g, i) => {
  const body = navJsx.slice(g.at, i + 1 < gates.length ? gates[i + 1].at : navJsx.length);
  return { ...g, hrefs: [...body.matchAll(/item=\{(\w+_ITEM)\}/g)].map((m) => consts[m[1]]) };
});
for (const b of blocks) {
  if (b.gateName && !old[b.gateName]) throw new Error(`unknown gate ${b.gateName} at ${ref}`);
  if (b.hrefs.some((h) => !h)) throw new Error(`unresolved item constant in block ${b.perm || b.gateName}`);
}

function sidebarVisible(me) {
  const out = [...navHrefs];
  for (const b of blocks) {
    const ok = b.perm ? old.canPerm(me, b.perm) : old.canAccess(me, old[b.gateName]);
    if (ok) out.push(...b.hrefs);
  }
  return out;
}

const hrefs = (items) => items.map((i) => i.href);
const personas = {};
for (const [name, me] of Object.entries(PERSONAS)) {
  const sidebar = sidebarVisible(me);
  const menuItems = hrefs(old.visibleMenuItems(me));
  const adminIndex = hrefs(old.visibleAdminSections(me));
  const reachable = [...new Set([...sidebar, ...adminIndex])].sort();
  personas[name] = { sidebar, menuItems, adminIndex, reachable };
}

const out = {
  generated_by: "apps/api/scripts/navregroup_baseline.mjs",
  ref,
  sidebar_rule: {
    always: navHrefs,
    blocks: blocks.map((b) => ({ gate: b.perm ? `can(${b.perm})` : b.gateName, hrefs: b.hrefs })),
  },
  menu_items_hrefs: hrefs(old.MENU_ITEMS),
  personas,
};
const json = JSON.stringify(out, null, 2) + "\n";
if (args.includes("--write")) {
  writeFileSync(path.join(REPO, "docs", "NAVREGROUP_BASELINE.json"), json);
  process.stderr.write("wrote docs/NAVREGROUP_BASELINE.json\n");
} else {
  process.stdout.write(json);
}
