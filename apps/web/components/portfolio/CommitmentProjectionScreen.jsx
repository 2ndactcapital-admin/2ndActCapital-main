"use client";

/**
 * CommitmentProjectionScreen — TA Model Sprint 3.
 *
 * READ-ONLY: this screen displays a real commitment's real, saved projection
 * (GET /modeling/ta/projection/{commitment_id}) and offers a live "what if"
 * tool against the real, non-persisting preview endpoint (POST
 * /modeling/ta/projection/preview — proven in Sprint 1 to write nothing).
 * There is no save/edit control anywhere here — per the brief, projected
 * cash flows are NEVER persisted, so a "saved projection history" feature
 * would be building a screen for state that does not exist.
 *
 * PERMISSION GATING: the projection endpoint is all-or-nothing on
 * view_portfolio (require_permission -> 403), unlike the TA settings screen's
 * can_read/can_write envelope. There is no can_write here to gate — nothing
 * on this screen writes anything — so a 403 from the initial GET simply
 * means the whole screen renders nothing but a refusal message (Task 1b/4:
 * "proven server-side and by absent UI").
 *
 * DECIMAL DISCIPLINE (Task 3): every rendered monetary/rate value goes
 * through lib/decimalString.js's exact string formatters. Nothing here calls
 * Number()/parseFloat()/parseInt() on a period's contribution/distribution/
 * nav/cumulative_* or on any rate field.
 *
 * TA MODEL SPRINT 4 additions:
 *  - Task 4: a real confidence-tier card (ConfidenceTierCard) — the GET
 *    projection endpoint did not publish any confidence signal before this
 *    sprint (Task 1c: confirmed absent by reading this exact file).
 *  - Task 3: a real Calibrate panel (CalibratePanel), rendered ONLY when the
 *    server's own `projection.permissions.can_calibrate` is true — no
 *    client-side default, no `|| true` fallback (CLAUDE.md's Permission
 *    Envelope Pattern: a missing/false envelope must fail closed). Preview
 *    (dry_run) then confirm, against the real endpoint both times.
 *  - Task 2: a real obligation-ledger panel (ObligationLedgerPanel) — the
 *    36-month forward capital-call visibility view, computed at read time.
 */

import { useEffect, useMemo, useState } from "react";

import DataGrid from "@/components/ui/DataGrid";
import { formatApiError } from "@/components/admin/TriggerDetailPane";
import ProjectionChart from "@/components/portfolio/ProjectionChart";
import { formatMoneyExact, formatNumberExact, formatRateExact } from "@/lib/decimalString";

const CARD = { borderColor: "#ece8dd", boxShadow: "0 1px 3px rgba(0,0,0,0.06)" };
const EYEBROW = "block text-[10px] font-semibold uppercase tracking-[0.12em] text-[var(--2a-text-muted)]";
const CONTROL =
  "w-full rounded border border-[var(--2a-border)] bg-white px-2 py-1.5 text-xs text-[var(--2a-text)] focus:outline-none focus:ring-1 focus:ring-[var(--2a-gold)]";

function ReadRow({ label, children }) {
  return (
    <div className="flex items-baseline justify-between gap-3 border-b border-[var(--2a-border)] py-1.5 last:border-0">
      <span className={EYEBROW}>{label}</span>
      <span className="text-right text-xs text-[var(--2a-text)]">{children}</span>
    </div>
  );
}

const PERIOD_COLUMNS = [
  { field: "period", headerName: "Period", align: "right" },
  { field: "contribution", headerName: "Contribution", align: "right", cell: formatMoneyExact },
  { field: "distribution", headerName: "Distribution", align: "right", cell: formatMoneyExact },
  { field: "nav", headerName: "NAV", align: "right", cell: formatMoneyExact },
  { field: "cumulative_paid_in", headerName: "Cumulative paid-in", align: "right", cell: formatMoneyExact },
  { field: "cumulative_distributed", headerName: "Cumulative distributed", align: "right", cell: formatMoneyExact },
];

// ── strategy picker: shown only when the commitment has no active override
// and the real backend 422 says a strategy_key is required ──────────────

function StrategyPicker({ commitmentId, strategyKeys, detail, onLoaded }) {
  const [key, setKey] = useState(strategyKeys[0] || "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  async function load() {
    if (!key) return;
    setBusy(true);
    setError(null);
    try {
      const res = await fetch(
        `/api/modeling/ta/projection/${commitmentId}?strategy_key=${encodeURIComponent(key)}`,
        { cache: "no-store" },
      );
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        setError(formatApiError(data.error, "Could not load a projection for that strategy."));
        return;
      }
      onLoaded(data);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="rounded-lg border bg-white p-6" style={CARD}>
      <p className="text-sm text-[var(--2a-text)]">
        This commitment has no calibrated or overridden TA parameters yet — pick
        a strategy to project against its platform defaults.
      </p>
      {detail && <p className="mt-1 text-xs text-[var(--2a-text-muted)]">{detail}</p>}
      <div className="mt-3 flex items-end gap-2">
        <label className="block">
          <span className={EYEBROW}>Strategy</span>
          <select className={CONTROL} value={key} onChange={(e) => setKey(e.target.value)}>
            {strategyKeys.length === 0 && <option value="">No strategies available</option>}
            {strategyKeys.map((k) => (
              <option key={k} value={k}>{k.replace(/_/g, " ")}</option>
            ))}
          </select>
        </label>
        <button
          type="button"
          disabled={busy || !key}
          onClick={load}
          className="rounded bg-[var(--2a-navy)] px-3 py-1.5 text-xs font-medium text-white disabled:opacity-50"
        >
          {busy ? "Loading…" : "Project"}
        </button>
      </div>
      {error && <p className="mt-2 text-xs" style={{ color: "#9B2335" }}>{error}</p>}
    </div>
  );
}

// ── "what if" panel: live preview via the real, non-persisting endpoint ──

function WhatIfPanel({ projection }) {
  const params = projection.params;
  const [form, setForm] = useState({
    bow_factor: params.bow_factor,
    growth_rate: params.growth_rate,
    periods_per_year: String(params.periods_per_year),
  });
  const [preview, setPreview] = useState(null);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);

  const set = (field, value) => setForm((f) => ({ ...f, [field]: value }));

  async function runPreview() {
    setBusy(true);
    setError(null);
    try {
      const res = await fetch("/api/modeling/ta/projection/preview", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          params_override: {
            rate_of_contribution: params.rate_of_contribution,
            rate_of_distribution: params.rate_of_distribution,
            growth_rate: form.growth_rate,
            bow_factor: form.bow_factor,
            fund_life_years: params.fund_life_years,
            periods_per_year: Number(form.periods_per_year),
          },
          committed_capital: projection.committed_capital,
          called_to_date: projection.called_to_date,
          distributed_to_date: projection.distributed_to_date,
          current_nav: projection.current_nav,
          horizon_periods: projection.periods.length,
        }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        setError(formatApiError(data.error, "Could not compute a preview."));
        return;
      }
      setPreview(data);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="rounded-lg border bg-white p-4" style={CARD}>
      <div className="mb-3 flex items-baseline justify-between">
        <h2 className="font-[Spectral,Georgia,serif] text-base text-[var(--2a-navy)]">
          What if…
        </h2>
        <span className="rounded-full bg-[#F5EEDD] px-2 py-0.5 text-[10px] font-medium uppercase tracking-[0.08em] text-[var(--2a-navy)]">
          Preview — not saved
        </span>
      </div>
      <p className="mb-3 text-xs text-[var(--2a-text-muted)]">
        Adjust an assumption and see its effect. This calls the real preview
        endpoint every time — nothing here is computed in the browser, and
        nothing it returns is ever written to this commitment.
      </p>

      <div className="grid grid-cols-3 gap-3">
        <label className="block">
          <span className={EYEBROW}>Bow factor</span>
          <input
            className={CONTROL} value={form.bow_factor} inputMode="decimal"
            onChange={(e) => set("bow_factor", e.target.value)}
          />
        </label>
        <label className="block">
          <span className={EYEBROW}>Growth rate (per period)</span>
          <input
            className={CONTROL} value={form.growth_rate} inputMode="decimal"
            onChange={(e) => set("growth_rate", e.target.value)}
          />
        </label>
        <label className="block">
          <span className={EYEBROW}>Periods per year</span>
          <input
            className={CONTROL} value={form.periods_per_year} inputMode="numeric"
            onChange={(e) => set("periods_per_year", e.target.value)}
          />
        </label>
      </div>

      <div className="mt-3">
        <button
          type="button"
          disabled={busy}
          onClick={runPreview}
          className="rounded bg-[var(--2a-navy)] px-3 py-1.5 text-xs font-medium text-white disabled:opacity-50"
        >
          {busy ? "Computing…" : "Run preview"}
        </button>
      </div>

      {error && <p className="mt-2 text-xs" style={{ color: "#9B2335" }}>{error}</p>}

      {preview && (
        <div className="mt-4 border-t border-[var(--2a-border)] pt-3">
          <p className="mb-2 text-[10px] uppercase tracking-[0.12em] text-[var(--2a-text-muted)]">
            Preview result — {preview.periods.length} periods, unsaved
          </p>
          <ProjectionChart periods={preview.periods} />
        </div>
      )}
    </div>
  );
}

// ── confidence tier — TA Model Sprint 4, Task 4 ──────────────────────────

const TIER_LABEL = {
  OBSERVED: "Observed",
  ASSUMED: "Assumed",
  STRATEGY_DEFAULT: "Strategy default",
  PEER_CALIBRATED: "Peer calibrated",
};

const TIER_COLOR = {
  OBSERVED: { bg: "#EAF3EC", text: "#2D6A4F" },
  ASSUMED: { bg: "#FBEFEF", text: "#9B2335" },
  STRATEGY_DEFAULT: { bg: "#F5EEDD", text: "var(--2a-navy)" },
  PEER_CALIBRATED: { bg: "#F5EEDD", text: "var(--2a-navy)" },
};

function ConfidenceTierCard({ projection }) {
  const tier = projection.confidence_tier;
  const color = TIER_COLOR[tier] || TIER_COLOR.STRATEGY_DEFAULT;
  return (
    <div className="rounded-lg border bg-white p-4" style={CARD}>
      <div className="mb-2 flex items-center justify-between">
        <h2 className="font-[Spectral,Georgia,serif] text-base text-[var(--2a-navy)]">
          Confidence
        </h2>
        <span
          className="rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase tracking-[0.08em]"
          style={{ backgroundColor: color.bg, color: color.text }}
        >
          {TIER_LABEL[tier] || tier}
        </span>
      </div>
      {/* Plain-language explanation, sourced from the server — never a
          client-side re-derivation, since the real evidentiary basis (an
          override's `source`) lives in the database, not in this component. */}
      <p className="text-xs text-[var(--2a-text-muted)]">
        {projection.confidence_description}
      </p>
    </div>
  );
}

// ── Calibrate panel — TA Model Sprint 4, Task 3 ──────────────────────────
// Preview (dry_run) then confirm, both against the real POST /calibrate
// endpoint (gated server-side on manage_portfolio — a real, stricter gate
// than the view_portfolio reads elsewhere on this screen, per Task 1b).
// Rendered by the parent ONLY when projection.permissions.can_calibrate is
// true — see the screen's own render logic below.

async function postCalibrate(commitmentId, { taStrategyKey, periodsPerYear, dryRun }) {
  const res = await fetch(`/api/modeling/ta/calibrate/${commitmentId}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      ta_strategy_key: taStrategyKey,
      periods_per_year: periodsPerYear,
      dry_run: dryRun,
    }),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const err = new Error(formatApiError(data.error, "Calibration was refused."));
    err.status = res.status;
    throw err;
  }
  return data;
}

function CalibratePanel({ commitmentId, projection, onCalibrated }) {
  const [strategyKey, setStrategyKey] = useState(projection.ta_strategy_key);
  const [periodsPerYear, setPeriodsPerYear] = useState(String(projection.params.periods_per_year));
  const [preview, setPreview] = useState(null);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  const [confirmed, setConfirmed] = useState(false);

  async function runPreview() {
    setBusy(true);
    setError(null);
    setPreview(null);
    setConfirmed(false);
    try {
      const data = await postCalibrate(commitmentId, {
        taStrategyKey: strategyKey, periodsPerYear: Number(periodsPerYear), dryRun: true,
      });
      setPreview(data);
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }

  async function confirmCalibration() {
    setBusy(true);
    setError(null);
    try {
      const data = await postCalibrate(commitmentId, {
        taStrategyKey: strategyKey, periodsPerYear: Number(periodsPerYear), dryRun: false,
      });
      setConfirmed(true);
      setPreview(null);
      onCalibrated(data);
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="rounded-lg border bg-white p-4" style={CARD}>
      <div className="mb-2 flex items-baseline justify-between">
        <h2 className="font-[Spectral,Georgia,serif] text-base text-[var(--2a-navy)]">
          Calibrate
        </h2>
        <span className="rounded-full bg-[#F5EEDD] px-2 py-0.5 text-[10px] font-medium uppercase tracking-[0.08em] text-[var(--2a-navy)]">
          Preview — not saved
        </span>
      </div>
      <p className="mb-3 text-xs text-[var(--2a-text-muted)]">
        Fits this commitment&rsquo;s parameters to its own realized capital
        calls and distributions, already on file — nothing to upload. Preview
        the result before it is saved as this commitment&rsquo;s active
        parameters.
      </p>

      <div className="grid grid-cols-2 gap-3">
        <label className="block">
          <span className={EYEBROW}>Strategy</span>
          <select
            className={CONTROL} value={strategyKey}
            onChange={(e) => { setStrategyKey(e.target.value); setPreview(null); setConfirmed(false); }}
          >
            <option value={strategyKey}>{strategyKey.replace(/_/g, " ")}</option>
          </select>
        </label>
        <label className="block">
          <span className={EYEBROW}>Frequency</span>
          <select
            className={CONTROL} value={periodsPerYear}
            onChange={(e) => { setPeriodsPerYear(e.target.value); setPreview(null); setConfirmed(false); }}
          >
            <option value="1">Annual</option>
            <option value="4">Quarterly</option>
          </select>
        </label>
      </div>

      <div className="mt-3 flex gap-2">
        <button
          type="button" disabled={busy} onClick={runPreview}
          className="rounded border border-[var(--2a-navy)] px-3 py-1.5 text-xs font-medium text-[var(--2a-navy)] disabled:opacity-50"
        >
          {busy && !preview ? "Fitting…" : "Preview calibration"}
        </button>
        {preview && (
          <button
            type="button" disabled={busy} onClick={confirmCalibration}
            className="rounded bg-[var(--2a-navy)] px-3 py-1.5 text-xs font-medium text-white disabled:opacity-50"
          >
            {busy ? "Saving…" : "Confirm calibration"}
          </button>
        )}
      </div>

      {/* The server's own refusal message, verbatim — e.g. the frequency-
          aware minimum-periods floor's real text — never re-derived here. */}
      {error && <p className="mt-2 text-xs" style={{ color: "#9B2335" }}>{error}</p>}

      {confirmed && (
        <p className="mt-2 text-xs" style={{ color: "#2D6A4F" }}>
          Calibration saved — this commitment&rsquo;s parameters and confidence
          tier above now reflect it.
        </p>
      )}

      {preview && !confirmed && (
        <div className="mt-3 border-t border-[var(--2a-border)] pt-3">
          <p className="mb-2 text-[10px] uppercase tracking-[0.12em] text-[var(--2a-text-muted)]">
            Fitted from {preview.realized_periods_used} realized period(s) — would upgrade
            confidence to {TIER_LABEL[preview.confidence_tier] || preview.confidence_tier}
          </p>
          <ReadRow label="Rate of contribution">{formatRateExact(preview.params.rate_of_contribution)}</ReadRow>
          <ReadRow label="Rate of distribution">{formatRateExact(preview.params.rate_of_distribution)}</ReadRow>
          <ReadRow label="Growth rate">{formatRateExact(preview.params.growth_rate)}</ReadRow>
        </div>
      )}
    </div>
  );
}

// ── Obligation ledger — TA Model Sprint 4, Task 2 ────────────────────────
// A real 36-month forward capital-call visibility view, fetched from the
// real, read-time-only endpoint (GET /modeling/ta/obligations/{id}) — never
// computed client-side, never persisted server-side.

function ObligationLedgerPanel({ commitmentId, projection }) {
  const [ledger, setLedger] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    let cancelled = false;
    async function load() {
      try {
        const res = await fetch(
          `/api/modeling/ta/obligations/${commitmentId}?strategy_key=${encodeURIComponent(projection.ta_strategy_key)}`,
          { cache: "no-store" },
        );
        const data = await res.json().catch(() => ({}));
        if (!res.ok) {
          if (!cancelled) setError(formatApiError(data.error, "Could not load the obligation ledger."));
          return;
        }
        if (!cancelled) setLedger(data);
      } catch {
        if (!cancelled) setError("Could not load the obligation ledger.");
      }
    }
    load();
    return () => { cancelled = true; };
  }, [commitmentId, projection.ta_strategy_key]);

  return (
    <div className="rounded-lg border bg-white p-4" style={CARD}>
      <div className="mb-2 flex items-baseline justify-between">
        <h2 className="font-[Spectral,Georgia,serif] text-base text-[var(--2a-navy)]">
          Obligation ledger
        </h2>
        <span className="text-[10px] uppercase tracking-[0.12em] text-[var(--2a-text-muted)]">
          computed live, never saved
        </span>
      </div>
      {error && <p className="text-xs" style={{ color: "#9B2335" }}>{error}</p>}
      {!error && !ledger && <p className="text-xs text-[var(--2a-text-muted)]">Loading…</p>}
      {ledger && (
        <>
          <ReadRow label={`Next ${ledger.visibility_horizon_years} years — projected calls`}>
            {formatMoneyExact(ledger.total_projected_contribution)}
          </ReadRow>
          {ledger.by_year.map((y) => (
            <ReadRow key={y.year_offset} label={`Year ${y.year_offset + 1}`}>
              {formatMoneyExact(y.projected_contribution)}
            </ReadRow>
          ))}
        </>
      )}
    </div>
  );
}

// ── screen ────────────────────────────────────────────────────────────

export default function CommitmentProjectionScreen({
  commitmentId,
  initialProjection,
  initialError,
  strategyKeys,
}) {
  const [projection, setProjection] = useState(initialProjection);
  const [loadError, setLoadError] = useState(initialError);

  const rows = useMemo(
    () => (projection?.periods || []).map((p) => ({ ...p, id: p.period })),
    [projection],
  );

  if (loadError?.status === 403) {
    return (
      <div className="rounded-lg border bg-white p-10 text-center text-sm text-[var(--2a-text-muted)]" style={CARD}>
        You do not have permission to view this commitment&rsquo;s projection.
      </div>
    );
  }
  if (loadError?.status === 404) {
    return (
      <div className="rounded-lg border bg-white p-10 text-center text-sm text-[var(--2a-text-muted)]" style={CARD}>
        No commitment was found with that id.
      </div>
    );
  }
  if (loadError?.status === 422 && !projection) {
    return (
      <StrategyPicker
        commitmentId={commitmentId}
        strategyKeys={strategyKeys}
        detail={loadError.message}
        onLoaded={(data) => {
          setProjection(data);
          setLoadError(null);
        }}
      />
    );
  }
  if (loadError && !projection) {
    return (
      <div className="rounded-lg border bg-white p-10 text-center text-sm" style={{ ...CARD, color: "#9B2335" }}>
        Could not load this commitment&rsquo;s projection: {loadError.message}
      </div>
    );
  }
  if (!projection) return null;

  // TA MODEL SPRINT 4, TASK 4 — a fresh, independent GET after a real
  // calibration is confirmed, so the confidence tier/params displayed above
  // reflect the upgrade on the same load, not only after a manual refresh.
  async function refreshProjection() {
    const res = await fetch(`/api/modeling/ta/projection/${commitmentId}`, { cache: "no-store" });
    const data = await res.json().catch(() => ({}));
    if (res.ok) setProjection(data);
  }

  return (
    <div className="space-y-4">
      <div className="rounded-lg border bg-white p-4" style={CARD}>
        <div className="mb-3 flex items-baseline justify-between">
          <h2 className="font-[Spectral,Georgia,serif] text-base text-[var(--2a-navy)]">
            Projected cash flows — {projection.ta_strategy_key.replace(/_/g, " ")}
          </h2>
          <span className="text-[10px] uppercase tracking-[0.12em] text-[var(--2a-text-muted)]">
            {projection.periods.length} periods
          </span>
        </div>
        <ProjectionChart periods={projection.periods} />
      </div>

      <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_20rem]">
        <div className="rounded-lg border bg-white p-4" style={CARD}>
          <h2 className="mb-3 font-[Spectral,Georgia,serif] text-base text-[var(--2a-navy)]">
            By period
          </h2>
          <DataGrid
            gridId="ta-projection-periods"
            columnDefs={PERIOD_COLUMNS}
            rowData={rows}
            getRowId={(row) => row.id}
            emptyMessage="No projected periods."
          />
        </div>

        <div className="space-y-4">
          <ConfidenceTierCard projection={projection} />

          <div className="rounded-lg border bg-white p-4" style={CARD}>
            <h2 className="mb-3 font-[Spectral,Georgia,serif] text-base text-[var(--2a-navy)]">
              Current state
            </h2>
            <ReadRow label="Current NAV">{formatMoneyExact(projection.current_nav)}</ReadRow>
            <ReadRow label="Committed capital">{formatMoneyExact(projection.committed_capital)}</ReadRow>
            <ReadRow label="Called to date">{formatMoneyExact(projection.called_to_date)}</ReadRow>
            <ReadRow label="Distributed to date">{formatMoneyExact(projection.distributed_to_date)}</ReadRow>
          </div>

          <div className="rounded-lg border bg-white p-4" style={CARD}>
            <h2 className="mb-3 font-[Spectral,Georgia,serif] text-base text-[var(--2a-navy)]">
              Parameters
            </h2>
            <ReadRow label="Rate of contribution">{formatRateExact(projection.params.rate_of_contribution)}</ReadRow>
            <ReadRow label="Rate of distribution">{formatRateExact(projection.params.rate_of_distribution)}</ReadRow>
            <ReadRow label="Growth rate">{formatRateExact(projection.params.growth_rate)}</ReadRow>
            <ReadRow label="Bow factor">{formatNumberExact(projection.params.bow_factor)}</ReadRow>
            <ReadRow label="Fund life">{formatNumberExact(projection.params.fund_life_years)} yrs</ReadRow>
            <ReadRow label="Periods/year">{projection.params.periods_per_year}</ReadRow>
          </div>

          <ObligationLedgerPanel commitmentId={commitmentId} projection={projection} />
        </div>
      </div>

      {/* Task 3: fail-closed on the server's own envelope — no truthy
          fallback. A missing/false can_calibrate renders no Calibrate
          control at all, per CLAUDE.md's Permission Envelope Pattern. */}
      {projection.permissions?.can_calibrate === true && (
        <CalibratePanel commitmentId={commitmentId} projection={projection} onCalibrated={refreshProjection} />
      )}

      <WhatIfPanel projection={projection} />
    </div>
  );
}
