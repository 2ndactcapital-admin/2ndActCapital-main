import { redirect } from "next/navigation";
import { getHostSession } from "@/lib/authServer";
import AppShell from "@/components/AppShell";
import { fetchAPI } from "@/lib/api";
import { labelFor } from "@/lib/menuVisibility";
import { ProviderHome } from "@/components/replication/ModelCatalog";

export const dynamic = "force-dynamic";

// REP15 — Platform → Published models (Super Admin, acting in the Hollisworks platform org): the registered adviser
// entity, the publishing gate (OFF until the entity and its ADV are on file), and the subscribers by status, account
// count and model assets only.
export default async function ModelProviderPage() {
  const session = await getHostSession();
  if (!session) redirect("/auth/login?returnTo=/admin/model-provider");
  let initial = null;
  let subscribers = null;
  let error = null;
  try {
    [initial, subscribers] = await Promise.all([fetchAPI("/api/v1/model-provider"),
      fetchAPI("/api/v1/model-provider/subscribers")]);
  } catch (e) {
    error = e.status === 403 ? "Published models are managed by Hollisworks platform staff, in the platform org." : e.message;
  }
  return (
    <AppShell user={session.user}>
      <h1 className="font-serif text-3xl font-semibold text-navy">{labelFor("/admin/model-provider")}</h1>
      <p className="mt-1 text-sm text-text-muted">The platform as a sub-adviser. Nothing is published to, or subscribed by, a client
        firm until a registered adviser entity and its ADV are on file and the gate is opened.</p>
      <div className="mt-6">
        {error ? <div className="rounded-[6px] border border-[#ece8dd] bg-white p-10 text-center text-sm text-text-muted">{error}</div>
          : <ProviderHome initial={initial} initialSubscribers={subscribers} />}
      </div>
    </AppShell>
  );
}
