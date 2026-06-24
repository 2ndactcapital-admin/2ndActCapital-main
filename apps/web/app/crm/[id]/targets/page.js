import { redirect, notFound } from "next/navigation";
import { auth0 } from "@/lib/auth0";
import AppShell from "@/components/AppShell";
import EntityTypeBadge from "@/components/EntityTypeBadge";
import TargetEditor from "@/components/crm/TargetEditor";
import AllocationHeatMap from "@/components/visualizations/AllocationHeatMap";
import GapAnalysisBar from "@/components/visualizations/GapAnalysisBar";
import { isStaff } from "@/lib/roles";
import { fetchAPI } from "@/lib/api";

const TABS = [
  { key: "targets", label: "Targets" },
  { key: "heat_map", label: "Allocation Heat Map" },
  { key: "gap", label: "Gap Analysis" },
];

const API_BASE =
  process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

export default async function EntityTargetsPage({ params, searchParams }) {
  const { id } = await params;
  const { tab = "targets" } = await searchParams;

  const session = await auth0.getSession();
  if (!session) {
    redirect(`/auth/login?returnTo=/crm/${id}/targets`);
  }
  if (!isStaff(session.user)) {
    redirect(`/crm/${id}`);
  }

  const [entityResult, taxonomyResult, targetsResult, allocResult] =
    await Promise.allSettled([
      fetchAPI(`/api/v1/entities/${id}`),
      fetchAPI("/api/v1/taxonomy"),
      fetchAPI(`/api/v1/portfolio/targets?entity_id=${id}`),
      fetchAPI(`/api/v1/portfolio/allocations?entity_id=${id}`),
    ]);

  if (entityResult.status === "rejected") {
    if (entityResult.reason?.status === 404) notFound();
    throw entityResult.reason;
  }

  const entityData = entityResult.value;
  const entity = entityData.entity ?? entityData;
  const taxonomy = taxonomyResult.status === "fulfilled" ? taxonomyResult.value : null;
  const targets = targetsResult.status === "fulfilled" ? targetsResult.value : [];
  const allocations = allocResult.status === "fulfilled" ? allocResult.value : [];

  return (
    <AppShell user={session.user}>
      {/* Breadcrumb */}
      <nav className="text-sm text-text-muted">
        <a href="/crm" className="hover:text-navy">
          CRM
        </a>
        <span className="mx-2">›</span>
        <a href={`/crm/${id}`} className="hover:text-navy">
          {entity.display_name}
        </a>
        <span className="mx-2">›</span>
        <span className="text-text-secondary">Target Allocations</span>
      </nav>

      {/* Header */}
      <div className="mt-3 flex items-center gap-3">
        <h1 className="text-3xl font-semibold text-navy">
          {entity.display_name}
        </h1>
        <EntityTypeBadge type={entity.entity_type} />
      </div>
      <p className="mt-1 text-sm text-text-muted">
        Target asset allocations — changes are stored historically
      </p>

      {/* Tab navigation */}
      <div className="mt-6 flex flex-wrap gap-1 border-b border-border">
        {TABS.map((t) => (
          <a
            key={t.key}
            href={`/crm/${id}/targets?tab=${t.key}`}
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

      <div className="mt-6">
        {/* Targets editor */}
        {tab === "targets" && (
          <TargetEditor
            entity={entity}
            taxonomy={taxonomy}
            initialTargets={targets}
            apiBase={API_BASE}
          />
        )}

        {/* Heat map */}
        {tab === "heat_map" && (
          <AllocationHeatMap allocations={allocations} taxonomy={taxonomy} />
        )}

        {/* Gap analysis */}
        {tab === "gap" && (
          <GapAnalysisBar allocations={allocations} />
        )}
      </div>
    </AppShell>
  );
}
