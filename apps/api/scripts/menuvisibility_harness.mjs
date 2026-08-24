/**
 * Hermetic Node harness for the REAL deployed menu-visibility rule.
 *
 * Imports apps/web/lib/menuVisibility.mjs directly — the same module the sidebar
 * (components/Sidebar.jsx, via lib/usePermissions.js) and the /admin index
 * (app/admin/page.js) import. Nothing is re-implemented here, so a pass means
 * the shipped rule behaves as asserted, not that a test agrees with itself.
 * Same discipline as authhostconfig_harness.mjs.
 *
 * Prints one JSON object on stdout; verify_superadminmenu.py reads it.
 */

import {
  MENU_ITEMS,
  canAccess,
  canPerm,
  isSuperAdmin,
  visibleAdminSections,
  visibleMenuItems,
} from "../../web/lib/menuVisibility.mjs";

// ── Fixtures — shaped exactly like a /api/v1/users/me response ──────────────

// The reported case: platform staff, ZERO profiles, and (as in the live DB) a
// granted role that is NOT super_admin. This is the payload that used to lose
// menu items.
const SUPER_ADMIN_NO_PROFILES = {
  account_role: "super_admin",
  role: "member",
  roles: ["member"],
  permissions: ["view_dashboard"], // deliberately WITHOUT manage_members
};

// Same account with no granted roles at all — the other super-admin shape.
const SUPER_ADMIN_NO_ROLES = {
  account_role: "super_admin",
  role: "super_admin",
  roles: [],
  permissions: [],
};

// The live shape today: super_admin holding the granted 'admin' role, whose
// permission set happens to include manage_members.
const SUPER_ADMIN_GRANTED_ADMIN = {
  account_role: "super_admin",
  role: "admin",
  roles: ["admin"],
  permissions: ["manage_members", "view_dashboard"],
};

// REGRESSION FIXTURES — these must be byte-for-byte unchanged by this sprint.
const PLAIN_MEMBER = {
  account_role: "member",
  role: "member",
  roles: ["member"],
  permissions: ["view_dashboard", "view_marketplace"],
};

const MEMBER_WITH_MANAGE_MEMBERS = {
  account_role: "member",
  role: "member_manager",
  roles: ["member_manager"],
  permissions: ["manage_members", "view_dashboard"],
};

const ORG_ADMIN = {
  account_role: "org_admin",
  role: "admin",
  roles: ["admin"],
  permissions: ["manage_members", "view_dashboard"],
};

// Pre-RBAC single operator: no roles at all → default-allow posture.
const NO_ROLES_YET = {
  account_role: "member",
  role: null,
  roles: [],
  permissions: [],
};

const hrefs = (items) => items.map((i) => i.href);

// ── The pre-fix rule, reproduced EXACTLY as it stood, for the regression proof.
// Sidebar:      can(perm) = roles.length === 0 || permissions.includes(perm)
//               role gates compared account_role directly.
// /admin index: identical logic, independently copied.
// No super-admin bypass in either. Running both rules over the same fixture is
// what proves a regular user's menu is UNCHANGED rather than merely asserting it.
function legacyVisible(me) {
  const permissions = me?.permissions ?? [];
  const roles = me?.roles ?? [];
  const accountRole = me?.account_role ?? null;
  const noRolesYet = roles.length === 0;
  const legacyCan = (perm) => noRolesYet || permissions.includes(perm);
  return MENU_ITEMS.filter((item) => {
    const gate = item.gate;
    if (!gate) return true;
    if (gate.perm && !legacyCan(gate.perm)) return false;
    if (gate.roles && !gate.roles.includes(accountRole)) return false;
    return true;
  }).map((i) => i.href);
}

const sameSet = (a, b) =>
  a.length === b.length && a.every((x, i) => x === b[i]);

const result = {
  allHrefs: hrefs(MENU_ITEMS),
  adminIndexHrefs: hrefs(MENU_ITEMS.filter((i) => i.adminIndex)),

  // Task 1b/1c: enumerate every gate so the report is generated from the real
  // table, not transcribed by hand.
  gates: MENU_ITEMS.map((i) => ({
    href: i.href,
    label: i.label,
    gate: i.gate ? (i.gate.perm ? `perm:${i.gate.perm}` : `roles:${i.gate.roles.join("|")}`) : "none",
  })),

  superAdminNoProfiles: {
    isSuper: isSuperAdmin(SUPER_ADMIN_NO_PROFILES),
    canManageMembers: canPerm(SUPER_ADMIN_NO_PROFILES, "manage_members"),
    visible: hrefs(visibleMenuItems(SUPER_ADMIN_NO_PROFILES)),
    visibleAdmin: hrefs(visibleAdminSections(SUPER_ADMIN_NO_PROFILES)),
    legacyVisible: legacyVisible(SUPER_ADMIN_NO_PROFILES),
  },
  superAdminNoRoles: {
    visible: hrefs(visibleMenuItems(SUPER_ADMIN_NO_ROLES)),
  },
  superAdminGrantedAdmin: {
    visible: hrefs(visibleMenuItems(SUPER_ADMIN_GRANTED_ADMIN)),
  },

  regressions: [
    ["plain_member", PLAIN_MEMBER],
    ["member_with_manage_members", MEMBER_WITH_MANAGE_MEMBERS],
    ["org_admin", ORG_ADMIN],
    ["no_roles_yet", NO_ROLES_YET],
  ].map(([name, me]) => {
    const now = hrefs(visibleMenuItems(me));
    const before = legacyVisible(me);
    return { name, now, before, unchanged: sameSet(now, before) };
  }),

  // Defence in depth: a role gate that forgets to name super_admin must still
  // let platform staff through.
  gateOmittingSuperAdmin: canAccess(SUPER_ADMIN_NO_PROFILES, {
    roles: ["org_admin"],
  }),
};

process.stdout.write(JSON.stringify(result, null, 2));
