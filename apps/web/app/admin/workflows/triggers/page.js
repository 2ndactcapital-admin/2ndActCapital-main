import Link from "next/link";
import { redirect } from "next/navigation";
import { getHostSession } from "@/lib/authServer";
import AppShell from "@/components/AppShell";
import { getWorkflowTriggers, getWorkflows } from "@/lib/api";
import WorkflowTriggerScheduler from "@/components/admin/WorkflowTriggerScheduler";

// Workflow Manager — Scheduler / Routine Viewer. Lists workflow triggers for
// the org (all orgs for a Super Admin). Chancery Phase 7 adds the ability to
// configure a 'document_confirmed' event trigger — the first trigger type that
// actually fires. Configuring a trigger only automates WHICH runs auto-start;
// every started run still honours each step's autonomy tier.
export default async function WorkflowTriggersPage() {
  const session = await getHostSession();
  if (!session) {
    redirect("/auth/login?returnTo=/admin/workflows/triggers");
  }

  let triggers = [];
  let workflows = [];
  let error = null;
  try {
    triggers = await getWorkflowTriggers();
    workflows = await getWorkflows();
  } catch (e) {
    error = e.status === 403 ? "forbidden" : e.message;
  }

  return (
    <AppShell user={session.user}>
      <div className="flex items-baseline justify-between">
        <div>
          <h1 className="text-3xl font-semibold text-navy">Scheduler & Routines</h1>
          <p className="mt-1 text-sm text-text-muted">
            Configured triggers — schedules and events do not fire autonomously yet
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
          initialTriggers={triggers}
          workflows={workflows}
        />
      )}
    </AppShell>
  );
}
