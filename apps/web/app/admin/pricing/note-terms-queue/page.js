import { redirect } from "next/navigation";
import { auth0 } from "@/lib/auth0";
import AppShell from "@/components/AppShell";
import NoteTermsQueueManager from "@/components/admin/NoteTermsQueueManager";
import { getNoteTermsQueue } from "@/lib/api";

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
  const session = await auth0.getSession();
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
        <NoteTermsQueueManager initialPayload={payload} />
      )}
    </AppShell>
  );
}
