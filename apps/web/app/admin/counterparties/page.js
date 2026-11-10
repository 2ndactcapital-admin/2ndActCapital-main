import { redirect } from "next/navigation";
import { getHostSession } from "@/lib/authServer";
import AppShell from "@/components/AppShell";
import { fetchAPI } from "@/lib/api";
import { labelFor } from "@/lib/menuVisibility";
import CounterpartiesAdmin, { SimulatedBanner } from "@/components/counterparties/Counterparties";

// CPSIM01 — Super Admin → Counterparties: the registry of SIMULATED counterparties (custodians and banks the
// simulator plays). Every control comes from the server's envelope; the SIMULATED banner is on every state.
export default async function CounterpartiesPage() {
  const session = await getHostSession();
  if (!session) redirect("/auth/login?returnTo=/admin/counterparties");
  let list = null;
  let orgAccess = null;
  let error = null;
  try {
    [list, orgAccess] = await Promise.all([fetchAPI("/api/v1/admin/counterparties"),
      fetchAPI("/api/v1/admin/counterparties/org-access")]);
  } catch (e) {
    error = e.status === 403 ? "forbidden" : e.message;
  }
  return (
    <AppShell user={session.user}>
      <h1 className="text-3xl font-semibold text-navy">{labelFor("/admin/counterparties")}</h1>
      <p className="mt-1 text-sm text-text-muted">
        Simulated custodians and banks for testing and demonstration — each with its own address, key and business date.
      </p>
      <div className="mt-6">
        {error ? (
          <div className="space-y-4">
            <SimulatedBanner />
            <div className="rounded-[6px] border border-[#ece8dd] bg-white p-10 text-center text-sm text-text-muted">
              {error === "forbidden" ? "Counterparties are managed by Super Admins." : `Could not load counterparties: ${error}`}
            </div>
          </div>
        ) : (
          <CounterpartiesAdmin initialList={list} initialOrgAccess={orgAccess} />
        )}
      </div>
    </AppShell>
  );
}
