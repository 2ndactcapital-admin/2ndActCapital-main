import Link from "next/link";
import { redirect } from "next/navigation";
import { auth0 } from "@/lib/auth0";
import AppShell from "@/components/AppShell";
import { getWorkflowRun } from "@/lib/api";
import { formatDateTime, statusPillClass, personLabel } from "@/lib/workflowFormat";

// Workflow Manager — Phase 4 Run Console drill-in. Shows one run's status plus
// each run-step's status / result / error_detail. Read-only.
export default async function WorkflowRunDetailPage({ params }) {
  const { runId } = await params;
  const session = await auth0.getSession();
  if (!session) {
    redirect(`/auth/login?returnTo=/admin/workflows/runs/${runId}`);
  }

  let data = null;
  let error = null;
  try {
    data = await getWorkflowRun(runId);
  } catch (e) {
    error =
      e.status === 403
        ? "forbidden"
        : e.status === 404
        ? "notfound"
        : e.message;
  }

  const run = data?.run;
  const steps = data?.steps || [];

  return (
    <AppShell user={session.user}>
      <div className="flex items-baseline justify-between">
        <h1 className="text-3xl font-semibold text-navy">
          {run ? run.workflow_name : "Run"}
        </h1>
        <Link href="/admin/workflows/runs" className="text-sm text-gold hover:underline">
          ← Run Console
        </Link>
      </div>

      {error === "forbidden" ? (
        <div className="mt-6 rounded-lg border border-border bg-bg-card p-10 text-center text-sm text-text-muted">
          You do not have permission to view this run.
        </div>
      ) : error === "notfound" ? (
        <div className="mt-6 rounded-lg border border-border bg-bg-card p-10 text-center text-sm text-text-muted">
          Run not found.
        </div>
      ) : error ? (
        <div className="mt-6 rounded-lg border border-border bg-bg-card p-10 text-center text-sm text-gold">
          Could not load run: {error}
        </div>
      ) : (
        <>
          <div className="mt-4 rounded-lg border border-border bg-bg-card p-5">
            <div className="flex flex-wrap items-center gap-x-8 gap-y-2 text-sm">
              <div>
                <span className="text-text-muted">Status: </span>
                <span className={statusPillClass(run.status)}>{run.status}</span>
              </div>
              <div>
                <span className="text-text-muted">Version: </span>
                <span className="text-text-secondary">v{run.version_number}</span>
              </div>
              <div>
                <span className="text-text-muted">Started by: </span>
                <span className="text-text-secondary">
                  {personLabel(run.started_by_name, run.started_by_email)}
                </span>
              </div>
              <div>
                <span className="text-text-muted">Started: </span>
                <span className="text-text-secondary">
                  {formatDateTime(run.started_at)}
                </span>
              </div>
              {run.completed_at && (
                <div>
                  <span className="text-text-muted">Completed: </span>
                  <span className="text-text-secondary">
                    {formatDateTime(run.completed_at)}
                  </span>
                </div>
              )}
            </div>
            {run.error_detail && (
              <p className="mt-3 rounded border border-gold px-3 py-2 text-sm text-gold">
                {run.error_detail}
              </p>
            )}
          </div>

          <h2 className="mt-6 text-base font-semibold text-navy">Steps</h2>
          <div className="mt-3 overflow-hidden rounded-lg border border-border bg-bg-card">
            <table className="w-full text-left text-sm">
              <thead className="border-b border-border text-xs uppercase tracking-wide text-text-muted">
                <tr>
                  <th className="px-4 py-3 font-semibold">Step</th>
                  <th className="px-4 py-3 font-semibold">Type</th>
                  <th className="px-4 py-3 font-semibold">Tier</th>
                  <th className="px-4 py-3 font-semibold">Status</th>
                  <th className="px-4 py-3 font-semibold">Detail</th>
                </tr>
              </thead>
              <tbody>
                {steps.map((s) => (
                  <tr key={s.id} className="border-b border-border align-top last:border-0">
                    <td className="px-4 py-3">
                      <span className="font-medium text-navy">
                        {s.display_name || s.step_key}
                      </span>
                      <span className="ml-2 text-xs text-text-muted">{s.step_key}</span>
                    </td>
                    <td className="px-4 py-3 text-text-secondary">{s.step_type}</td>
                    <td className="px-4 py-3 text-text-secondary">{s.autonomy_tier}</td>
                    <td className="px-4 py-3">
                      <span className={statusPillClass(s.status)}>{s.status}</span>
                    </td>
                    <td className="px-4 py-3 text-text-muted">
                      {s.error_detail ? (
                        <span className="text-gold">{s.error_detail}</span>
                      ) : s.result ? (
                        <code className="text-xs break-all">
                          {JSON.stringify(s.result)}
                        </code>
                      ) : (
                        "—"
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </AppShell>
  );
}
