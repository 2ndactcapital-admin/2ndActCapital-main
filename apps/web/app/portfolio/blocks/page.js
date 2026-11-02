import { redirect } from "next/navigation";
import { getHostSession } from "@/lib/authServer";
import AppShell from "@/components/AppShell";
import { fetchAPI } from "@/lib/api";
import { labelFor } from "@/lib/menuVisibility";
import BlockList from "@/components/blocks/BlockList";

// REP9 — Portfolio → Blocks: approved account instructions aggregated into a
// block under the firm's allocation policy, executed manually by a broker of
// the adviser's choice, allocated at the average price and settled DVP. The
// list and the builder's candidates are fetched here so the first paint
// carries the server's real envelopes.
export default async function BlocksPage() {
  const session = await getHostSession();
  if (!session) redirect("/auth/login?returnTo=/portfolio/blocks");

  let envelope = null;
  let candidates = null;
  let error = null;
  try {
    envelope = await fetchAPI("/api/v1/execution/blocks", { searchParams: { status: "open" } });
  } catch (e) {
    error = e.status === 403 ? "forbidden" : e.message;
  }
  if (!error) {
    try {
      candidates = await fetchAPI("/api/v1/execution/blocks/candidates");
    } catch {
      candidates = null;
    }
  }
  return (
    <AppShell user={session.user}>
      <h1 className="text-3xl font-semibold text-navy">{labelFor("/portfolio/blocks")}</h1>
      <p className="mt-1 text-sm text-text-muted">
        One order for many accounts: the allocation is fixed before execution, the fills are booked at the average
        price, and each account settles to its own custodian. Always available as a printed order execution sheet.
      </p>
      <div className="mt-6">
        {error === "forbidden" ? (
          <div className="rounded-[6px] border border-[#ece8dd] bg-white p-10 text-center text-sm text-text-muted">
            Blocks are open to staff with block-trading access.
          </div>
        ) : error ? (
          <div className="rounded-[6px] border border-[#ece8dd] bg-white p-10 text-center text-sm text-text-muted">
            Could not load blocks: {error}
          </div>
        ) : (
          <BlockList initialEnvelope={envelope} candidates={candidates} />
        )}
      </div>
    </AppShell>
  );
}
