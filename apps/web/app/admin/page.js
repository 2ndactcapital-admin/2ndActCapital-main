import { redirect } from "next/navigation";
import { getHostSession } from "@/lib/authServer";
import AppShell from "@/components/AppShell";
import { getMe } from "@/lib/api";

export const metadata = {
  title: "Admin — 2nd Act Capital",
};

// The admin sections, each gated by the SAME real checks the sidebar uses:
//   * `perm`  — an effective permission key (services/profiles user_has_permission
//               → surfaced in /users/me `permissions`). Single-admin safety:
//               when the user has no roles assigned yet, permission gates
//               default-allow, mirroring the backend + usePermissions().
//   * `roles` — the raw account role (users.role), for the Sprint 24+ gates that
//               key off org_admin / super_admin directly.
// This is deliberately NOT a new gating system — it reads the exact same
// /users/me payload the client sidebar consumes.
const SECTIONS = [
  {
    href: "/admin/users",
    label: "User Management",
    desc: "Manage member access and roles.",
    perm: "manage_members",
  },
  {
    href: "/admin/staff-visibility",
    label: "Staff Visibility",
    desc: "Staff teams and per-entity assignments.",
    perm: "manage_members",
  },
  {
    href: "/admin/profiles",
    label: "Profiles",
    desc: "Permission profiles for members.",
    roles: ["org_admin", "super_admin"],
  },
  {
    href: "/admin/permission-sets",
    label: "Permission Sets",
    desc: "Reusable bundles of permissions.",
    roles: ["org_admin", "super_admin"],
  },
  {
    href: "/admin/workflows",
    label: "Workflows",
    desc: "Governance and approval workflows.",
    roles: ["org_admin", "super_admin"],
  },
  {
    href: "/admin/settings",
    label: "Organization",
    desc: "White-label and organization settings.",
    roles: ["org_admin", "super_admin"],
  },
  {
    href: "/admin/restricted-access",
    label: "Restricted Access",
    desc: "Restrict accounts and manage allow-lists.",
    roles: ["super_admin"],
  },
  {
    href: "/admin/trading-authority",
    label: "Trading Authority",
    desc: "Per-entity trading-authority tiers.",
    roles: ["super_admin"],
  },
  {
    href: "/admin/platform",
    label: "Platform",
    desc: "Platform-wide settings across all orgs.",
    roles: ["super_admin"],
  },
];

function visibleSections(me) {
  const permissions = me?.permissions || [];
  const roles = me?.roles || [];
  const accountRole = me?.account_role || null;
  // No roles assigned yet → default-allow permission gates (matches backend +
  // usePermissions()). Role gates still require the actual account role.
  const noRolesYet = roles.length === 0;
  const can = (perm) => noRolesYet || permissions.includes(perm);

  return SECTIONS.filter((s) => {
    if (s.perm && !can(s.perm)) return false;
    if (s.roles && !s.roles.includes(accountRole)) return false;
    return true;
  });
}

export default async function AdminIndexPage() {
  const session = await getHostSession();
  if (!session) redirect("/auth/login?returnTo=/admin");

  let me = null;
  try {
    me = await getMe();
  } catch {
    // If /users/me fails we render an empty state rather than crashing.
  }

  const sections = visibleSections(me);

  return (
    <AppShell user={session.user}>
      <div>
        <h1 className="text-3xl font-semibold text-navy">Admin</h1>
        <p className="mt-1 text-sm text-text-muted">
          Administration areas available to you.
        </p>
      </div>

      {sections.length === 0 ? (
        <div className="mt-6 rounded-lg border border-border bg-bg-card p-10 text-center text-sm text-text-muted">
          You do not have access to any administration areas.
        </div>
      ) : (
        <div className="mt-6 grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {sections.map((s) => (
            <a
              key={s.href}
              href={s.href}
              className="block rounded-md border border-border bg-bg-card p-5 transition-colors hover:border-gold"
              style={{ boxShadow: "0 1px 3px rgba(0,0,0,0.06)" }}
            >
              <div className="text-base font-semibold text-navy">{s.label}</div>
              <p className="mt-1 text-sm text-text-muted">{s.desc}</p>
            </a>
          ))}
        </div>
      )}
    </AppShell>
  );
}
