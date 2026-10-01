"use client";

/**
 * SystemOneCatalogManager — the System One model catalog (ensemblesystemone).
 *
 * Sits under the LLM catalog on /admin/model-catalog, with the same list +
 * right-pane shape. A System One model answers typed questions with
 * probabilities (Jev, from TypeSafe); it fills the third slot of an ensemble.
 *
 * Every write control renders only inside `permissions.can_write` from the
 * server's envelope — no fallback, so a missing envelope fails closed. The
 * availability choices offered by hand come from the envelope too; 'available'
 * is never among them, because only "Verify now" (a real call through the
 * proxy) can make an entry available.
 */

import { useCallback, useEffect, useMemo, useState } from "react";

import DataGrid from "@/components/ui/DataGrid";

const CARD = { borderColor: "#ece8dd", boxShadow: "0 1px 3px rgba(0,0,0,0.06)" };
const CONTROL =
  "w-full rounded border border-[var(--2a-border)] bg-white px-2 py-1.5 text-xs text-[var(--2a-text)] focus:outline-none focus:ring-1 focus:ring-[var(--2a-gold)] disabled:bg-[var(--2a-bg)] disabled:text-[var(--2a-text-muted)]";
const EYEBROW =
  "block text-[10px] font-semibold uppercase tracking-[0.12em] text-[var(--2a-text-muted)]";
const ERROR = { color: "#9B2335" };
const EMPTY_FORM = { key: "", display_name: "", provider: "typesafe", model_route: "", notes: "" };

function ReadRow({ label, children }) {
  return (
    <div className="flex items-baseline justify-between gap-3 border-b border-[var(--2a-border)] py-1.5 last:border-0">
      <span className={EYEBROW}>{label}</span>
      <span className="text-right text-xs text-[var(--2a-text)]">{children}</span>
    </div>
  );
}

async function send(url, method, body) {
  const res = await fetch(url, {
    method,
    headers: body ? { "Content-Type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await res.json().catch(() => ({}));
  return { ok: res.ok, data };
}

export default function SystemOneCatalogManager() {
  const [rows, setRows] = useState([]);
  const [permissions, setPermissions] = useState(null);
  const [vocabularies, setVocabularies] = useState(null);
  const [selectedKey, setSelectedKey] = useState(null);
  const [mode, setMode] = useState("read");
  const [loadError, setLoadError] = useState(null);
  const [form, setForm] = useState(EMPTY_FORM);
  const [message, setMessage] = useState(null);
  const [busy, setBusy] = useState(false);
  const [confirmingDelete, setConfirmingDelete] = useState(false);

  const canWrite = permissions?.can_write === true;
  const manualAvailability = vocabularies?.manual_availability || [];

  const reload = useCallback(async () => {
    try {
      const res = await fetch("/api/admin/system-one-catalog", { cache: "no-store" });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        setLoadError(typeof data.error === "string" ? data.error : "Could not load the System One catalog.");
        return;
      }
      setRows(data.rows || []);
      setPermissions(data.permissions || null);
      setVocabularies(data.vocabularies || null);
      setLoadError(null);
    } catch (e) {
      setLoadError(e.message);
    }
  }, []);

  useEffect(() => {
    reload();
  }, [reload]);

  const selected = useMemo(
    () => rows.find((r) => r.key === selectedKey) || null,
    [rows, selectedKey],
  );

  const columnDefs = useMemo(
    () => [
      {
        field: "display_name",
        headerName: "Model",
        cell: (value, row) => (
          <span className="font-medium text-[var(--2a-navy)]">
            {value}
            {row.is_default && (
              <span className="ml-2 text-[10px] uppercase tracking-[0.12em] text-[var(--2a-gold)]">
                Default
              </span>
            )}
          </span>
        ),
      },
      { field: "provider", headerName: "Provider" },
      {
        field: "model_route",
        headerName: "Calls",
        cell: (value) => <code className="text-[11px]">{value}</code>,
      },
      { field: "availability", headerName: "Availability" },
    ],
    [],
  );

  async function run(action, after) {
    setBusy(true);
    setMessage(null);
    try {
      const { ok, data } = await action();
      if (!ok) {
        setMessage({ error: true, text: typeof data.error === "string" ? data.error : "Request failed." });
        return;
      }
      if (after) after(data);
      await reload();
    } finally {
      setBusy(false);
    }
  }

  function submitCreate(e) {
    e.preventDefault();
    run(
      () => send("/api/admin/system-one-catalog", "POST", { ...form, notes: form.notes || null }),
      (data) => {
        setForm(EMPTY_FORM);
        setMode("read");
        setSelectedKey(data.key);
      },
    );
  }

  const base = selected ? `/api/admin/system-one-catalog/${encodeURIComponent(selected.key)}` : "";

  return (
    <section className="mt-10">
      <h2 className="font-[Spectral,Georgia,serif] text-xl text-[var(--2a-navy)]">System One models</h2>
      <p className="mt-1 text-sm text-[var(--2a-text-muted)]">
        Models that answer typed questions with calibrated probabilities. Each
        ensemble pairs two language models with one of these. An entry becomes
        available only after a real call through the AI gateway succeeds.
      </p>

      <div className="mt-4 grid gap-4 lg:grid-cols-[minmax(0,1fr)_22rem]">
        <div className="rounded-lg border bg-white p-4" style={CARD}>
          <div className="mb-3 flex flex-wrap items-baseline justify-between gap-2">
            <p className="text-xs text-[var(--2a-text-muted)]">
              {rows.length} System One model{rows.length === 1 ? "" : "s"}
            </p>
            {canWrite ? (
              <button
                type="button"
                onClick={() => {
                  setSelectedKey(null);
                  setMessage(null);
                  setMode("create");
                }}
                className="rounded bg-[var(--2a-navy)] px-3 py-1.5 text-xs font-medium text-white"
              >
                Add System One model
              </button>
            ) : (
              <span className="text-[10px] uppercase tracking-[0.12em] text-[var(--2a-text-muted)]">
                View only
              </span>
            )}
          </div>
          {loadError && <p className="mb-2 text-xs" style={ERROR}>{loadError}</p>}
          <DataGrid
            gridId="system-one-catalog"
            columnDefs={columnDefs}
            rowData={rows}
            getRowId={(row) => row.key}
            selectedRowId={selectedKey}
            onRowClick={(row) => {
              setSelectedKey(row.key);
              setMode("read");
              setMessage(null);
              setConfirmingDelete(false);
            }}
            emptyMessage="No System One models yet."
          />
        </div>

        <div className="rounded-lg border bg-white" style={CARD}>
          {mode === "create" && canWrite ? (
            <form className="space-y-3 p-4" onSubmit={submitCreate}>
              <h3 className="font-[Spectral,Georgia,serif] text-base text-[var(--2a-navy)]">
                Add a System One model
              </h3>
              {[
                ["key", "Catalog key", "typesafe-jev-latest", true],
                ["display_name", "Display name", "Jev latest (TypeSafe)", false],
                ["model_route", "Model to call", "jev-latest", true],
                ["notes", "Notes", "Why this entry", false],
              ].map(([field, label, placeholder, mono]) => (
                <label key={field} className="block">
                  <span className={EYEBROW}>{label}</span>
                  <input
                    className={`${CONTROL} mt-1 ${mono ? "font-mono" : ""}`}
                    required={field !== "notes"}
                    placeholder={placeholder}
                    value={form[field]}
                    onChange={(e) => setForm((f) => ({ ...f, [field]: e.target.value }))}
                  />
                </label>
              ))}
              <label className="block">
                <span className={EYEBROW}>Provider</span>
                <select
                  className={`${CONTROL} mt-1`}
                  value={form.provider}
                  onChange={(e) => setForm((f) => ({ ...f, provider: e.target.value }))}
                >
                  <option value="typesafe">TypeSafe</option>
                </select>
              </label>
              <p className="text-xs text-[var(--2a-text-muted)]">
                New entries start disabled. Run Verify now to make one available.
              </p>
              {message && <p className="text-xs" style={message.error ? ERROR : undefined}>{message.text}</p>}
              <div className="flex gap-2 pt-1">
                <button
                  type="submit"
                  disabled={busy}
                  className="rounded bg-[var(--2a-navy)] px-3 py-1.5 text-xs font-medium text-white disabled:opacity-50"
                >
                  {busy ? "Adding…" : "Add"}
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
          ) : selected ? (
            <div className="p-4">
              <h3 className="font-[Spectral,Georgia,serif] text-base text-[var(--2a-navy)]">
                {selected.display_name}
              </h3>
              <div className="mt-3">
                <ReadRow label="Catalog key"><code className="text-[11px]">{selected.key}</code></ReadRow>
                <ReadRow label="Model to call"><code className="text-[11px]">{selected.model_route}</code></ReadRow>
                <ReadRow label="Version last reported">{selected.model_version || "never reported"}</ReadRow>
                <ReadRow label="Availability">{selected.availability}</ReadRow>
                <ReadRow label="Default">{selected.is_default ? "Yes" : "No"}</ReadRow>
                <ReadRow label="Last verified">
                  {selected.last_verified_at ? new Date(selected.last_verified_at).toLocaleString() : "never"}
                </ReadRow>
              </div>
              {selected.last_check_detail && (
                <p className="mt-3 text-xs text-[var(--2a-text-secondary)]">{selected.last_check_detail}</p>
              )}

              {canWrite && (
                <div className="mt-4 space-y-3 border-t border-[var(--2a-border)] pt-3">
                  <div className="flex flex-wrap gap-2">
                    <button
                      type="button"
                      disabled={busy}
                      onClick={() =>
                        run(
                          () => send(`${base}/verify`, "POST"),
                          (data) =>
                            setMessage({ error: !data.ok, text: data.detail || "Check finished." }),
                        )
                      }
                      className="rounded bg-[var(--2a-navy)] px-3 py-1.5 text-xs font-medium text-white disabled:opacity-50"
                    >
                      {busy ? "Working…" : "Verify now"}
                    </button>
                    {!selected.is_default && (
                      <button
                        type="button"
                        disabled={busy}
                        onClick={() => run(() => send(`${base}/default`, "PUT"))}
                        className="rounded border border-[var(--2a-border)] px-3 py-1.5 text-xs text-[var(--2a-text-secondary)] disabled:opacity-50"
                      >
                        Make default
                      </button>
                    )}
                  </div>

                  {manualAvailability.length > 0 && (
                    <label className="block">
                      <span className={EYEBROW}>Set availability</span>
                      <select
                        className={`${CONTROL} mt-1`}
                        disabled={busy}
                        value=""
                        onChange={(e) =>
                          e.target.value &&
                          run(() => send(`${base}/availability`, "PUT", { availability: e.target.value }))
                        }
                      >
                        <option value="">Choose…</option>
                        {manualAvailability.map((a) => (
                          <option key={a} value={a}>{a}</option>
                        ))}
                      </select>
                    </label>
                  )}

                  {message && <p className="text-xs" style={message.error ? ERROR : undefined}>{message.text}</p>}

                  {confirmingDelete ? (
                    <div className="flex gap-2">
                      <button
                        type="button"
                        disabled={busy}
                        onClick={() =>
                          run(() => send(base, "DELETE"), () => {
                            setSelectedKey(null);
                            setConfirmingDelete(false);
                          })
                        }
                        className="rounded px-3 py-1.5 text-xs font-medium text-white disabled:opacity-50"
                        style={{ background: "#9B2335" }}
                      >
                        Remove permanently
                      </button>
                      <button
                        type="button"
                        onClick={() => setConfirmingDelete(false)}
                        className="rounded border border-[var(--2a-border)] px-3 py-1.5 text-xs text-[var(--2a-text-secondary)]"
                      >
                        Keep it
                      </button>
                    </div>
                  ) : (
                    <button
                      type="button"
                      onClick={() => setConfirmingDelete(true)}
                      className="text-xs underline decoration-dotted"
                      style={ERROR}
                    >
                      Remove this entry…
                    </button>
                  )}
                </div>
              )}
            </div>
          ) : (
            <div className="p-6 text-xs text-[var(--2a-text-muted)]">
              Select an entry to see its version, its last check, and why it is
              or is not available.
            </div>
          )}
        </div>
      </div>
    </section>
  );
}
