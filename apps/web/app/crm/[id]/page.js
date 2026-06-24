import { redirect, notFound } from "next/navigation";
import { auth0 } from "@/lib/auth0";
import AppShell from "@/components/AppShell";
import EntityTypeBadge from "@/components/EntityTypeBadge";
import EntityDetailTabs from "@/components/crm/EntityDetailTabs";
import { fetchAPI } from "@/lib/api";

export default async function EntityDetailPage({ params }) {
  const { id } = await params;

  const session = await auth0.getSession();
  if (!session) {
    redirect(`/auth/login?returnTo=/crm/${id}`);
  }

  let full;
  try {
    full = await fetchAPI(`/api/v1/entities/${id}/full`);
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

  const entity = full.entity;

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

      <div className="mt-8">
        <EntityDetailTabs full={full} graph={graph} />
      </div>
    </AppShell>
  );
}
