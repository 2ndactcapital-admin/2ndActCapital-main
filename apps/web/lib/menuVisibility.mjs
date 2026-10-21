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
// org_admin role reconciliation (Task 4): these 5 items used to gate on the
// `users.role` string directly ({ roles: [ORG_ADMIN, SUPER_ADMIN] }). They now
// gate on the real `manage_org_settings` permission, resolved the same way
// GATE_MANAGE_MEMBERS already is — `canPerm` already carries the super_admin
// bypass, so nothing else changes for platform staff.
export const GATE_MANAGE_ORG_SETTINGS = { perm: "manage_org_settings" };
export const GATE_SUPER_ADMIN = { roles: [SUPER_ADMIN] };
// modelresearch.structural — the same permission as GATE_MANAGE_ORG_SETTINGS,
// but STRICT: it fails CLOSED. `canPerm` default-allows when `roles` is empty,
// and usePermissions substitutes `{ roles: [], permissions: [] }` whenever
// /users/me fails, so a lost envelope there reads as "allowed". A strict gate
// passes only for Super Admin or an explicitly listed permission. No envelope,
// a malformed one, or a role-less account all hide the item.
export const GATE_MANAGE_ORG_SETTINGS_STRICT = {
  perm: "manage_org_settings",
  strict: true,
};

// navregroup — the read gate of every "manage_portfolio"-family screen
// (positions, transactions, securities, billing groups, custody import) is
// rbac.has_permission("view_portfolio"); canPerm mirrors it exactly, including
// the role-less default-allow branch.
export const GATE_VIEW_PORTFOLIO = { perm: "view_portfolio" };
// navregroup Task 3 — GET /vdr-proposals now requires manage_deals, the same
// permission approve/reject always required. There is no separate read key.
export const GATE_MANAGE_DEALS = { perm: "manage_deals" };

/** Navigation sections, top to bottom. `header` is null for the main section. */
export const SECTIONS = [
  { id: "main", header: null },
  { id: "org_admin", header: "Admin — Organization" },
  { id: "platform", header: "Admin — Platform" },
];

/**
 * Master pages ("groups"). Each one is a single sidebar entry whose tabs are
 * the MENU_ITEMS carrying its id in `group`. `order` shares one number space
 * with the standalone items of the same section, so the sidebar can interleave
 * them. `landing` is the group's own route: it redirects to the first tab the
 * caller can see (landingFor), or renders the refusal when there is none. For
 * Portfolio and Tenants the landing IS the first tab, whose gate is null /
 * the group's only gate, so no separate route exists.
 */
export const GROUPS = [
  {
    id: "portfolio", label: "Portfolio", section: "main", order: 40, landing: "/portfolio",
    icon: "portfolio",
    description: "Investments, positions, transactions, securities and commitments.",
  },
  {
    id: "documents", label: "Documents", section: "main", order: 90, landing: "/documents",
    icon: "portfolio-reporting",
    description: "Find documents you can see, by name, category or meaning.",
  },
  {
    id: "people", label: "People & Access", section: "org_admin", order: 20, landing: "/admin/people",
    icon: "investment-profile",
    description: "Members, staff teams and coverage, profiles and permission sets.",
  },
  {
    id: "settings", label: "Settings", section: "org_admin", order: 30, landing: "/admin/configuration",
    icon: "admin",
    description: "White-label organization settings and TA model defaults.",
  },
  {
    id: "workflows", label: "Workflows", section: "org_admin", order: 40, landing: "/admin/automation",
    icon: "admin",
    description: "Governed process definitions, their runs and their schedules.",
  },
  {
    id: "billing", label: "Billing & Custody", section: "org_admin", order: 50, landing: "/admin/billing",
    icon: "admin",
    description: "Billing groups and custodian position imports.",
  },
  {
    id: "deal_intake", label: "Deal Intake", section: "org_admin", order: 60, landing: "/admin/deal-intake",
    icon: "marketplace",
    description: "Deals proposed from data-room uploads, awaiting review.",
  },
  {
    id: "tenants", label: "Tenants", section: "platform", order: 10, landing: "/admin/platform",
    icon: "admin",
    description: "Platform-wide settings across all organizations.",
  },
  {
    id: "controls", label: "Controls", section: "platform", order: 20, landing: "/admin/controls",
    icon: "admin",
    description: "Restricted accounts and per-entity trading authority.",
  },
  {
    id: "ai_models", label: "AI Models", section: "platform", order: 30, landing: "/admin/ai-models",
    icon: "admin",
    description: "The platform model catalog and model research.",
  },
  {
    id: "structured_notes", label: "Structured Notes", section: "platform", order: 40,
    landing: "/admin/structured-notes", icon: "admin",
    description: "EDGAR download, the terms review queue, gold answers, accuracy and the volatility surface.",
  },
];

/**
 * Every navigation item and the gate it is displayed behind — the ONE menu
 * definition. components/Sidebar.jsx, components/TabbedSection.jsx, the group
 * landing routes and the /admin index all render from this list; none of them
 * may keep an item list or a gate of its own (navregroup: the sidebar's
 * hard-coded copy had already drifted from this one).
 *
 *   section    'main' | 'org_admin' | 'platform'
 *   group      the GROUPS id this item is a tab of, or null for a standalone item
 *   tab_order  position within its group (1-based); null when standalone
 *   order      position within the section, standalone items only
 *   gate       null = any signed-in user; see canAccess
 *   adminIndex listed (inside its group's card) on the /admin landing page
 *   icon       BrandNavIcon name, standalone items only (groups carry their own)
 *
 * Every gate mirrors the page's REAL server gate (docs/NAVREGROUP_DISCOVERY.md
 * §1a). A page whose server gate the client cannot evaluate, or that has none,
 * is gated GATE_SUPER_ADMIN here (fail closed) and recorded as a [FIND].
 */
const main = (order, href, label, icon, gate = null) => ({
  section: "main", group: null, tab_order: null, order, href, label, icon, gate, adminIndex: false,
});
const tab = (section, group, tab_order, href, label, gate, adminIndex) => ({
  section, group, tab_order, order: null, href, label, icon: null, gate, adminIndex,
});

export const MENU_ITEMS = [
  main(10, "/dashboard", "Dashboard", "dashboard"),
  main(20, "/crm", "CRM", "crm"),
  main(30, "/marketplace", "Marketplace", "marketplace"),
  tab("main", "portfolio", 1, "/portfolio", "Overview", null, false),
  // Positions, Securities and Commitments were in the sidebar UNGATED; that is
  // kept so no persona loses a link (their API gate is view_portfolio — a
  // pre-existing mismatch recorded as a [FIND], not changed here).
  tab("main", "portfolio", 2, "/portfolio/positions", "Positions", null, false),
  tab("main", "portfolio", 3, "/portfolio/transactions", "Transactions", GATE_VIEW_PORTFOLIO, false),
  tab("main", "portfolio", 4, "/portfolio/securities", "Securities", null, false),
  tab("main", "portfolio", 5, "/portfolio/commitments", "Commitments", null, false),
  // [FIND] allocation-lens accepts any entity in the org for entity/subtree
  // selectors and the page has no gate of its own: listed for Super Admin only.
  tab("main", "portfolio", 6, "/portfolio/allocation", "Allocation", GATE_SUPER_ADMIN, false),
  // GET /me/ownership-graph is scoped to the caller's own visibility engine.
  tab("main", "portfolio", 7, "/portfolio/ownership-tree", "Ownership tree", null, false),
  tab("main", "portfolio", 8, "/portfolio-reporting", "Reporting", null, false),
  // mkt04a — the market API admits any valid session, so no gate here either.
  main(50, "/market", "Market Indicators", "portfolio-reporting"),
  main(60, "/spvs", "SPV Manager", "spv-manager"),
  main(70, "/insurance", "Insurance", "insurance"),
  main(80, "/community", "Community", "community"),
  // navregroup Task 3 — both searches now filter by the caller's own
  // visibility (staff visibility / delegate grants, then restricted access).
  tab("main", "documents", 1, "/admin/document-search", "Search", null, false),
  tab("main", "documents", 2, "/admin/semantic-search", "Semantic search", null, false),
  main(100, "/notifications", "Notifications", "notifications"),

  {
    section: "org_admin", group: null, tab_order: null, order: 10, href: "/admin", label: "Admin",
    icon: "admin", gate: GATE_MANAGE_MEMBERS, adminIndex: false,
  },
  tab("org_admin", "people", 1, "/admin/users", "Users", GATE_MANAGE_MEMBERS, true),
  tab("org_admin", "people", 2, "/admin/staff-visibility", "Teams & Coverage", GATE_MANAGE_MEMBERS, true),
  tab("org_admin", "people", 3, "/admin/profiles", "Profiles", GATE_MANAGE_ORG_SETTINGS, true),
  tab("org_admin", "people", 4, "/admin/permission-sets", "Permission Sets", GATE_MANAGE_ORG_SETTINGS, true),
  tab("org_admin", "settings", 1, "/admin/settings", "Organization", GATE_MANAGE_ORG_SETTINGS, true),
  tab("org_admin", "settings", 2, "/admin/modeling/ta", "TA Model Defaults", GATE_MANAGE_ORG_SETTINGS, true),
  // The Workflows API gates on PROFILE-axis keys (author_workflows,
  // view_workflow_runs, configure_workflow_triggers) that /users/me does not
  // publish. Workflows keeps its existing gate; Runs and Triggers — newly
  // listed — fail closed to Super Admin until /users/me carries those keys.
  tab("org_admin", "workflows", 1, "/admin/workflows", "Workflows", GATE_MANAGE_ORG_SETTINGS, true),
  tab("org_admin", "workflows", 2, "/admin/workflows/runs", "Runs", GATE_SUPER_ADMIN, true),
  tab("org_admin", "workflows", 3, "/admin/workflows/triggers", "Triggers", GATE_SUPER_ADMIN, true),
  tab("org_admin", "billing", 1, "/admin/billing-groups", "Billing Groups", GATE_VIEW_PORTFOLIO, true),
  tab("org_admin", "billing", 2, "/admin/custody-import", "Custody Import", GATE_VIEW_PORTFOLIO, true),
  tab("org_admin", "deal_intake", 1, "/admin/vdr-proposals", "VDR Proposals", GATE_MANAGE_DEALS, true),

  tab("platform", "tenants", 1, "/admin/platform", "Tenants", GATE_SUPER_ADMIN, true),
  tab("platform", "controls", 1, "/admin/restricted-access", "Restricted Access", GATE_SUPER_ADMIN, true),
  tab("platform", "controls", 2, "/admin/trading-authority", "Trading Authority", GATE_SUPER_ADMIN, true),
  tab("platform", "ai_models", 1, "/admin/model-catalog", "Model Catalog", GATE_SUPER_ADMIN, true),
  // modelresearch.structural — STRICT: fails closed on a lost envelope. Its
  // gate is manage_org_settings, so an org admin sees the AI Models group too.
  tab("platform", "ai_models", 2, "/admin/model-research", "Model Research", GATE_MANAGE_ORG_SETTINGS_STRICT, true),
  tab("platform", "structured_notes", 1, "/admin/edgar-pipeline", "Download", GATE_SUPER_ADMIN, true),
  tab("platform", "structured_notes", 2, "/admin/pricing/note-terms-queue", "Review queue", GATE_SUPER_ADMIN, true),
  tab("platform", "structured_notes", 3, "/admin/note-extraction/gold", "Gold answers", GATE_SUPER_ADMIN, true),
  tab("platform", "structured_notes", 4, "/admin/note-extraction/results", "Accuracy results", GATE_SUPER_ADMIN, true),
  tab("platform", "structured_notes", 5, "/admin/pricing/surface", "Volatility surface", GATE_SUPER_ADMIN, true),
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

/**
 * Fail-closed permission check: Super Admin first, then the permission must be
 * explicitly present in a real `permissions` array. There is no default-allow
 * branch and no fallback, so a missing or malformed envelope returns false.
 */
export function canPermStrict(me, permission) {
  if (!me || typeof me !== "object") return false;
  if (isSuperAdmin(me)) return true;
  if (!Array.isArray(me.permissions)) return false;
  return me.permissions.includes(permission);
}

/** True when `me` may see an item carrying `gate` (null gate = always). */
export function canAccess(me, gate) {
  if (!gate) return true;
  if (gate.strict) return canPermStrict(me, gate.perm);
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

// ── navregroup: groups, tabs and sections ──────────────────────────────────

const groupById = (id) => GROUPS.find((g) => g.id === id) || null;

/** The tabs of `groupId` that `me` can see, in tab order. */
export function visibleTabs(me, groupId) {
  return MENU_ITEMS.filter((i) => i.group === groupId && canAccess(me, i.gate)).sort(
    (a, b) => a.tab_order - b.tab_order,
  );
}

/** The first visible tab's href, or null when `me` can see no tab of the group. */
export function landingFor(me, groupId) {
  return visibleTabs(me, groupId)[0]?.href ?? null;
}

/** Groups of `section` with at least one visible tab, in section order. */
export function visibleGroups(me, section) {
  return GROUPS.filter((g) => g.section === section && visibleTabs(me, g.id).length > 0).sort(
    (a, b) => a.order - b.order,
  );
}

/**
 * What the sidebar renders for one section: standalone items and groups,
 * interleaved by `order`. A group entry links to landingFor and lists the hrefs
 * of its visible tabs (for the active state).
 */
export function visibleSectionEntries(me, section) {
  const items = MENU_ITEMS.filter(
    (i) => i.section === section && i.group === null && canAccess(me, i.gate),
  ).map((i) => ({ kind: "item", key: i.href, order: i.order, label: i.label, icon: i.icon,
    href: i.href, tabHrefs: [i.href] }));
  const groups = visibleGroups(me, section).map((g) => {
    const tabs = visibleTabs(me, g.id);
    return { kind: "group", key: `group:${g.id}`, order: g.order, label: g.label, icon: g.icon,
      href: tabs[0].href, tabHrefs: tabs.map((t) => t.href), groupId: g.id };
  });
  return [...items, ...groups].sort((a, b) => a.order - b.order);
}

/** Sections with at least one visible entry, each with its entries. */
export function visibleSections(me) {
  return SECTIONS.map((s) => ({ ...s, entries: visibleSectionEntries(me, s.id) })).filter(
    (s) => s.entries.length > 0,
  );
}

const matches = (path, href) => path === href || (path || "").startsWith(href + "/");

/**
 * The tab a path belongs to: the LONGEST matching tab href across every group,
 * so /portfolio/positions is Positions (not Overview) and
 * /admin/workflows/runs/x is Runs (not Workflows). Returns { group, item } or null.
 */
export function tabForPath(path) {
  let best = null;
  for (const item of MENU_ITEMS) {
    if (item.group && matches(path, item.href) && (!best || item.href.length > best.href.length)) {
      best = item;
    }
  }
  return best ? { group: groupById(best.group), item: best } : null;
}

/** The group whose landing route is exactly `path`, or null. */
export function groupForLanding(path) {
  return GROUPS.find((g) => g.landing === path) || null;
}

/** True when a sidebar entry should show as active for `path`. */
export function entryIsActive(entry, path) {
  if (entry.kind === "group") {
    const hit = tabForPath(path);
    return (hit?.group?.id === entry.groupId) || groupForLanding(path)?.id === entry.groupId;
  }
  // A standalone item is active on its own path — but never when that path
  // belongs to a group (a tab or a landing route), so "/admin" does not light up
  // on every /admin/* page.
  if (path === entry.href) return true;
  return matches(path, entry.href) && !tabForPath(path) && !groupForLanding(path);
}

/** Label for an href, from this definition (page headings use it — Rule 1). */
export function labelFor(href) {
  return MENU_ITEMS.find((i) => i.href === href)?.label ?? null;
}

/**
 * The server decision a group landing route makes: redirect to the first
 * visible tab, or refuse (never a redirect back to itself).
 */
export function landingDecision(me, groupId) {
  const group = groupById(groupId);
  if (!group) return { kind: "refuse", group: null };
  const href = landingFor(me, groupId);
  return href ? { kind: "redirect", href, group } : { kind: "refuse", group };
}

/**
 * The /admin landing: one card per visible group in the two admin sections,
 * each with its visible tabs as links (adminIndex items only).
 */
export function visibleAdminGroups(me) {
  return ["org_admin", "platform"].flatMap((section) =>
    visibleGroups(me, section)
      .map((g) => ({ ...g, tabs: visibleTabs(me, g.id).filter((t) => t.adminIndex) }))
      .filter((g) => g.tabs.length > 0),
  );
}
