import { redirect } from "next/navigation";
import { getHostSession } from "@/lib/authServer";
import AppShell from "@/components/AppShell";
import { fetchAPI } from "@/lib/api";
import { labelFor } from "@/lib/menuVisibility";
import { NewIssues } from "@/components/replication/NoteChecksWorkspace";

// REP12 — Replication → Notes → New issues: every new or restated note checked
// automatically against what it is made of (verdict, a DRAFT replica when
// eligible, the embedded-fee estimate where quoted), newest first.
export default async function NewIssuesPage() {
  const session = await getHostSession();
  if (!session) redirect("/auth/login?returnTo=/portfolio/replication/notes/new-issues");
  let envelope = null;
  let error = null;
  try {
    envelope = await fetchAPI("/api/v1/replication/notes/checks");
  } catch (e) {
    error = e.status === 403 ? "New issues are open to firm staff with replication access." : e.message;
  }
  return (
    <AppShell user={session.user}>
      <h1 className="text-3xl font-semibold text-navy">{labelFor("/portfolio/replication/notes/new-issues")}</h1>
      <p className="mt-1 text-sm text-text-muted">How expensive each new note looks against what it is made of — from its public filing.</p>
      <div className="mt-6">
        {error ? <div className="rounded-[6px] border border-[#ece8dd] bg-white p-10 text-center text-sm text-text-muted">{error}</div>
          : <NewIssues initialEnvelope={envelope} />}
      </div>
    </AppShell>
  );
}
