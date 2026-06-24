"use client";

import { useState, useTransition } from "react";

async function apiFetch(path, options = {}) {
  const { method = "GET", body, searchParams } = options;
  const url = new URL(path, window.location.origin);
  if (searchParams) {
    for (const [k, v] of Object.entries(searchParams)) {
      if (v != null && v !== "") url.searchParams.set(k, v);
    }
  }
  const headers = {};
  if (body !== undefined) headers["Content-Type"] = "application/json";
  const res = await fetch(url, {
    method,
    headers,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) {
    let detail;
    try { detail = (await res.json()).detail; } catch { /* ignore */ }
    throw new Error(detail || `Request failed (${res.status})`);
  }
  if (res.status === 204) return null;
  return res.json();
}

export default function TargetEditor({ entity, taxonomy, initialTargets, apiBase }) {
  // Build a flat map of current targets keyed by taxonomy_key
  const [targets, setTargets] = useState(() => {
    const map = {};
    for (const t of initialTargets) {
      map[t.taxonomy_key] = t;
    }
    return map;
  });
  // Draft values: {taxonomy_key: string (input value)}
  const [drafts, setDrafts] = useState(() => {
    const map = {};
    for (const t of initialTargets) {
      if (!t.inherited) map[t.taxonomy_key] = String(t.target_pct);
    }
    return map;
  });
  const [error, setError] = useState(null);
  const [success, setSuccess] = useState(false);
  const [isPending, startTransition] = useTransition();

  const scs = taxonomy?.super_classes || [];

  // Collect all taxonomy keys to display (super_class + major_class)
  const rows = [];
  for (const sc of scs) {
    rows.push({ key: sc.key, label: sc.label, level: "super_class", scLabel: null });
    for (const mc of sc.major_classes || []) {
      rows.push({ key: mc.key, label: mc.label, level: "major_class", scLabel: sc.label });
    }
  }

  function handleChange(key, value) {
    setDrafts((prev) => ({ ...prev, [key]: value }));
    setSuccess(false);
  }

  function handleClearDraft(key) {
    setDrafts((prev) => {
      const next = { ...prev };
      delete next[key];
      return next;
    });
  }

  async function handleClearOverride(key) {
    setError(null);
    try {
      const res = await fetch(
        `${apiBase}/api/v1/portfolio/targets?entity_id=${entity.id}&taxonomy_key=${encodeURIComponent(key)}`,
        { method: "DELETE" },
      );
      if (!res.ok) throw new Error(`Failed (${res.status})`);
      // Refetch targets
      const updated = await fetch(
        `${apiBase}/api/v1/portfolio/targets?entity_id=${entity.id}`,
      ).then((r) => r.json());
      const map = {};
      const newDrafts = { ...drafts };
      for (const t of updated) {
        map[t.taxonomy_key] = t;
        if (!t.inherited) {
          newDrafts[t.taxonomy_key] = String(t.target_pct);
        } else {
          delete newDrafts[t.taxonomy_key];
        }
      }
      setTargets(map);
      setDrafts(newDrafts);
    } catch (err) {
      setError(err.message);
    }
  }

  async function handleSave() {
    setError(null);
    setSuccess(false);

    const items = Object.entries(drafts)
      .filter(([, v]) => v !== "" && !isNaN(Number(v)))
      .map(([taxonomy_key, v]) => ({
        taxonomy_key,
        target_pct: Number(v),
      }));

    if (items.length === 0) {
      setError("No valid target values to save.");
      return;
    }

    startTransition(async () => {
      try {
        const res = await fetch(
          `${apiBase}/api/v1/portfolio/targets?entity_id=${entity.id}`,
          {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ items }),
          },
        );
        if (!res.ok) {
          let detail;
          try { detail = (await res.json()).detail; } catch { /* ignore */ }
          throw new Error(detail || `Save failed (${res.status})`);
        }
        const updated = await res.json();
        const map = {};
        const newDrafts = {};
        for (const t of updated) {
          map[t.taxonomy_key] = t;
          if (!t.inherited) newDrafts[t.taxonomy_key] = String(t.target_pct);
        }
        setTargets(map);
        setDrafts(newDrafts);
        setSuccess(true);
      } catch (err) {
        setError(err.message);
      }
    });
  }

  return (
    <div className="space-y-6">
      {error && (
        <div className="rounded-md border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
          {error}
        </div>
      )}
      {success && (
        <div className="rounded-md border border-green-200 bg-green-50 px-4 py-3 text-sm text-green-700">
          Targets saved successfully.
        </div>
      )}

      <div className="overflow-hidden rounded-lg border border-border bg-bg-card">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-border bg-bg-sidebar">
              <th className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide text-text-muted">
                Asset Class
              </th>
              <th className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide text-text-muted">
                Current Target
              </th>
              <th className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide text-text-muted w-36">
                Set Target (%)
              </th>
              <th className="px-4 py-3" />
            </tr>
          </thead>
          <tbody className="divide-y divide-border">
            {rows.map((row) => {
              const current = targets[row.key];
              const draftVal = drafts[row.key] ?? "";
              const isInherited = current?.inherited === true;
              const hasDirectTarget = current && !isInherited;

              return (
                <tr
                  key={row.key}
                  className={row.level === "super_class" ? "bg-bg-sidebar" : ""}
                >
                  <td className="px-4 py-3">
                    {row.level === "major_class" && (
                      <span className="mr-2 text-text-muted">↳</span>
                    )}
                    <span
                      className={
                        row.level === "super_class"
                          ? "font-semibold text-navy"
                          : "text-text-secondary"
                      }
                    >
                      {row.label}
                    </span>
                    {row.scLabel && (
                      <span className="ml-2 text-xs text-text-muted">
                        {row.scLabel}
                      </span>
                    )}
                  </td>
                  <td className="px-4 py-3">
                    {current ? (
                      <div>
                        <span className={`tabular-nums font-medium ${isInherited ? "text-text-muted" : "text-navy"}`}>
                          {current.target_pct}%
                        </span>
                        {isInherited && (
                          <span className="ml-2 text-xs text-text-muted">
                            inherited from {current.inherited_from_entity_name || "parent"}
                          </span>
                        )}
                      </div>
                    ) : (
                      <span className="text-text-muted">—</span>
                    )}
                  </td>
                  <td className="px-4 py-3">
                    <input
                      type="number"
                      min="0"
                      max="100"
                      step="0.1"
                      value={draftVal}
                      onChange={(e) => handleChange(row.key, e.target.value)}
                      placeholder={isInherited ? `${current.target_pct} (inherited)` : "0.0"}
                      className="w-28 rounded border border-border bg-bg-app px-2 py-1 text-sm tabular-nums focus:border-navy focus:outline-none"
                    />
                  </td>
                  <td className="px-4 py-3 text-right">
                    {hasDirectTarget && (
                      <button
                        type="button"
                        onClick={() => handleClearOverride(row.key)}
                        className="text-xs text-text-muted hover:text-red-500 transition-colors"
                      >
                        Clear override
                      </button>
                    )}
                    {draftVal !== "" && (
                      <button
                        type="button"
                        onClick={() => handleClearDraft(row.key)}
                        className="ml-2 text-xs text-text-muted hover:text-text-secondary transition-colors"
                      >
                        ✕
                      </button>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      <div className="flex items-center justify-between">
        <p className="text-xs text-text-muted">
          Targets are stored historically. Previous values are preserved.
        </p>
        <button
          type="button"
          onClick={handleSave}
          disabled={isPending}
          className="rounded-md bg-navy px-4 py-2 text-sm font-medium text-white hover:bg-navy/90 disabled:opacity-50 transition-colors"
        >
          {isPending ? "Saving…" : "Save Targets"}
        </button>
      </div>
    </div>
  );
}
