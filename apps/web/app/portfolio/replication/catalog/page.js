import { redirect } from "next/navigation";
import { getHostSession } from "@/lib/authServer";
import AppShell from "@/components/AppShell";
import { fetchAPI } from "@/lib/api";
import { labelFor } from "@/lib/menuVisibility";
import { CatalogHome } from "@/components/replication/ModelCatalog";

// REP15 — Replication → Model catalog: models a registered sub-adviser publishes, the sub-advisory agreement
// acknowledged by version, this firm's subscriptions (read-only, "Provided by …"), new versions to adopt (a second
// person approves) and copies. The envelope is fetched server-side so the first paint carries the server's own flags.
export default async function ModelCatalogPage() {
  const session = await getHostSession();
  if (!session) redirect("/auth/login?returnTo=/portfolio/replication/catalog");
  let initial = null;
  let error = null;
  try {
    initial = await fetchAPI("/api/v1/model-catalog");
  } catch (e) {
    error = e.status === 403 ? "The model catalog is open to staff with model access." : e.message;
  }
  return (
    <AppShell user={session.user}>
      <h1 className="font-serif text-3xl font-semibold text-navy">{labelFor("/portfolio/replication/catalog")}</h1>
      <p className="mt-1 text-sm text-text-muted">Models provided by a registered sub-adviser. A subscribed model is read-only here;
        adopting a new version is your firm's own decision, and nothing here trades.</p>
      <div className="mt-6">
        {error ? <div className="rounded-[6px] border border-[#ece8dd] bg-white p-10 text-center text-sm text-text-muted">{error}</div>
          : <CatalogHome initial={initial} />}
      </div>
    </AppShell>
  );
}
