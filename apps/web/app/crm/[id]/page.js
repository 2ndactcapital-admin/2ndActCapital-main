import { redirect, notFound } from "next/navigation";
import { getHostSession } from "@/lib/authServer";
import AppShell from "@/components/AppShell";
import RecordPage from "@/components/record/RecordPage";
import { fetchAPI } from "@/lib/api";
import TeamPanel from "@/components/visibility/TeamPanel";
import { normalizeAsOf, todayIso } from "@/lib/recordTabs.mjs";

// NAV3 — the ONE Entities record page, at the existing CRM record URL (every old /crm/<id> and ?tab= link keeps
// working: an old tab key opens the new tab that contains it). The full record stays STAFF-ONLY (GET /full =
// internal); everything else is read by each tab, lazily, from its existing API.
export default async function EntityDetailPage({ params, searchParams }) {
  const { id } = await params;
  const sp = (await searchParams) || {};

  const session = await getHostSession();
  if (!session) {
    redirect(`/auth/login?returnTo=/crm/${id}`);
  }

  const today = todayIso();
  const asOf = normalizeAsOf(typeof sp.as_of === "string" ? sp.as_of : undefined, today);

  const [fullResult, graphResult, teamResult, peopleResult, teamsResult, meResult, hhResult, vocabResult] =
    await Promise.allSettled([
      fetchAPI(`/api/v1/entities/${id}/full`),
      fetchAPI(`/api/v1/entities/${id}/ownership-graph`),
      // VISIBILITY1 — the record's Team panel; refused → no panel
      fetchAPI(`/api/v1/staff-visibility/entities/${id}/team`),
      fetchAPI("/api/v1/admin/staff/people"),
      fetchAPI("/api/v1/admin/staff/teams"),
      fetchAPI("/api/v1/users/me"),
      // the record's primary household (manage_members); refused → no household context, no household tabs
      fetchAPI(`/api/v1/admin/entities/${id}/primary-household`),
      fetchAPI("/api/v1/crm/vocabularies"),
    ]);
  const ok = (r, dflt) => (r.status === "fulfilled" ? r.value : dflt);

  if (fullResult.status === "rejected") {
    if (fullResult.reason?.status === 404) notFound();
    throw fullResult.reason;
  }
  const full = fullResult.value;
  const graph = ok(graphResult, { root_id: id, nodes: [], edges: [] });
  const household = ok(hhResult, null)?.primary_household || null;
  const team = ok(teamResult, null);   // VISIBILITY1 — the record's Team panel; refused → no panel

  return (
    <AppShell user={session.user}>
      <RecordPage full={full} graph={graph} me={ok(meResult, null)} household={household}
        vocab={ok(vocabResult, null)?.vocabularies || null} asOf={asOf} today={today}
        teamPanel={team ? (
          <TeamPanel kind="entity" id={full.entity.id} initialEnvelope={team} people={ok(peopleResult, []) || []}
            teams={ok(teamsResult, []) || []} />
        ) : null} />
    </AppShell>
  );
}
