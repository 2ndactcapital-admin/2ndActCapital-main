"use client";

/**
 * Ensemble — the three models that check structured-note hazard fields:
 * Model 1 and Model 2 (language models, from the platform model catalog) and
 * one System One model (typed decisions with probabilities, from the System
 * One catalog). The note-term corpus is shared by every organization, so this
 * selection is platform-wide.
 *
 * Everything rendered comes from the server's envelope: both option lists,
 * each option's availability reason, the System One preselection (the catalog
 * default), and the blocker explaining why no valid ensemble is possible.
 * Write controls render only inside permissions.can_write === true; a missing
 * envelope renders none (fail closed). The server enforces every rule shown.
 *
 * A selection is never edited. Activating creates a new ensemble version with
 * each model's exact version recorded, and retires the previous one.
 */

import { useState, useTransition } from "react";
import { activateEnsembleAction, loadEnsembleAction } from "@/lib/noteTermsQueueActions";

const CARD = { borderColor: "var(--2a-border)", boxShadow: "0 1px 3px rgba(0,0,0,0.06)" };
const NOTICE = { backgroundColor: "var(--2a-gold-light)", color: "var(--2a-navy)" };
const SELECT =
  "mt-1 w-full rounded-md border border-border bg-bg-card p-2 text-sm text-text-primary";

function llmLabel(o) {
  const base = `${o.display_name} — ${o.model_version || "no exact version"}`;
  return o.selectable ? base : `${base} (unavailable: ${o.unavailable_reason})`;
}

function systemOneLabel(o) {
  const base = `${o.display_name} — ${o.model_version || o.model_route}${o.is_default ? " · default" : ""}`;
  return o.selectable ? base : `${base} (unavailable: ${o.unavailable_reason})`;
}

function modelCell(name, version) {
  return (
    <span>
      <span className="font-medium text-text-primary">{name}</span>
      <span className="ml-1 text-text-muted">{version}</span>
    </span>
  );
}

function initialChoice(data) {
  return {
    model_1: "",
    model_2: "",
    system_one_model: data?.ok ? data.picker?.preselected?.system_one_model || "" : "",
  };
}

export default function EnsemblePanel({ taskKey, initial }) {
  const [data, setData] = useState(initial);
  const [choice, setChoice] = useState(() => initialChoice(initial));
  const [notes, setNotes] = useState("");
  const [message, setMessage] = useState(null);
  const [pending, startTransition] = useTransition();

  const picker = data?.ok ? data.picker : null;
  const canWrite = picker?.permissions?.can_write === true;
  const llms = picker?.llm_options || [];
  const systemOnes = picker?.system_one_options || [];
  const history = picker?.history || [];
  const active = picker?.active || null;

  async function refresh() {
    const next = await loadEnsembleAction(taskKey);
    setData(next);
    setChoice(initialChoice(next));
  }

  const { model_1: m1, model_2: m2, system_one_model: s1 } = choice;
  const selectedSystemOne = systemOnes.find((o) => o.key === s1);
  let hint = null;
  if (m1 && m2 && m1 === m2) hint = "Model 1 and Model 2 must be different models.";
  else if (selectedSystemOne && !selectedSystemOne.selectable)
    hint = `${selectedSystemOne.display_name} is unavailable: ${selectedSystemOne.unavailable_reason}`;
  const ready = Boolean(m1 && m2 && s1) && !hint && !picker?.blocker;

  function save() {
    setMessage(null);
    startTransition(async () => {
      const res = await activateEnsembleAction(taskKey, m1, m2, s1, notes);
      if (!res.ok) {
        setMessage({ kind: "error", text: res.error });
        return;
      }
      setMessage({ kind: "ok", text: "New ensemble version activated." });
      setNotes("");
      await refresh();
    });
  }

  if (!picker) {
    return (
      <section className="mt-6 rounded-lg border bg-bg-card p-5" style={CARD}>
        <h2 className="text-base font-semibold text-navy">Ensemble</h2>
        <p className="mt-2 text-sm text-text-muted">
          {data?.status === 403
            ? "Super Admin access required."
            : `Could not load the ensemble: ${data?.error || "no response"}`}
        </p>
        <button
          type="button"
          onClick={() => startTransition(refresh)}
          className="mt-3 text-sm text-navy hover:underline"
        >
          Retry
        </button>
      </section>
    );
  }

  return (
    <section className="mt-6 rounded-lg border bg-bg-card p-5" style={CARD}>
      <h2 className="text-base font-semibold text-navy">Ensemble</h2>
      <p className="mt-1 text-sm text-text-muted">
        Two language models read each filing independently; a System One model
        gives a calibrated decision alongside them. One selection applies across
        the platform, because every organization shares the same note-terms
        corpus.
      </p>

      <div className="mt-4 rounded-md border border-border p-3 text-sm">
        <div className="text-xs font-medium uppercase tracking-wide text-text-muted">
          Active selection
        </div>
        {active ? (
          <div className="mt-2 grid gap-1">
            <div>Model 1: {modelCell(active.model_1, active.model_1_version)}</div>
            <div>Model 2: {modelCell(active.model_2, active.model_2_version)}</div>
            <div>System One: {modelCell(active.system_one_model, active.system_one_model_version)}</div>
            <div className="text-text-muted">
              Active since {new Date(active.activated_at).toLocaleString()}
            </div>
          </div>
        ) : (
          <p className="mt-2 text-text-muted">
            No ensemble has been selected yet. Extraction does not read this
            selection yet; it still uses its built-in model settings.
          </p>
        )}
      </div>

      {picker.blocker && (
        <p className="mt-4 rounded-md px-3 py-2 text-sm" style={NOTICE}>
          No valid ensemble can be activated right now. {picker.blocker}
        </p>
      )}

      {canWrite && (
        <div className="mt-5">
          <div className="grid gap-4 md:grid-cols-3">
            {[
              ["model_1", "Model 1"],
              ["model_2", "Model 2"],
            ].map(([field, label]) => (
              <label key={field} className="block text-sm">
                <span className="font-medium text-text-primary">{label}</span>
                <select
                  value={choice[field]}
                  onChange={(e) => setChoice({ ...choice, [field]: e.target.value })}
                  className={SELECT}
                >
                  <option value="">Select a model</option>
                  {llms.map((o) => (
                    <option key={o.model_id} value={o.model_id} disabled={!o.selectable}>
                      {llmLabel(o)}
                    </option>
                  ))}
                </select>
              </label>
            ))}
            <label className="block text-sm">
              <span className="font-medium text-text-primary">System One model</span>
              <select
                value={s1}
                onChange={(e) => setChoice({ ...choice, system_one_model: e.target.value })}
                className={SELECT}
              >
                <option value="">Select a System One model</option>
                {systemOnes.map((o) => (
                  // The preselected default stays choosable even when
                  // unavailable, so the reason it cannot be activated shows.
                  <option
                    key={o.key}
                    value={o.key}
                    disabled={!o.selectable && o.key !== picker.default_system_one_model}
                  >
                    {systemOneLabel(o)}
                  </option>
                ))}
              </select>
            </label>
          </div>

          <label className="mt-4 block text-sm">
            <span className="font-medium text-text-primary">Notes</span>
            <input
              type="text"
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
              placeholder="Why this selection"
              className={SELECT}
            />
          </label>

          <p className="mt-4 text-sm text-text-primary">
            Activating creates a new ensemble version with each model&apos;s exact
            version recorded. The previous version is retired, not changed.
          </p>
          {hint && <p className="mt-2 text-sm text-text-muted">{hint}</p>}
          <button
            type="button"
            onClick={save}
            disabled={pending || !ready}
            className="mt-3 rounded-md bg-navy px-4 py-2 text-sm font-medium text-white disabled:opacity-50"
          >
            {pending ? "Activating…" : "Activate new ensemble version"}
          </button>
        </div>
      )}

      {message && (
        <p
          className="mt-3 rounded-md px-3 py-2 text-sm"
          style={message.kind === "ok" ? { color: "var(--2a-navy)" } : NOTICE}
        >
          {message.text}
        </p>
      )}

      <div className="mt-6">
        <div className="text-xs font-medium uppercase tracking-wide text-text-muted">History</div>
        {history.length === 0 ? (
          <p className="mt-2 text-sm text-text-muted">No ensemble versions yet.</p>
        ) : (
          <table className="mt-2 w-full text-left text-sm">
            <thead className="text-text-muted">
              <tr>
                <th className="py-1 pr-3 font-medium">Created</th>
                <th className="py-1 pr-3 font-medium">Model 1</th>
                <th className="py-1 pr-3 font-medium">Model 2</th>
                <th className="py-1 pr-3 font-medium">System One</th>
                <th className="py-1 pr-3 font-medium">Status</th>
                <th className="py-1 font-medium">Notes</th>
              </tr>
            </thead>
            <tbody>
              {history.map((row) => (
                <tr key={row.id} className="border-t border-border align-top">
                  <td className="py-2 pr-3">{new Date(row.created_at).toLocaleString()}</td>
                  <td className="py-2 pr-3">{modelCell(row.model_1, row.model_1_version)}</td>
                  <td className="py-2 pr-3">{modelCell(row.model_2, row.model_2_version)}</td>
                  <td className="py-2 pr-3">
                    {modelCell(row.system_one_model, row.system_one_model_version)}
                  </td>
                  <td className="py-2 pr-3">
                    {row.is_active
                      ? "Active"
                      : row.retired_at
                        ? `Retired ${new Date(row.retired_at).toLocaleString()}`
                        : "Never activated"}
                  </td>
                  <td className="py-2 text-text-muted">{row.notes || "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </section>
  );
}
