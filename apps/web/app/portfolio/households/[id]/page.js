import { redirect } from "next/navigation";
import { getHostSession } from "@/lib/authServer";
import AppShell from "@/components/AppShell";
import { fetchAPI } from "@/lib/api";
import XrayView from "@/components/xray/XrayView";
import AuthorityPanel from "@/components/authority/AuthorityPanel";

// REP7 — Household → tab "X-ray": the look-through of every fund the
// household holds (managed and held-away accounts), replicable funds and the
// one-click join request. The envelope is fetched server-side so the first
// paint carries the server's own flags; a caller without view_replication or
// without access to the household sees no X-ray.
export default async function HouseholdPage({ params, searchParams }) {
  const { id } = await params;
  const sp = (await searchParams) || {};
  const session = await getHostSession();
  if (!session) redirect(`/auth/login?returnTo=/portfolio/households/${id}`);
  const tab = typeof sp.tab === "string" ? sp.tab : "xray";

  let envelope = null;
  let error = null;
  try {
    envelope = await fetchAPI(`/api/v1/xray/households/${encodeURIComponent(id)}`);
  } catch (e) {
    error = e.status === 404 ? "This household was not found." : e.status === 403
      ? "The X-ray is open to staff with replication access." : e.message;
  }
  // REP7B — tab "Authority": the household's authority (read-only | advise | managed) and its accounts
  let authority = null;
  if (tab === "authority") {
    try {
      authority = await fetchAPI(`/api/v1/authority/households/${encodeURIComponent(id)}`);
    } catch {
      authority = null; // no view_authority (403): the tab says so
    }
  }
  const name = envelope?.xray?.household?.name || authority?.household?.name || "Household";
  return (
    <AppShell user={session.user}>
      <p className="text-sm"><a className="text-navy underline" href="/portfolio/households">Households</a></p>
      <h1 className="mt-1 text-3xl font-semibold text-navy">{name}</h1>
      <nav className="mt-3 flex gap-4 border-b border-[#ece8dd] text-sm">
        <a className={`pb-2 ${tab === "xray" ? "border-b-2 border-[#C5A880] text-navy" : "text-text-muted"}`} href={`/portfolio/households/${id}?tab=xray`}>X-ray</a>
        <a className={`pb-2 ${tab === "authority" ? "border-b-2 border-[#C5A880] text-navy" : "text-text-muted"}`} href={`/portfolio/households/${id}?tab=authority`}>Authority</a>
      </nav>
      <div className="mt-4">
        {tab === "authority" ? (authority ? <AuthorityPanel initialEnvelope={authority} kind="household" />
          : <div className="rounded-[6px] border border-[#ece8dd] bg-white p-10 text-center text-sm text-text-muted">The authority setting is open to staff with authority access.</div>)
          : error ? (
          <div className="rounded-[6px] border border-[#ece8dd] bg-white p-10 text-center text-sm text-text-muted">{error}</div>
        ) : <XrayView initialEnvelope={envelope} />}
      </div>
    </AppShell>
  );
}
