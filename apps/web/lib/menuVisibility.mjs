/**
 * Pure, dependency-free menu visibility rules — the SINGLE source of truth for
 * which navigation items a given /users/me payload can see.
 *
 * This module deliberately imports NOTHING (no React, no "@/..." aliases) so the
 * EXACT rule the deployed sidebar and /admin index use can be exercised by a
 * plain Node harness (apps/api/scripts/menuvisibility_harness.mjs). Same
 * discipline, and for the same reason, as `lib/authHostConfig.mjs`: a menu gate
 * that is re-implemented inside a test proves only that the test agrees with
 * itself.
 *
 * THE BUG THIS CENTRALIZES (superadminmenu sprint). The gate logic existed in
 * TWO independent copies — `lib/usePermissions.can()` (the sidebar) and
 * `app/admin/page.js visibleSections()` (the /admin index) — and NEITHER had a
 * Super Admin bypass. Every other enforcement layer in this codebase checks
 * `is_super_admin` FIRST, before any granular check: RLS policies,
 * restricted_access, staff_visibility, trading_authority, the Workflow Manager's
 * resolver, and (since commit 470eb26) services.rbac.has_permission. These two
 * menus were the last holdouts.
 *
 * Why "no roles assigned yet → default-allow" did NOT cover it: that shield only
 * holds while `user_roles` is empty, and it is not empty. The live super_admin
 * account (jlarizza@culmina.io, users.role = 'super_admin') holds a granted
 * 'admin' role, so it takes the strict per-permission branch. Its menu survives
 * today only because 'admin' happens to include `manage_members`; granting it
 * any role that does not (e.g. 'member') would silently remove Admin, User
 * Management and Staff Visibility from the sidebar while the backend continued
 * to authorize all three pages.
 *
 * A hidden link is not a permission — every gate here has a real server-side
 * counterpart. This decides what is DISPLAYED, nothing more.
 */

export const SUPER_ADMIN = "super_admin";
export const ORG_ADMIN = "org_admin";

/** Gate shapes used below, named so both menus and the harness agree. */
export const GATE_MANAGE_MEMBERS = { perm: "manage_members" };
export const GATE_ORG_OR_SUPER_ADMIN = { roles: [ORG_ADMIN, SUPER_ADMIN] };
export const GATE_SUPER_ADMIN = { roles: [SUPER_ADMIN] };

/**
 * Every navigation item and the gate it is displayed behind.
 *
 * `gate: null` means "always shown to any authenticated user". Order matches the
 * sidebar top-to-bottom. `adminIndex: true` marks the items the /admin landing
 * page also lists (it shows administration areas only, not the primary nav).
 */
export const MENU_ITEMS = [
  { href: "/dashboard", label: "Dashboard", gate: null },
  { href: "/crm", label: "CRM", gate: null },
  { href: "/marketplace", label: "Marketplace", gate: null },
  { href: "/portfolio", label: "Investments", gate: null },
  { href: "/portfolio-reporting", label: "Portfolio Reporting", gate: null },
  { href: "/spvs", label: "SPV Manager", gate: null },
  { href: "/insurance", label: "Insurance", gate: null },
  { href: "/community", label: "Community", gate: null },
  { href: "/notifications", label: "Notifications", gate: null },

  { href: "/admin", label: "Admin", gate: GATE_MANAGE_MEMBERS },
  {
    href: "/admin/users",
    label: "User Management",
    gate: GATE_MANAGE_MEMBERS,
    adminIndex: true,
  },
  {
    href: "/admin/staff-visibility",
    label: "Staff Visibility",
    gate: GATE_MANAGE_MEMBERS,
    adminIndex: true,
  },

  {
    href: "/admin/profiles",
    label: "Profiles",
    gate: GATE_ORG_OR_SUPER_ADMIN,
    adminIndex: true,
  },
  {
    href: "/admin/permission-sets",
    label: "Permission Sets",
    gate: GATE_ORG_OR_SUPER_ADMIN,
    adminIndex: true,
  },
  {
    href: "/admin/workflows",
    label: "Workflows",
    gate: GATE_ORG_OR_SUPER_ADMIN,
    adminIndex: true,
  },
  {
    href: "/admin/settings",
    label: "Organization",
    gate: GATE_ORG_OR_SUPER_ADMIN,
    adminIndex: true,
  },
  {
    href: "/admin/modeling/ta",
    label: "TA Model Defaults",
    gate: GATE_ORG_OR_SUPER_ADMIN,
    adminIndex: true,
  },

  {
    href: "/admin/restricted-access",
    label: "Restricted Access",
    gate: GATE_SUPER_ADMIN,
    adminIndex: true,
  },
  {
    href: "/admin/trading-authority",
    label: "Trading Authority",
    gate: GATE_SUPER_ADMIN,
    adminIndex: true,
  },
  {
    href: "/admin/pricing/note-terms-queue",
    label: "Note Terms Review",
    gate: GATE_SUPER_ADMIN,
    adminIndex: true,
  },
  {
    href: "/admin/pricing/surface",
    label: "Volatility Surface",
    gate: GATE_SUPER_ADMIN,
    adminIndex: true,
  },
  {
    href: "/admin/platform",
    label: "Platform",
    gate: GATE_SUPER_ADMIN,
    adminIndex: true,
  },
];

/** The raw `users.role` on the /users/me payload (NOT the granted user_roles name). */
export function accountRoleOf(me) {
  return me?.account_role ?? null;
}

/** True when this is platform staff. The bypass every gate checks FIRST. */
export function isSuperAdmin(me) {
  return accountRoleOf(me) === SUPER_ADMIN;
}

/**
 * Effective permission check.
 *
 * 1. Super Admin passes, FIRST, before anything else.
 * 2. Otherwise, a user with NO granted roles default-allows (single-admin
 *    safety, mirroring services.rbac.has_permission).
 * 3. Otherwise the granted permission set is authoritative.
 */
export function canPerm(me, permission) {
  if (isSuperAdmin(me)) return true;
  const roles = me?.roles ?? [];
  if (roles.length === 0) return true;
  return (me?.permissions ?? []).includes(permission);
}

/** True when `me` may see an item carrying `gate` (null gate = always). */
export function canAccess(me, gate) {
  if (!gate) return true;
  if (gate.perm && !canPerm(me, gate.perm)) return false;
  // Role gates already name super_admin explicitly wherever they are used, so
  // no separate bypass is needed — but check it first anyway so the rule is the
  // same everywhere and a future gate that omits super_admin cannot lock
  // platform staff out.
  if (gate.roles && !isSuperAdmin(me) && !gate.roles.includes(accountRoleOf(me))) {
    return false;
  }
  return true;
}

/** Every menu item `me` can see, in sidebar order. */
export function visibleMenuItems(me) {
  return MENU_ITEMS.filter((item) => canAccess(me, item.gate));
}

/** The subset the /admin landing page lists. */
export function visibleAdminSections(me) {
  return MENU_ITEMS.filter((item) => item.adminIndex && canAccess(me, item.gate));
}
