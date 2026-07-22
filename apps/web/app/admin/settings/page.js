import { redirect } from "next/navigation";

import AppShell from "@/components/AppShell";
import OrgSettingsEditor from "@/components/admin/OrgSettingsEditor";
import { auth0 } from "@/lib/auth0";
import { loadTheme } from "@/lib/theme";

// Sprint 24 — Org Admin screen. Scoped to the caller's own org: there is no
// org switcher here, and the org_id comes from the session-resolved theme
// payload, never from the URL or a request body.
export default async function OrgSettingsPage() {
  const session = await auth0.getSession();
  if (!session) {
    redirect("/auth/login?returnTo=/admin/settings");
  }

  const theme = await loadTheme();
  const role = theme.role;
  const allowed = role === "org_admin" || role === "super_admin";

  return (
    <AppShell user={session.user}>
      <div>
        <h1 className="text-3xl font-semibold text-navy">Organization Settings</h1>
        <p className="mt-1 text-sm text-text-muted">
          Branding, footer, locale and naming for {theme.org_name || "your organization"}
        </p>
      </div>

      {allowed ? (
        <OrgSettingsEditor
          orgId={theme.org_id}
          orgName={theme.org_name}
          canEdit
        />
      ) : (
        <div className="mt-6 rounded-md border border-border bg-bg-card p-10 text-center text-sm text-text-muted">
          Organization settings are restricted to Org Admins.
        </div>
      )}
    </AppShell>
  );
}
