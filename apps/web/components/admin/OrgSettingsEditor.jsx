"use client";

import { useEffect, useMemo, useState } from "react";

import { COLOR_LABELS, COLOR_VARS } from "@/lib/theme";

/**
 * Sprint 24 — category-grouped org settings editor.
 *
 * Shared by the Super Admin screen (which can switch orgs) and the Org Admin
 * screen (locked to the caller's own org). The backend is the real gate; this
 * component only decides what to render.
 */

const CATEGORY_ORDER = ["branding", "footer", "locale", "naming", "ai", "general"];

const CATEGORY_LABELS = {
  branding: "Branding",
  footer: "Footer",
  locale: "Locale",
  naming: "Naming",
  // Mini-Bedrock (S24) built ai.model.* as backend-only config; S25 surfaces it
  // here so an org_admin can see/edit the default, provider, fallback, and the
  // document-classifier task override through the real settings screen.
  ai: "AI Models",
  general: "General",
};

function isColorKey(key) {
  return Object.prototype.hasOwnProperty.call(COLOR_VARS, key);
}

const HEX_RE = /^#[0-9a-fA-F]{6}$/;

export default function OrgSettingsEditor({ orgId, orgName, canEdit = true }) {
  const [rows, setRows] = useState(null);
  const [draft, setDraft] = useState({});
  const [status, setStatus] = useState(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState(null);

  useEffect(() => {
    if (!orgId) return;
    let active = true;
    setRows(null);
    setDraft({});
    setError(null);
    setStatus(null);

    fetch(`/api/orgs/${orgId}/settings?detail=1`, { cache: "no-store" })
      .then((res) => (res.ok ? res.json() : Promise.reject(res)))
      .then((data) => {
        if (!active) return;
        setRows(data.settings || []);
      })
      .catch(async (res) => {
        if (!active) return;
        const body = await res?.json?.().catch(() => ({}));
        setError(body?.error || "Could not load settings.");
      });

    return () => {
      active = false;
    };
  }, [orgId]);

  const grouped = useMemo(() => {
    const byCategory = {};
    for (const row of rows || []) {
      (byCategory[row.category] ||= []).push(row);
    }
    return byCategory;
  }, [rows]);

  const dirtyKeys = Object.keys(draft);

  function valueOf(row) {
    return Object.prototype.hasOwnProperty.call(draft, row.key)
      ? draft[row.key]
      : row.value ?? "";
  }

  function setValue(key, value) {
    setDraft((d) => ({ ...d, [key]: value }));
    setStatus(null);
  }

  const invalidColors = dirtyKeys.filter(
    (k) => isColorKey(k) && draft[k] && !HEX_RE.test(draft[k]),
  );

  async function save() {
    if (invalidColors.length) return;
    setSaving(true);
    setError(null);
    try {
      const res = await fetch(`/api/orgs/${orgId}/settings`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        // Empty string clears a setting back to null rather than storing "".
        body: JSON.stringify({
          values: Object.fromEntries(
            dirtyKeys.map((k) => [k, draft[k] === "" ? null : draft[k]]),
          ),
        }),
      });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(body.error || "Save failed");

      // Re-read so is_default flags and updated_at reflect what was written.
      const fresh = await fetch(`/api/orgs/${orgId}/settings?detail=1`, {
        cache: "no-store",
      }).then((r) => r.json());
      setRows(fresh.settings || []);
      setDraft({});
      setStatus("Saved. Reload to see the new theme applied.");
    } catch (e) {
      setError(e.message);
    } finally {
      setSaving(false);
    }
  }

  if (error && !rows) {
    return (
      <div className="mt-6 rounded-md border border-border bg-bg-card p-10 text-center text-sm text-text-muted">
        {error}
      </div>
    );
  }

  if (!rows) {
    return (
      <div className="mt-6 rounded-md border border-border bg-bg-card p-10 text-center text-sm text-text-muted">
        Loading settings…
      </div>
    );
  }

  return (
    <div className="mt-6">
      <div className="flex items-center justify-between">
        <p className="text-sm text-text-muted">
          {orgName ? `Editing ${orgName}. ` : ""}
          Keys marked <em>default</em> have not been configured for this
          organization and are inherited.
        </p>
        {canEdit && (
          <button
            type="button"
            onClick={save}
            disabled={!dirtyKeys.length || saving || invalidColors.length > 0}
            className="rounded border px-4 py-2 text-sm font-medium transition-colors disabled:opacity-40"
            style={{
              background: "var(--2a-navy)",
              color: "var(--2a-bg)",
              borderColor: "var(--2a-navy)",
            }}
          >
            {saving ? "Saving…" : `Save ${dirtyKeys.length || ""}`.trim()}
          </button>
        )}
      </div>

      {invalidColors.length > 0 && (
        <p className="mt-2 text-sm" style={{ color: "#9B2335" }}>
          Colours must be 6-digit hex (#RRGGBB): {invalidColors.join(", ")}
        </p>
      )}
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

      {CATEGORY_ORDER.filter((c) => grouped[c]?.length).map((category) => (
        <section key={category} className="mt-8">
          <h2
            className="text-xs font-semibold uppercase"
            style={{ letterSpacing: "0.22em", color: "var(--2a-gold)" }}
          >
            {CATEGORY_LABELS[category] || category}
          </h2>

          <div className="mt-3 overflow-hidden rounded-md border border-border bg-bg-card">
            {grouped[category].map((row, i) => {
              const value = valueOf(row);
              const color = isColorKey(row.key);
              return (
                <div
                  key={row.key}
                  className="flex items-center gap-4 px-4 py-3"
                  style={{
                    borderTop: i === 0 ? "none" : "1px solid var(--2a-border)",
                  }}
                >
                  <div className="w-72 shrink-0">
                    <div className="text-sm text-text-primary">
                      {color ? COLOR_LABELS[row.key] : row.key}
                    </div>
                    {color && (
                      <div className="text-xs text-text-muted">{row.key}</div>
                    )}
                  </div>

                  {color && (
                    // Live swatch — shows the colour actually being entered.
                    <span
                      aria-hidden="true"
                      className="h-7 w-7 shrink-0 rounded border"
                      style={{
                        background: HEX_RE.test(value) ? value : "transparent",
                        borderColor: "var(--2a-border)",
                      }}
                    />
                  )}

                  <input
                    type="text"
                    value={value === null ? "" : value}
                    disabled={!canEdit}
                    onChange={(e) => setValue(row.key, e.target.value)}
                    placeholder={color ? "#RRGGBB" : "not set"}
                    className="flex-1 rounded border px-3 py-1.5 text-sm disabled:opacity-60"
                    style={{
                      borderColor: "var(--2a-border)",
                      background: "var(--2a-bg-card)",
                      fontFamily: color ? "ui-monospace, monospace" : undefined,
                    }}
                  />

                  {color && canEdit && (
                    <input
                      type="color"
                      aria-label={`${row.key} picker`}
                      value={HEX_RE.test(value) ? value : "#000000"}
                      onChange={(e) => setValue(row.key, e.target.value.toUpperCase())}
                      className="h-8 w-10 shrink-0 cursor-pointer rounded border"
                      style={{ borderColor: "var(--2a-border)" }}
                    />
                  )}

                  <span className="w-16 shrink-0 text-right text-xs text-text-muted">
                    {row.is_default ? "default" : ""}
                  </span>
                </div>
              );
            })}
          </div>
        </section>
      ))}
    </div>
  );
}
