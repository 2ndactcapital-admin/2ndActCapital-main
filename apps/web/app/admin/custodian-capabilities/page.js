import { redirect } from "next/navigation";
import { getHostSession } from "@/lib/authServer";
import AppShell from "@/components/AppShell";
import { fetchAPI } from "@/lib/api";
import { labelFor } from "@/lib/menuVisibility";
import CapabilityMatrix from "@/components/custodians/CapabilityMatrix";

// REP2 — Billing & Custody → Custodian capabilities: what each custodian route
// can do, with evidence and as-of dates. Staff read; Super Admin edits.
export default async function CustodianCapabilitiesPage() {
  const session = await getHostSession();
  if (!session) redirect("/auth/login?returnTo=/admin/custodian-capabilities");

  let envelope = null;
  let error = null;
  try {
    envelope = await fetchAPI("/api/v1/custodians/capabilities");
  } catch (e) {
    error = e.status === 403 ? "forbidden" : e.message;
  }
  return (
    <AppShell user={session.user}>
      <h1 className="text-3xl font-semibold text-navy">{labelFor("/admin/custodian-capabilities")}</h1>
      <p className="mt-1 text-sm text-text-muted">
        What each custodian route can do, the evidence for it, and when it was recorded.
      </p>
      <div className="mt-6">
        {error === "forbidden" ? (
          <div className="rounded-[6px] border border-[#ece8dd] bg-white p-10 text-center text-sm text-text-muted">
            Custodian capabilities are open to staff.
          </div>
        ) : error ? (
          <div className="rounded-[6px] border border-[#ece8dd] bg-white p-10 text-center text-sm text-text-muted">
            Could not load capabilities: {error}
          </div>
        ) : (
          <CapabilityMatrix initialEnvelope={envelope} />
        )}
      </div>
    </AppShell>
  );
}
