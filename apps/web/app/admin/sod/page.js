import { redirect } from "next/navigation";
import { getHostSession } from "@/lib/authServer";
import AppShell from "@/components/AppShell";
import SegregationOfDuties from "@/components/admin/SegregationOfDuties";
import { fetchAPI } from "@/lib/api";
import { labelFor } from "@/lib/menuVisibility";

// S30 — Admin → Segregation of duties: duties, rules (floor / org), conflicting duties, self-approvals, refused checks.
// Readable by manage_org_settings | manage_compliance | view_audit_log; editing is decided by the server's envelope,
// fetched here so the first paint carries it.
export const dynamic = "force-dynamic";

export default async function SegregationOfDutiesPage() {
  const session = await getHostSession();
  if (!session) redirect("/auth/login?returnTo=/admin/sod");
  let initial = null;
  let error = null;
  try {
    initial = await fetchAPI("/api/v1/admin/sod");
  } catch (e) {
    error = e.status === 403 ? "Segregation of duties is open to administrators and compliance." : e.message;
  }
  return (
    <AppShell user={session.user}>
      <h1 className="font-serif text-3xl font-semibold text-navy">{labelFor("/admin/sod")}</h1>
      <p className="mt-1 text-sm text-text-muted">Who may make and who may check each kind of item. The same person never
        approves what they made; the firm can add stricter rules on top of the platform floor.</p>
      <div className="mt-6">
        {error ? <div className="rounded-[6px] border border-[#ece8dd] bg-white p-10 text-center text-sm text-text-muted">{error}</div>
          : <SegregationOfDuties initial={initial} />}
      </div>
    </AppShell>
  );
}
