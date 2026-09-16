import { redirect } from "next/navigation";

import AppShell from "@/components/AppShell";
import ModelCatalogManager from "@/components/admin/ModelCatalogManager";
import { getHostSession } from "@/lib/authServer";
import { loadTheme } from "@/lib/themeServer";

export const dynamic = "force-dynamic";

// LiteLLM Phase D2 — Super Admin only. The curated platform model list; an
// org's own subset of it lives on the Organization Settings screen instead
// (OrgSettingsEditor's "AI Models" section), gated on manage_org_settings —
// this page is a DIFFERENT, higher-privilege gate, mirroring /admin/platform.
export default async function ModelCatalogPage() {
  const session = await getHostSession();
  if (!session) {
    redirect("/auth/login?returnTo=/admin/model-catalog");
  }

  const theme = await loadTheme();
  const isSuperAdmin = theme.role === "super_admin";

  return (
    <AppShell user={session.user}>
      <div>
        <h1 className="text-3xl font-semibold text-navy">Model Catalog</h1>
        <p className="mt-1 text-sm text-text-muted">
          The models Hollisworks supports platform-wide. Client organizations
          choose from this list on their own Organization Settings screen.
        </p>
      </div>

      {isSuperAdmin ? (
        <ModelCatalogManager />
      ) : (
        <div className="mt-6 rounded-md border border-border bg-bg-card p-10 text-center text-sm text-text-muted">
          The model catalog is restricted to Super Admins.
        </div>
      )}
    </AppShell>
  );
}
