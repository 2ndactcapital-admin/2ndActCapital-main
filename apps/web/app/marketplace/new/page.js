import { redirect } from "next/navigation";
import { auth0 } from "@/lib/auth0";
import AppShell from "@/components/AppShell";
import NewDealForm from "@/components/marketplace/NewDealForm";
import { getConfig } from "@/lib/api";
import { isStaff } from "@/lib/roles";

export default async function NewDealPage() {
  const session = await auth0.getSession();
  if (!session) {
    redirect("/auth/login?returnTo=/marketplace/new");
  }
  // Staff only — members are redirected back to the marketplace.
  if (!isStaff(session.user)) {
    redirect("/marketplace");
  }

  // Asset class options come from config when present (never hardcoded);
  // otherwise the form falls back to free-text inputs.
  let superClasses = [];
  let assetClasses = [];
  try {
    const config = await getConfig();
    superClasses = config
      .filter((c) => c.category === "asset_super_class")
      .map((c) => c.config_key);
    assetClasses = config
      .filter((c) => c.category === "asset_class")
      .map((c) => c.config_key);
  } catch {
    // Free-text fallback.
  }

  return (
    <AppShell user={session.user}>
      <nav className="text-sm text-text-muted">
        <a href="/marketplace" className="hover:text-navy">
          Marketplace
        </a>
        <span className="mx-2">›</span>
        <span className="text-text-secondary">New Deal</span>
      </nav>

      <h1 className="mt-3 text-2xl font-semibold text-navy">New Deal</h1>

      <div className="mt-8">
        <NewDealForm superClasses={superClasses} assetClasses={assetClasses} />
      </div>
    </AppShell>
  );
}
