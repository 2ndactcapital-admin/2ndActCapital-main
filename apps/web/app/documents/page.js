import { redirect } from "next/navigation";
import { getHostSession } from "@/lib/authServer";
import AppShell from "@/components/AppShell";
import DocumentSearch from "@/components/admin/DocumentSearch";

// BUGFIX1 — /documents IS the Documents group's first tab (Search), for every
// signed-in user, instead of redirecting into /admin/document-search. Results
// are filtered server-side by the caller's own visibility: the vault and the
// deal / entity / SPV attachments, each under its record's rule.
export default async function DocumentsPage() {
  const session = await getHostSession();
  if (!session) {
    redirect("/auth/login?returnTo=/documents");
  }

  return (
    <AppShell user={session.user}>
      <div>
        <h1 className="text-3xl font-semibold text-navy">Document Search</h1>
        <p className="mt-1 text-sm text-text-muted">
          Keyword search across file names, categories and extracted text — in the
          document vault and in the files attached to deals, entities and SPVs.
        </p>
      </div>
      <DocumentSearch />
    </AppShell>
  );
}
