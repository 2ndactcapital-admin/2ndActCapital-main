/**
 * Hermetic Node harness for the Model Research menu entry (modelresearch.structural).
 *
 * Imports apps/web/lib/menuVisibility.mjs directly, the same module the sidebar
 * (components/Sidebar.jsx) and the /admin index (app/admin/page.js) import.
 * Nothing is re-implemented here.
 *
 * stdin: JSON object { name: mePayload, ... }. verify_modelresearch.py passes the
 * REAL /api/v1/users/me responses of its fixture users, so the envelope fed to the
 * render rule is the server's own, not a hand-built one. The harness adds the
 * missing / malformed envelope shapes itself.
 *
 * stdout: one JSON object, read by verify_modelresearch.py.
 */

import {
  GATE_MANAGE_ORG_SETTINGS_STRICT,
  MENU_ITEMS,
  canAccess,
  visibleAdminSections,
  visibleMenuItems,
} from "../../web/lib/menuVisibility.mjs";

const HREF = "/admin/model-research";

let raw = "";
for await (const chunk of process.stdin) raw += chunk;
const real = raw.trim() ? JSON.parse(raw) : {};

// Envelopes that must all fail CLOSED.
const lost = {
  missing_null: null,
  missing_undefined: undefined,
  empty_object: {},
  // Exactly what lib/usePermissions.js substitutes when /users/me fails.
  use_permissions_fallback: { role: null, roles: [], permissions: [] },
  permissions_not_array: { account_role: "member", roles: ["member"], permissions: "manage_org_settings" },
  role_less_account: { account_role: "member", role: null, roles: [], permissions: [] },
};

function evaluate(me) {
  return {
    gate: canAccess(me, GATE_MANAGE_ORG_SETTINGS_STRICT),
    sidebar: visibleMenuItems(me).some((i) => i.href === HREF),
    adminIndex: visibleAdminSections(me).some((i) => i.href === HREF),
  };
}

const item = MENU_ITEMS.find((i) => i.href === HREF) || null;

const result = {
  item: item
    ? { href: item.href, label: item.label, adminIndex: item.adminIndex === true, strict: item.gate?.strict === true, perm: item.gate?.perm ?? null }
    : null,
  real: Object.fromEntries(Object.entries(real).map(([k, me]) => [k, evaluate(me)])),
  lost: Object.fromEntries(Object.entries(lost).map(([k, me]) => [k, evaluate(me)])),
};

process.stdout.write(JSON.stringify(result));
