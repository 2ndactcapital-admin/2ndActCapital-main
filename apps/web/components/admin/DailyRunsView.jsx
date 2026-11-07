/**
 * AIBUDGET1 — one daily document job's runs: today's run (status, documents
 * done, tokens and cost per model, which budget stopped it), the last 30 days,
 * and "Run now".
 *
 * Pure (no server actions) so the node render tests mount it with the real
 * envelope shape from GET /admin/ai/daily-runs. Status labels, budget units
 * and the day boundary come from the envelope; "Run now" renders only when
 * permissions.can_write === true AND the job has an active config AND no run
 * is going (the server refuses all three cases anyway).
 */

import { BusyButton, BusyError } from "@/components/ui/Busy";
import { budgetKinds, canWrite, rowBudget, runUsageLines, statusLabel, stopText } from "@/lib/aiBudget.mjs";

const CARD = { borderColor: "var(--2a-border)", boxShadow: "0 1px 3px rgba(0,0,0,0.06)" };

function RunRow({ envelope, run }) {
  const kinds = budgetKinds(envelope);
  const stop = stopText(kinds, run);
  return (
    <tr className="border-t border-border align-top" data-run-id={run.id}>
      <td className="py-2 pr-3">{run.budget_day}</td>
      <td className="py-2 pr-3">{run.trigger_label}</td>
      <td className="py-2 pr-3">
        {statusLabel(envelope, run.status)}
        {stop && <div className="text-xs text-text-muted">{stop}</div>}
        {run.status === "stopped_error" && run.stop_detail && (
          <div className="text-xs text-text-muted">{run.stop_detail}</div>
        )}
      </td>
      <td className="py-2 pr-3">
        {run.documents_done}
        {run.documents_failed ? <span className="text-text-muted"> ({run.documents_failed} unreadable)</span> : null}
      </td>
      <td className="py-2 text-xs text-text-muted">
        {runUsageLines(run.usage).map((line) => (
          <div key={line}>{line}</div>
        ))}
      </td>
    </tr>
  );
}

export function DailyJobCard({ envelope, job, onRunNow, pending, busyAny = false }) {
  const kinds = budgetKinds(envelope);
  const running = (job.today_runs || []).some((r) => r.status === "running");
  const showRunNow = canWrite(envelope) && Boolean(job.active) && !running && typeof onRunNow === "function";
  const today = job.today_runs || [];
  return (
    <section className="mt-4 rounded-lg border bg-bg-card p-5" style={CARD} data-daily-job={job.task_key}>
      <div className="flex items-start justify-between gap-4">
        <div>
          <h3 className="text-base font-semibold text-navy">{job.label}</h3>
          <p className="mt-1 text-xs text-text-muted">{job.data_label}</p>
        </div>
        {showRunNow && (
          // CATCHALL1 busy — Run now (busy from the panel's useBusy)
          <BusyButton
            onClick={() => onRunNow(job.task_key)}
            busy={pending}
            disabled={busyAny}
            busyLabel="Starting…"
            className="rounded-md bg-navy px-4 py-2 text-sm font-medium text-white disabled:opacity-50"
          >
            Run now
          </BusyButton>
        )}
      </div>
      {job.active ? (
        <p className="mt-3 text-sm text-text-primary">
          {job.active.model_1} ({rowBudget(kinds, job.active, "model_1")}) and {job.active.model_2} (
          {rowBudget(kinds, job.active, "model_2")})
          {job.queue_pending !== null && job.queue_pending !== undefined && (
            <span className="text-text-muted">
              {" "}
              · {job.queue_pending >= job.queue_pending_capped_at ? `${job.queue_pending}+` : job.queue_pending}{" "}
              document(s) waiting
            </span>
          )}
        </p>
      ) : (
        <p className="mt-3 text-sm text-text-muted">
          No models are assigned to this job yet, so it does not run. Assign Model 1 and Model 2 below.
        </p>
      )}
      <div className="mt-4 text-xs font-medium uppercase tracking-wide text-text-muted">Today ({job.today} UTC)</div>
      {today.length === 0 ? (
        <p className="mt-1 text-sm text-text-muted">No run today yet.</p>
      ) : (
        <table className="mt-1 w-full text-left text-sm">
          <tbody>
            {today.map((r) => (
              <RunRow key={r.id} envelope={envelope} run={r} />
            ))}
          </tbody>
        </table>
      )}
      <div className="mt-4 text-xs font-medium uppercase tracking-wide text-text-muted">
        Last {envelope?.schedule?.history_days || 30} days
      </div>
      {(job.history || []).length === 0 ? (
        <p className="mt-1 text-sm text-text-muted">No runs yet.</p>
      ) : (
        <table className="mt-1 w-full text-left text-sm">
          <thead className="text-text-muted">
            <tr>
              <th className="py-1 pr-3 font-medium">Day</th>
              <th className="py-1 pr-3 font-medium">Started by</th>
              <th className="py-1 pr-3 font-medium">Status</th>
              <th className="py-1 pr-3 font-medium">Documents</th>
              <th className="py-1 font-medium">Tokens and cost per model</th>
            </tr>
          </thead>
          <tbody>
            {job.history.map((r) => (
              <RunRow key={r.id} envelope={envelope} run={r} />
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}

export default function DailyRunsView({ envelope, onRunNow, pendingTask, message, error = null }) {
  if (!envelope) return null;
  const never = envelope?.vocabularies?.never_scheduled || [];
  return (
    <div>
      <p className="text-sm text-text-muted">
        {envelope.day_boundary?.label}. Runs start after {envelope.schedule?.starts_after_utc} UTC each day
        {envelope.schedule?.enabled === false ? " (the daily schedule is turned off)" : ""}, read each job&apos;s
        queue in order, and stop when the queue is empty or a model&apos;s daily budget is used up; the next day
        resumes where it stopped.
        {never.length > 0 && ` Never run automatically: ${never.map((j) => j.label).join(", ")}.`}
      </p>
      {message && (
        <p className="mt-3 rounded-md px-3 py-2 text-sm" style={{ backgroundColor: "var(--2a-gold-light)", color: "var(--2a-navy)" }}>
          {message}
        </p>
      )}
      <BusyError error={error} className="mt-3 block text-sm text-[#9B2335]" />
      {(envelope.rows || []).map((job) => (
        <DailyJobCard
          key={job.task_key}
          envelope={envelope}
          job={job}
          onRunNow={onRunNow}
          pending={pendingTask === job.task_key}
          busyAny={pendingTask != null}
        />
      ))}
    </div>
  );
}
