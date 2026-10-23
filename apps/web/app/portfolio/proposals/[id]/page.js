import { redirect } from "next/navigation";
import { getHostSession } from "@/lib/authServer";
import AppShell from "@/components/AppShell";
import { fetchAPI } from "@/lib/api";
import ProposalDetail from "@/components/proposals/ProposalDetail";

// REP5 — one rebalance proposal. The envelope (the server's flags, its actions
// list for this caller, and the approval task) is fetched server-side.
export default async function ProposalPage({ params }) {
  const { id } = await params;
  const session = await getHostSession();
  if (!session) redirect(`/auth/login?returnTo=/portfolio/proposals/${id}`);

  let envelope = null;
  let error = null;
  try {
    envelope = await fetchAPI(`/api/v1/proposals/${encodeURIComponent(id)}`);
  } catch (e) {
    error = e.status === 404 ? "This proposal was not found." : e.status === 403
      ? "Proposals are open to staff with proposal access." : e.message;
  }
  return (
    <AppShell user={session.user}>
      <p className="text-sm"><a className="text-navy underline" href="/portfolio/accounts">Accounts</a></p>
      <h1 className="mt-1 text-3xl font-semibold text-navy">Rebalance proposal</h1>
      <div className="mt-4">
        {error ? (
          <div className="rounded-[6px] border border-[#ece8dd] bg-white p-10 text-center text-sm text-text-muted">{error}</div>
        ) : (
          <ProposalDetail initialEnvelope={envelope} />
        )}
      </div>
    </AppShell>
  );
}
