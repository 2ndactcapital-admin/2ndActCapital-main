import { redirect } from "next/navigation";
import { getHostSession } from "@/lib/authServer";
import AppShell from "@/components/AppShell";
import DocumentReviewManager from "@/components/admin/DocumentReviewManager";
import { getDocumentReview } from "@/lib/api";

// Chancery Phase 6 — the review / confirm screen for ONE ingested document.
// Server component: fetch the one-call review payload (extracted fields, source
// location, entity/record links), then hand it to the client Manager for inline
// correction, link editing, and the confirm action. Auth + org-scoping are
// enforced server-side by FastAPI (org_id travels in the JWT).
export default async function DocumentReviewPage({ params }) {
  const { documentId } = await params;
  const session = await getHostSession();
  if (!session) {
    redirect(`/auth/login?returnTo=/admin/document-review/${documentId}`);
  }

  let payload = null;
  let error = null;
  try {
    payload = await getDocumentReview(documentId);
  } catch (e) {
    if (e.status === 403) error = "forbidden";
    else if (e.status === 404) error = "notfound";
    else error = e.message;
  }

  return (
    <AppShell user={session.user}>
      <div>
        <h1 className="text-3xl font-semibold text-navy">Document Review</h1>
        <p className="mt-1 text-sm text-text-muted">
          Review extracted fields, correct values, and confirm this document
        </p>
      </div>

      {error === "forbidden" ? (
        <div className="mt-6 rounded-lg border border-border bg-bg-card p-10 text-center text-sm text-text-muted">
          You do not have permission to review this document.
        </div>
      ) : error === "notfound" ? (
        <div className="mt-6 rounded-lg border border-border bg-bg-card p-10 text-center text-sm text-text-muted">
          Document not found.
        </div>
      ) : error ? (
        <div className="mt-6 rounded-lg border border-border bg-bg-card p-10 text-center text-sm text-[#9B2335]">
          Could not load document: {error}
        </div>
      ) : (
        <DocumentReviewManager documentId={documentId} initialPayload={payload} />
      )}
    </AppShell>
  );
}
