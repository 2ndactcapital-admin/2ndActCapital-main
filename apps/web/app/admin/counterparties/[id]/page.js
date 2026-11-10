import { redirect } from "next/navigation";
import { getHostSession } from "@/lib/authServer";
import AppShell from "@/components/AppShell";
import { fetchAPI } from "@/lib/api";
import { CounterpartyDetail, SimulatedBanner } from "@/components/counterparties/Counterparties";

// CPSIM01 — one simulated counterparty: Connection, FIX, Bank details, Behaviour, Clock, Activity, Import / Export
// (placeholder until cpsim02). Tabs a type does not declare are disabled; every control is the envelope's.
export default async function CounterpartyPage({ params }) {
  const { id } = await params;
  const session = await getHostSession();
  if (!session) redirect(`/auth/login?returnTo=/admin/counterparties/${encodeURIComponent(id)}`);
  let detail = null;
  let requests = null;
  let error = null;
  try {
    [detail, requests] = await Promise.all([fetchAPI(`/api/v1/admin/counterparties/${encodeURIComponent(id)}`),
      fetchAPI(`/api/v1/admin/counterparties/${encodeURIComponent(id)}/requests`)]);
  } catch (e) {
    error = e.status === 403 ? "Counterparties are managed by Super Admins." : e.status === 404 ? "No such counterparty."
      : `Could not load the counterparty: ${e.message}`;
  }
  return (
    <AppShell user={session.user}>
      <p className="text-sm"><a className="text-navy underline" href="/admin/counterparties">Counterparties</a></p>
      <div className="mt-4 space-y-4">
        <SimulatedBanner />
        {error ? (
          <div className="rounded-[6px] border border-[#ece8dd] bg-white p-10 text-center text-sm text-text-muted">{error}</div>
        ) : (
          <CounterpartyDetail envelope={detail} requests={requests} />
        )}
      </div>
    </AppShell>
  );
}
