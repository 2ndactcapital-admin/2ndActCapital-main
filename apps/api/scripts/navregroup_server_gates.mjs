/**
 * navregroup Task 1a — the REAL server gate behind every navigable page, as
 * traced through the page → Next API route → FastAPI handler (see
 * docs/NAVREGROUP_DISCOVERY.md for file:line of each). Test data, not deployed
 * code: the harness checks every href the menu shows against this table, so a
 * menu item a server would refuse is caught.
 *
 * Gate kinds and how a /users/me-shaped persona is evaluated against them:
 *   session          any valid session (FastAPI middleware 401s otherwise)
 *   rbac:<perm>      services.rbac.has_permission — Super Admin first, then the
 *                    role-axis grant. (AUTHZ1 removed the role-less default-allow.)
 *   token:<perm>     formerly services.permissions token claims; since AUTHZ1 the
 *                    same DB Principal check as rbac (services.authz).
 *   profile:<a|b>    services.profiles.user_has_permission (profile + permission
 *                    sets ONLY, no default-allow), Super Admin first. Personas
 *                    carry `profile_permissions` (none do → Super Admin only).
 *   super_admin      users.role = 'super_admin'
 *   missing          no page exists at this path (Next 404) — nobody is served
 */
export const SERVER_GATES = {
  "/dashboard": "session",
  "/crm": "session",
  "/marketplace": "session",
  "/portfolio": "session",
  "/portfolio/positions": "rbac:view_portfolio",
  "/portfolio/transactions": "rbac:view_portfolio",
  "/portfolio/securities": "rbac:view_portfolio",
  "/portfolio/commitments": "session",
  "/portfolio/allocation": "session",
  "/portfolio/ownership-tree": "session",
  "/portfolio-reporting": "missing",
  "/market": "session",
  "/spvs": "session",
  "/insurance": "missing",
  "/community": "missing",
  "/notifications": "session",
  "/admin/document-search": "session",
  "/admin/semantic-search": "session",
  // BUGFIX1 — Documents moved out of /admin (the old URLs redirect here).
  "/documents": "session",
  "/documents/semantic": "session",
  "/admin": "session",
  "/admin/users": "rbac:manage_members",
  "/admin/staff-visibility": "rbac:manage_members",
  "/admin/profiles": "rbac:manage_org_settings",
  "/admin/permission-sets": "rbac:manage_org_settings",
  "/admin/settings": "rbac:manage_org_settings",
  "/admin/modeling/ta": "rbac:manage_org_settings",
  "/admin/workflows": "profile:author_workflows",
  "/admin/workflows/runs": "profile:view_workflow_runs",
  "/admin/workflows/triggers": "profile:view_workflow_runs|configure_workflow_triggers",
  "/admin/billing-groups": "rbac:view_portfolio",
  "/admin/custody-import": "rbac:view_portfolio",
  "/admin/vdr-proposals": "rbac:manage_deals",
  "/admin/model-research": "rbac:manage_org_settings",
  "/admin/model-catalog": "super_admin",
  "/admin/platform": "super_admin",
  "/admin/restricted-access": "super_admin",
  "/admin/trading-authority": "super_admin",
  "/admin/pricing/note-terms-queue": "super_admin",
  "/admin/pricing/surface": "super_admin",
  "/admin/edgar-pipeline": "super_admin",
  "/admin/note-extraction/gold": "super_admin",
  "/admin/note-extraction/results": "super_admin",
};

const isSuper = (me) => me?.account_role === "super_admin";

/** True when the page's server gate would serve `me`. Unknown href → false. */
export function serverAllows(me, href) {
  const gate = SERVER_GATES[href];
  if (!gate || gate === "missing") return false;
  if (gate === "session") return true;
  if (gate === "super_admin") return isSuper(me);
  const [kind, arg] = gate.split(":");
  const perms = me?.permissions ?? [];
  if (kind === "rbac" || kind === "token") return isSuper(me) || perms.includes(arg);
  if (kind === "profile") {
    const granted = me?.profile_permissions ?? [];
    return isSuper(me) || arg.split("|").some((p) => granted.includes(p));
  }
  return false;
}
