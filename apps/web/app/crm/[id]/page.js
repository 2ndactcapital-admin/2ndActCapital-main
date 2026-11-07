import { redirect, notFound } from "next/navigation";
import { getHostSession } from "@/lib/authServer";
import AppShell from "@/components/AppShell";
import EntityTypeBadge from "@/components/EntityTypeBadge";
import EntityDetailTabs from "@/components/crm/EntityDetailTabs";
import { fetchAPI } from "@/lib/api";
import TeamPanel from "@/components/visibility/TeamPanel";

export default async function EntityDetailPage({ params }) {
  const { id } = await params;

  const session = await getHostSession();
  if (!session) {
    redirect(`/auth/login?returnTo=/crm/${id}`);
  }

  const [fullResult, graphResult, teamResult, peopleResult, teamsResult] = await Promise.allSettled([
    fetchAPI(`/api/v1/entities/${id}/full`),
    fetchAPI(`/api/v1/entities/${id}/ownership-graph`),
    // VISIBILITY1 — the record's Team panel; refused → no panel
    fetchAPI(`/api/v1/staff-visibility/entities/${id}/team`),
    fetchAPI("/api/v1/admin/staff/people"),
    fetchAPI("/api/v1/admin/staff/teams"),
  ]);
  const team = teamResult.status === "fulfilled" ? teamResult.value : null;
  const people = peopleResult.status === "fulfilled" ? peopleResult.value : [];
  const teams = teamsResult.status === "fulfilled" ? teamsResult.value : [];

  if (fullResult.status === "rejected") {
    if (fullResult.reason?.status === 404) notFound();
    throw fullResult.reason;
  }
  const full = fullResult.value;
  const graph =
    graphResult.status === "fulfilled"
      ? graphResult.value
      : { root_id: id, nodes: [], edges: [] };

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
      <a
        href={`/crm/${entity.id}/hierarchy`}
        className="text-sm text-gold hover:underline mt-1"
      >
        View ownership structure →
      </a>

      {team ? (
        <div className="mt-4"><TeamPanel kind="entity" id={entity.id} initialEnvelope={team} people={people || []} teams={teams || []} /></div>
      ) : null}

      <div className="mt-8">
        <EntityDetailTabs full={full} graph={graph} />
      </div>
    </AppShell>
  );
}
