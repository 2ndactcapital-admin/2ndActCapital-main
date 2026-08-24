import { redirect } from "next/navigation";
import { getHostSession } from "@/lib/authServer";
import AppShell from "@/components/AppShell";
import DocumentSearch from "@/components/admin/DocumentSearch";

// Chancery Phase 9 — the SEPARATE, secondary, explicit document search. This is a
// distinct action, not blended into the contextual Documents panel. It does basic
// metadata + extracted-text substring matching only — NOT semantic/vector search
// (that is Phase 11). Auth + org-scoping are enforced server-side by FastAPI.
export default async function DocumentSearchPage() {
  const session = await getHostSession();
  if (!session) {
    redirect("/auth/login?returnTo=/admin/document-search");
  }

  return (
    <AppShell user={session.user}>
      <div>
        <h1 className="text-3xl font-semibold text-navy">Document Search</h1>
        <p className="mt-1 text-sm text-text-muted">
          Basic search across filename, category, and extracted text. This is a
          simple keyword match — not semantic search.
        </p>
      </div>
      <DocumentSearch />
    </AppShell>
  );
}
