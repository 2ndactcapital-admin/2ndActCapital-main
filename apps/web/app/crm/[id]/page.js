import { redirect, notFound } from "next/navigation";
import { auth0 } from "@/lib/auth0";
import AppShell from "@/components/AppShell";
import EntityTypeBadge from "@/components/EntityTypeBadge";
import EntityDetailsForm from "@/components/crm/EntityDetailsForm";
import AttributesSection from "@/components/crm/AttributesSection";
import OwnershipTree from "@/components/crm/OwnershipTree";
import { fetchAPI } from "@/lib/api";

export default async function EntityDetailPage({ params }) {
  const session = await auth0.getSession();
  if (!session) {
    const { id } = await params;
    redirect(`/auth/login?returnTo=/crm/${id}`);
  }

  const { id } = await params;

  let detail;
  try {
    detail = await fetchAPI(`/api/v1/entities/${id}`);
  } catch (error) {
    if (error.status === 404) notFound();
    throw error;
  }

  let graph = null;
  try {
    graph = await fetchAPI(`/api/v1/entities/${id}/ownership-graph`);
  } catch {
    graph = { root_id: id, nodes: [], edges: [] };
  }

  const entity = detail.entity;
  const hasOwners = (detail.owners || []).length > 0;
  const hasHoldings = (detail.holdings || []).length > 0;

  return (
    <AppShell user={session.user}>
      {/* Breadcrumb */}
      <nav className="text-sm text-text-muted">
        <a href="/crm" className="hover:text-navy">
          CRM
        </a>
        <span className="mx-2">›</span>
        <span className="text-text-secondary">{entity.display_name}</span>
      </nav>

      {/* Header */}
      <div className="mt-3 flex items-center gap-3">
        <h1 className="text-3xl font-semibold text-navy">{entity.display_name}</h1>
        <EntityTypeBadge type={entity.entity_type} />
      </div>

      {/* Two columns */}
      <div className="mt-8 grid gap-8 lg:grid-cols-2">
        {/* Left: editable details */}
        <div className="rounded-lg border border-border bg-bg-card p-6">
          <EntityDetailsForm entity={entity} />
        </div>

        {/* Right: ownership graph */}
        <div className="rounded-lg border border-border bg-bg-card p-6">
          <h2 className="text-base font-semibold text-navy">Ownership</h2>
          <div className="mt-4 space-y-6">
            <OwnershipTree
              graph={graph}
              rootId={id}
              direction="up"
              title="Owned by"
            />
            {hasHoldings && (
              <OwnershipTree
                graph={graph}
                rootId={id}
                direction="down"
                title="Owns"
              />
            )}
            {!hasOwners && !hasHoldings && (
              <p className="text-sm text-text-muted">
                No ownership relationships recorded.
              </p>
            )}
          </div>
        </div>
      </div>

      {/* Attributes */}
      <div className="mt-8 max-w-3xl">
        <AttributesSection entityId={id} attributes={detail.attributes || []} />
      </div>
    </AppShell>
  );
}
