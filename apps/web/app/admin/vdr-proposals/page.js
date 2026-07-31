import { redirect } from "next/navigation";
import { auth0 } from "@/lib/auth0";
import AppShell from "@/components/AppShell";
import VDRProposalsManager from "@/components/admin/VDRProposalsManager";

// Chancery Phase 10 — VDR intake + deal-proposal review.
//
// This is the entry point for marking a document-drop upload as a Virtual Data
// Room for a NEW deal (the `is_vdr` flag on the existing intake path) and for
// reviewing the aggregate-analysis proposals it produces. There was no existing
// Chancery upload UI to bolt the flag onto (intake had been backend/verify-only),
// and Phase 5's proposal-review UI was never built as a page either — so this is
// a single minimal admin surface reusing the existing intake endpoint. Approval
// creates a REAL deal via the same createDeal core the marketplace uses and
// links every document in the drop; NO deal is ever auto-created.
export default async function VDRProposalsPage() {
  const session = await auth0.getSession();
  if (!session) {
    redirect("/auth/login?returnTo=/admin/vdr-proposals");
  }

  return (
    <AppShell user={session.user}>
      <div>
        <h1 className="text-3xl font-semibold text-navy">VDR Deal Proposals</h1>
        <p className="mt-1 text-sm text-text-muted">
          Upload a data room as a VDR to have its documents read together into a
          proposed deal, then approve or decline. Nothing is created until you
          approve.
        </p>
      </div>
      <VDRProposalsManager />
    </AppShell>
  );
}
