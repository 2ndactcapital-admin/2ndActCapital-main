import { redirect } from "next/navigation";
import { auth0 } from "@/lib/auth0";
import AppShell from "@/components/AppShell";
import UserManagement from "@/components/admin/UserManagement";
import { getAdminUsers, getAdminRoles, getProfiles } from "@/lib/api";

export default async function AdminUsersPage() {
  const session = await auth0.getSession();
  if (!session) {
    redirect("/auth/login?returnTo=/admin/users");
  }

  let users = [];
  let roles = [];
  let profiles = [];
  let error = null;
  try {
    [users, roles] = await Promise.all([
      getAdminUsers({ limit: 200 }),
      getAdminRoles(),
    ]);
    // SOC Phase A: the profile selector is additive. If the admin lacks profile-
    // management access it simply renders no options — never block the page.
    profiles = await getProfiles().catch(() => []);
  } catch (e) {
    error = e.status === 403 ? "forbidden" : e.message;
  }

  return (
    <AppShell user={session.user}>
      <div>
        <h1 className="text-3xl font-semibold text-navy">User Management</h1>
        <p className="mt-1 text-sm text-text-muted">
          Manage member access and roles
        </p>
      </div>

      {error === "forbidden" ? (
        <div className="mt-6 rounded-lg border border-border bg-bg-card p-10 text-center text-sm text-text-muted">
          You do not have permission to manage members.
        </div>
      ) : error ? (
        <div className="mt-6 rounded-lg border border-border bg-bg-card p-10 text-center text-sm text-[#9B2335]">
          Could not load users: {error}
        </div>
      ) : (
        <UserManagement
          initialUsers={users || []}
          roles={roles || []}
          profiles={profiles || []}
        />
      )}
    </AppShell>
  );
}
