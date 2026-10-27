import { redirect } from "next/navigation";
import { getHostSession } from "@/lib/authServer";
import AppShell from "@/components/AppShell";
import { fetchAPI } from "@/lib/api";
import AccountModelPanel from "@/components/proposals/AccountModelPanel";
import AccountTaxPanel from "@/components/tax/AccountTaxPanel";
import HeldAwayPanel from "@/components/heldaway/HeldAwayPanel";

// REP5 — one account: its model, settings, a change awaiting approval, the
// transition plan and its proposals. The envelope (flags, actions, registry
// offer, harvesting-engine options) is fetched server-side. REP6: the Tax
// panel's envelope too — a caller without view_tax gets a 403 and no panel.
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
  let tax = null;
  if (!error) {
    try {
      tax = await fetchAPI(`/api/v1/tax/accounts/${encodeURIComponent(id)}`);
    } catch {
      tax = null; // no view_tax (403) or no panel: the Tax panel is simply not shown
    }
  }
  // REP7 — a held-away account: its sources, imports, positions and lots (read-only; no trade control)
  let heldAway = null;
  if (!error && envelope?.account?.is_held_away === true) {
    try {
      heldAway = await fetchAPI(`/api/v1/held-away/accounts/${encodeURIComponent(id)}`);
    } catch {
      heldAway = null; // no view_held_away (403): the panel is simply not shown
    }
  }
  return (
    <AppShell user={session.user}>
      <p className="text-sm"><a className="text-navy underline" href="/portfolio/accounts">Accounts</a></p>
      <h1 className="mt-1 text-3xl font-semibold text-navy">Account</h1>
      <div className="mt-4">
        {error ? (
          <div className="rounded-[6px] border border-[#ece8dd] bg-white p-10 text-center text-sm text-text-muted">{error}</div>
        ) : (
          <div className="space-y-5">
            {heldAway ? <HeldAwayPanel initialEnvelope={heldAway} /> : null}
            <AccountModelPanel initialEnvelope={envelope} />
            {tax ? <AccountTaxPanel envelope={tax} /> : null}
          </div>
        )}
      </div>
    </AppShell>
  );
}
