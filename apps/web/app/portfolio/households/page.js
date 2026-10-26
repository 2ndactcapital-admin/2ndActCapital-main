import { redirect } from "next/navigation";
import { getHostSession } from "@/lib/authServer";
import AppShell from "@/components/AppShell";
import { fetchAPI } from "@/lib/api";

// REP7 — the households the caller may see (view_replication + household
// access), each linking to its X-ray tab.
export default async function HouseholdsPage() {
  const session = await getHostSession();
  if (!session) redirect("/auth/login?returnTo=/portfolio/households");
  let rows = [];
  let error = null;
  try {
    const env = await fetchAPI("/api/v1/xray/households");
    rows = Array.isArray(env?.rows) ? env.rows : [];
  } catch (e) {
    error = e.status === 403 ? "Households are open to staff with replication access." : e.message;
  }
  return (
    <AppShell user={session.user}>
      <h1 className="text-3xl font-semibold text-navy">Households</h1>
      <div className="mt-4 rounded-[6px] border border-[#ece8dd] bg-white p-6 text-sm">
        {error ? <p className="text-text-muted">{error}</p> : rows.length ? (
          <ul className="space-y-1">{rows.map((r) => (
            <li key={r.id}><a className="text-navy underline" href={`/portfolio/households/${r.id}?tab=xray`}>{r.name}</a>
              <span className="text-text-muted"> · {r.accounts} account(s){r.held_away ? `, ${r.held_away} held away` : ""}</span></li>
          ))}</ul>
        ) : <p className="text-text-muted">No household with accounts.</p>}
      </div>
    </AppShell>
  );
}
