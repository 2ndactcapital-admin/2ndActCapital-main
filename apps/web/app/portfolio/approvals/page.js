import { redirect } from "next/navigation";
import { getHostSession } from "@/lib/authServer";
import AppShell from "@/components/AppShell";
import { fetchAPI } from "@/lib/api";
import { labelFor } from "@/lib/menuVisibility";
import BulkApprove from "@/components/approvals/BulkApprove";

// BULKAPPROVE1 — Approvals → Bulk approve: the pending maker-checker items a
// second person decides (authority & custody changes, held-away entries),
// many at once — each one still its own decision through the single path.
// The first tab's envelope is fetched here so the first paint carries the
// server's own flags; ?type= picks the tab (the server validates it).
export default async function ApprovalsPage({ searchParams }) {
  const sp = (await searchParams) || {};
  const session = await getHostSession();
  if (!session) redirect("/auth/login?returnTo=/portfolio/approvals");
  const type = typeof sp.type === "string" ? sp.type : "authority";

  let envelope = null;
  let error = null;
  try {
    envelope = await fetchAPI(`/api/v1/approvals/bulk?type=${encodeURIComponent(type)}`);
  } catch (e) {
    error = e.status === 403 ? "forbidden" : e.message;
  }
  return (
    <AppShell user={session.user}>
      <h1 className="text-3xl font-semibold text-navy">{labelFor("/portfolio/approvals")} — Bulk approve</h1>
      <p className="mt-1 text-sm text-text-muted">
        Changes waiting for a second person. Each selected item is decided on its own, under your name, by the same
        rules as approving it from its own page.
      </p>
      <div className="mt-6">
        {error === "forbidden" ? (
          <div className="rounded-[6px] border border-[#ece8dd] bg-white p-10 text-center text-sm text-text-muted">
            Approvals are open to staff with authority or held-away access.
          </div>
        ) : error ? (
          <div className="rounded-[6px] border border-[#ece8dd] bg-white p-10 text-center text-sm text-text-muted">
            Could not load approvals: {error}
          </div>
        ) : (
          <BulkApprove initialEnvelope={envelope} />
        )}
      </div>
    </AppShell>
  );
}
