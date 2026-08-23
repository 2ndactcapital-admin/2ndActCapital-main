"use client";

import { useMemo, useState } from "react";

/**
 * Sprint 31 — smile chart: market quotes vs the fitted SSVI curve, one
 * maturity at a time.
 *
 * The model curve is computed HERE, client-side, from rho/eta/gamma/theta_atm.
 * There is no round-trip to the server for chart points — the surface is three
 * numbers plus a per-slice theta, so shipping curve samples over the wire would
 * be strictly worse.
 *
 * Hand-rolled SVG: apps/web has no charting library and this sprint is not the
 * place to introduce one. Every colour is a `--2a-*` token, so the chart
 * re-themes with the tenant like everything else.
 */

// --- SSVI, mirrored from services/pricing/ssvi_surface.py --------------------
// phi(theta) = eta / (theta^gamma * (1+theta)^(1-gamma))
function ssviPhi(theta, eta, gamma) {
  return eta / (Math.pow(theta, gamma) * Math.pow(1 + theta, 1 - gamma));
}

// w(k) = theta/2 * (1 + rho*phi*k + sqrt((phi*k + rho)^2 + (1 - rho^2)))
function ssviW(k, theta, rho, phi) {
  const v = phi * k;
  return (
    0.5 * theta * (1 + rho * v + Math.sqrt((v + rho) ** 2 + (1 - rho * rho)))
  );
}

function modelIv(k, T, theta, rho, eta, gamma) {
  const w = ssviW(k, theta, rho, ssviPhi(theta, eta, gamma));
  return Math.sqrt(Math.max(w, 1e-12) / T);
}

const W = 760;
const H = 380;
const M = { top: 20, right: 20, bottom: 48, left: 62 };
const PLOT_W = W - M.left - M.right;
const PLOT_H = H - M.top - M.bottom;

function niceTicks(lo, hi, count) {
  if (!(hi > lo)) return [lo];
  const raw = (hi - lo) / count;
  const mag = Math.pow(10, Math.floor(Math.log10(raw)));
  const norm = raw / mag;
  const step = (norm >= 5 ? 10 : norm >= 2 ? 5 : norm >= 1 ? 2 : 1) * mag;
  const out = [];
  for (let t = Math.ceil(lo / step) * step; t <= hi + step * 1e-9; t += step) {
    out.push(t);
  }
  return out;
}

export default function SmileChart({ fit, marketPoints = [] }) {
  const slices = useMemo(
    () => [...(fit?.per_slice || [])].sort((a, b) => a.T - b.T),
    [fit],
  );
  const [selectedT, setSelectedT] = useState(null);

  const activeT = selectedT ?? slices[0]?.T ?? null;
  const activeSlice = slices.find((s) => s.T === activeT) || null;

  // The extrapolation boundary is the longest LISTED tenor. Take it from the
  // slice list rather than comparing against `fit.max_listed_maturity`: the
  // engine reports that unrounded while per_slice.T is rounded to 4dp, so an
  // equality test between the two would essentially never hold and the marker
  // would silently never appear.
  const boundaryT = slices.length ? slices[slices.length - 1].T : null;

  // Market points carry T rounded to the same 4dp as per_slice.T (see the
  // router), so an exact match is safe here.
  const points = useMemo(
    () => marketPoints.filter((p) => p.T === activeT),
    [marketPoints, activeT],
  );

  const chart = useMemo(() => {
    if (!activeSlice || points.length === 0) return null;

    const ks = points.map((p) => p.k);
    let kMin = Math.min(...ks);
    let kMax = Math.max(...ks);
    const kPad = (kMax - kMin) * 0.05 || 0.01;
    kMin -= kPad;
    kMax += kPad;

    const curve = [];
    const STEPS = 120;
    for (let i = 0; i <= STEPS; i += 1) {
      const k = kMin + ((kMax - kMin) * i) / STEPS;
      curve.push({
        k,
        iv: modelIv(k, activeSlice.T, activeSlice.theta_atm, fit.rho, fit.eta, fit.gamma),
      });
    }

    const ivs = [...points.map((p) => p.iv), ...curve.map((c) => c.iv)].filter(
      (v) => Number.isFinite(v),
    );
    let ivMin = Math.min(...ivs);
    let ivMax = Math.max(...ivs);
    const ivPad = (ivMax - ivMin) * 0.1 || 0.01;
    ivMin -= ivPad;
    ivMax += ivPad;

    const x = (k) => M.left + ((k - kMin) / (kMax - kMin)) * PLOT_W;
    const y = (iv) => M.top + PLOT_H - ((iv - ivMin) / (ivMax - ivMin)) * PLOT_H;

    return {
      curvePath: curve
        .map((c, i) => `${i === 0 ? "M" : "L"}${x(c.k).toFixed(2)},${y(c.iv).toFixed(2)}`)
        .join(" "),
      dots: points.map((p) => ({ cx: x(p.k), cy: y(p.iv), k: p.k, iv: p.iv })),
      xTicks: niceTicks(kMin, kMax, 6).map((t) => ({ v: t, px: x(t) })),
      yTicks: niceTicks(ivMin, ivMax, 5).map((t) => ({ v: t, py: y(t) })),
      zeroX: kMin < 0 && kMax > 0 ? x(0) : null,
    };
  }, [activeSlice, points, fit]);

  if (slices.length === 0) return null;

  const isBoundary =
    activeSlice != null && boundaryT != null && activeSlice.T === boundaryT;

  return (
    <section className="mt-6 rounded-md border border-border bg-bg-card p-6">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="text-xl font-semibold text-navy">Smile</h2>
        <p className="text-xs text-text-muted">
          Gold points are market quotes. The navy line is the fitted SSVI slice,
          computed in the browser from ρ, η, γ and this slice&apos;s θ.
        </p>
      </div>

      {/* Maturity selector, ordered short to long. The final pill is the last
          LISTED tenor — the extrapolation boundary. */}
      <div className="mt-4 flex flex-wrap items-center gap-2">
        <span className="text-xs uppercase tracking-[0.22em] text-gold">
          Maturity
        </span>
        {slices.map((s) => {
          const active = s.T === activeT;
          const boundary = s.T === boundaryT;
          return (
            <button
              key={s.T}
              type="button"
              onClick={() => setSelectedT(s.T)}
              className={`rounded-sm border px-2.5 py-1 text-xs tabular-nums transition-colors ${
                active
                  ? "border-navy bg-navy text-bg-card"
                  : "border-border bg-bg-app text-text-secondary hover:border-gold"
              }`}
              title={
                boundary
                  ? "Longest listed tenor — the extrapolation boundary"
                  : `${s.n_quotes} quotes`
              }
            >
              {s.T.toFixed(2)}y{boundary ? " ◆" : ""}
            </button>
          );
        })}
      </div>

      {isBoundary && (
        <p className="mt-3 rounded-sm border border-gold bg-bg-app px-3 py-2 text-xs text-text-secondary">
          ◆ Longest listed tenor. This is the extrapolation boundary — any
          maturity beyond {fit.max_listed_maturity.toFixed(2)}y is extrapolated,
          not observed.
        </p>
      )}

      {chart ? (
        <div className="mt-4 overflow-x-auto">
          <svg
            viewBox={`0 0 ${W} ${H}`}
            className="w-full min-w-[640px]"
            role="img"
            aria-label={`Implied volatility smile at ${activeT} years`}
          >
            <rect
              x={M.left}
              y={M.top}
              width={PLOT_W}
              height={PLOT_H}
              fill="var(--2a-bg)"
              stroke="var(--2a-border)"
            />

            {chart.yTicks.map((t) => (
              <g key={`y${t.v}`}>
                <line
                  x1={M.left}
                  x2={M.left + PLOT_W}
                  y1={t.py}
                  y2={t.py}
                  stroke="var(--2a-border)"
                  strokeDasharray="2 3"
                />
                <text
                  x={M.left - 8}
                  y={t.py + 4}
                  textAnchor="end"
                  fontSize="11"
                  fill="var(--2a-text-muted)"
                >
                  {(t.v * 100).toFixed(1)}%
                </text>
              </g>
            ))}

            {chart.xTicks.map((t) => (
              <g key={`x${t.v}`}>
                <line
                  y1={M.top}
                  y2={M.top + PLOT_H}
                  x1={t.px}
                  x2={t.px}
                  stroke="var(--2a-border)"
                  strokeDasharray="2 3"
                />
                <text
                  x={t.px}
                  y={M.top + PLOT_H + 16}
                  textAnchor="middle"
                  fontSize="11"
                  fill="var(--2a-text-muted)"
                >
                  {t.v.toFixed(2)}
                </text>
              </g>
            ))}

            {/* k = 0 is the forward — worth its own rule. */}
            {chart.zeroX != null && (
              <line
                x1={chart.zeroX}
                x2={chart.zeroX}
                y1={M.top}
                y2={M.top + PLOT_H}
                stroke="var(--2a-gold)"
                strokeWidth="1"
              />
            )}

            <path
              d={chart.curvePath}
              fill="none"
              stroke="var(--2a-navy)"
              strokeWidth="1.8"
            />

            {chart.dots.map((d, i) => (
              <circle
                key={i}
                cx={d.cx}
                cy={d.cy}
                r="3"
                fill="var(--2a-gold)"
                stroke="var(--2a-navy)"
                strokeWidth="0.6"
              >
                <title>{`k ${d.k.toFixed(4)} · IV ${(d.iv * 100).toFixed(2)}%`}</title>
              </circle>
            ))}

            <text
              x={M.left + PLOT_W / 2}
              y={H - 8}
              textAnchor="middle"
              fontSize="12"
              fill="var(--2a-text-secondary)"
            >
              log-moneyness k = ln(K/F)
            </text>
            <text
              transform={`rotate(-90 14 ${M.top + PLOT_H / 2})`}
              x={14}
              y={M.top + PLOT_H / 2}
              textAnchor="middle"
              fontSize="12"
              fill="var(--2a-text-secondary)"
            >
              implied volatility
            </text>
          </svg>
        </div>
      ) : (
        <p className="mt-4 text-sm text-text-muted">
          No market points returned for this maturity.
        </p>
      )}
    </section>
  );
}
