import { redirect } from "next/navigation";
import { getHostSession } from "@/lib/authServer";
import AppShell from "@/components/AppShell";
import NoteTermsQueueManager from "@/components/admin/NoteTermsQueueManager";
import EnsemblePanel from "@/components/admin/EnsemblePanel";
import { getNoteTermsQueue } from "@/lib/api";
import { loadEnsembleAction } from "@/lib/noteTermsQueueActions";

// The ensemble task this page governs — a task key, not a display label.
const ENSEMBLE_TASK_KEY = "note_terms_hazard";

// The note-terms review queue + STP trust policy. Server component: fetch the
// one-call queue payload (queued rows with their ensemble disagreements, source
// excerpts, and the active policies), then hand it to the client Manager.
//
// Super Admin is enforced SERVER-SIDE by FastAPI — the nav entry is gated too,
// but a hidden link is not a permission. A non-super-admin who types the URL
// gets a 403 from the API and the "not permitted" panel below.
//
// `.js`, not `.tsx`: apps/web has no TypeScript at all (128 .js/.jsx route and
// component files, zero .tsx). Matching the house convention was the point of
// the discovery step; introducing the repo's first TS file here would not.
export default async function NoteTermsQueuePage() {
  const session = await getHostSession();
  if (!session) {
    redirect("/auth/login?returnTo=/admin/pricing/note-terms-queue");
  }

  let payload = null;
  let error = null;
  try {
    payload = await getNoteTermsQueue();
  } catch (e) {
    if (e.status === 403) error = "forbidden";
    else error = e.message;
  }

  // Loaded separately so a LiteLLM outage (the catalog reads /model/info)
  // degrades only the Ensemble panel, never the review queue.
  const ensemble = error ? null : await loadEnsembleAction(ENSEMBLE_TASK_KEY);

  return (
    <AppShell user={session.user}>
      <div>
        <h1 className="text-3xl font-semibold text-navy">Note Terms Review</h1>
        <p className="mt-1 text-sm text-text-muted">
          Settle the hazard fields the two extraction readers disagreed on, and
          decide which issuers earn straight-through processing.
        </p>
      </div>

      {error === "forbidden" ? (
        <div className="mt-6 rounded-lg border border-border bg-bg-card p-10 text-center text-sm text-text-muted">
          Super Admin access required.
        </div>
      ) : error ? (
        <div className="mt-6 rounded-lg border border-border bg-bg-card p-10 text-center text-sm text-text-muted">
          Could not load the review queue: {error}
        </div>
      ) : (
        <>
          <NoteTermsQueueManager initialPayload={payload} />
          <EnsemblePanel taskKey={ENSEMBLE_TASK_KEY} initial={ensemble} />
        </>
      )}
    </AppShell>
  );
}
