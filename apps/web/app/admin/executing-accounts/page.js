import { redirect } from "next/navigation";
import { getHostSession } from "@/lib/authServer";
import AppShell from "@/components/AppShell";
import { fetchAPI } from "@/lib/api";
import { labelFor } from "@/lib/menuVisibility";
import ExecutionAccounts from "@/components/blocks/ExecutionAccounts";

// REP9 — Settings → Executing accounts: the firm's own omnibus / executing
// accounts at brokers (masked references), where blocks execute.
export default async function ExecutingAccountsPage() {
  const session = await getHostSession();
  if (!session) redirect("/auth/login?returnTo=/admin/executing-accounts");
  let envelope = null;
  let error = null;
  try {
    envelope = await fetchAPI("/api/v1/execution/blocks/settings/execution-accounts");
  } catch (e) {
    error = e.status === 403 ? "forbidden" : e.message;
  }
  return (
    <AppShell user={session.user}>
      <h1 className="text-3xl font-semibold text-navy">{labelFor("/admin/executing-accounts")}</h1>
      <div className="mt-6">
        {error === "forbidden" ? (
          <div className="rounded-[6px] border border-[#ece8dd] bg-white p-10 text-center text-sm text-text-muted">
            Executing accounts are open to staff with block-trading access.
          </div>
        ) : error ? (
          <div className="rounded-[6px] border border-[#ece8dd] bg-white p-10 text-center text-sm text-text-muted">
            Could not load executing accounts: {error}
          </div>
        ) : (
          <ExecutionAccounts initialEnvelope={envelope} />
        )}
      </div>
    </AppShell>
  );
}
