import { redirect } from "next/navigation";
import { getHostSession } from "@/lib/authServer";
import AppShell from "@/components/AppShell";
import { fetchAPI } from "@/lib/api";
import EntryView from "@/components/replication/EntryView";

// REP1 — one registry entry: Terms, Holdings, Sources. The envelope (with the
// server's can_write / can_approve / maker flags) is fetched server-side.
export default async function ReplicationEntryPage({ params }) {
  const { id } = await params;
  const session = await getHostSession();
  if (!session) redirect(`/auth/login?returnTo=/portfolio/replication/${id}`);

  let envelope = null;
  let error = null;
  try {
    envelope = await fetchAPI(`/api/v1/replication/registry/${encodeURIComponent(id)}`);
  } catch (e) {
    error = e.status === 404 ? "This fund is not in your registry." : e.status === 403
      ? "Replication is open to staff with replication access." : e.message;
  }
  return (
    <AppShell user={session.user}>
      <p className="text-sm"><a className="text-navy underline" href="/portfolio/replication">Replication</a></p>
      <h1 className="mt-1 text-3xl font-semibold text-navy">{envelope?.entry?.display_name || "Replication"}</h1>
      <div className="mt-4">
        {error ? (
          <div className="rounded-[6px] border border-[#ece8dd] bg-white p-10 text-center text-sm text-text-muted">{error}</div>
        ) : (
          <EntryView initialEnvelope={envelope} />
        )}
      </div>
    </AppShell>
  );
}
