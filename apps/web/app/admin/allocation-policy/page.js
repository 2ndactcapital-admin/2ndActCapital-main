import { redirect } from "next/navigation";
import { getHostSession } from "@/lib/authServer";
import AppShell from "@/components/AppShell";
import { fetchAPI } from "@/lib/api";
import { labelFor } from "@/lib/menuVisibility";
import AllocationPolicy from "@/components/blocks/AllocationPolicy";

// REP9 — Settings → Allocation policy: the firm's written aggregation and
// allocation policy, versioned, each version approved by a second person.
export default async function AllocationPolicyPage() {
  const session = await getHostSession();
  if (!session) redirect("/auth/login?returnTo=/admin/allocation-policy");
  let envelope = null;
  let error = null;
  try {
    envelope = await fetchAPI("/api/v1/execution/blocks/settings/policies");
  } catch (e) {
    error = e.status === 403 ? "forbidden" : e.message;
  }
  return (
    <AppShell user={session.user}>
      <h1 className="text-3xl font-semibold text-navy">{labelFor("/admin/allocation-policy")}</h1>
      <div className="mt-6">
        {error === "forbidden" ? (
          <div className="rounded-[6px] border border-[#ece8dd] bg-white p-10 text-center text-sm text-text-muted">
            The allocation policy is open to staff with block-trading access.
          </div>
        ) : error ? (
          <div className="rounded-[6px] border border-[#ece8dd] bg-white p-10 text-center text-sm text-text-muted">
            Could not load the allocation policy: {error}
          </div>
        ) : (
          <AllocationPolicy initialEnvelope={envelope} />
        )}
      </div>
    </AppShell>
  );
}
