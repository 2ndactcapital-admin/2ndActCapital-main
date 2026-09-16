"use client";

/**
 * ModelTaskAssignment — LiteLLM Phase E, per-task model + effort.
 *
 * Reads services/extraction.MODEL_TASK_REGISTRY via GET .../settings/ai-tasks
 * (server-driven — a future 4th dial appears here with zero frontend
 * changes). Each row is one assignable AI task: a model dropdown (options =
 * the org's authorised catalog, from the server's own vocabularies.editable —
 * never a client-side default list, Rule 1) and, ONLY when the currently
 * resolved model reports supports_reasoning: true, an effort dropdown.
 *
 * `canWrite` comes from the server's own envelope (permissions.can_write),
 * never the `canEdit` prop threaded down from the parent screen — the same
 * discipline OrgModelSelector already established, for the same reason: the
 * two can legitimately disagree, and the server's own answer wins.
 *
 * Leaving a task's model unset keeps it resolving exactly as before this
 * screen existed (its dedicated key, falling back to ai.model.default) —
 * nothing changes until an admin deliberately assigns something.
 */

import { useEffect, useState } from "react";

const CARD = { borderColor: "#ece8dd", boxShadow: "0 1px 3px rgba(0,0,0,0.06)" };

export default function ModelTaskAssignment({ orgId }) {
  const [tasks, setTasks] = useState(null);
  const [models, setModels] = useState([]);
  const [effortLevels, setEffortLevels] = useState([]);
  const [permissions, setPermissions] = useState({ can_read: true, can_write: false });
  const [draft, setDraft] = useState({}); // { [key]: { model_id, effort } }
  const [error, setError] = useState(null);
  const [status, setStatus] = useState(null);
  const [savingKey, setSavingKey] = useState(null);

  useEffect(() => {
    if (!orgId) return;
    let active = true;
    load();
    return () => {
      active = false;
    };

    function load() {
      setTasks(null);
      setError(null);
      setStatus(null);
      setDraft({});
      fetch(`/api/orgs/${orgId}/settings/ai-tasks`, { cache: "no-store" })
        .then((res) => (res.ok ? res.json() : Promise.reject(res)))
        .then((data) => {
          if (!active) return;
          setTasks(data.tasks || []);
          setModels(data.vocabularies?.assignable_models || []);
          setEffortLevels(data.vocabularies?.effort_levels || []);
          // NO FALLBACK. can_write is false unless the server said otherwise.
          setPermissions(data.permissions || { can_read: true, can_write: false });
        })
        .catch(async (res) => {
          if (!active) return;
          const body = await res?.json?.().catch(() => ({}));
          setError(body?.error || "Could not load AI task assignments.");
        });
    }
  }, [orgId]);

  const canWrite = !!permissions?.can_write;

  function draftFor(task) {
    return (
      draft[task.key] ?? {
        model_id: task.assigned_model ?? "",
        effort: task.assigned_effort ?? "",
      }
    );
  }

  function setDraftField(task, field, value) {
    const base = draftFor(task);
    setDraft((d) => ({ ...d, [task.key]: { ...base, [field]: value } }));
    setStatus(null);
  }

  // The dropdown's OWN selection decides whether the effort control shows —
  // not the last-loaded task.supports_reasoning, which describes whichever
  // model is currently assigned, not whichever one is mid-edit.
  function supportsReasoning(task, d) {
    if (!d.model_id) return task.supports_reasoning;
    const picked = models.find((m) => m.model_id === d.model_id);
    return picked ? !!picked.supports_reasoning : false;
  }

  async function save(task) {
    const d = draftFor(task);
    setSavingKey(task.key);
    setError(null);
    try {
      const res = await fetch(
        `/api/orgs/${orgId}/settings/ai-tasks/${encodeURIComponent(task.key)}`,
        {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            model_id: d.model_id || null,
            effort: supportsReasoning(task, d) ? d.effort || null : null,
          }),
        },
      );
      const body = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(body.error || "Save failed");
      setTasks((prev) =>
        (prev || []).map((t) =>
          t.key === task.key
            ? {
                ...t,
                assigned_model: body.model_id,
                assigned_effort: body.effort,
                supports_reasoning: supportsReasoning(task, d),
              }
            : t,
        ),
      );
      setDraft((dr) => {
        const next = { ...dr };
        delete next[task.key];
        return next;
      });
      setStatus(`Saved "${task.label}".`);
    } catch (e) {
      setError(e.message);
    } finally {
      setSavingKey(null);
    }
  }

  if (!tasks) return null;

  return (
    <section className="mt-8">
      <h2
        className="text-xs font-semibold uppercase"
        style={{ letterSpacing: "0.22em", color: "var(--2a-gold)" }}
      >
        AI Task Assignment
      </h2>
      <p className="mt-2 text-xs text-text-muted">
        Which model handles each AI task, and how hard it works. Leaving a
        task unassigned keeps it resolving exactly as before.
      </p>

      <div className="mt-3 overflow-hidden rounded-md border border-border bg-bg-card" style={CARD}>
        {tasks.map((task, i) => {
          const d = draftFor(task);
          const dirty =
            (d.model_id || "") !== (task.assigned_model || "") ||
            (d.effort || "") !== (task.assigned_effort || "");
          const reasoning = supportsReasoning(task, d);
          return (
            <div
              key={task.key}
              className="px-4 py-3"
              style={{ borderTop: i === 0 ? "none" : "1px solid var(--2a-border)" }}
            >
              <div className="flex items-center gap-4">
                <div className="w-64 shrink-0">
                  <div className="text-sm text-text-primary">{task.label}</div>
                  <div className="text-xs text-text-muted">{task.description}</div>
                </div>

                <select
                  value={d.model_id}
                  disabled={!canWrite}
                  onChange={(e) => setDraftField(task, "model_id", e.target.value)}
                  className="flex-1 rounded border px-3 py-1.5 text-sm disabled:opacity-60"
                  style={{ borderColor: "var(--2a-border)", background: "var(--2a-bg-card)" }}
                >
                  <option value="">
                    {task.is_default_model
                      ? `Platform default (${task.assigned_model || "unset"})`
                      : "Reset to platform default"}
                  </option>
                  {models.map((m) => (
                    <option key={m.model_id} value={m.model_id}>
                      {m.display_name}
                    </option>
                  ))}
                </select>

                {reasoning ? (
                  <select
                    value={d.effort}
                    disabled={!canWrite}
                    onChange={(e) => setDraftField(task, "effort", e.target.value)}
                    className="w-40 shrink-0 rounded border px-3 py-1.5 text-sm disabled:opacity-60"
                    style={{ borderColor: "var(--2a-border)", background: "var(--2a-bg-card)" }}
                  >
                    <option value="">No effort set</option>
                    {effortLevels.map((lvl) => (
                      <option key={lvl.value} value={lvl.value}>
                        {lvl.label}
                      </option>
                    ))}
                  </select>
                ) : (
                  <span className="w-40 shrink-0 text-right text-xs text-text-muted">
                    No effort control
                  </span>
                )}

                {canWrite && (
                  <button
                    type="button"
                    onClick={() => save(task)}
                    disabled={!dirty || savingKey === task.key}
                    className="shrink-0 rounded border px-3 py-1.5 text-xs font-medium transition-colors disabled:opacity-40"
                    style={{
                      background: "var(--2a-navy)",
                      color: "var(--2a-bg)",
                      borderColor: "var(--2a-navy)",
                    }}
                  >
                    {savingKey === task.key ? "Saving…" : "Save"}
                  </button>
                )}
              </div>
            </div>
          );
        })}
      </div>

      {error && (
        <p className="mt-2 text-sm" style={{ color: "#9B2335" }}>
          {error}
        </p>
      )}
      {status && (
        <p className="mt-2 text-sm" style={{ color: "#2D6A4F" }}>
          {status}
        </p>
      )}
    </section>
  );
}
