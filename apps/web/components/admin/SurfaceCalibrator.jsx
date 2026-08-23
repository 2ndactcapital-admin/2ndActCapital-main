"use client";

import { useEffect, useRef, useState } from "react";
import DataGrid from "@/components/ui/DataGrid";
import SmileChart from "@/components/admin/SmileChart";

/**
 * Sprint 31 — SSVI surface calibration: input card, headline diagnostics,
 * per-slice table, smile chart.
 *
 * A REJECTED FIT IS A LEGITIMATE OUTCOME, not an error to hide. The quality
 * gate exists precisely so a surface SSVI cannot reproduce is refused rather
 * than quietly returned. Failures therefore render as first-class panels with
 * the breached threshold spelled out — never as a toast that disappears.
 */

const TICKERS = [
  { value: "^SPX", label: "^SPX — S&P 500 index options" },
  { value: "^XSP", label: "^XSP — Mini-SPX index options" },
];

// The request is one synchronous call, so these phases are elapsed-time
// estimates over a 15-40s run, not server progress events. They exist because
// a 40s spinner with no text reads as a hang. Labelled as "typically" in the UI
// so nobody mistakes them for a real progress feed.
const PHASES = [
  { at: 0, label: "Fetching option chains…" },
  { at: 10000, label: "Calibrating the surface…" },
  { at: 22000, label: "Validating no-arbitrage and fit quality…" },
];

const FAILURE_TITLES = {
  insufficient_data: "Not enough liquid quotes",
  quality_gate_failed: "Fit rejected by the quality gate",
  arbitrage_violation: "Fit rejected — no-arbitrage violation",
  invalid_ticker: "Unsupported ticker",
  insufficient_memory: "Not enough memory to run",
  out_of_memory: "Ran out of memory mid-calibration",
  module_unavailable: "Pricing engine unavailable",
  data_provider_error: "Market data provider failed",
  timeout: "Calibration timed out",
  forbidden: "Super Admin access required",
  unauthorized: "Not signed in",
  unexpected_error: "Unexpected failure",
};

/** vol points, 2dp — the unit the desk actually reads. */
function volPoints(decimalVol) {
  if (decimalVol == null || !Number.isFinite(decimalVol)) return "—";
  return `${(decimalVol * 100).toFixed(2)}`;
}

/** Green under 1.0 vol point, amber 1.0-1.5, red above. */
function rmseTone(decimalVol) {
  if (decimalVol == null || !Number.isFinite(decimalVol)) return "text-text-muted";
  const vp = decimalVol * 100;
  if (vp < 1.0) return "text-green-700";
  if (vp <= 1.5) return "text-amber-600";
  return "text-red-600";
}

/**
 * Pull the breached threshold out of the engine's message so the UI can say by
 * how much, e.g. "pooled IV RMSE 0.0210 exceeds tolerance 0.0150".
 */
function parseQualityBreach(detail) {
  if (!detail) return null;
  const m = detail.match(
    /(pooled|peak slice) IV RMSE\s+([0-9.]+)\s+exceeds tolerance\s+([0-9.]+)/i,
  );
  if (!m) return null;
  const actual = Number(m[2]);
  const tolerance = Number(m[3]);
  if (!Number.isFinite(actual) || !Number.isFinite(tolerance)) return null;
  return {
    which: m[1].toLowerCase() === "pooled" ? "Pooled" : "Peak slice",
    actual,
    tolerance,
    overBy: actual - tolerance,
  };
}

function Metric({ label, value, sub, tone }) {
  return (
    <div className="border-l border-border pl-4 first:border-l-0 first:pl-0">
      <div className="text-xs uppercase tracking-[0.22em] text-gold">{label}</div>
      <div className={`mt-1 text-2xl tabular-nums ${tone || "text-navy"}`}>
        {value}
      </div>
      {sub && <div className="mt-0.5 text-xs text-text-muted">{sub}</div>}
    </div>
  );
}

export default function SurfaceCalibrator() {
  const [ticker, setTicker] = useState("^SPX");
  const [busy, setBusy] = useState(false);
  const [phase, setPhase] = useState(PHASES[0].label);
  const [result, setResult] = useState(null);
  const [failure, setFailure] = useState(null);
  const timers = useRef([]);

  useEffect(() => () => timers.current.forEach(clearTimeout), []);

  async function calibrate() {
    setBusy(true);
    setResult(null);
    setFailure(null);
    setPhase(PHASES[0].label);

    timers.current.forEach(clearTimeout);
    timers.current = PHASES.slice(1).map((p) =>
      setTimeout(() => setPhase(p.label), p.at),
    );

    try {
      const res = await fetch("/api/admin/pricing/surface", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ticker }),
      });
      const data = await res.json().catch(() => null);

      if (!res.ok || !data || data.status !== "ok") {
        setFailure({
          status: data?.status || "unexpected_error",
          detail: data?.detail || `Request failed (${res.status}).`,
        });
      } else {
        setResult(data);
      }
    } catch (error) {
      setFailure({
        status: "unexpected_error",
        detail: error?.message || "Request failed.",
      });
    } finally {
      timers.current.forEach(clearTimeout);
      timers.current = [];
      setBusy(false);
    }
  }

  const fit = result?.fit;
  const breach =
    failure?.status === "quality_gate_failed"
      ? parseQualityBreach(failure.detail)
      : null;

  const sliceColumns = [
    {
      field: "T",
      headerName: "T (years)",
      align: "right",
      cell: (v) => (v == null ? "—" : v.toFixed(4)),
    },
    {
      field: "theta_atm",
      headerName: "θ ATM",
      align: "right",
      cell: (v) => (v == null ? "—" : v.toFixed(6)),
    },
    { field: "n_quotes", headerName: "Quotes", align: "right" },
    {
      field: "rmse_iv",
      headerName: "RMSE IV (vol pts)",
      align: "right",
      cell: (v) => volPoints(v),
    },
    {
      field: "max_abs_iv_err",
      headerName: "Max abs IV err (vol pts)",
      align: "right",
      cell: (v) => volPoints(v),
    },
    {
      field: "forward",
      headerName: "Forward",
      align: "right",
      cell: (v) => (v == null ? "—" : v.toFixed(2)),
    },
    {
      field: "discount_factor",
      headerName: "Discount factor",
      align: "right",
      cell: (v) => (v == null ? "—" : v.toFixed(6)),
    },
  ];

  return (
    <div className="mt-6">
      {/* ── Input ─────────────────────────────────────────────────────────── */}
      <section className="rounded-md border border-border bg-bg-card p-6">
        <div className="flex flex-wrap items-end gap-4">
          <label className="flex flex-col gap-1">
            <span className="text-xs uppercase tracking-[0.22em] text-gold">
              Underlying
            </span>
            <select
              value={ticker}
              onChange={(e) => setTicker(e.target.value)}
              disabled={busy}
              className="rounded-sm border border-border bg-bg-app px-3 py-2 text-sm text-text-primary disabled:opacity-60"
            >
              {TICKERS.map((t) => (
                <option key={t.value} value={t.value}>
                  {t.label}
                </option>
              ))}
            </select>
          </label>

          <button
            type="button"
            onClick={calibrate}
            disabled={busy}
            className="rounded-sm bg-navy px-5 py-2 text-sm font-semibold text-bg-card transition-opacity hover:opacity-90 disabled:opacity-60"
          >
            {busy ? "Calibrating…" : "Calibrate"}
          </button>

          {busy && (
            <span className="text-sm text-text-secondary" aria-live="polite">
              {phase}{" "}
              <span className="text-text-muted">(typically 15–40s)</span>
            </span>
          )}
        </div>
        <p className="mt-3 max-w-3xl text-xs text-text-muted">
          European-exercise index options only. The engine never falls back to
          SPY — SPY is American exercise, which turns put-call parity into an
          inequality and would silently bias the derived forward.
        </p>
      </section>

      {/* ── Failure ───────────────────────────────────────────────────────── */}
      {failure && (
        <section className="mt-6 rounded-md border border-border bg-bg-card p-6">
          <div className="text-xs uppercase tracking-[0.22em] text-gold">
            {failure.status}
          </div>
          <h2 className="mt-1 text-xl font-semibold text-navy">
            {FAILURE_TITLES[failure.status] || "Calibration failed"}
          </h2>
          <p className="mt-2 max-w-3xl text-sm text-text-secondary">
            {failure.detail}
          </p>

          {breach && (
            <div className="mt-4 rounded-sm border border-border bg-bg-app p-4">
              <div className="text-xs uppercase tracking-[0.22em] text-gold">
                Threshold breached
              </div>
              <div className="mt-2 grid gap-4 sm:grid-cols-3">
                <Metric
                  label={`${breach.which} RMSE`}
                  value={volPoints(breach.actual)}
                  sub="vol points"
                  tone={rmseTone(breach.actual)}
                />
                <Metric
                  label="Tolerance"
                  value={volPoints(breach.tolerance)}
                  sub="vol points"
                />
                <Metric
                  label="Over by"
                  value={volPoints(breach.overBy)}
                  sub="vol points"
                  tone="text-red-600"
                />
              </div>
            </div>
          )}

          {failure.status === "quality_gate_failed" && (
            <p className="mt-4 max-w-3xl text-xs text-text-muted">
              This is the gate working, not a bug. A surface SSVI&apos;s three
              parameters cannot reproduce is refused rather than returned. If
              this persists on liquid quotes, the answer is eSSVI or per-slice
              SVI with calendar constraints — not a looser tolerance.
            </p>
          )}
        </section>
      )}

      {/* ── Results ───────────────────────────────────────────────────────── */}
      {fit && (
        <>
          <section className="mt-6 rounded-md border border-border bg-bg-card p-6">
            <div className="flex flex-wrap items-baseline justify-between gap-2">
              <h2 className="text-xl font-semibold text-navy">
                {fit.ticker} — calibrated surface
              </h2>
              <span className="text-xs text-text-muted">
                as of {fit.as_of} · engine v{fit.version}
              </span>
            </div>

            <div className="mt-5 grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
              <Metric
                label="ρ"
                value={fit.rho.toFixed(4)}
                sub="skew (well identified)"
              />
              <Metric
                label="η"
                value={fit.eta.toFixed(4)}
                sub="weakly identified — expect drift"
              />
              <Metric
                label="γ"
                value={fit.gamma.toFixed(4)}
                sub="weakly identified — expect drift"
              />
              <Metric
                label="Pooled IV RMSE"
                value={volPoints(fit.rmse_iv_pooled)}
                sub="vol points"
                tone={rmseTone(fit.rmse_iv_pooled)}
              />
              <Metric
                label="Peak slice IV RMSE"
                value={volPoints(fit.rmse_iv_peak_slice)}
                sub="vol points"
              />
              <Metric
                label="Quotes / maturities"
                value={`${fit.n_points} / ${fit.n_slices}`}
                sub="observations used"
              />
              <Metric
                label="Longest listed tenor"
                value={`${fit.max_listed_maturity.toFixed(2)}y`}
                sub="beyond this is extrapolation"
              />
            </div>

            {fit.theta_adjusted && (
              <p className="mt-5 rounded-sm border border-amber-600 bg-bg-app px-3 py-2 text-sm text-amber-700">
                θ adjusted — the ATM total-variance term structure was not
                monotone and a PAVA adjustment was applied. SSVI&apos;s
                no-calendar-arbitrage guarantee is conditional on that
                monotonicity, so the fit is to the adjusted θ, not the raw one.
              </p>
            )}

            {result.expiry_cap && (
              <p className="mt-3 rounded-sm border border-border bg-bg-app px-3 py-2 text-sm text-text-secondary">
                Expiry cap bound: {result.expiry_cap.processed} of{" "}
                {result.expiry_cap.available} in-window expiries processed (cap{" "}
                {result.expiry_cap.cap}). {result.expiry_cap.note}
              </p>
            )}

            {result.memory?.peak_rss_mb != null && (
              <p className="mt-3 text-xs text-text-muted">
                Peak RSS {result.memory.peak_rss_mb} MB
                {result.memory.limit_mb != null
                  ? ` of ${result.memory.limit_mb} MB (${result.memory.source})`
                  : ""}
                .
              </p>
            )}
          </section>

          <section className="mt-6">
            <h2 className="mb-3 text-xl font-semibold text-navy">Per-slice fit</h2>
            <DataGrid
              gridId="ssvi-per-slice"
              columnDefs={sliceColumns}
              rowData={fit.per_slice || []}
              getRowId={(row) => String(row.T)}
              enablePagination={false}
              enableGlobalFilter={false}
              emptyMessage="No slices in the fit."
            />
          </section>

          <SmileChart fit={fit} marketPoints={result.market_points || []} />
        </>
      )}
    </div>
  );
}
