"use client";

import { useState, useTransition } from "react";
import { createEventTriggerAction } from "@/lib/workflowActions";
import { statusPillClass } from "@/lib/workflowFormat";

// Chancery Phase 7 — extends the existing Scheduler / Routine Viewer with the
// ability to configure a 'document_confirmed' event trigger pointing at a
// workflow. Read-only listing was Phase 4; the create form is the only write.
// Configuring a trigger only automates WHICH runs auto-start — every started
// run still honours each step's autonomy tier (Tier-1 pauses for approval).
export default function WorkflowTriggerScheduler({
  initialTriggers = [],
  workflows = [],
}) {
  const [triggers, setTriggers] = useState(initialTriggers);
  const [definitionId, setDefinitionId] = useState("");
  const [pending, startTransition] = useTransition();
  const [error, setError] = useState(null);
  const [notice, setNotice] = useState(null);

  function submit() {
    if (!definitionId) {
      setError("Choose a workflow to trigger.");
      return;
    }
    setError(null);
    setNotice(null);
    startTransition(async () => {
      const res = await createEventTriggerAction(definitionId);
      if (res.ok) {
        setTriggers(res.triggers);
        setDefinitionId("");
        setNotice("Event trigger created — it will start this workflow whenever a document is confirmed.");
      } else {
        setError(res.error || "Could not create the trigger.");
      }
    });
  }

  return (
    <>
      <section
        className="mt-6 rounded-lg border bg-bg-card p-5"
        style={{ borderColor: "#ece8dd", boxShadow: "0 1px 3px rgba(0,0,0,0.06)" }}
      >
        <h2 className="text-base font-semibold text-navy">
          Add an event trigger
        </h2>
        <p className="mt-1 text-sm text-text-muted">
          Start a workflow automatically when a document is confirmed. Each
          started run still pauses for approval at every step that requires it.
        </p>
        <div className="mt-4 flex flex-wrap items-end gap-3">
          <label className="flex flex-col gap-1 text-sm">
            <span className="text-xs uppercase tracking-wide text-text-muted">
              Workflow
            </span>
            <select
              value={definitionId}
              onChange={(e) => setDefinitionId(e.target.value)}
              className="min-w-[18rem] rounded-md border border-border bg-bg-card px-3 py-2 text-sm text-text-primary outline-none focus:ring-2 focus:ring-navy"
            >
              <option value="">Select a workflow…</option>
              {workflows.map((w) => (
                <option key={w.id} value={w.id}>
                  {w.name}
                </option>
              ))}
            </select>
          </label>
          <label className="flex flex-col gap-1 text-sm">
            <span className="text-xs uppercase tracking-wide text-text-muted">
              Event
            </span>
            <span className="rounded-md border border-border bg-bg-app px-3 py-2 text-sm text-text-secondary">
              Document confirmed
            </span>
          </label>
          <button
            type="button"
            onClick={submit}
            disabled={pending}
            className="rounded-md bg-navy px-4 py-2 text-sm font-medium text-white disabled:opacity-50"
          >
            {pending ? "Adding…" : "Add trigger"}
          </button>
        </div>
        {error && <p className="mt-3 text-sm text-error">{error}</p>}
        {notice && <p className="mt-3 text-sm text-success">{notice}</p>}
      </section>

      {triggers.length === 0 ? (
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
    </>
  );
}
