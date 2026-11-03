import { redirect } from "next/navigation";
import { getHostSession } from "@/lib/authServer";
import AppShell from "@/components/AppShell";
import { fetchAPI } from "@/lib/api";
import { labelFor } from "@/lib/menuVisibility";
import { NotesList } from "@/components/replication/NotesWorkspace";

// REP11 — Replication → Notes: every structured note with extracted 424B2
// terms, its replicability verdict and the plain reasons.
export default async function NotesPage() {
  const session = await getHostSession();
  if (!session) redirect("/auth/login?returnTo=/portfolio/replication/notes");
  let envelope = null;
  let error = null;
  try {
    envelope = await fetchAPI("/api/v1/replication/notes");
  } catch (e) {
    error = e.status === 403 ? "Notes are open to staff with replication access." : e.message;
  }
  return (
    <AppShell user={session.user}>
      <h1 className="text-3xl font-semibold text-navy">{labelFor("/portfolio/replication/notes")}</h1>
      <p className="mt-1 text-sm text-text-muted">What each note really is, from its public filing — and whether a transparent replica can be built.</p>
      <div className="mt-6">
        {error ? <div className="rounded-[6px] border border-[#ece8dd] bg-white p-10 text-center text-sm text-text-muted">{error}</div>
          : <NotesList initialEnvelope={envelope} />}
      </div>
    </AppShell>
  );
}
