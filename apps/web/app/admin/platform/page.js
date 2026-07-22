import { redirect } from "next/navigation";

import AppShell from "@/components/AppShell";
import PlatformSettings from "@/components/admin/PlatformSettings";
import { auth0 } from "@/lib/auth0";
import { loadTheme } from "@/lib/theme";

// Sprint 24 — Super Admin only. Ripasso platform staff administer every
// tenant org from here, including onboarding new clients.
export default async function PlatformSettingsPage() {
  const session = await auth0.getSession();
  if (!session) {
    redirect("/auth/login?returnTo=/admin/platform");
  }

  const theme = await loadTheme();
  const isSuperAdmin = theme.role === "super_admin";

  return (
    <AppShell user={session.user}>
      <div>
        <h1 className="text-3xl font-semibold text-navy">Platform Settings</h1>
        <p className="mt-1 text-sm text-text-muted">
          Manage every client organization and its branding
        </p>
      </div>

      {isSuperAdmin ? (
        <PlatformSettings />
      ) : (
        <div className="mt-6 rounded-md border border-border bg-bg-card p-10 text-center text-sm text-text-muted">
          Platform settings are restricted to Super Admins.
        </div>
      )}
    </AppShell>
  );
}
