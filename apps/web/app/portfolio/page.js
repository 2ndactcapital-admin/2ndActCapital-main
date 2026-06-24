import { redirect } from "next/navigation";
import { auth0 } from "@/lib/auth0";
import AppShell from "@/components/AppShell";
import StageBar from "@/components/marketplace/StageBar";
import StatusBadge from "@/components/marketplace/StatusBadge";
import { getMyInvestments, getConfig } from "@/lib/api";
import { formatCurrency, formatDate } from "@/lib/format";

export default async function PortfolioPage() {
  const session = await auth0.getSession();
  if (!session) {
    redirect("/auth/login?returnTo=/portfolio");
  }

  let investments = [];
  let investmentStages = [];

  const [investmentsRes, stagesRes] = await Promise.allSettled([
    getMyInvestments(),
    getConfig("investment_stages"),
  ]);
  if (investmentsRes.status === "fulfilled")
    investments = investmentsRes.value || [];
  if (stagesRes.status === "fulfilled")
    investmentStages = stagesRes.value || [];

  // Group investments by stage in config order.
  const stageOrder = investmentStages.map((s) => s.config_key);
  const grouped = {};
  for (const inv of investments) {
    const key = inv.stage || "unknown";
    if (!grouped[key]) grouped[key] = [];
    grouped[key].push(inv);
  }

  // Build stage summary for StageBar (only stages with investments).
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
          Your investment stage tracking
        </p>
      </div>

      {stageSummary.length > 0 && (
        <div className="mt-6">
          <StageBar stageSummary={stageSummary} stages={investmentStages} />
        </div>
      )}

      {investments.length === 0 ? (
        <div className="mt-8 rounded-lg border border-border bg-bg-card p-10 text-center text-sm text-text-muted">
          You have not indicated interest in any deals yet.{" "}
          <a href="/marketplace" className="font-medium text-navy hover:underline">
            Browse the marketplace →
          </a>
        </div>
      ) : (
        <div className="mt-8 space-y-8">
          {stageKeys.map((stageKey) => {
            const stageConfig = investmentStages.find(
              (s) => s.config_key === stageKey,
            );
            const stageLabel =
              stageConfig?.config_value || stageKey;
            const items = grouped[stageKey] || [];
            return (
              <section key={stageKey}>
                <h2 className="text-sm font-semibold uppercase tracking-wide text-text-muted">
                  {stageLabel}{" "}
                  <span className="ml-1 text-text-muted font-normal">
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
    </AppShell>
  );
}
