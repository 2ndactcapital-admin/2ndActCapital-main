import Link from "next/link";
import { redirect } from "next/navigation";
import { auth0 } from "@/lib/auth0";
import AppShell from "@/components/AppShell";
import { getWorkflowRuns } from "@/lib/api";
import { formatDateTime, statusPillClass, personLabel } from "@/lib/workflowFormat";

// Workflow Manager — Phase 4 Run Console. Lists the org's workflow runs (all
// orgs for a Super Admin) with status, who started them, and when. Each row
// links into the per-run step detail. Read-only; Org Admin (own org) or Super
// Admin, enforced server-side by the FastAPI gate.
export default async function WorkflowRunsPage() {
  const session = await auth0.getSession();
  if (!session) {
    redirect("/auth/login?returnTo=/admin/workflows/runs");
  }

  let runs = [];
  let error = null;
  try {
    runs = await getWorkflowRuns();
  } catch (e) {
    error = e.status === 403 ? "forbidden" : e.message;
  }

  return (
    <AppShell user={session.user}>
      <div className="flex items-baseline justify-between">
        <div>
          <h1 className="text-3xl font-semibold text-navy">Run Console</h1>
          <p className="mt-1 text-sm text-text-muted">
            Workflow runs and their step-by-step audit trail
          </p>
        </div>
        <Link href="/admin/workflows" className="text-sm text-gold hover:underline">
          ← Workflows
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
      ) : runs.length === 0 ? (
        <div className="mt-6 rounded-lg border border-border bg-bg-card p-10 text-center text-sm text-text-muted">
          No workflow runs yet.
        </div>
      ) : (
        <div className="mt-6 overflow-hidden rounded-lg border border-border bg-bg-card">
          <table className="w-full text-left text-sm">
            <thead className="border-b border-border text-xs uppercase tracking-wide text-text-muted">
              <tr>
                <th className="px-4 py-3 font-semibold">Workflow</th>
                <th className="px-4 py-3 font-semibold">Status</th>
                <th className="px-4 py-3 font-semibold">Started by</th>
                <th className="px-4 py-3 font-semibold">Started</th>
              </tr>
            </thead>
            <tbody>
              {runs.map((r) => (
                <tr key={r.id} className="border-b border-border last:border-0">
                  <td className="px-4 py-3">
                    <Link
                      href={`/admin/workflows/runs/${r.id}`}
                      className="font-medium text-navy hover:underline"
                    >
                      {r.workflow_name}
                    </Link>
                    <span className="ml-2 text-xs text-text-muted">
                      v{r.version_number}
                    </span>
                  </td>
                  <td className="px-4 py-3">
                    <span className={statusPillClass(r.status)}>{r.status}</span>
                  </td>
                  <td className="px-4 py-3 text-text-secondary">
                    {personLabel(r.started_by_name, r.started_by_email)}
                  </td>
                  <td className="px-4 py-3 text-text-muted">
                    {formatDateTime(r.started_at)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </AppShell>
  );
}
