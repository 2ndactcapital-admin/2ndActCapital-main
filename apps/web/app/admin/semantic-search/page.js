import { redirect } from "next/navigation";
import { getHostSession } from "@/lib/authServer";
import AppShell from "@/components/AppShell";
import SemanticSearch from "@/components/admin/SemanticSearch";

// Chancery Phase 11b — semantic (vector) search. A distinct, explicit action,
// separate from the Phase-9 keyword search: it embeds the query and ranks
// documents by meaning (pgvector cosine similarity), not substring matching.
// Auth, org-scoping, and per-user visibility are enforced server-side by FastAPI.
export default async function SemanticSearchPage() {
  const session = await getHostSession();
  if (!session) {
    redirect("/auth/login?returnTo=/admin/semantic-search");
  }

  return (
    <AppShell user={session.user}>
      <div>
        <h1 className="text-3xl font-semibold text-navy">Semantic Search</h1>
        <p className="mt-1 text-sm text-text-muted">
          Search documents by meaning, not keywords. Results are ranked by
          similarity and respect your document access.
        </p>
      </div>
      <SemanticSearch />
    </AppShell>
  );
}
