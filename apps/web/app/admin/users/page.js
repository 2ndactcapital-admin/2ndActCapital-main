import { redirect } from "next/navigation";
import { getHostSession } from "@/lib/authServer";
import AppShell from "@/components/AppShell";
import UserManagement from "@/components/admin/UserManagement";
import { getAdminUsers, getAdminRoles, getProfiles } from "@/lib/api";
import { STATUS_FILTER_OPTIONS, listParams } from "@/lib/userManagementModel.mjs";

// USERMGMT1: the pending-sign-in to-do links here as ?status=pending, so the
// page honours that filter on first load (only the API's own status values).
export default async function AdminUsersPage({ searchParams }) {
  const session = await getHostSession();
  if (!session) {
    redirect("/auth/login?returnTo=/admin/users");
  }
  const params = (await searchParams) || {};
  const requested = typeof params.status === "string" ? params.status : "";
  const initialStatus = STATUS_FILTER_OPTIONS.some((o) => o.value === requested) ? requested : "";

  let users = [];
  let roles = [];
  let profiles = [];
  let error = null;
  try {
    [users, roles] = await Promise.all([
      getAdminUsers(listParams({ status: initialStatus })),
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
        <h1 className="text-3xl font-semibold text-navy">People &amp; Access</h1>
        <p className="mt-1 text-sm text-text-muted">
          Who can sign in, how they sign in, and what they can reach
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
          key={initialStatus}
          initialUsers={users || []}
          roles={roles || []}
          profiles={profiles || []}
          initialStatus={initialStatus}
        />
      )}
    </AppShell>
  );
}
