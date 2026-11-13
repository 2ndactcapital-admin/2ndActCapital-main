/**
 * NAV1 — a JSON snapshot of a menu definition: every item (href, section, group, group label, label, gate,
 * comingSoon) and, per persona, the set of hrefs the menu offers (sidebar entries + every visible tab) and the
 * /admin AREA decision. Run over the PRE-NAV1 module once to freeze nav1_baseline.json; verify_nav1.py and the web
 * test run it over the live module and diff.
 *
 *   node apps/api/scripts/nav1_snapshot.mjs [path/to/menuVisibility.mjs]
 */
import path from "node:path";
import { pathToFileURL, fileURLToPath } from "node:url";
import { PERSONAS } from "./nav1_personas.mjs";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const target = process.argv[2] || path.join(HERE, "../../web/lib/menuVisibility.mjs");

export async function snapshot(modPath) {
  const m = await import(pathToFileURL(path.resolve(modPath)).href);
  const groups = Object.fromEntries(m.GROUPS.map((g) => [g.id, g]));
  const items = m.MENU_ITEMS.map((i) => ({
    href: i.href, section: i.section, group: i.group, group_label: i.group ? groups[i.group]?.label ?? null : null,
    label: i.label, gate: i.gate ?? null, comingSoon: !!i.comingSoon,
  }));
  const personas = {};
  for (const [name, me] of Object.entries(PERSONAS)) {
    const offered = new Set();
    for (const s of m.visibleSections(me)) {
      for (const e of s.entries) {
        offered.add(e.href);
        if (e.kind === "group") for (const t of m.visibleTabs(me, e.groupId)) offered.add(t.href);
      }
    }
    personas[name] = {
      hrefs: [...offered].sort(),
      admin_area: m.canAccess(me, m.GATE_ADMIN_AREA),
      areas: Object.fromEntries(Object.keys(m.AREA_GATES).map((k) => [k, m.canOpenArea(me, k)])),
    };
  }
  return { items, personas };
}

if (import.meta.url === pathToFileURL(process.argv[1]).href) {
  process.stdout.write(JSON.stringify(await snapshot(target), null, 1) + "\n");
}
