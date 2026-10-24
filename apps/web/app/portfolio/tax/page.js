import { redirect } from "next/navigation";
import { getHostSession } from "@/lib/authServer";
import AppShell from "@/components/AppShell";
import { fetchAPI } from "@/lib/api";
import { labelFor } from "@/lib/menuVisibility";
import TaxWorkspace from "@/components/tax/TaxWorkspace";

// REP6 — Portfolio → Tax: the harvest dashboard across households,
// substitutions, profiles and declared outside trades. The dashboard's
// envelope is fetched here so the first paint carries the server's own flags.
export default async function TaxPage({ searchParams }) {
  const session = await getHostSession();
  if (!session) redirect("/auth/login?returnTo=/portfolio/tax");
  const sp = (await searchParams) || {};

  let envelope = null;
  let error = null;
  try {
    envelope = await fetchAPI("/api/v1/tax/overview");
  } catch (e) {
    error = e.status === 403 ? "forbidden" : e.message;
  }
  return (
    <AppShell user={session.user}>
      <h1 className="text-3xl font-semibold text-navy">{labelFor("/portfolio/tax")}</h1>
      <p className="mt-1 text-sm text-text-muted">
        Loss harvesting, household wash-sale control and the loss bank. Every harvest is a proposal a person approves; every tax figure is an estimate.
      </p>
      <div className="mt-6">
        {error === "forbidden" ? (
          <div className="rounded-[6px] border border-[#ece8dd] bg-white p-10 text-center text-sm text-text-muted">
            Tax is open to staff with tax access.
          </div>
        ) : error ? (
          <div className="rounded-[6px] border border-[#ece8dd] bg-white p-10 text-center text-sm text-text-muted">
            Could not load the tax dashboard: {error}
          </div>
        ) : (
          <TaxWorkspace initialEnvelope={envelope} initialView={typeof sp.view === "string" ? sp.view : "harvest"} />
        )}
      </div>
    </AppShell>
  );
}
