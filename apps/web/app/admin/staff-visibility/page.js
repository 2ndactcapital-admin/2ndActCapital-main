import { redirect } from "next/navigation";
import { auth0 } from "@/lib/auth0";
import AppShell from "@/components/AppShell";
import StaffVisibilityManager from "@/components/admin/StaffVisibilityManager";
import {
  getAdminUsers,
  getStaffAssignments,
  getStaffTeams,
  listEntities,
} from "@/lib/api";

// SOC Phase 2 admin screen: create teams, manage members, and assign a user or
// team to an entity. This only POPULATES the assignment data the staff-
// visibility resolver reads — it does NOT change any endpoint's visibility.
export default async function StaffVisibilityPage() {
  const session = await auth0.getSession();
  if (!session) {
    redirect("/auth/login?returnTo=/admin/staff-visibility");
  }

  let teams = [];
  let assignments = [];
  let users = [];
  let entities = [];
  let error = null;
  try {
    [teams, assignments, users, entities] = await Promise.all([
      getStaffTeams(),
      getStaffAssignments(),
      getAdminUsers({ limit: 200 }),
      listEntities({ limit: 200 }),
    ]);
  } catch (e) {
    error = e.status === 403 ? "forbidden" : e.message;
  }

  return (
    <AppShell user={session.user}>
      <div>
        <h1 className="text-3xl font-semibold text-navy">Staff Visibility</h1>
        <p className="mt-1 text-sm text-text-muted">
          Teams and entity assignments for staff visibility
        </p>
      </div>

      {error === "forbidden" ? (
        <div className="mt-6 rounded-lg border border-border bg-bg-card p-10 text-center text-sm text-text-muted">
          You do not have permission to manage staff visibility.
        </div>
      ) : error ? (
        <div className="mt-6 rounded-lg border border-border bg-bg-card p-10 text-center text-sm text-[#9B2335]">
          Could not load staff visibility data: {error}
        </div>
      ) : (
        <StaffVisibilityManager
          initialTeams={teams || []}
          initialAssignments={assignments || []}
          users={users || []}
          entities={entities || []}
        />
      )}
    </AppShell>
  );
}
