/**
 * Hermetic Node harness for the REAL deployed menu-visibility rule.
 *
 * Imports apps/web/lib/menuVisibility.mjs directly — the same module the sidebar
 * (components/Sidebar.jsx, via lib/usePermissions.js) and the /admin index
 * (app/admin/page.js) import. Nothing is re-implemented here, so a pass means
 * the shipped rule behaves as asserted, not that a test agrees with itself.
 * Same discipline as authhostconfig_harness.mjs.
 *
 * Prints one JSON object on stdout; verify_superadminmenu.py reads it, and
 * verify_navregroup.py reads its `navregroup` block (persona snapshot against
 * docs/NAVREGROUP_BASELINE.json, server-gate cross-check, group/tab rules).
 */

import { readFileSync } from "node:fs";

import {
  GROUPS,
  MENU_ITEMS,
  canAccess,
  canPerm,
  isSuperAdmin,
  landingDecision,
  landingFor,
  visibleAdminGroups,
  visibleAdminSections,
  visibleGroups,
  visibleMenuItems,
  visibleSections,
  visibleTabs,
} from "../../web/lib/menuVisibility.mjs";
import { PERSONAS } from "./navregroup_personas.mjs";
import { SERVER_GATES, serverAllows } from "./navregroup_server_gates.mjs";

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

// org_admin role reconciliation: a MIGRATED org_admin now also holds the real
// granted role/permission (Task 3), not just the account_role string. Updated
// from this sprint's own predecessor shape ({ role: "admin", permissions:
// ["manage_members", ...] }, no manage_org_settings) — that shape represented
// the PRE-migration world where the 5 org-admin menu items gated on
// account_role directly; gating on the real manage_org_settings permission
// instead (this sprint's Task 4) is what makes that fixture's org-admin menu
// access correct again, deliberately, not a regression.
const ORG_ADMIN = {
  account_role: "org_admin",
  role: "org_admin",
  roles: ["org_admin"],
  permissions: ["manage_org_settings"],
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

// AUTHZ1 — the ONLY intended menu change: a role-less account no longer
// default-allows, so it loses exactly the items the legacy rule showed it
// through that branch. Written out by hand (not derived from the module).
const AUTHZ1_LEGACY_LOSS = {
  no_roles_yet: ["/admin", "/admin/billing-groups", "/admin/custody-import", "/admin/modeling/ta",
    "/admin/permission-sets", "/admin/profiles", "/admin/settings", "/admin/staff-visibility",
    "/admin/users", "/admin/vdr-proposals", "/admin/workflows", "/portfolio/transactions"],
};

const result = {
  allHrefs: hrefs(MENU_ITEMS),
  adminIndexHrefs: hrefs(MENU_ITEMS.filter((i) => i.adminIndex)),

  // Task 1b/1c: enumerate every gate so the report is generated from the real
  // table, not transcribed by hand.
  gates: MENU_ITEMS.map((i) => ({
    href: i.href,
    label: i.label,
    gate: i.gate
      ? i.gate.perm
        ? `perm:${i.gate.perm}${i.gate.strict ? " (strict)" : ""}`
        : `roles:${i.gate.roles.join("|")}`
      : "none",
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
    // Items behind a STRICT gate (modelresearch.structural) postdate the legacy
    // rule and deliberately do NOT inherit its role-less default-allow, so the
    // before/after comparison covers only the items the legacy rule governed.
    const strictHrefs = new Set(
      MENU_ITEMS.filter((i) => i.gate?.strict).map((i) => i.href),
    );
    const now = hrefs(visibleMenuItems(me)).filter((h) => !strictHrefs.has(h));
    const before = legacyVisible(me).filter((h) => !strictHrefs.has(h));
    const lost = before.filter((h) => !now.includes(h)).sort();
    const expectedLoss = [...(AUTHZ1_LEGACY_LOSS[name] ?? [])].sort();
    return {
      name, now, before, unchanged: sameSet(now, before), lost, expected_loss: expectedLoss,
      // unchanged, OR changed by exactly the AUTHZ1 default-allow removal.
      authz1_expected: sameSet(lost, expectedLoss) && now.every((h) => before.includes(h)),
    };
  }),

  // Defence in depth: a role gate that forgets to name super_admin must still
  // let platform staff through.
  gateOmittingSuperAdmin: canAccess(SUPER_ADMIN_NO_PROFILES, {
    roles: ["org_admin"],
  }),
};

// ── navregroup — persona snapshot against the frozen baseline + group rules ──
//
// The baseline (docs/NAVREGROUP_BASELINE.json) was computed by
// navregroup_baseline.mjs from the PRE-sprint Sidebar.jsx + menuVisibility.mjs at
// a pinned commit. "After" is what the ONE definition now makes reachable: every
// visible item (a standalone item via the sidebar, a tab via its group's sidebar
// entry and tab bar) — the /admin cards link only to those same tabs.
const BASELINE = JSON.parse(
  readFileSync(new URL("../../../docs/NAVREGROUP_BASELINE.json", import.meta.url), "utf8"),
);

// The previously unlisted pages each persona now gains, written out by hand —
// each one behind the page's real server gate (navregroup_server_gates.mjs).
const DOCS = ["/admin/document-search", "/admin/semantic-search"];
const INTENDED_ADDITIONS = {
  plain_member: ["/portfolio/transactions", "/portfolio/ownership-tree", ...DOCS,
    "/admin/billing-groups", "/admin/custody-import"],
  staff_member: ["/portfolio/transactions", "/portfolio/ownership-tree", ...DOCS,
    "/admin/billing-groups", "/admin/custody-import", "/admin/vdr-proposals"],
  org_admin_manage_members: ["/portfolio/ownership-tree", ...DOCS],
  org_admin_manage_org_settings: ["/portfolio/ownership-tree", ...DOCS],
  super_admin: ["/portfolio/transactions", "/portfolio/allocation", "/portfolio/ownership-tree", ...DOCS,
    "/admin/workflows/runs", "/admin/workflows/triggers", "/admin/billing-groups", "/admin/custody-import",
    "/admin/vdr-proposals"],
  // AUTHZ1: role-less gains only the ungated pages (no default-allow).
  no_roles: ["/portfolio/ownership-tree", ...DOCS],
};

// AUTHZ1 — what a persona loses from the frozen navregroup baseline: only the
// role-less persona, and only the items it reached through default-allow.
const AUTHZ1_REMOVALS = {
  no_roles: ["/admin", "/admin/modeling/ta", "/admin/permission-sets", "/admin/profiles",
    "/admin/settings", "/admin/staff-visibility", "/admin/users", "/admin/workflows"],
};

const sorted = (xs) => [...new Set(xs)].sort();
const minus = (a, b) => a.filter((x) => !b.includes(x));

const personaResults = Object.entries(PERSONAS).map(([name, me]) => {
  const baseline = BASELINE.personas[name]?.reachable ?? null;
  const after = sorted([
    ...visibleMenuItems(me).map((i) => i.href),
    ...visibleAdminGroups(me).flatMap((g) => g.tabs.map((t) => t.href)),
  ]);
  const sidebarHrefs = visibleSections(me).flatMap((s) => s.entries.flatMap((e) => e.tabHrefs));
  const removed = baseline ? minus(baseline, after) : null;
  const added = baseline ? minus(after, baseline) : null;
  const intended = sorted(INTENDED_ADDITIONS[name]);
  const refusedAfter = after.filter((h) => !serverAllows(me, h));
  const refusedBefore = (baseline ?? []).filter((h) => !serverAllows(me, h));
  return {
    name,
    baseline_count: baseline?.length ?? 0,
    after_count: after.length,
    removed,
    added: added ? sorted(added) : null,
    intended,
    additions_exact: !!added && JSON.stringify(sorted(added)) === JSON.stringify(intended),
    none_removed: !!removed && removed.length === 0,
    authz1_removals: sorted(AUTHZ1_REMOVALS[name] ?? []),
    removals_exact: !!removed
      && JSON.stringify(sorted(removed)) === JSON.stringify(sorted(AUTHZ1_REMOVALS[name] ?? [])),
    // Every reachable href must be in the sidebar model too (nothing reachable
    // only through a side door the definition does not render).
    after_equals_sidebar_tabs: JSON.stringify(sorted(sidebarHrefs)) === JSON.stringify(after),
    unknown_server_gate: after.filter((h) => !(h in SERVER_GATES)),
    refused_after: refusedAfter,
    refused_before: refusedBefore,
    // No NEW refusal: a href the server refuses is only tolerated when the same
    // persona already had it before the sprint (a pre-existing [FIND]).
    new_refusals: minus(refusedAfter, refusedBefore),
    additions_refused: (added ?? []).filter((h) => !serverAllows(me, h)),
  };
});

const tabHrefs = new Set(MENU_ITEMS.map((i) => i.href));
const groupResults = Object.entries(PERSONAS).flatMap(([name, me]) =>
  GROUPS.map((g) => {
    const tabs = visibleTabs(me, g.id).map((t) => t.href);
    const shown = visibleGroups(me, g.section).some((x) => x.id === g.id);
    const decision = landingDecision(me, g.id);
    return {
      persona: name,
      group: g.id,
      visible_tabs: tabs.length,
      shown_iff_tab: shown === tabs.length > 0,
      landing_is_first: landingFor(me, g.id) === (tabs[0] ?? null),
      decision: decision.kind,
      decision_ok: tabs.length > 0
        ? decision.kind === "redirect" && decision.href === tabs[0]
        : decision.kind === "refuse",
      // A redirect always lands on a TAB page, never on a landing route, so a
      // landing cannot bounce to another landing (or itself).
      redirect_target_is_tab: decision.kind !== "redirect"
        || (tabHrefs.has(decision.href) && !GROUPS.some((x) => x.landing === decision.href && x.id !== g.id)),
    };
  }),
);

// A group whose landing IS one of its tab hrefs has no route of its own, so that
// tab must be the first visible tab for EVERY persona that sees the group.
const landingIsTabGroups = GROUPS.filter((g) => tabHrefs.has(g.landing)).map((g) => ({
  group: g.id,
  landing: g.landing,
  ok: Object.values(PERSONAS).every(
    (me) => visibleTabs(me, g.id).length === 0 || landingFor(me, g.id) === g.landing,
  ),
}));

result.navregroup = {
  baseline_ref: BASELINE.ref,
  personas: personaResults,
  groups: groupResults,
  refusal_pairs: groupResults.filter((r) => r.decision === "refuse").map((r) => `${r.persona}:${r.group}`),
  landing_is_tab_groups: landingIsTabGroups,
  every_item_has_section: MENU_ITEMS.every((i) => ["main", "org_admin", "platform"].includes(i.section)),
  every_group_item_known: MENU_ITEMS.every((i) => i.group === null || GROUPS.some((g) => g.id === i.group)),
  every_group_has_tab: GROUPS.every((g) => MENU_ITEMS.some((i) => i.group === g.id)),
  labels: Object.fromEntries(MENU_ITEMS.map((i) => [i.href, i.label])),
  group_labels: Object.fromEntries(GROUPS.map((g) => [g.id, g.label])),
  group_landings: Object.fromEntries(GROUPS.map((g) => [g.id, g.landing])),
};

process.stdout.write(JSON.stringify(result, null, 2));
