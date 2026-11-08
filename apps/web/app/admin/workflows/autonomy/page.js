import { redirect } from "next/navigation";
import { getHostSession } from "@/lib/authServer";
import AppShell from "@/components/AppShell";
import WorkflowAutonomy from "@/components/admin/WorkflowAutonomy";
import { fetchAPI } from "@/lib/api";
import { labelFor } from "@/lib/menuVisibility";

// S29B — Admin → Workflows → Autonomy: switches, limits, verbs by autonomy class, requests awaiting a decision, shadow
// runs awaiting review, kill switches. Readable by manage_org_settings | author_workflows | view_workflow_runs; every
// control is decided by the server's envelope, fetched here so the first paint carries it.
export const dynamic = "force-dynamic";

export default async function WorkflowAutonomyPage() {
  const session = await getHostSession();
  if (!session) redirect("/auth/login?returnTo=/admin/workflows/autonomy");
  let initial = null;
  let error = null;
  try {
    initial = await fetchAPI("/api/v1/admin/workflow-autonomy");
  } catch (e) {
    error = e.status === 403 ? "Workflow autonomy is open to administrators and workflow authors." : e.message;
  }
  return (
    <AppShell user={session.user}>
      <h1 className="font-serif text-3xl font-semibold text-navy">{labelFor("/admin/workflows/autonomy")}</h1>
      <p className="mt-1 text-sm text-text-muted">Which scheduled and event-started workflows may make their own changes,
        within which limits. Every unattended change is made on behalf of the trigger&apos;s owner and recorded. Money
        movement, custody and trades are never made unattended.</p>
      <div className="mt-6">
        {error ? <div className="rounded-[6px] border border-[#ece8dd] bg-white p-10 text-center text-sm text-text-muted">{error}</div>
          : <WorkflowAutonomy initial={initial} />}
      </div>
    </AppShell>
  );
}
