"use client";

/**
 * ModelCatalogManager — the platform model catalog screen (litellmphased2).
 *
 * Built on the same components/ui/DataGrid + right-pane shape the Triggers
 * screen established: a list on the left, a detail/add pane on the right,
 * `permissions.can_write` from the server the ONLY thing deciding whether
 * any write control renders (no `?? true` fallback — a missing envelope
 * fails closed).
 *
 * This screen curates WHICH models exist platform-wide. It does not touch
 * any org's own selection from that list — that lives on the Organization
 * Settings screen (OrgSettingsEditor's "AI Models" section), gated on
 * manage_org_settings rather than super_admin. Two different privilege
 * levels, two different screens, on purpose.
 */

import { useCallback, useEffect, useMemo, useState } from "react";

import DataGrid from "@/components/ui/DataGrid";

const CARD = { borderColor: "#ece8dd", boxShadow: "0 1px 3px rgba(0,0,0,0.06)" };
const CONTROL =
  "w-full rounded border border-[var(--2a-border)] bg-white px-2 py-1.5 text-xs text-[var(--2a-text)] focus:outline-none focus:ring-1 focus:ring-[var(--2a-gold)] disabled:bg-[var(--2a-bg)] disabled:text-[var(--2a-text-muted)]";
const EYEBROW =
  "block text-[10px] font-semibold uppercase tracking-[0.12em] text-[var(--2a-text-muted)]";

function formatPrice(perToken) {
  if (perToken === null || perToken === undefined) return "—";
  const perMillion = perToken * 1_000_000;
  return `$${perMillion.toFixed(2)} / 1M tok`;
}

function ReadRow({ label, children }) {
  return (
    <div className="flex items-baseline justify-between gap-3 border-b border-[var(--2a-border)] py-1.5 last:border-0">
      <span className={EYEBROW}>{label}</span>
      <span className="text-right text-xs text-[var(--2a-text)]">{children}</span>
    </div>
  );
}

export default function ModelCatalogManager() {
  const [rows, setRows] = useState([]);
  const [permissions, setPermissions] = useState({ can_read: true, can_write: false });
  const [selectedId, setSelectedId] = useState(null);
  const [mode, setMode] = useState("read");
  const [loadError, setLoadError] = useState(null);

  const [form, setForm] = useState({ model_id: "", display_name: "", provider: "anthropic" });
  const [saveError, setSaveError] = useState(null);
  const [busy, setBusy] = useState(false);
  const [confirmingDelete, setConfirmingDelete] = useState(false);

  const canWrite = !!permissions?.can_write;

  const reload = useCallback(async () => {
    try {
      const res = await fetch("/api/admin/model-catalog", { cache: "no-store" });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        setLoadError(
          typeof data.error === "string" ? data.error : "Could not load the model catalog.",
        );
        return;
      }
      setRows(data.models || []);
      if (data.permissions) setPermissions(data.permissions);
      setLoadError(null);
    } catch (e) {
      setLoadError(e.message);
    }
  }, []);

  useEffect(() => {
    reload();
  }, [reload]);

  const selected = useMemo(
    () => rows.find((r) => String(r.model_id) === String(selectedId)) || null,
    [rows, selectedId],
  );

  useEffect(() => {
    if (selectedId && !selected && mode !== "create") {
      setSelectedId(null);
      setMode("read");
    }
  }, [selectedId, selected, mode]);

  const columnDefs = useMemo(
    () => [
      {
        field: "display_name",
        headerName: "Model",
        enableColumnFilter: true,
        filterPlaceholder: "Model…",
        cell: (value) => <span className="font-medium text-[var(--2a-navy)]">{value}</span>,
      },
      {
        field: "provider",
        headerName: "Provider",
        enableColumnFilter: true,
        filterPlaceholder: "Provider…",
      },
      {
        field: "model_id",
        headerName: "Model ID",
        cell: (value) => <code className="text-[11px]">{value}</code>,
      },
      {
        field: "context_window",
        headerName: "Context",
        align: "right",
        cell: (value) => (value ? value.toLocaleString() : "—"),
      },
      {
        field: "input_cost_per_token",
        headerName: "Input price",
        align: "right",
        cell: (value) => formatPrice(value),
      },
    ],
    [],
  );

  async function submitCreate(e) {
    e.preventDefault();
    setBusy(true);
    setSaveError(null);
    try {
      const res = await fetch("/api/admin/model-catalog", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(form),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        setSaveError(typeof data.error === "string" ? data.error : "Could not add the model.");
        return;
      }
      setForm({ model_id: "", display_name: "", provider: "anthropic" });
      setMode("read");
      setSelectedId(data.model_id);
      await reload();
    } finally {
      setBusy(false);
    }
  }

  async function remove() {
    if (!selected) return;
    setBusy(true);
    setSaveError(null);
    try {
      const res = await fetch(
        `/api/admin/model-catalog/${encodeURIComponent(selected.model_id)}`,
        { method: "DELETE" },
      );
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        setSaveError(typeof data.error === "string" ? data.error : "Could not remove the model.");
        return;
      }
      setSelectedId(null);
      setConfirmingDelete(false);
      await reload();
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="mt-6 grid gap-4 lg:grid-cols-[minmax(0,1fr)_22rem]">
      <div className="rounded-lg border bg-white p-4" style={CARD}>
        <div className="mb-3 flex flex-wrap items-baseline justify-between gap-2">
          <p className="text-xs text-[var(--2a-text-muted)]">
            {rows.length} model{rows.length === 1 ? "" : "s"} on the curated
            platform list
          </p>
          {canWrite ? (
            <button
              type="button"
              onClick={() => {
                setSelectedId(null);
                setSaveError(null);
                setMode("create");
              }}
              className="rounded bg-[var(--2a-navy)] px-3 py-1.5 text-xs font-medium text-white"
            >
              Add model
            </button>
          ) : (
            <span className="text-[10px] uppercase tracking-[0.12em] text-[var(--2a-text-muted)]">
              View only
            </span>
          )}
        </div>

        {loadError && (
          <p className="mb-2 text-xs" style={{ color: "#9B2335" }}>
            {loadError}
          </p>
        )}

        <DataGrid
          gridId="model-catalog"
          columnDefs={columnDefs}
          rowData={rows}
          getRowId={(row) => String(row.model_id)}
          selectedRowId={selectedId}
          onRowClick={(row) => {
            setSelectedId(String(row.model_id));
            setMode("read");
            setSaveError(null);
            setConfirmingDelete(false);
          }}
          quickFilterPlaceholder="Search models…"
          emptyMessage="No models on the platform catalog yet."
        />
      </div>

      <div className="rounded-lg border bg-white" style={CARD}>
        {mode === "create" ? (
          <div className="p-4">
            <h2 className="font-[Spectral,Georgia,serif] text-base text-[var(--2a-navy)]">
              Add a model
            </h2>
            <form className="mt-4 space-y-3" onSubmit={submitCreate}>
              <label className="block">
                <span className={EYEBROW}>Model ID</span>
                <input
                  className={`${CONTROL} mt-1 font-mono`}
                  required
                  placeholder="claude-opus-4-1"
                  value={form.model_id}
                  onChange={(e) => setForm((f) => ({ ...f, model_id: e.target.value }))}
                />
              </label>
              <label className="block">
                <span className={EYEBROW}>Display name</span>
                <input
                  className={`${CONTROL} mt-1`}
                  required
                  placeholder="Claude Opus"
                  value={form.display_name}
                  onChange={(e) => setForm((f) => ({ ...f, display_name: e.target.value }))}
                />
              </label>
              <label className="block">
                <span className={EYEBROW}>Provider</span>
                <select
                  className={`${CONTROL} mt-1`}
                  value={form.provider}
                  onChange={(e) => setForm((f) => ({ ...f, provider: e.target.value }))}
                >
                  <option value="anthropic">Anthropic</option>
                  <option value="voyage">Voyage</option>
                </select>
              </label>
              {saveError && (
                <p className="text-xs" style={{ color: "#9B2335" }}>
                  {saveError}
                </p>
              )}
              <div className="flex gap-2 pt-1">
                <button
                  type="submit"
                  disabled={busy}
                  className="rounded bg-[var(--2a-navy)] px-3 py-1.5 text-xs font-medium text-white disabled:opacity-50"
                >
                  {busy ? "Adding…" : "Add model"}
                </button>
                <button
                  type="button"
                  onClick={() => setMode("read")}
                  className="rounded border border-[var(--2a-border)] px-3 py-1.5 text-xs text-[var(--2a-text-secondary)]"
                >
                  Cancel
                </button>
              </div>
            </form>
          </div>
        ) : selected ? (
          <div className="p-4">
            <h2 className="font-[Spectral,Georgia,serif] text-base text-[var(--2a-navy)]">
              {selected.display_name}
            </h2>
            <div className="mt-3">
              <ReadRow label="Model ID">
                <code className="text-[11px]">{selected.model_id}</code>
              </ReadRow>
              <ReadRow label="Provider">{selected.provider}</ReadRow>
              <ReadRow label="Context window">
                {selected.context_window ? selected.context_window.toLocaleString() : "not on the live proxy"}
              </ReadRow>
              <ReadRow label="Input price">{formatPrice(selected.input_cost_per_token)}</ReadRow>
              <ReadRow label="Output price">{formatPrice(selected.output_cost_per_token)}</ReadRow>
              <ReadRow label="Added">
                {selected.created_at ? new Date(selected.created_at).toLocaleDateString() : "—"}
              </ReadRow>
            </div>

            {canWrite && (
              <div className="mt-4 border-t border-[var(--2a-border)] pt-3">
                {confirmingDelete ? (
                  <div className="space-y-2">
                    <p className="text-xs" style={{ color: "#9B2335" }}>
                      Remove <strong>{selected.display_name}</strong> from the
                      platform catalog? Every org that has authorised it loses
                      access immediately.
                    </p>
                    {saveError && (
                      <p className="text-xs" style={{ color: "#9B2335" }}>
                        {saveError}
                      </p>
                    )}
                    <div className="flex gap-2">
                      <button
                        type="button"
                        disabled={busy}
                        onClick={remove}
                        className="rounded px-3 py-1.5 text-xs font-medium text-white disabled:opacity-50"
                        style={{ background: "#9B2335" }}
                      >
                        {busy ? "Removing…" : "Remove permanently"}
                      </button>
                      <button
                        type="button"
                        onClick={() => setConfirmingDelete(false)}
                        className="rounded border border-[var(--2a-border)] px-3 py-1.5 text-xs text-[var(--2a-text-secondary)]"
                      >
                        Keep it
                      </button>
                    </div>
                  </div>
                ) : (
                  <button
                    type="button"
                    onClick={() => setConfirmingDelete(true)}
                    className="text-xs underline decoration-dotted"
                    style={{ color: "#9B2335" }}
                  >
                    Remove this model…
                  </button>
                )}
              </div>
            )}
          </div>
        ) : (
          <div className="p-6 text-xs text-[var(--2a-text-muted)]">
            Select a model to see its live context window and pricing —
            fetched from the LiteLLM proxy where a matching deployment exists.
          </div>
        )}
      </div>
    </div>
  );
}
