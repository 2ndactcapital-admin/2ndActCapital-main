import { redirect, notFound } from "next/navigation";
import { getHostSession } from "@/lib/authServer";
import { isStaff } from "@/lib/roles";
import AppShell from "@/components/AppShell";
import EntityTypeBadge from "@/components/EntityTypeBadge";
import HierarchyBuilder from "@/components/crm/HierarchyBuilder";
import { fetchAPI } from "@/lib/api";

export default async function EntityHierarchyPage({ params }) {
  const { id } = await params;

  const session = await getHostSession();
  if (!session) {
    redirect(`/auth/login?returnTo=/crm/${id}/hierarchy`);
  }

  const staff = isStaff(session.user);

  const [fullResult, treeResult, lookthroughResult] = await Promise.allSettled([
    fetchAPI(`/api/v1/entities/${id}/full`),
    fetchAPI(`/api/v1/entities/${id}/tree`),
    fetchAPI(`/api/v1/entities/${id}/lookthrough`),
  ]);

  if (fullResult.status === "rejected") {
    if (fullResult.reason?.status === 404) notFound();
    throw fullResult.reason;
  }

  const full = fullResult.value;
  const tree =
    treeResult.status === "fulfilled"
      ? treeResult.value
      : { root_id: id, nodes: [], edges: [] };
  const lookthrough =
    lookthroughResult.status === "fulfilled"
      ? lookthroughResult.value
      : { allocations: [], total_value: null };

  const entity = full.entity;
  const lookthroughList = lookthroughResult.status === "fulfilled"
    ? (lookthroughResult.value?.lookthrough ?? [])
    : [];

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
        <span className="text-text-secondary">Ownership Hierarchy</span>
      </nav>

      {/* Header */}
      <div className="mt-3 flex items-center gap-3">
        <h1 className="text-3xl font-semibold text-navy">
          {entity.display_name} — Ownership Hierarchy
        </h1>
        <EntityTypeBadge type={entity.entity_type} />
      </div>

      <div className="mt-8">
        <HierarchyBuilder
          entityId={id}
          tree={tree}
          lookthrough={lookthroughList}
          staff={staff}
        />
      </div>
    </AppShell>
  );
}
