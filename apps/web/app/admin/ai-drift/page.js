import { redirect } from "next/navigation";
import { getHostSession } from "@/lib/authServer";
import AppShell from "@/components/AppShell";
import { getAiDrift, getAiDriftTask, getAiDriftThresholds } from "@/lib/api";
import { labelFor } from "@/lib/menuVisibility";
import AiDriftMonitor from "@/components/admin/AiDriftMonitor";

export const dynamic = "force-dynamic";

// S28 — Super Admin → AI → Drift monitor: one row per AI task (24 h / 7 d against its 28-day baseline, a status per
// rule, open alerts); ?task=<task_type> shows that task's 90-day daily series, per-org findings and alerts. Org
// admins get the same page scoped to their own org by the SERVER (manage_org_settings), thresholds read-only. A 403
// renders the "not permitted" panel and no controls.
export default async function AiDriftPage({ searchParams }) {
  const session = await getHostSession();
  if (!session) redirect("/auth/login?returnTo=/admin/ai-drift");
  const search = (await searchParams) || {};
  const task = Array.isArray(search.task) ? search.task[0] : search.task;
  let overview = null;
  let thresholds = null;
  let detail = null;
  let error = null;
  try {
    [overview, thresholds, detail] = await Promise.all([
      task ? Promise.resolve(null) : getAiDrift(),
      getAiDriftThresholds(),
      task ? getAiDriftTask(task) : Promise.resolve(null),
    ]);
  } catch (e) {
    error = e.status === 403 ? "forbidden" : e.message;
  }
  return (
    <AppShell user={session.user}>
      <h1 className="text-3xl font-semibold text-navy">{labelFor("/admin/ai-drift")}</h1>
      <p className="mt-1 text-sm text-text-muted">
        Each AI task's recent behaviour against its own history: cost, latency, fallbacks, failures and the models that actually ran.
        {task ? <> <a className="ml-2 text-navy underline" href="/admin/ai-drift">All tasks</a></> : null}
      </p>
      <div className="mt-6">
        {error === "forbidden" ? (
          <div className="rounded-[6px] border border-[#ece8dd] bg-white p-10 text-center text-sm text-text-muted">
            The drift monitor is available to organization administrators.
          </div>
        ) : error ? (
          <div className="rounded-[6px] border border-[#ece8dd] bg-white p-10 text-center text-sm text-text-muted">
            Could not load the drift monitor: {error}
          </div>
        ) : (
          <AiDriftMonitor initialOverview={overview} initialThresholds={thresholds} initialDetail={detail} />
        )}
      </div>
    </AppShell>
  );
}
