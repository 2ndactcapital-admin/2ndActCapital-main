import { redirect } from "next/navigation";
import { auth0 } from "@/lib/auth0";
import AppShell from "@/components/AppShell";
import StageBar from "@/components/marketplace/StageBar";
import StatusBadge from "@/components/marketplace/StatusBadge";
import AllocationHeatMap from "@/components/visualizations/AllocationHeatMap";
import GapAnalysisBar from "@/components/visualizations/GapAnalysisBar";
import { getMyInvestments, getConfig, getEntityAllocations, getTaxonomy } from "@/lib/api";
import { formatCurrency, formatDate } from "@/lib/format";

const TABS = [
  { key: "investments", label: "Investments" },
  { key: "allocation", label: "Allocation Heat Map" },
  { key: "gap", label: "Gap Analysis" },
];

export default async function PortfolioPage({ searchParams }) {
  const session = await auth0.getSession();
  if (!session) {
    redirect("/auth/login?returnTo=/portfolio");
  }

  const { tab = "investments", entity_id } = await searchParams;

  let investments = [];
  let investmentStages = [];
  let allocations = [];
  let taxonomy = null;

  const fetches = [
    getMyInvestments(),
    getConfig("investment_stages"),
  ];

  if (tab === "allocation" || tab === "gap") {
    fetches.push(getEntityAllocations(entity_id || null));
    fetches.push(getTaxonomy());
  }

  const results = await Promise.allSettled(fetches);
  if (results[0].status === "fulfilled") investments = results[0].value || [];
  if (results[1].status === "fulfilled") investmentStages = results[1].value || [];
  if (results[2]?.status === "fulfilled") allocations = results[2].value || [];
  if (results[3]?.status === "fulfilled") taxonomy = results[3].value || null;

  // Stage grouping for investments tab
  const stageOrder = investmentStages.map((s) => s.config_key);
  const grouped = {};
  for (const inv of investments) {
    const key = inv.stage || "unknown";
    if (!grouped[key]) grouped[key] = [];
    grouped[key].push(inv);
  }
  const stageSummary = stageOrder
    .filter((k) => grouped[k]?.length > 0)
    .map((k) => ({ stage: k, count: grouped[k].length }));
  const stageKeys = [
    ...stageOrder,
    ...Object.keys(grouped).filter((k) => !stageOrder.includes(k)),
  ].filter((k) => grouped[k]);

  return (
    <AppShell user={session.user}>
      <div>
        <h1 className="text-3xl font-semibold text-navy">My Portfolio</h1>
        <p className="mt-1 text-sm text-text-muted">
          Your investment stage tracking and allocation analysis
        </p>
      </div>

      {/* Tab navigation */}
      <div className="mt-6 flex flex-wrap gap-1 border-b border-border">
        {TABS.map((t) => (
          <a
            key={t.key}
            href={`/portfolio?tab=${t.key}${entity_id ? `&entity_id=${entity_id}` : ""}`}
            className={`-mb-px border-b-2 px-4 py-2 text-sm font-medium transition-colors ${
              tab === t.key
                ? "border-navy text-navy"
                : "border-transparent text-text-muted hover:text-text-secondary"
            }`}
          >
            {t.label}
          </a>
        ))}
      </div>

      {/* Investments tab */}
      {tab === "investments" && (
        <div className="mt-6">
          {stageSummary.length > 0 && (
            <div className="mb-6">
              <StageBar stageSummary={stageSummary} stages={investmentStages} />
            </div>
          )}

          {investments.length === 0 ? (
            <div className="rounded-lg border border-border bg-bg-card p-10 text-center text-sm text-text-muted">
              You have not indicated interest in any deals yet.{" "}
              <a
                href="/marketplace"
                className="font-medium text-navy hover:underline"
              >
                Browse the marketplace →
              </a>
            </div>
          ) : (
            <div className="space-y-8">
              {stageKeys.map((stageKey) => {
                const stageConfig = investmentStages.find(
                  (s) => s.config_key === stageKey,
                );
                const stageLabel = stageConfig?.config_value || stageKey;
                const items = grouped[stageKey] || [];
                return (
                  <section key={stageKey}>
                    <h2 className="text-sm font-semibold uppercase tracking-wide text-text-muted">
                      {stageLabel}{" "}
                      <span className="ml-1 font-normal text-text-muted">
                        ({items.length})
                      </span>
                    </h2>
                    <div className="mt-3 grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
                      {items.map((inv) => (
                        <a
                          key={inv.id}
                          href={`/marketplace/${inv.deal_id}`}
                          className="block rounded-lg border border-border bg-bg-card p-4 hover:border-navy transition-colors"
                        >
                          <div className="flex items-start justify-between gap-2">
                            <p className="font-medium text-navy line-clamp-2">
                              {inv.deal_name || inv.deal_id}
                            </p>
                            {inv.deal_status && (
                              <StatusBadge status={inv.deal_status} />
                            )}
                          </div>
                          {inv.invested_amount != null && (
                            <p className="mt-2 text-sm text-text-secondary tabular-nums">
                              {formatCurrency(inv.invested_amount)}
                            </p>
                          )}
                          {inv.updated_at && (
                            <p className="mt-1 text-xs text-text-muted">
                              Updated {formatDate(inv.updated_at)}
                            </p>
                          )}
                        </a>
                      ))}
                    </div>
                  </section>
                );
              })}
            </div>
          )}
        </div>
      )}

      {/* Allocation Heat Map tab */}
      {tab === "allocation" && (
        <div className="mt-6">
          <AllocationHeatMap allocations={allocations} taxonomy={taxonomy} />
        </div>
      )}

      {/* Gap Analysis tab */}
      {tab === "gap" && (
        <div className="mt-6">
          <GapAnalysisBar allocations={allocations} />
        </div>
      )}
    </AppShell>
  );
}
