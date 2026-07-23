import { redirect } from "next/navigation";
import { auth0 } from "@/lib/auth0";
import AppShell from "@/components/AppShell";
import RestrictedAccessManager from "@/components/admin/RestrictedAccessManager";
import {
  getAdminUsers,
  getRestrictedAccounts,
  listEntities,
} from "@/lib/api";

// SOC Phase 4 admin screen: flag/unflag an entity as restricted and manage its
// allow-list. Super Admin only. This only POPULATES the restriction data the
// unified filter_restricted reads — it does NOT change any endpoint's
// visibility enforcement.
export default async function RestrictedAccessPage() {
  const session = await auth0.getSession();
  if (!session) {
    redirect("/auth/login?returnTo=/admin/restricted-access");
  }

  let restricted = [];
  let users = [];
  let entities = [];
  let error = null;
  try {
    const [data, u, e] = await Promise.all([
      getRestrictedAccounts(),
      getAdminUsers({ limit: 200 }),
      listEntities({ limit: 200 }),
    ]);
    restricted = data.restricted || [];
    users = u || [];
    entities = e || [];
  } catch (e) {
    error = e.status === 403 ? "forbidden" : e.message;
  }

  return (
    <AppShell user={session.user}>
      <div>
        <h1 className="text-3xl font-semibold text-navy">Restricted Access</h1>
        <p className="mt-1 text-sm text-text-muted">
          Flag an account as restricted and manage who may see it
        </p>
      </div>

      {error === "forbidden" ? (
        <div className="mt-6 rounded-lg border border-border bg-bg-card p-10 text-center text-sm text-text-muted">
          You do not have permission to manage restricted access.
        </div>
      ) : error ? (
        <div className="mt-6 rounded-lg border border-border bg-bg-card p-10 text-center text-sm text-[#9B2335]">
          Could not load restricted-access data: {error}
        </div>
      ) : (
        <RestrictedAccessManager
          initialRestricted={restricted}
          users={users}
          entities={entities}
        />
      )}
    </AppShell>
  );
}
