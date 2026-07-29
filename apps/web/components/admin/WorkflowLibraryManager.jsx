"use client";

import { useState, useTransition } from "react";
import Link from "next/link";
import { createWorkflowAction } from "@/lib/workflowActions";

const CARD = {
  borderColor: "#ece8dd",
  boxShadow: "0 1px 3px rgba(0,0,0,0.06)",
};

function Card({ title, hint, children }) {
  return (
    <section className="rounded-lg border bg-bg-card p-5" style={CARD}>
      <h2 className="text-base font-semibold text-navy">{title}</h2>
      {hint && <p className="mt-1 text-sm text-text-muted">{hint}</p>}
      <div className="mt-4">{children}</div>
    </section>
  );
}

function inputClass() {
  return "rounded-md border border-border bg-bg-card px-3 py-2 text-sm text-text-primary outline-none focus:ring-2 focus:ring-navy";
}

function stepSummary(w) {
  const steps = w.step_count || 0;
  const approvals = w.approval_step_count || 0;
  if (steps === 0) return "No steps yet";
  const stepLabel = `${steps} step${steps === 1 ? "" : "s"}`;
  const approvalLabel = `${approvals} require approval`;
  return `${stepLabel}, ${approvalLabel}`;
}

export default function WorkflowLibraryManager({ initialWorkflows = [] }) {
  const [workflows, setWorkflows] = useState(initialWorkflows);
  const [pending, startTransition] = useTransition();
  const [error, setError] = useState(null);

  const [name, setName] = useState("");
  const [desc, setDesc] = useState("");

  function submitWorkflow() {
    if (!desc.trim()) {
      setError("A natural-language description is required.");
      return;
    }
    setError(null);
    startTransition(async () => {
      const res = await createWorkflowAction(name.trim(), desc.trim());
      if (res.ok) {
        setWorkflows(res.workflows || [...workflows, res.workflow]);
        setName("");
        setDesc("");
      } else {
        setError(res.error || "Could not create workflow.");
      }
    });
  }

  return (
    <div className="mt-6 space-y-6">
      {error && (
        <div className="rounded-md border border-border bg-bg-card px-4 py-2 text-sm text-[#9B2335]">
          {error}
        </div>
      )}
      {pending && (
        <p className="text-xs text-text-muted">
          Generating workflow from your description…
        </p>
      )}

      <Card
        title="New workflow"
        hint="Describe the process in plain language. It is converted into a governed, executable BPMN definition you can then refine in the editor."
      >
        <div className="flex flex-wrap items-end gap-3">
          <div className="flex flex-col">
            <label className="text-xs font-medium uppercase tracking-wide text-text-muted">
              Name
            </label>
            <input
              className={`mt-1 w-56 ${inputClass()}`}
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="Optional — derived if blank"
            />
          </div>
          <div className="flex min-w-0 flex-1 flex-col">
            <label className="text-xs font-medium uppercase tracking-wide text-text-muted">
              Description
            </label>
            <input
              className={`mt-1 w-full ${inputClass()}`}
              value={desc}
              onChange={(e) => setDesc(e.target.value)}
              placeholder="e.g. When a new deal arrives, pull its documents, have a partner approve it, then notify the member."
            />
          </div>
          <button
            type="button"
            onClick={submitWorkflow}
            disabled={pending}
            className="rounded-md bg-navy px-4 py-2 text-sm font-medium text-bg-app hover:opacity-90 disabled:opacity-60"
          >
            Create workflow
          </button>
        </div>
      </Card>

      <Card title="Library" hint="Open a workflow to view and edit its diagram.">
        {workflows.length === 0 ? (
          <p className="text-sm text-text-muted">No workflows yet.</p>
        ) : (
          <ul className="space-y-3">
            {workflows.map((w) => (
              <li
                key={w.id}
                className="rounded-md border border-border bg-bg-app"
              >
                <div className="flex flex-wrap items-center gap-3 p-3">
                  <div className="min-w-0">
                    <p className="text-sm font-medium text-text-primary">
                      {w.name}
                      {w.current_version_number != null && (
                        <span className="ml-2 inline-flex items-center rounded-full bg-gold-light px-2 py-0.5 text-[11px] font-medium text-navy">
                          v{w.current_version_number}
                        </span>
                      )}
                    </p>
                    {w.description && (
                      <p className="truncate text-xs text-text-muted">
                        {w.description}
                      </p>
                    )}
                  </div>
                  <span className="text-xs text-text-muted">
                    {stepSummary(w)}
                  </span>
                  <div className="ml-auto flex items-center gap-3">
                    <Link
                      href={`/admin/workflows/${w.id}/edit`}
                      className="text-sm font-medium text-navy hover:underline"
                    >
                      Open editor
                    </Link>
                  </div>
                </div>
              </li>
            ))}
          </ul>
        )}
      </Card>
    </div>
  );
}
