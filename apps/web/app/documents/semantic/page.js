import { redirect } from "next/navigation";
import { getHostSession } from "@/lib/authServer";
import AppShell from "@/components/AppShell";
import SemanticSearch from "@/components/admin/SemanticSearch";

// BUGFIX1 — the Documents group's second tab (moved from /admin/semantic-search,
// which now redirects here). Items with no embedding yet are listed separately
// as "not yet indexed for meaning search" rather than silently omitted.
export default async function SemanticSearchPage() {
  const session = await getHostSession();
  if (!session) {
    redirect("/auth/login?returnTo=/documents/semantic");
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
