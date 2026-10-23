import { redirect } from "next/navigation";
import { getHostSession } from "@/lib/authServer";
import AppShell from "@/components/AppShell";
import { fetchAPI } from "@/lib/api";
import { labelFor } from "@/lib/menuVisibility";
import AccountsList from "@/components/proposals/AccountsList";

// REP5 — Portfolio → Accounts: every open account with the model it follows,
// its drift, its last proposal and its open instructions. The first page is
// fetched here so the first paint carries the server's real envelope.
export default async function AccountsPage() {
  const session = await getHostSession();
  if (!session) redirect("/auth/login?returnTo=/portfolio/accounts");

  let envelope = null;
  let error = null;
  try {
    envelope = await fetchAPI("/api/v1/proposals/accounts");
  } catch (e) {
    error = e.status === 403 ? "forbidden" : e.message;
  }
  return (
    <AppShell user={session.user}>
      <h1 className="text-3xl font-semibold text-navy">{labelFor("/portfolio/accounts")}</h1>
      <p className="mt-1 text-sm text-text-muted">
        Accounts and the models they follow. Every rebalance is a proposal a second person approves — never a trade.
      </p>
      <div className="mt-6">
        {error === "forbidden" ? (
          <div className="rounded-[6px] border border-[#ece8dd] bg-white p-10 text-center text-sm text-text-muted">
            Accounts are open to staff with proposal access.
          </div>
        ) : error ? (
          <div className="rounded-[6px] border border-[#ece8dd] bg-white p-10 text-center text-sm text-text-muted">
            Could not load accounts: {error}
          </div>
        ) : (
          <AccountsList initialEnvelope={envelope} />
        )}
      </div>
    </AppShell>
  );
}
