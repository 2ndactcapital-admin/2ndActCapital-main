"use client";

/**
 * TaSettingsScreen — TA Model admin settings (TA Model Sprint 2).
 *
 * Same real, established pattern as the Workflow Triggers screen
 * (WorkflowTriggerScheduler.jsx / TriggerDetailPane.jsx): a DataGrid list —
 * here, the 8 real TA strategies — with a right-pane editor, built on a
 * server-published `permissions` envelope with NO client-side fallback.
 * `formatApiError` is imported from TriggerDetailPane rather than
 * re-implemented, so a real 422/400/403 body is rendered verbatim here the
 * same way it is there — one formatter, not two copies that could drift.
 *
 * WHY THERE IS NO CLIENT-SIDE bow>0 / RATE-RANGE VALIDATION HERE
 * ─────────────────────────────────────────────────────────────────────────
 * Every numeric rule (bow_factor >= 0, rates in [0,1], fund_life_years > 0,
 * periods_per_year >= 1) lives in services.ta_model.TAParams.__post_init__,
 * enforced identically whether the router is validating a settings write or
 * resolving a projection. This screen sends what the admin typed and shows
 * whatever the API says back — the message text, not a re-derived one.
 *
 * WHY EVERY RATE/BOW/LIFE FIELD IS A PLAIN TEXT INPUT, NEVER type="number"
 * ─────────────────────────────────────────────────────────────────────────
 * These are Decimal values serialized as JSON STRINGS end to end (CLAUDE.md:
 * "never round-trip a rate through a JS float that could re-serialize
 * imprecisely"). A number input coerces through a JS float on every
 * keystroke; a text input keeps the exact characters the admin typed until
 * the moment they are sent, unmodified, as a string.
 *
 * WHY A SAVE SENDS ONLY THE ONE EDITED STRATEGY, NOT ALL 8
 * ─────────────────────────────────────────────────────────────────────────
 * `modeling.ta.strategy_defaults` is one jsonb blob covering all 8 strategies
 * (Task 1a). The API now MERGES a partial per-strategy submission into the
 * org's existing blob (apps/api/routers/modeling_ta.py) rather than replacing
 * it wholesale — the fix this sprint made for the real clobber bug a naive
 * "always send the full object" client would otherwise need to work around.
 * This screen relies on that merge and sends only what the admin changed.
 *
 * PLATFORM-DEFAULT VS. YOUR-OVERRIDE
 * ─────────────────────────────────────────────────────────────────────────
 * `strategy_overrides[key]` comes from the server (services.ta_config.
 * strategy_overrides — a real Decimal-value comparison against the seed, not
 * a per-row "have I ever written this key" flag, since the row is one blob
 * for all 8 strategies and cannot itself carry per-strategy provenance).
 */

import { useCallback, useEffect, useMemo, useState } from "react";

import DataGrid from "@/components/ui/DataGrid";
import { formatApiError } from "@/components/admin/TriggerDetailPane";

const CARD = { borderColor: "#ece8dd", boxShadow: "0 1px 3px rgba(0,0,0,0.06)" };
const CONTROL =
  "w-full rounded border border-[var(--2a-border)] bg-white px-2 py-1.5 text-xs text-[var(--2a-text)] focus:outline-none focus:ring-1 focus:ring-[var(--2a-gold)] disabled:bg-[var(--2a-bg)] disabled:text-[var(--2a-text-muted)]";
const EYEBROW =
  "block text-[10px] font-semibold uppercase tracking-[0.12em] text-[var(--2a-text-muted)]";

const STRATEGY_FIELDS = [
  ["rate_of_contribution", "Rate of contribution (per period)"],
  ["rate_of_distribution", "Rate of distribution (per period)"],
  ["growth_rate", "NAV growth rate (per period)"],
  ["bow_factor", "Bow factor"],
  ["fund_life_years", "Fund life (years)"],
];

const SETTINGS_KEY = {
  strategyDefaults: "modeling.ta.strategy_defaults",
  horizonYears: "modeling.ta.projection_horizon_years",
  periodsPerYear: "modeling.ta.default_periods_per_year",
  calibrationMinYears: "modeling.ta.calibration_min_years",
};

// "growth_equity" -> "Growth equity". A mechanical transform of the real
// server-sent key, not a hardcoded label lookup that could drift from
// services.ta_config.TA_STRATEGY_KEYS.
function humanize(key) {
  const s = key.replace(/_/g, " ");
  return s.charAt(0).toUpperCase() + s.slice(1);
}

function Field({ label, children, hint }) {
  return (
    <label className="block">
      <span className={EYEBROW}>{label}</span>
      <div className="mt-1">{children}</div>
      {hint ? (
        <span className="mt-1 block text-[10px] text-[var(--2a-text-muted)]">
          {hint}
        </span>
      ) : null}
    </label>
  );
}

function ReadRow({ label, children }) {
  return (
    <div className="flex items-baseline justify-between gap-3 border-b border-[var(--2a-border)] py-1.5 last:border-0">
      <span className={EYEBROW}>{label}</span>
      <span className="text-right text-xs text-[var(--2a-text)]">{children}</span>
    </div>
  );
}

function OverridePill({ isOverride }) {
  return (
    <span
      className="rounded-full px-2 py-0.5 text-[10px] font-medium uppercase tracking-[0.08em]"
      style={
        isOverride
          ? { background: "#F5EEDD", color: "var(--2a-navy)" }
          : { background: "var(--2a-bg)", color: "var(--2a-text-muted)" }
      }
    >
      {isOverride ? "Your override" : "Platform default"}
    </span>
  );
}

function paramsFromStrategy(strategyDefaults, key) {
  const raw = strategyDefaults?.[key] || {};
  return {
    rate_of_contribution: raw.rate_of_contribution ?? "",
    rate_of_distribution: raw.rate_of_distribution ?? "",
    growth_rate: raw.growth_rate ?? "",
    bow_factor: raw.bow_factor ?? "",
    fund_life_years: raw.fund_life_years ?? "",
  };
}

// ── Right pane: one strategy's params ───────────────────────────────────

function StrategyDetailPane({
  strategyKey,
  strategyDefaults,
  isOverride,
  canWrite,
  onSaved,
}) {
  const [editing, setEditing] = useState(false);
  const [form, setForm] = useState(() =>
    paramsFromStrategy(strategyDefaults, strategyKey),
  );
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    setForm(paramsFromStrategy(strategyDefaults, strategyKey));
    setEditing(false);
    setError(null);
  }, [strategyKey, strategyDefaults]);

  if (!strategyKey) {
    return (
      <div
        className="rounded-lg border bg-white p-6 text-xs text-[var(--2a-text-muted)]"
        style={CARD}
      >
        Select a strategy to see its full TA parameters and — with the
        manage-settings permission — edit them.
      </div>
    );
  }

  const set = (field, value) => setForm((f) => ({ ...f, [field]: value }));

  async function save() {
    setBusy(true);
    setError(null);
    try {
      const res = await fetch("/api/admin/modeling/ta/defaults", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          values: {
            [SETTINGS_KEY.strategyDefaults]: { [strategyKey]: form },
          },
        }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        setError(formatApiError(data.error, "Could not save this strategy."));
        return;
      }
      setEditing(false);
      onSaved?.(data);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="rounded-lg border bg-white" style={CARD}>
      <div className="flex items-start justify-between gap-3 border-b border-[var(--2a-border)] px-4 py-3">
        <div>
          <h2 className="font-[Spectral,Georgia,serif] text-base text-[var(--2a-navy)]">
            {humanize(strategyKey)}
          </h2>
        </div>
        <OverridePill isOverride={isOverride} />
      </div>

      <div className="space-y-4 px-4 py-4">
        {!editing && (
          <div>
            {STRATEGY_FIELDS.map(([field, label]) => (
              <ReadRow key={field} label={label}>
                {form[field] === "" ? "—" : form[field]}
              </ReadRow>
            ))}
          </div>
        )}

        {canWrite && !editing && (
          <div className="border-t border-[var(--2a-border)] pt-3">
            <button
              type="button"
              onClick={() => setEditing(true)}
              className="rounded border border-[var(--2a-navy)] px-3 py-1.5 text-xs text-[var(--2a-navy)] hover:bg-[var(--2a-bg)]"
            >
              Edit
            </button>
          </div>
        )}

        {canWrite && editing && (
          <div className="space-y-3 border-t border-[var(--2a-border)] pt-3">
            {STRATEGY_FIELDS.map(([field, label]) => (
              <Field key={field} label={label}>
                <input
                  className={CONTROL}
                  value={form[field]}
                  onChange={(e) => set(field, e.target.value)}
                  placeholder="e.g. 0.0788"
                  inputMode="decimal"
                />
              </Field>
            ))}
            <p className="text-[10px] leading-relaxed text-[var(--2a-text-muted)]">
              Rate/growth figures are PER PERIOD at this org&rsquo;s configured
              periods-per-year, not annual — the compound-equivalent rate for
              an annual target. Sent and validated exactly as typed.
            </p>
            <div className="flex gap-2 pt-1">
              <button
                type="button"
                disabled={busy}
                onClick={save}
                className="rounded bg-[var(--2a-navy)] px-3 py-1.5 text-xs font-medium text-white disabled:opacity-50"
              >
                {busy ? "Saving…" : "Save changes"}
              </button>
              <button
                type="button"
                onClick={() => {
                  setForm(paramsFromStrategy(strategyDefaults, strategyKey));
                  setEditing(false);
                  setError(null);
                }}
                className="rounded border border-[var(--2a-border)] px-3 py-1.5 text-xs text-[var(--2a-text-secondary)]"
              >
                Cancel
              </button>
            </div>
          </div>
        )}

        {error && (
          <p className="text-xs" style={{ color: "#9B2335" }}>
            {error}
          </p>
        )}
      </div>
    </div>
  );
}

// ── Platform-level settings (horizon / frequency / calibration floor) ───

function PlatformSettingsCard({ envelope, canWrite, onSaved }) {
  const [editing, setEditing] = useState(false);
  const [form, setForm] = useState(() => ({
    horizonYears: String(envelope[SETTINGS_KEY.horizonYears] ?? ""),
    periodsPerYear: String(envelope[SETTINGS_KEY.periodsPerYear] ?? ""),
    calibrationMinYears: String(envelope[SETTINGS_KEY.calibrationMinYears] ?? ""),
  }));
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  const [floor, setFloor] = useState(null);
  const [floorError, setFloorError] = useState(null);

  useEffect(() => {
    setForm({
      horizonYears: String(envelope[SETTINGS_KEY.horizonYears] ?? ""),
      periodsPerYear: String(envelope[SETTINGS_KEY.periodsPerYear] ?? ""),
      calibrationMinYears: String(envelope[SETTINGS_KEY.calibrationMinYears] ?? ""),
    });
    setEditing(false);
    setError(null);
  }, [envelope]);

  // The REAL frequency-aware calibration floor, from the API's own
  // ta_calibrate.minimum_realized_periods — recomputed whenever the admin
  // changes periods_per_year while editing, never re-derived in the browser.
  useEffect(() => {
    if (!editing) return;
    const ppy = Number(form.periodsPerYear);
    if (!Number.isInteger(ppy) || ppy < 1) {
      setFloor(null);
      setFloorError(null);
      return;
    }
    let cancelled = false;
    setFloorError(null);
    fetch(`/api/admin/modeling/ta/calibration-floor?periods_per_year=${ppy}`, {
      cache: "no-store",
    })
      .then(async (res) => {
        const data = await res.json().catch(() => ({}));
        if (cancelled) return;
        if (!res.ok) {
          setFloor(null);
          setFloorError(formatApiError(data.error, "Could not compute the floor."));
          return;
        }
        setFloor(data);
      })
      .catch(() => {
        if (!cancelled) setFloorError("Could not compute the floor.");
      });
    return () => {
      cancelled = true;
    };
  }, [editing, form.periodsPerYear]);

  async function save() {
    setBusy(true);
    setError(null);
    try {
      const res = await fetch("/api/admin/modeling/ta/defaults", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          values: {
            [SETTINGS_KEY.horizonYears]: Number(form.horizonYears),
            [SETTINGS_KEY.periodsPerYear]: Number(form.periodsPerYear),
            [SETTINGS_KEY.calibrationMinYears]: Number(form.calibrationMinYears),
          },
        }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        setError(formatApiError(data.error, "Could not save platform settings."));
        return;
      }
      setEditing(false);
      onSaved?.(data);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="rounded-lg border bg-white p-4" style={CARD}>
      <div className="mb-3 flex items-baseline justify-between">
        <h2 className="font-[Spectral,Georgia,serif] text-base text-[var(--2a-navy)]">
          Platform-level settings
        </h2>
        {canWrite && !editing && (
          <button
            type="button"
            onClick={() => setEditing(true)}
            className="rounded border border-[var(--2a-navy)] px-3 py-1.5 text-xs text-[var(--2a-navy)] hover:bg-[var(--2a-bg)]"
          >
            Edit
          </button>
        )}
      </div>

      {!editing ? (
        <div>
          <ReadRow label="Projection horizon">{form.horizonYears} years</ReadRow>
          <ReadRow label="Default periods per year">{form.periodsPerYear}</ReadRow>
          <ReadRow label="Calibration minimum">{form.calibrationMinYears} years</ReadRow>
        </div>
      ) : (
        <div className="space-y-3">
          <div className="grid grid-cols-3 gap-3">
            <Field label="Projection horizon (years)">
              <input
                type="number"
                min="1"
                className={CONTROL}
                value={form.horizonYears}
                onChange={(e) =>
                  setForm((f) => ({ ...f, horizonYears: e.target.value }))
                }
              />
            </Field>
            <Field label="Default periods per year">
              <input
                type="number"
                min="1"
                className={CONTROL}
                value={form.periodsPerYear}
                onChange={(e) =>
                  setForm((f) => ({ ...f, periodsPerYear: e.target.value }))
                }
              />
            </Field>
            <Field label="Calibration minimum (years)">
              <input
                type="number"
                min="1"
                className={CONTROL}
                value={form.calibrationMinYears}
                onChange={(e) =>
                  setForm((f) => ({ ...f, calibrationMinYears: e.target.value }))
                }
              />
            </Field>
          </div>

          <div className="rounded border border-[var(--2a-border)] bg-[var(--2a-bg)] p-3">
            <span className={EYEBROW}>Resulting calibration floor</span>
            {floorError ? (
              <p className="mt-1 text-[11px]" style={{ color: "#9B2335" }}>
                {floorError}
              </p>
            ) : floor ? (
              <p className="mt-1 text-xs text-[var(--2a-text)]">
                At {floor.periods_per_year} periods/year, calibrating a
                commitment needs at least{" "}
                <strong>{floor.minimum_realized_periods} realized periods</strong>{" "}
                ({floor.calibration_min_years} years) of history — computed by
                the real calibration engine, not re-derived here.
              </p>
            ) : (
              <p className="mt-1 text-[11px] text-[var(--2a-text-muted)]">
                Enter a valid periods-per-year to see the real floor.
              </p>
            )}
          </div>

          <div className="flex gap-2 pt-1">
            <button
              type="button"
              disabled={busy}
              onClick={save}
              className="rounded bg-[var(--2a-navy)] px-3 py-1.5 text-xs font-medium text-white disabled:opacity-50"
            >
              {busy ? "Saving…" : "Save changes"}
            </button>
            <button
              type="button"
              onClick={() => {
                setForm({
                  horizonYears: String(envelope[SETTINGS_KEY.horizonYears] ?? ""),
                  periodsPerYear: String(envelope[SETTINGS_KEY.periodsPerYear] ?? ""),
                  calibrationMinYears: String(
                    envelope[SETTINGS_KEY.calibrationMinYears] ?? "",
                  ),
                });
                setEditing(false);
                setError(null);
              }}
              className="rounded border border-[var(--2a-border)] px-3 py-1.5 text-xs text-[var(--2a-text-secondary)]"
            >
              Cancel
            </button>
          </div>
        </div>
      )}

      {error && (
        <p className="mt-2 text-xs" style={{ color: "#9B2335" }}>
          {error}
        </p>
      )}
    </div>
  );
}

// ── Screen ────────────────────────────────────────────────────────────

export default function TaSettingsScreen({ initialEnvelope }) {
  const [envelope, setEnvelope] = useState(initialEnvelope);
  // NO FALLBACK. can_write is false unless the server said otherwise.
  const [permissions, setPermissions] = useState(
    initialEnvelope?.permissions || { can_read: true, can_write: false },
  );
  const [selectedKey, setSelectedKey] = useState(null);
  const [loadError, setLoadError] = useState(null);

  const canWrite = !!permissions?.can_write;
  const strategyDefaults = envelope?.[SETTINGS_KEY.strategyDefaults] || {};
  const strategyOverrides = envelope?.strategy_overrides || {};

  const reload = useCallback(async () => {
    try {
      const res = await fetch("/api/admin/modeling/ta/defaults", {
        cache: "no-store",
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        setLoadError(
          typeof data.error === "string" ? data.error : "Could not load TA settings.",
        );
        return;
      }
      setEnvelope(data);
      if (data.permissions) setPermissions(data.permissions);
      setLoadError(null);
    } catch (e) {
      setLoadError(e.message);
    }
  }, []);

  const rows = useMemo(
    () =>
      Object.keys(strategyDefaults).map((key) => ({
        key,
        label: humanize(key),
        is_override: !!strategyOverrides[key],
        ...paramsFromStrategy(strategyDefaults, key),
      })),
    [strategyDefaults, strategyOverrides],
  );

  const columnDefs = useMemo(
    () => [
      {
        field: "label",
        headerName: "Strategy",
        enableColumnFilter: true,
        filterPlaceholder: "Strategy…",
        cell: (value) => (
          <span className="font-medium text-[var(--2a-navy)]">{value}</span>
        ),
      },
      {
        field: "is_override",
        headerName: "Source",
        cell: (value) => <OverridePill isOverride={value} />,
      },
      { field: "bow_factor", headerName: "Bow", align: "right" },
      {
        field: "rate_of_contribution",
        headerName: "Contribution/period",
        align: "right",
      },
      {
        field: "rate_of_distribution",
        headerName: "Distribution/period",
        align: "right",
      },
      { field: "growth_rate", headerName: "Growth/period", align: "right" },
      { field: "fund_life_years", headerName: "Fund life (yrs)", align: "right" },
    ],
    [],
  );

  // A save's own PUT response already carries the fresh envelope, but this
  // re-reads through the real GET anyway — the same "reload after every
  // mutation" discipline the Triggers screen uses, so the screen never trusts
  // its own write echo over an independent read (and stays correct if
  // another admin changed a different strategy in the meantime).
  function handleSaved() {
    reload();
  }

  const overrideCount = Object.values(strategyOverrides).filter(Boolean).length;

  return (
    <div className="space-y-4">
      <PlatformSettingsCard
        envelope={envelope || {}}
        canWrite={canWrite}
        onSaved={handleSaved}
      />

      <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_22rem]">
        <div className="rounded-lg border bg-white p-4" style={CARD}>
          <div className="mb-3 flex flex-wrap items-baseline justify-between gap-2">
            <p className="text-xs text-[var(--2a-text-muted)]">
              {rows.length} strateg{rows.length === 1 ? "y" : "ies"} —{" "}
              {overrideCount} overridden, {rows.length - overrideCount} on
              platform default
            </p>
            {!canWrite && (
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
            gridId="ta-strategy-defaults"
            columnDefs={columnDefs}
            rowData={rows}
            getRowId={(row) => row.key}
            selectedRowId={selectedKey}
            onRowClick={(row) => setSelectedKey(row.key)}
            quickFilterPlaceholder="Search strategies…"
            emptyMessage="No TA strategies configured."
          />
        </div>

        <StrategyDetailPane
          strategyKey={selectedKey}
          strategyDefaults={strategyDefaults}
          isOverride={!!strategyOverrides[selectedKey]}
          canWrite={canWrite}
          onSaved={handleSaved}
        />
      </div>
    </div>
  );
}
