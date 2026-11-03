import { redirect } from "next/navigation";
import { getHostSession } from "@/lib/authServer";
import AppShell from "@/components/AppShell";
import { fetchAPI } from "@/lib/api";
import { ReplicaDetail } from "@/components/replication/NotesWorkspace";

// REP11 — one note replica: legs (manual instructions), computed against stated,
// the payoff, pricing (an estimate) and the maker-checker approval.
export default async function ReplicaPage({ params }) {
  const { id } = await params;
  const session = await getHostSession();
  if (!session) redirect(`/auth/login?returnTo=/portfolio/replication/notes/replicas/${id}`);
  let envelope = null;
  let error = null;
  try {
    envelope = await fetchAPI(`/api/v1/replication/notes/replicas/${encodeURIComponent(id)}`);
  } catch (e) {
    error = e.status === 404 ? "This replica was not found." : e.status === 403
      ? "Replicas are open to staff with replication access." : e.message;
  }
  return (
    <AppShell user={session.user}>
      <a className="text-sm text-navy underline" href="/portfolio/replication/notes">All notes</a>
      <div className="mt-4">
        {error ? <div className="rounded-[6px] border border-[#ece8dd] bg-white p-10 text-center text-sm text-text-muted">{error}</div>
          : <ReplicaDetail initialEnvelope={envelope} />}
      </div>
    </AppShell>
  );
}
