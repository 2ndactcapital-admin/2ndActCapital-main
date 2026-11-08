import { redirect } from "next/navigation";
import { getHostSession } from "@/lib/authServer";
import AppShell from "@/components/AppShell";
import { fetchAPI } from "@/lib/api";
import { Section351Plan } from "@/components/replication/Section351";

// REP14 — one Section 351 plan: the contributing accounts and their tests, consents, the tax adviser's confirmation,
// the disclosure acknowledgement, readiness and control, basis records, the tracker and the transfer instructions.
export default async function Section351PlanPage({ params }) {
  const { id } = await params;
  const session = await getHostSession();
  if (!session) redirect(`/auth/login?returnTo=/portfolio/replication/section351/${id}`);
  let envelope = null;
  let error = null;
  try {
    envelope = await fetchAPI(`/api/v1/section351/plans/${encodeURIComponent(id)}`);
  } catch (e) {
    error = e.status === 404 ? "This plan was not found." : e.status === 403
      ? "Section 351 planning is open to staff with proposal access." : e.message;
  }
  return (
    <AppShell user={session.user}>
      <p className="text-sm"><a className="text-navy underline" href="/portfolio/replication/section351">Section 351</a></p>
      <div className="mt-4">
        {error ? <div className="rounded-[6px] border border-[#ece8dd] bg-white p-10 text-center text-sm text-text-muted">{error}</div>
          : <Section351Plan planId={id} initialEnvelope={envelope} />}
      </div>
    </AppShell>
  );
}
