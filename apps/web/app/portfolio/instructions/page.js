import { redirect } from "next/navigation";
import { getHostSession } from "@/lib/authServer";
import AppShell from "@/components/AppShell";
import { fetchAPI } from "@/lib/api";
import { labelFor } from "@/lib/menuVisibility";
import InstructionList from "@/components/execution/InstructionList";

// REP2 — Portfolio → Instructions: execution instructions, the path for every
// trade a person carries out. The first page and the account picker are
// fetched here so the first paint carries the server's real envelope.
export default async function InstructionsPage() {
  const session = await getHostSession();
  if (!session) redirect("/auth/login?returnTo=/portfolio/instructions");

  let envelope = null;
  let error = null;
  try {
    envelope = await fetchAPI("/api/v1/execution/instructions", { searchParams: { status: "open" } });
  } catch (e) {
    error = e.status === 403 ? "forbidden" : e.message;
  }
  let accounts = [];
  if (!error) {
    try {
      accounts = (await fetchAPI("/api/v1/execution/accounts"))?.rows || [];
    } catch {
      accounts = [];
    }
  }
  return (
    <AppShell user={session.user}>
      <h1 className="text-3xl font-semibold text-navy">{labelFor("/portfolio/instructions")}</h1>
      <p className="mt-1 text-sm text-text-muted">
        Every trade a person carries out: approved by a second person, sent, filled, reconciled and closed.
      </p>
      <div className="mt-6">
        {error === "forbidden" ? (
          <div className="rounded-[6px] border border-[#ece8dd] bg-white p-10 text-center text-sm text-text-muted">
            Instructions are open to staff with execution access.
          </div>
        ) : error ? (
          <div className="rounded-[6px] border border-[#ece8dd] bg-white p-10 text-center text-sm text-text-muted">
            Could not load instructions: {error}
          </div>
        ) : (
          <InstructionList initialEnvelope={envelope} accounts={accounts} />
        )}
      </div>
    </AppShell>
  );
}
