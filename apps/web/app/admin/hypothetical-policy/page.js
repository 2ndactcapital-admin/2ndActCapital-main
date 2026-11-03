import { redirect } from "next/navigation";
import { getHostSession } from "@/lib/authServer";
import AppShell from "@/components/AppShell";
import { fetchAPI } from "@/lib/api";
import { labelFor } from "@/lib/menuVisibility";
import HypotheticalPolicy from "@/components/admin/HypotheticalPolicy";

// REP10 — Settings → Hypothetical-performance policy: the firm's own words, who
// may see a backtest and its disclosure; each version approved by a second person.
export default async function HypotheticalPolicyPage() {
  const session = await getHostSession();
  if (!session) redirect("/auth/login?returnTo=/admin/hypothetical-policy");
  let envelope = null;
  let error = null;
  try {
    envelope = await fetchAPI("/api/v1/replication/hypothetical-policy");
  } catch (e) {
    error = e.status === 403 ? "The policy is open to staff with replication access." : e.message;
  }
  return (
    <AppShell user={session.user}>
      <h1 className="text-3xl font-semibold text-navy">{labelFor("/admin/hypothetical-policy")}</h1>
      <div className="mt-6">
        {error ? <div className="rounded-[6px] border border-[#ece8dd] bg-white p-10 text-center text-sm text-text-muted">{error}</div>
          : <HypotheticalPolicy initialEnvelope={envelope} />}
      </div>
    </AppShell>
  );
}
