import { redirect } from "next/navigation";
import { auth0 } from "@/lib/auth0";
import AppShell from "@/components/AppShell";
import ProfilesManager from "@/components/admin/ProfilesManager";
import { getActionPermissions, getProfiles } from "@/lib/api";

// SOC Phase A admin screen: manage the org's profiles (personas) and each
// profile's base permission grants against the action registry. Org Admin (own
// org) or Super Admin, enforced server-side. Profiles are the additive
// permission layer read by services.profiles — this does NOT change roles.
export default async function AdminProfilesPage() {
  const session = await auth0.getSession();
  if (!session) {
    redirect("/auth/login?returnTo=/admin/profiles");
  }

  let profiles = [];
  let permissions = [];
  let error = null;
  try {
    [profiles, permissions] = await Promise.all([
      getProfiles(),
      getActionPermissions(),
    ]);
  } catch (e) {
    error = e.status === 403 ? "forbidden" : e.message;
  }

  return (
    <AppShell user={session.user}>
      <div>
        <h1 className="text-3xl font-semibold text-navy">Profiles</h1>
        <p className="mt-1 text-sm text-text-muted">
          Personas and their base permission grants
        </p>
      </div>

      {error === "forbidden" ? (
        <div className="mt-6 rounded-lg border border-border bg-bg-card p-10 text-center text-sm text-text-muted">
          You do not have permission to manage profiles.
        </div>
      ) : error ? (
        <div className="mt-6 rounded-lg border border-border bg-bg-card p-10 text-center text-sm text-[#9B2335]">
          Could not load profiles: {error}
        </div>
      ) : (
        <ProfilesManager
          initialProfiles={profiles || []}
          permissions={permissions || []}
        />
      )}
    </AppShell>
  );
}
