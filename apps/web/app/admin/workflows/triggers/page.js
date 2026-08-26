import Link from "next/link";
import { redirect } from "next/navigation";
import { getHostSession } from "@/lib/authServer";
import AppShell from "@/components/AppShell";
import { getWorkflowTriggers, getWorkflows } from "@/lib/api";
import WorkflowTriggerScheduler from "@/components/admin/WorkflowTriggerScheduler";

// Workflow Manager — Triggers. Every trigger for the org (all orgs for a Super
// Admin), with its full recurrence, its firing history and — for a caller
// holding configure_workflow_triggers — create / edit / pause / delete.
//
// Since the scheduler sprint these rows really fire: a scheduled trigger is
// picked up by the Render cron tick and started through the same
// workflow_engine.start_workflow_run a manual start uses. Configuring one still
// only automates WHICH runs start; every started run honours each step's
// autonomy tier.
//
// THE TWO FETCHES ARE INDEPENDENT ON PURPOSE. `getWorkflows()` needs
// author_workflows, which a view-only trigger reader does not hold. Sharing one
// try/catch — as this page used to — meant a 403 from the workflow list wiped
// out the trigger list too, and the whole screen rendered as "forbidden" to
// someone who was in fact allowed to read it.
export default async function WorkflowTriggersPage() {
  const session = await getHostSession();
  if (!session) {
    redirect("/auth/login?returnTo=/admin/workflows/triggers");
  }

  let envelope = null;
  let error = null;
  try {
    envelope = await getWorkflowTriggers();
  } catch (e) {
    error = e.status === 403 ? "forbidden" : e.message;
  }

  // Only needed to populate the create form's workflow picker. A caller who
  // cannot author workflows simply gets an empty picker — and, not holding the
  // configure key either in any realistic grant, no create form at all.
  let workflows = [];
  try {
    workflows = await getWorkflows();
  } catch {
    workflows = [];
  }

  return (
    <AppShell user={session.user}>
      <div className="flex items-baseline justify-between">
        <div>
          <h1 className="text-3xl font-semibold text-navy">Triggers</h1>
          <p className="mt-1 text-sm text-text-muted">
            What starts a workflow, and when — schedules fire on their own
            timezone; each started run still pauses at every step that requires
            approval
          </p>
        </div>
        <Link href="/admin/workflows" className="text-sm text-gold hover:underline">
          ← Workflows
        </Link>
      </div>

      {error === "forbidden" ? (
        <div className="mt-6 rounded-lg border border-border bg-bg-card p-10 text-center text-sm text-text-muted">
          You do not have permission to view workflow triggers.
        </div>
      ) : error ? (
        <div className="mt-6 rounded-lg border border-border bg-bg-card p-10 text-center text-sm text-gold">
          Could not load triggers: {error}
        </div>
      ) : (
        <WorkflowTriggerScheduler
          initialRows={envelope?.rows || []}
          initialPermissions={envelope?.permissions || null}
          workflows={workflows}
        />
      )}
    </AppShell>
  );
}
