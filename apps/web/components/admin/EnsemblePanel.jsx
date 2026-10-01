"use client";

/**
 * Ensemble — which three models check the structured-note hazard fields.
 *
 * Review model 1 and Review model 2 read the filing independently; the
 * Comparison model judges their answers. Every option shows the exact model
 * version and its availability. Unavailable options stay visible but
 * disabled, with the reason, so nobody wonders where a model went.
 *
 * A selection is never edited. Saving creates a new ensemble version and
 * retires the previous one; rows already extracted keep the models that
 * produced them. The server enforces every rule shown here (super admin, the
 * distinct-model checks, availability) — the client mirrors them only to
 * avoid a pointless round trip.
 *
 * Everything the panel renders comes from the server's envelope: the model
 * list, the availability vocabulary, and permissions.can_write. A missing
 * envelope renders no write controls (fail closed).
 */

import { useState, useTransition } from "react";
import { activateEnsembleAction, loadEnsembleAction } from "@/lib/noteTermsQueueActions";

const CARD = { borderColor: "var(--2a-border)", boxShadow: "0 1px 3px rgba(0,0,0,0.06)" };
const NOTICE = { backgroundColor: "var(--2a-gold-light)", color: "var(--2a-navy)" };

const SLOTS = [
  { field: "review_model_1", label: "Review model 1", review: true },
  { field: "review_model_2", label: "Review model 2", review: true },
  { field: "comparison_model", label: "Comparison model", review: false },
];

function optionState(entry, slot, kinds) {
  if (!kinds.includes(entry.kind)) {
    return { disabled: true, reason: `${entry.kind} models cannot fill this slot` };
  }
  if (!entry.selectable) {
    return { disabled: true, reason: entry.unavailable_reason || "not available" };
  }
  return { disabled: false, reason: null };
}

function optionLabel(entry, state) {
  const version = entry.model_version || "no exact version";
  const availability = entry.availability || "not curated";
  const base = `${entry.display_name} — ${version} · ${availability}`;
  return state.disabled ? `${base} (unavailable: ${state.reason})` : base;
}

function modelCell(name, version) {
  return (
    <span>
      <span className="font-medium text-text-primary">{name}</span>
      <span className="ml-1 text-text-muted">{version}</span>
    </span>
  );
}

export default function EnsemblePanel({ taskKey, initial }) {
  const [data, setData] = useState(initial);
  const [choice, setChoice] = useState({
    review_model_1: "",
    review_model_2: "",
    comparison_model: "",
  });
  const [notes, setNotes] = useState("");
  const [message, setMessage] = useState(null);
  const [pending, startTransition] = useTransition();

  const catalog = data?.ok ? data.catalog : null;
  const ensembles = data?.ok ? data.ensembles : null;
  const canWrite = catalog?.permissions?.can_write === true;
  const rows = catalog?.rows || [];
  const vocab = catalog?.vocabularies || {};
  const history = ensembles?.rows || [];
  const active = ensembles?.active || null;

  function reload() {
    startTransition(async () => {
      setData(await loadEnsembleAction(taskKey));
    });
  }

  const { review_model_1: m1, review_model_2: m2, comparison_model: cmp } = choice;
  let blocker = null;
  if (!m1 || !m2 || !cmp) blocker = "Choose all three models.";
  else if (m1 === m2) blocker = "Review model 1 and Review model 2 must be different models.";
  else if (cmp === m1 || cmp === m2) blocker = "The comparison model must differ from both review models.";

  function save() {
    setMessage(null);
    startTransition(async () => {
      const res = await activateEnsembleAction(taskKey, m1, m2, cmp, notes);
      if (!res.ok) {
        setMessage({ kind: "error", text: res.error });
        return;
      }
      const warnings = res.result?.warnings || [];
      setMessage({
        kind: warnings.length ? "warning" : "ok",
        text: warnings.length
          ? `New ensemble version saved. ${warnings.join(" ")}`
          : "New ensemble version saved.",
      });
      setChoice({ review_model_1: "", review_model_2: "", comparison_model: "" });
      setNotes("");
      setData(await loadEnsembleAction(taskKey));
    });
  }

  if (!data?.ok) {
    return (
      <section className="mt-6 rounded-lg border bg-bg-card p-5" style={CARD}>
        <h2 className="text-base font-semibold text-navy">Ensemble</h2>
        <p className="mt-2 text-sm text-text-muted">
          {data?.status === 403
            ? "Super Admin access required."
            : `Could not load the model catalog: ${data?.error || "no response"}`}
        </p>
        <button type="button" onClick={reload} className="mt-3 text-sm text-navy hover:underline">
          Retry
        </button>
      </section>
    );
  }

  return (
    <section className="mt-6 rounded-lg border bg-bg-card p-5" style={CARD}>
      <h2 className="text-base font-semibold text-navy">Ensemble</h2>
      <p className="mt-1 text-sm text-text-muted">
        The models that check hazard fields. Two review models read each filing
        independently; the comparison model judges their answers.
      </p>

      <div className="mt-4 rounded-md border border-border p-3 text-sm">
        <div className="text-xs font-medium uppercase tracking-wide text-text-muted">
          Active selection
        </div>
        {active ? (
          <div className="mt-2 grid gap-1">
            <div>Review model 1: {modelCell(active.review_model_1, active.review_model_1_version)}</div>
            <div>Review model 2: {modelCell(active.review_model_2, active.review_model_2_version)}</div>
            <div>
              Comparison model: {modelCell(active.comparison_model, active.comparison_model_version)}
              <span className="ml-1 text-text-muted">({active.comparison_kind})</span>
            </div>
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

      {canWrite && (
        <div className="mt-5">
          <div className="grid gap-4 md:grid-cols-3">
            {SLOTS.map((slot) => {
              const kinds = slot.review
                ? vocab.review_model_kinds || []
                : vocab.comparison_model_kinds || [];
              return (
                <label key={slot.field} className="block text-sm">
                  <span className="font-medium text-text-primary">{slot.label}</span>
                  <select
                    value={choice[slot.field]}
                    onChange={(e) => setChoice({ ...choice, [slot.field]: e.target.value })}
                    className="mt-1 w-full rounded-md border border-border bg-bg-card p-2 text-sm text-text-primary"
                  >
                    <option value="">Select a model</option>
                    {rows.map((entry) => {
                      const state = optionState(entry, slot, kinds);
                      return (
                        <option key={entry.key} value={entry.key} disabled={state.disabled}>
                          {optionLabel(entry, state)}
                        </option>
                      );
                    })}
                  </select>
                </label>
              );
            })}
          </div>

          <label className="mt-4 block text-sm">
            <span className="font-medium text-text-primary">Notes</span>
            <input
              type="text"
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
              placeholder="Why this selection"
              className="mt-1 w-full rounded-md border border-border bg-bg-card p-2 text-sm text-text-primary"
            />
          </label>

          <p className="mt-4 text-sm text-text-primary">
            Changing models creates a new ensemble version. Existing rows keep
            the models that produced them.
          </p>
          {blocker && (m1 || m2 || cmp) && (
            <p className="mt-2 text-sm text-text-muted">{blocker}</p>
          )}
          <button
            type="button"
            onClick={save}
            disabled={pending || Boolean(blocker)}
            className="mt-3 rounded-md bg-navy px-4 py-2 text-sm font-medium text-white disabled:opacity-50"
          >
            {pending ? "Saving…" : "Save new ensemble version"}
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
        <div className="text-xs font-medium uppercase tracking-wide text-text-muted">
          History
        </div>
        {history.length === 0 ? (
          <p className="mt-2 text-sm text-text-muted">No ensemble versions yet.</p>
        ) : (
          <table className="mt-2 w-full text-left text-sm">
            <thead className="text-text-muted">
              <tr>
                <th className="py-1 pr-3 font-medium">Created</th>
                <th className="py-1 pr-3 font-medium">Review model 1</th>
                <th className="py-1 pr-3 font-medium">Review model 2</th>
                <th className="py-1 pr-3 font-medium">Comparison model</th>
                <th className="py-1 pr-3 font-medium">Status</th>
                <th className="py-1 font-medium">Notes</th>
              </tr>
            </thead>
            <tbody>
              {history.map((row) => (
                <tr key={row.id} className="border-t border-border align-top">
                  <td className="py-2 pr-3">{new Date(row.created_at).toLocaleString()}</td>
                  <td className="py-2 pr-3">{modelCell(row.review_model_1, row.review_model_1_version)}</td>
                  <td className="py-2 pr-3">{modelCell(row.review_model_2, row.review_model_2_version)}</td>
                  <td className="py-2 pr-3">
                    {modelCell(row.comparison_model, row.comparison_model_version)}
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
