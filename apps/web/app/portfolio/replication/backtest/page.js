import { redirect } from "next/navigation";
import { getHostSession } from "@/lib/authServer";
import AppShell from "@/components/AppShell";
import { fetchAPI } from "@/lib/api";
import { labelFor } from "@/lib/menuVisibility";
import BacktestWorkspace from "@/components/replication/BacktestWorkspace";

// REP10 — Replication → Backtest: hypothetical performance of a replica and its
// harvesting against the fund, under the firm's hypothetical-performance policy.
export default async function BacktestPage() {
  const session = await getHostSession();
  if (!session) redirect("/auth/login?returnTo=/portfolio/replication/backtest");
  let envelope = null;
  let error = null;
  try {
    envelope = await fetchAPI("/api/v1/replication/backtests");
  } catch (e) {
    error = e.status === 403 ? "Backtests are open to staff with replication access." : e.message;
  }
  return (
    <AppShell user={session.user}>
      <h1 className="text-3xl font-semibold text-navy">{labelFor("/portfolio/replication/backtest")}</h1>
      <p className="mt-1 text-sm text-text-muted">Hypothetical performance — a replay through past prices, never an account's record.</p>
      <div className="mt-6">
        {error ? <div className="rounded-[6px] border border-[#ece8dd] bg-white p-10 text-center text-sm text-text-muted">{error}</div>
          : <BacktestWorkspace initialEnvelope={envelope} />}
      </div>
    </AppShell>
  );
}
