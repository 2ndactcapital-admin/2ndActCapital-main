import { redirect } from "next/navigation";
import { auth0 } from "@/lib/auth0";
import AppShell from "@/components/AppShell";
import PermissionSetsManager from "@/components/admin/PermissionSetsManager";
import { getActionPermissions, getAdminUsers, getPermissionSets } from "@/lib/api";

// SOC Phase A admin screen: manage permission sets (the additive grant bundles)
// and assign them to specific members on top of their profile. Org Admin (own
// org) or Super Admin, enforced server-side. Does NOT change roles.
export default async function AdminPermissionSetsPage() {
  const session = await auth0.getSession();
  if (!session) {
    redirect("/auth/login?returnTo=/admin/permission-sets");
  }

  let sets = [];
  let permissions = [];
  let users = [];
  let error = null;
  try {
    [sets, permissions, users] = await Promise.all([
      getPermissionSets(),
      getActionPermissions(),
      getAdminUsers({ limit: 200 }),
    ]);
  } catch (e) {
    error = e.status === 403 ? "forbidden" : e.message;
  }

  return (
    <AppShell user={session.user}>
      <div>
        <h1 className="text-3xl font-semibold text-navy">Permission Sets</h1>
        <p className="mt-1 text-sm text-text-muted">
          Additive grant bundles layered onto members
        </p>
      </div>

      {error === "forbidden" ? (
        <div className="mt-6 rounded-lg border border-border bg-bg-card p-10 text-center text-sm text-text-muted">
          You do not have permission to manage permission sets.
        </div>
      ) : error ? (
        <div className="mt-6 rounded-lg border border-border bg-bg-card p-10 text-center text-sm text-[#9B2335]">
          Could not load permission sets: {error}
        </div>
      ) : (
        <PermissionSetsManager
          initialSets={sets || []}
          permissions={permissions || []}
          users={users || []}
        />
      )}
    </AppShell>
  );
}
