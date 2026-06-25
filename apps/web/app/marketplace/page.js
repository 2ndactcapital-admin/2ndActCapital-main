import { redirect } from "next/navigation";
import { auth0 } from "@/lib/auth0";
import AppShell from "@/components/AppShell";
import DealsTable from "@/components/marketplace/DealsTable";
import MarketplaceFilters from "@/components/marketplace/MarketplaceFilters";
import StageBar from "@/components/marketplace/StageBar";
import NewDealButton from "@/components/marketplace/NewDealButton";
import { listDeals, getTaxonomy, getConfig, getStageSummary } from "@/lib/api";
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
  const dealStage = typeof params.deal_stage === "string" ? params.deal_stage : "";
  const featured = params.featured === "1";

  let deals = [];
  let taxonomy = { super_classes: [] };
  let stages = [];
  let stageSummary = [];
  let loadError = null;

  try {
    const [filtered, tax, stagesRes, summaryRes] = await Promise.all([
      listDeals({
        search: q || undefined,
        asset_class: assetClass || undefined,
        deal_stage: dealStage || undefined,
        is_featured: featured ? true : undefined,
        limit: 100,
      }),
      getTaxonomy(),
      getConfig("deal_stages"),
      getStageSummary(),
    ]);
    deals = filtered;
    taxonomy = tax;
    stages = stagesRes || [];
    stageSummary = summaryRes || [];
  } catch (error) {
    loadError = error.message;
  }

  const stageLabels = Object.fromEntries(
    stages.map((s) => [s.config_key, s.config_value])
  );

  return (
    <AppShell user={session.user}>
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-semibold text-navy">Marketplace</h1>
          <p className="mt-1 text-sm text-text-muted">
            Member investment opportunities
          </p>
        </div>
        {staff && <NewDealButton />}
      </div>

      {stageSummary.length > 0 && (
        <div className="mt-5">
          <StageBar stageSummary={stageSummary} stages={stages} />
        </div>
      )}

      <div className="mt-4">
        <MarketplaceFilters taxonomy={taxonomy} staff={staff} stages={stages} />
      </div>

      <div className="mt-4">
        {loadError ? (
          <div className="rounded-lg border border-border bg-bg-card p-8 text-center text-sm text-text-muted">
            Could not load deals: {loadError}
          </div>
        ) : (
          <DealsTable deals={deals} staff={staff} stageLabels={stageLabels} />
        )}
      </div>
    </AppShell>
  );
}
