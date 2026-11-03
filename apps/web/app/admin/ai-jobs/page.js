import { redirect } from "next/navigation";
import { getHostSession } from "@/lib/authServer";
import AppShell from "@/components/AppShell";
import EnsemblePanel from "@/components/admin/EnsemblePanel";
import DailyRunsPanel from "@/components/admin/DailyRunsPanel";
import { loadAiJobsAction, loadDailyRunsAction } from "@/lib/aiJobsActions";
import { loadEnsembleAction } from "@/lib/noteTermsQueueActions";
import { labelFor } from "@/lib/menuVisibility";

export const dynamic = "force-dynamic";

// AIBUDGET1 — Model Jobs. For each ensemble job (task keys and labels from the
// API, never hard-coded here): its Model 1 / Model 2 with each model's daily
// budget, chosen right after the model; then the daily document-reading runs.
// Super Admin is enforced SERVER-SIDE by FastAPI on every call this page makes;
// a 403 renders the "not permitted" panel and no controls.
export default async function AiJobsPage() {
  const session = await getHostSession();
  if (!session) {
    redirect("/auth/login?returnTo=/admin/ai-jobs");
  }

  const jobs = await loadAiJobsAction();
  const rows = jobs.ok ? jobs.jobs?.rows || [] : [];
  // Each picker loads separately: a LiteLLM outage (the catalog reads /model/info)
  // degrades only that panel.
  const pickers = await Promise.all(rows.map((j) => loadEnsembleAction(j.task_key)));
  const daily = jobs.ok ? await loadDailyRunsAction() : null;

  return (
    <AppShell user={session.user}>
      <div>
        <h1 className="text-3xl font-semibold text-navy">{labelFor("/admin/ai-jobs")}</h1>
        <p className="mt-1 text-sm text-text-muted">
          Which two models read each kind of document, and how much of each model a job may use per day.
        </p>
      </div>

      {!jobs.ok ? (
        <div className="mt-6 rounded-lg border border-border bg-bg-card p-10 text-center text-sm text-text-muted">
          {jobs.status === 403 ? "Super Admin access required." : `Could not load the jobs: ${jobs.error}`}
        </div>
      ) : (
        <>
          <h2 className="mt-8 text-xl font-semibold text-navy">Daily document reading</h2>
          <DailyRunsPanel initial={daily} />

          <h2 className="mt-10 text-xl font-semibold text-navy">Models and budgets by job</h2>
          {rows.map((job, i) => (
            <div key={job.task_key} className="mt-6">
              <h3 className="text-lg font-semibold text-navy">{job.label}</h3>
              <p className="text-xs text-text-muted">{job.data_label}</p>
              <EnsemblePanel taskKey={job.task_key} initial={pickers[i]} />
            </div>
          ))}
        </>
      )}
    </AppShell>
  );
}
