import { redirect } from "next/navigation";
import { getHostSession } from "@/lib/authServer";
import AppShell from "@/components/AppShell";
import { fetchAPI } from "@/lib/api";
import { labelFor } from "@/lib/menuVisibility";
import { Section351Home } from "@/components/replication/Section351";

// REP14 — Replication → Section 351: candidates (and why the others are not), the "run dry" thresholds, the
// sponsor profiles and the plans. Every envelope is fetched server-side so the first paint carries the server's own
// permission flags; ?plan=<id> (a task's link) opens that plan.
export default async function Section351Page({ searchParams }) {
  const session = await getHostSession();
  if (!session) redirect("/auth/login?returnTo=/portfolio/replication/section351");
  const sp = (await searchParams) || {};
  if (typeof sp.plan === "string" && sp.plan) redirect(`/portfolio/replication/section351/${encodeURIComponent(sp.plan)}`);
  let initial = null;
  let error = null;
  try {
    const [candidates, thresholds, profiles, plans] = await Promise.all([
      fetchAPI("/api/v1/section351/candidates"), fetchAPI("/api/v1/section351/thresholds"),
      fetchAPI("/api/v1/section351/sponsor-profiles"), fetchAPI("/api/v1/section351/plans")]);
    initial = { candidates, thresholds, profiles, plans };
  } catch (e) {
    error = e.status === 403 ? "Section 351 planning is open to staff with proposal access." : e.message;
  }
  return (
    <AppShell user={session.user}>
      <h1 className="font-serif text-3xl font-semibold text-navy">{labelFor("/portfolio/replication/section351")}</h1>
      <p className="mt-1 text-sm text-text-muted">When harvesting has run dry, appreciated replica accounts can be contributed in kind to a new
        ETF run by a third-party sponsor. Planning only — nothing here trades or moves a security.</p>
      <div className="mt-6">
        {error ? <div className="rounded-[6px] border border-[#ece8dd] bg-white p-10 text-center text-sm text-text-muted">{error}</div>
          : <Section351Home initial={initial} />}
      </div>
    </AppShell>
  );
}
