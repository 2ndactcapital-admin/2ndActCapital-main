import { redirect } from "next/navigation";
import { getHostSession } from "@/lib/authServer";
import AppShell from "@/components/AppShell";
import StaffVisibilityManager from "@/components/admin/StaffVisibilityManager";
import StaffVisibilityAdmin from "@/components/visibility/StaffVisibilityAdmin";
import {
  fetchAPI,
  getAdminUsers,
  getStaffAssignments,
  getStaffTeams,
  listEntities,
} from "@/lib/api";
import { labelFor } from "@/lib/menuVisibility";

// Admin → Staff visibility. VISIBILITY1: the preview ("what will each person
// see"), the org switch (org admin only, confirmed against the preview,
// audited), Unassigned families with bulk assign, People & managers — plus the
// SOC Phase 2 teams / assignments manager below. Assignments DECIDE what staff
// see once the org switches enforcement on.
export default async function StaffVisibilityPage() {
  const session = await getHostSession();
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
  // VISIBILITY1 — each envelope independently; a refused one (403) renders no section (fails closed).
  const [preview, unassigned, people] = await Promise.all([
    fetchAPI("/api/v1/admin/staff-visibility").catch(() => null),
    fetchAPI("/api/v1/admin/staff-visibility/unassigned").catch(() => null),
    fetchAPI("/api/v1/admin/staff/people").catch(() => null),
  ]);

  return (
    <AppShell user={session.user}>
      <div>
        <h1 className="text-3xl font-semibold text-navy">{labelFor("/admin/staff-visibility")}</h1>
        <p className="mt-1 text-sm text-text-muted">
          Which staff see which families — teams, assignments, managers, and the switch that enforces it
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
        <>
        <StaffVisibilityAdmin
          initialPreview={preview}
          initialUnassigned={unassigned}
          initialPeople={people || []}
          teams={teams || []}
          canManagePeople={Array.isArray(people)}
        />
        <StaffVisibilityManager
          initialTeams={teams || []}
          initialAssignments={assignments || []}
          users={users || []}
          entities={entities || []}
        />
        </>
      )}
    </AppShell>
  );
}
