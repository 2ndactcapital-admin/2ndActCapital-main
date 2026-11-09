import { redirect } from "next/navigation";
import { getHostSession } from "@/lib/authServer";
import AppShell from "@/components/AppShell";
import { fetchAPI } from "@/lib/api";
import TreasuryPanel from "@/components/treasury/TreasuryPanel";

// CASH01 — the family's own Treasury (cash coverage), read-only for a member. The API lists only the households the
// caller may open (a member: their own family's) and returns a member envelope with no write flag and the estimate
// labels from the disclosure library; another family's household is never listed and its id is a 404.
export default async function TreasuryPage({ searchParams }) {
  const sp = (await searchParams) || {};
  const session = await getHostSession();
  if (!session) redirect("/auth/login?returnTo=/treasury");
  let households = [];
  try {
    households = (await fetchAPI("/api/v1/cash/households"))?.households || [];
  } catch {
    households = [];
  }
  const wanted = typeof sp.household === "string" ? sp.household : households[0]?.id;
  let envelope = null;
  if (wanted && households.some((h) => h.id === wanted)) {
    try {
      envelope = await fetchAPI(`/api/v1/cash/households/${encodeURIComponent(wanted)}/treasury`);
    } catch {
      envelope = null;
    }
  }
  return (
    <AppShell user={session.user}>
      <h1 className="text-3xl font-semibold text-navy" style={{ fontFamily: "'Spectral', Georgia, serif" }}>Treasury</h1>
      {households.length > 1 ? (
        <nav className="mt-3 flex gap-4 border-b border-[#ece8dd] text-sm">
          {households.map((h) => (
            <a key={h.id} className={`pb-2 ${h.id === wanted ? "border-b-2 border-[#C5A880] text-navy" : "text-text-muted"}`}
              href={`/treasury?household=${encodeURIComponent(h.id)}`}>{h.name}</a>))}
        </nav>) : null}
      <div className="mt-4"><TreasuryPanel initialEnvelope={envelope} /></div>
    </AppShell>
  );
}
