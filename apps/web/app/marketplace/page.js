import { redirect } from "next/navigation";
import { auth0 } from "@/lib/auth0";
import AppShell from "@/components/AppShell";
import DealCard from "@/components/marketplace/DealCard";
import MarketplaceFilters from "@/components/marketplace/MarketplaceFilters";
import { listDeals } from "@/lib/api";
import { isStaff } from "@/lib/roles";

export default async function MarketplacePage({ searchParams }) {
  const session = await auth0.getSession();
  if (!session) {
    redirect("/auth/login?returnTo=/marketplace");
  }
  const staff = isStaff(session.user);

  const params = (await searchParams) || {};
  const q = typeof params.q === "string" ? params.q : "";
  const assetClass = typeof params.asset_class === "string" ? params.asset_class : "";
  const status = typeof params.status === "string" ? params.status : "";
  const featured = params.featured === "1";

  let deals = [];
  let assetClasses = [];
  let loadError = null;
  try {
    // Filtered list for display, plus the full catalog to derive the asset-class
    // dropdown (values come from the data, never hardcoded).
    const [filtered, all] = await Promise.all([
      listDeals({
        search: q || undefined,
        asset_class: assetClass || undefined,
        status: status || undefined,
        is_featured: featured ? true : undefined,
        limit: 100,
      }),
      listDeals({ limit: 200 }),
    ]);
    deals = filtered;
    assetClasses = [
      ...new Set((all || []).map((d) => d.asset_class).filter(Boolean)),
    ].sort();
  } catch (error) {
    loadError = error.message;
  }

  return (
    <AppShell user={session.user}>
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-semibold text-navy">Marketplace</h1>
          <p className="mt-1 text-sm text-text-muted">
            Member investment opportunities
          </p>
        </div>
        {staff && (
          <a
            href="/marketplace/new"
            className="rounded-md bg-navy px-4 py-2 text-sm font-medium text-bg-app transition-opacity hover:opacity-90"
          >
            New Deal
          </a>
        )}
      </div>

      <div className="mt-6">
        <MarketplaceFilters assetClasses={assetClasses} staff={staff} />
      </div>

      <div className="mt-4">
        {loadError ? (
          <div className="rounded-lg border border-border bg-bg-card p-8 text-center text-sm text-text-muted">
            Could not load deals: {loadError}
          </div>
        ) : deals.length === 0 ? (
          <div className="rounded-lg border border-border bg-bg-card p-12 text-center">
            <p className="text-sm font-medium text-text-secondary">
              No deals match your filters
            </p>
          </div>
        ) : (
          <div className="grid gap-5 md:grid-cols-2">
            {deals.map((deal) => (
              <DealCard key={deal.id} deal={deal} />
            ))}
          </div>
        )}
      </div>
    </AppShell>
  );
}
