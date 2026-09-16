"use client";

/**
 * OrgModelSelector — the org admin's half of the LiteLLM Phase D2 pick-list.
 *
 * Hollisworks curates WHICH models exist (ModelCatalogManager, super_admin
 * only, /admin/model-catalog). This component lets an org admin choose which
 * of THOSE curated models its own org may use — reads open to any org
 * member, writes gated on manage_org_settings, exactly like the
 * ai-credentials section this screen already carries.
 *
 * `canWrite` comes from the server's own envelope
 * (permissions.can_write on GET .../model-selections) — NOT the `canEdit`
 * prop this component's caller passes down, deliberately: the two can
 * legitimately disagree (a super_admin viewing another org can_write here
 * even where the generic settings PUT's own gate differs), and Rule 1's
 * permission-envelope pattern says the server's own answer wins, never a
 * value threaded down from a sibling screen's assumption.
 *
 * A checkbox list, not a DataGrid+pane: the curated list is a handful of
 * rows and the only real interaction is "authorised / not" — a full grid
 * would be more chrome than the task needs. The heavier grid pattern is
 * used on ModelCatalogManager, which is genuine multi-field CRUD.
 */

import { useEffect, useState } from "react";

const CARD = { borderColor: "#ece8dd", boxShadow: "0 1px 3px rgba(0,0,0,0.06)" };

export default function OrgModelSelector({ orgId }) {
  const [catalog, setCatalog] = useState(null);
  const [selected, setSelected] = useState([]);
  const [permissions, setPermissions] = useState({ can_read: true, can_write: false });
  const [draft, setDraft] = useState(null); // null = not yet touched by the user
  const [error, setError] = useState(null);
  const [status, setStatus] = useState(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (!orgId) return;
    let active = true;
    setCatalog(null);
    setError(null);
    setStatus(null);
    setDraft(null);

    fetch(`/api/orgs/${orgId}/settings/model-selections`, { cache: "no-store" })
      .then((res) => (res.ok ? res.json() : Promise.reject(res)))
      .then((data) => {
        if (!active) return;
        setCatalog(data.vocabularies?.catalog || []);
        setSelected(data.selected_model_ids || []);
        // NO FALLBACK. can_write is false unless the server said otherwise.
        setPermissions(data.permissions || { can_read: true, can_write: false });
      })
      .catch(async (res) => {
        if (!active) return;
        const body = await res?.json?.().catch(() => ({}));
        setError(body?.error || "Could not load the model list.");
      });

    return () => {
      active = false;
    };
  }, [orgId]);

  const canWrite = !!permissions?.can_write;
  const current = draft ?? selected;

  function toggle(modelId) {
    if (!canWrite) return;
    const base = draft ?? selected;
    setDraft(
      base.includes(modelId) ? base.filter((m) => m !== modelId) : [...base, modelId],
    );
    setStatus(null);
  }

  async function save() {
    if (draft === null) return;
    setSaving(true);
    setError(null);
    try {
      const res = await fetch(`/api/orgs/${orgId}/settings/model-selections`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ model_ids: draft }),
      });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(body.error || "Save failed");
      setSelected(body.selected_model_ids || []);
      setDraft(null);
      setStatus("Saved.");
    } catch (e) {
      setError(e.message);
    } finally {
      setSaving(false);
    }
  }

  if (!catalog) {
    return null;
  }

  return (
    <section className="mt-8">
      <h2
        className="text-xs font-semibold uppercase"
        style={{ letterSpacing: "0.22em", color: "var(--2a-gold)" }}
      >
        Available Models
      </h2>
      <p className="mt-2 text-xs text-text-muted">
        Which of Hollisworks&rsquo; curated models this organization may use.
        Leaving nothing selected keeps today&rsquo;s behavior unchanged — no
        model is refused until at least one is authorised here.
      </p>

      <div className="mt-3 overflow-hidden rounded-md border border-border bg-bg-card" style={CARD}>
        {catalog.length === 0 && (
          <p className="px-4 py-3 text-xs text-text-muted">
            The platform catalog is empty.
          </p>
        )}
        {catalog.map((m, i) => (
          <label
            key={m.model_id}
            className="flex items-center gap-3 px-4 py-3"
            style={{ borderTop: i === 0 ? "none" : "1px solid var(--2a-border)" }}
          >
            <input
              type="checkbox"
              className="accent-[var(--2a-navy)]"
              disabled={!canWrite}
              checked={current.includes(m.model_id)}
              onChange={() => toggle(m.model_id)}
            />
            <span className="flex-1 text-sm text-text-primary">{m.display_name}</span>
            <span className="text-xs text-text-muted">{m.provider}</span>
          </label>
        ))}
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

      {canWrite && (
        <button
          type="button"
          onClick={save}
          disabled={draft === null || saving}
          className="mt-3 rounded border px-4 py-2 text-sm font-medium transition-colors disabled:opacity-40"
          style={{
            background: "var(--2a-navy)",
            color: "var(--2a-bg)",
            borderColor: "var(--2a-navy)",
          }}
        >
          {saving ? "Saving…" : "Save model selection"}
        </button>
      )}
    </section>
  );
}
