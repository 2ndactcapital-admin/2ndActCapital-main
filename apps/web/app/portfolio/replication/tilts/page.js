import { redirect } from "next/navigation";
import { getHostSession } from "@/lib/authServer";
import AppShell from "@/components/AppShell";
import { fetchAPI } from "@/lib/api";
import { labelFor } from "@/lib/menuVisibility";
import TiltsWorkspace from "@/components/replication/TiltsWorkspace";

// REP10 — Replication → Tilts: one account's tilt sets ("the index plus my
// views", maker-checker) and its Completion. ?account=<id> picks the account;
// both envelopes are fetched here so the first paint carries the server's flags.
export default async function TiltsPage({ searchParams }) {
  const session = await getHostSession();
  if (!session) redirect("/auth/login?returnTo=/portfolio/replication/tilts");
  const sp = (await searchParams) || {};
  const account = typeof sp.account === "string" ? sp.account : "";
  let envelope = null;
  let completion = null;
  let error = null;
  if (account) {
    try {
      envelope = await fetchAPI(`/api/v1/replication/tilts/accounts/${encodeURIComponent(account)}`);
      completion = await fetchAPI(`/api/v1/replication/completion/accounts/${encodeURIComponent(account)}`);
    } catch (e) {
      error = e.status === 403 ? "Tilts are open to staff with replication access." : e.message;
    }
  }
  return (
    <AppShell user={session.user}>
      <h1 className="text-3xl font-semibold text-navy">{labelFor("/portfolio/replication/tilts")}</h1>
      <p className="mt-1 text-sm text-text-muted">The index plus the adviser's views, with the cost shown. Projected tracking error is hypothetical.</p>
      <form className="mt-4 flex gap-2 text-sm" method="get">
        <input className="rounded border border-[#E2E8F0] px-3 py-2" name="account" placeholder="Account id" defaultValue={account} />
        <button type="submit" className="rounded bg-navy px-3 py-1 text-white">Open</button>
      </form>
      <div className="mt-6">
        {!account ? <div className="rounded-[6px] border border-[#ece8dd] bg-white p-10 text-center text-sm text-text-muted">Open an account to see its tilts and completion.</div>
          : error ? <div className="rounded-[6px] border border-[#ece8dd] bg-white p-10 text-center text-sm text-text-muted">{error}</div>
          : <TiltsWorkspace accountId={account} initialEnvelope={envelope} initialCompletion={completion} />}
      </div>
    </AppShell>
  );
}
