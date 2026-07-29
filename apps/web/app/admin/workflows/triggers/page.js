import Link from "next/link";
import { redirect } from "next/navigation";
import { auth0 } from "@/lib/auth0";
import AppShell from "@/components/AppShell";
import { getWorkflowTriggers } from "@/lib/api";
import { statusPillClass } from "@/lib/workflowFormat";

// Workflow Manager — Phase 4 Scheduler / Routine Viewer. Lists workflow
// triggers for the org (all orgs for a Super Admin): trigger_type, schedule,
// event, and whether active. READ-ONLY in this phase — no trigger fires
// anything autonomously yet (that remains a separate, gated Wave 4 effort).
export default async function WorkflowTriggersPage() {
  const session = await auth0.getSession();
  if (!session) {
    redirect("/auth/login?returnTo=/admin/workflows/triggers");
  }

  let triggers = [];
  let error = null;
  try {
    triggers = await getWorkflowTriggers();
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
      ) : triggers.length === 0 ? (
        <div className="mt-6 rounded-lg border border-border bg-bg-card p-10 text-center text-sm text-text-muted">
          No triggers configured.
        </div>
      ) : (
        <div className="mt-6 overflow-hidden rounded-lg border border-border bg-bg-card">
          <table className="w-full text-left text-sm">
            <thead className="border-b border-border text-xs uppercase tracking-wide text-text-muted">
              <tr>
                <th className="px-4 py-3 font-semibold">Workflow</th>
                <th className="px-4 py-3 font-semibold">Type</th>
                <th className="px-4 py-3 font-semibold">Schedule / Event</th>
                <th className="px-4 py-3 font-semibold">State</th>
              </tr>
            </thead>
            <tbody>
              {triggers.map((t) => (
                <tr key={t.id} className="border-b border-border last:border-0">
                  <td className="px-4 py-3 font-medium text-navy">{t.workflow_name}</td>
                  <td className="px-4 py-3 text-text-secondary">{t.trigger_type}</td>
                  <td className="px-4 py-3 text-text-secondary">
                    {t.trigger_type === "scheduled" ? (
                      <code className="text-xs">{t.schedule_cron || "—"}</code>
                    ) : t.trigger_type === "event" ? (
                      t.event_type || "—"
                    ) : (
                      "—"
                    )}
                  </td>
                  <td className="px-4 py-3">
                    <span className={statusPillClass(t.is_active ? "active" : "pending")}>
                      {t.is_active ? "active" : "inactive"}
                    </span>
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
