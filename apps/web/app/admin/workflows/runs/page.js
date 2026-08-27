import Link from "next/link";
import { redirect } from "next/navigation";
import { getHostSession } from "@/lib/authServer";
import AppShell from "@/components/AppShell";
import { getWorkflowRuns } from "@/lib/api";
import WorkflowRunHistory from "@/components/admin/WorkflowRunHistory";

// Workflow Manager — Run History. What actually RAN, as opposed to the Triggers
// screen's what is scheduled to run.
//
// Since the scheduler sprint most rows here are started by a schedule rather
// than a person, so "Started by" resolves the originating TRIGGER for those —
// read from the run's own stored context, which is where the tick stamps it.
//
// Gated server-side on view_workflow_runs. Org Admin sees their own org; Super
// Admin sees across all orgs. Read-only: there is no write endpoint on a run.
export default async function WorkflowRunsPage({ searchParams }) {
  const session = await getHostSession();
  if (!session) {
    redirect("/auth/login?returnTo=/admin/workflows/runs");
  }
  // A run id in the query seeds the selected row, so the per-run deep link
  // (/admin/workflows/runs/{id}, and any member_todo pointing at a run) opens
  // ONE screen with that run's pane already open, rather than a second
  // renderer of the same information.
  const { run: runParam } = (await searchParams) || {};

  let envelope = null;
  let error = null;
  try {
    // The first paint is seeded with the default window — all statuses, all
    // time — and the screen re-reads through /api/admin/workflow-runs whenever
    // a filter changes. The SERVER applies every filter; see the component.
    envelope = await getWorkflowRuns({ period: "all" });
  } catch (e) {
    error = e.status === 403 ? "forbidden" : e.message;
  }

  return (
    <AppShell user={session.user}>
      <div className="flex items-baseline justify-between">
        <div>
          <h1 className="text-3xl font-semibold text-navy">Run History</h1>
          <p className="mt-1 text-sm text-text-muted">
            Every workflow run and its step-by-step audit trail — what a
            schedule started, what a member started, and what held
          </p>
        </div>
        <Link
          href="/admin/workflows/triggers"
          className="text-sm text-gold hover:underline"
        >
          Triggers →
        </Link>
      </div>

      {error === "forbidden" ? (
        <div className="mt-6 rounded-lg border border-border bg-bg-card p-10 text-center text-sm text-text-muted">
          You do not have permission to view workflow runs.
        </div>
      ) : error ? (
        <div className="mt-6 rounded-lg border border-border bg-bg-card p-10 text-center text-sm text-gold">
          Could not load runs: {error}
        </div>
      ) : (
        <WorkflowRunHistory
          initialRows={envelope?.rows || []}
          initialPermissions={envelope?.permissions || null}
          initialFilters={envelope?.filters || null}
          initialSelectedId={runParam || null}
        />
      )}
    </AppShell>
  );
}
