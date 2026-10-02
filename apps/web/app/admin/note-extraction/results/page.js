import { redirect } from "next/navigation";
import { getHostSession } from "@/lib/authServer";
import AppShell from "@/components/AppShell";
import NoteExtractionResults from "@/components/admin/NoteExtractionResults";
import { getNoteExtractionRuns } from "@/lib/api";

// Evaluation + pilot results (noteextractb1). The harness REPORTS; Joe picks
// the ensemble in the picker. Super Admin enforced server-side by FastAPI.
export default async function NoteExtractionResultsPage() {
  const session = await getHostSession();
  if (!session) {
    redirect("/auth/login?returnTo=/admin/note-extraction/results");
  }

  let initial = null;
  let error = null;
  try {
    initial = await getNoteExtractionRuns({});
  } catch (e) {
    error = e.status === 403 ? "forbidden" : e.message;
  }

  return (
    <AppShell user={session.user}>
      <div>
        <h1 className="text-3xl font-semibold text-navy">Extraction Results</h1>
        <p className="mt-1 text-sm text-text-muted">
          Each candidate model measured against the gold set — accuracy, missed and invented values,
          cost and latency — and the pilot&apos;s disagreement and review rates.
        </p>
      </div>
      {error === "forbidden" ? (
        <div className="mt-6 rounded-lg border border-border bg-bg-card p-10 text-center text-sm text-text-muted">
          Super Admin access required.
        </div>
      ) : error ? (
        <div className="mt-6 rounded-lg border border-border bg-bg-card p-10 text-center text-sm text-text-muted">
          Could not load the runs: {error}
        </div>
      ) : (
        <NoteExtractionResults initial={initial} />
      )}
    </AppShell>
  );
}
