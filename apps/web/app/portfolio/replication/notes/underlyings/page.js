import { redirect } from "next/navigation";
import { getHostSession } from "@/lib/authServer";
import AppShell from "@/components/AppShell";
import { fetchAPI } from "@/lib/api";
import { labelFor } from "@/lib/menuVisibility";
import { UnderlyingQueue } from "@/components/replication/NoteChecksWorkspace";

// REP12 — the Super Admin's underlying queue: confirm in one click (or several
// at once) the edges carrying a single registry proposal; each confirmation is
// a person's own, recorded with them. Nothing is ever confirmed automatically.
export default async function UnderlyingQueuePage() {
  const session = await getHostSession();
  if (!session) redirect("/auth/login?returnTo=/portfolio/replication/notes/underlyings");
  let envelope = null;
  let error = null;
  try {
    envelope = await fetchAPI("/api/v1/replication/notes/underlyings");
  } catch (e) {
    error = e.status === 403 ? "The underlying queue is open to the Super Admin." : e.message;
  }
  return (
    <AppShell user={session.user}>
      <h1 className="text-3xl font-semibold text-navy">{labelFor("/portfolio/replication/notes/underlyings")}</h1>
      <p className="mt-1 text-sm text-text-muted">A note can be replicated only once its underlying is confirmed by a person.</p>
      <div className="mt-6">
        {error ? <div className="rounded-[6px] border border-[#ece8dd] bg-white p-10 text-center text-sm text-text-muted">{error}</div>
          : <UnderlyingQueue initialEnvelope={envelope} />}
      </div>
    </AppShell>
  );
}
