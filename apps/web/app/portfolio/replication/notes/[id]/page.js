import { redirect } from "next/navigation";
import { getHostSession } from "@/lib/authServer";
import AppShell from "@/components/AppShell";
import { fetchAPI } from "@/lib/api";
import { NoteDetail } from "@/components/replication/NotesWorkspace";

// REP11 — one note: terms with the filing link, verdict and reasons, the payoff
// overlay, the replica builder, the differences.
export default async function NotePage({ params }) {
  const { id } = await params;
  const session = await getHostSession();
  if (!session) redirect(`/auth/login?returnTo=/portfolio/replication/notes/${id}`);
  let envelope = null;
  let error = null;
  try {
    envelope = await fetchAPI(`/api/v1/replication/notes/${encodeURIComponent(id)}`);
  } catch (e) {
    error = e.status === 404 ? "No extracted terms for this note." : e.status === 403
      ? "Notes are open to staff with replication access." : e.message;
  }
  return (
    <AppShell user={session.user}>
      <a className="text-sm text-navy underline" href="/portfolio/replication/notes">All notes</a>
      <div className="mt-4">
        {error ? <div className="rounded-[6px] border border-[#ece8dd] bg-white p-10 text-center text-sm text-text-muted">{error}</div>
          : <NoteDetail initialEnvelope={envelope} />}
      </div>
    </AppShell>
  );
}
