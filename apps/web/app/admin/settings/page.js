import { redirect } from "next/navigation";

import AppShell from "@/components/AppShell";
import OrgSettingsEditor from "@/components/admin/OrgSettingsEditor";
import { getHostSession } from "@/lib/authServer";
import { getMe } from "@/lib/api";
import { canPerm } from "@/lib/menuVisibility";
import { loadTheme } from "@/lib/themeServer";

export const dynamic = "force-dynamic";

// Sprint 24 — Org Admin screen. Scoped to the caller's own org: there is no
// org switcher here, and the org_id comes from the session-resolved theme
// payload, never from the URL or a request body.
//
// org_admin role reconciliation (Task 4): this page used to compare
// `theme.role` against the literal strings "org_admin" / "super_admin" —
// its own independent copy of the gate, separate from menuVisibility.mjs's
// (which every other org-admin-gated admin page defers to). It now calls the
// same `canPerm` the sidebar and /admin index already use, against the real
// `manage_org_settings` permission — one gate, not two that can drift.
export default async function OrgSettingsPage() {
  const session = await getHostSession();
  if (!session) {
    redirect("/auth/login?returnTo=/admin/settings");
  }

  const [theme, me] = await Promise.all([
    loadTheme(),
    getMe().catch(() => null),
  ]);
  const allowed = canPerm(me, "manage_org_settings");

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
