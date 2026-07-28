import { redirect, notFound } from "next/navigation";
import { auth0 } from "@/lib/auth0";
import AppShell from "@/components/AppShell";
import EntityTypeBadge from "@/components/EntityTypeBadge";
import EntityGraphNavigator from "@/components/graph/EntityGraphNavigator";
import { fetchAPI } from "@/lib/api";

// Staff-facing ownership tree graph. The graph data is sourced through the
// staff visibility engine + restricted-access filter server-side; this page
// only resolves the focal entity's name for the header.
export default async function StaffOwnershipGraphPage({ params }) {
  const { id } = await params;

  const session = await auth0.getSession();
  if (!session) {
    redirect(`/auth/login?returnTo=/crm/${id}/ownership-graph`);
  }

  let entity = null;
  try {
    const full = await fetchAPI(`/api/v1/entities/${id}/full`);
    entity = full?.entity ?? null;
  } catch (err) {
    if (err?.status === 404) notFound();
    throw err;
  }

  return (
    <AppShell user={session.user}>
      <nav className="text-sm text-text-muted">
        <a href="/crm" className="hover:text-navy">
          CRM
        </a>
        <span className="mx-2">›</span>
        <a href={`/crm/${id}`} className="hover:text-navy">
          {entity?.display_name ?? "Entity"}
        </a>
        <span className="mx-2">›</span>
        <span className="text-text-secondary">Ownership Tree</span>
      </nav>

      <div className="mt-3 flex items-center gap-3">
        <h1 className="text-3xl font-semibold text-navy">
          {entity?.display_name ?? "Entity"} — Ownership Tree
        </h1>
        {entity?.entity_type && <EntityTypeBadge type={entity.entity_type} />}
      </div>

      <div className="mt-8">
        <EntityGraphNavigator
          apiBase={`/api/entities/${id}/ownership-graph`}
          title="Ownership & Beneficiary Tree"
          emptyMessage="You do not have visibility into this entity's ownership tree."
        />
      </div>
    </AppShell>
  );
}
