import { redirect } from "next/navigation";
import { getHostSession } from "@/lib/authServer";
import AppShell from "@/components/AppShell";
import { fetchAPI } from "@/lib/api";
import AccountModelPanel from "@/components/proposals/AccountModelPanel";

// REP5 — one account: its model, settings, a change awaiting approval, the
// transition plan and its proposals. The envelope (flags, actions, registry
// offer, harvesting-engine options) is fetched server-side.
export default async function AccountPage({ params }) {
  const { id } = await params;
  const session = await getHostSession();
  if (!session) redirect(`/auth/login?returnTo=/portfolio/accounts/${id}`);

  let envelope = null;
  let error = null;
  try {
    envelope = await fetchAPI(`/api/v1/accounts/${encodeURIComponent(id)}/model`);
  } catch (e) {
    error = e.status === 404 ? "This account was not found." : e.status === 403
      ? "Accounts are open to staff with proposal access." : e.message;
  }
  return (
    <AppShell user={session.user}>
      <p className="text-sm"><a className="text-navy underline" href="/portfolio/accounts">Accounts</a></p>
      <h1 className="mt-1 text-3xl font-semibold text-navy">Account</h1>
      <div className="mt-4">
        {error ? (
          <div className="rounded-[6px] border border-[#ece8dd] bg-white p-10 text-center text-sm text-text-muted">{error}</div>
        ) : (
          <AccountModelPanel initialEnvelope={envelope} />
        )}
      </div>
    </AppShell>
  );
}
