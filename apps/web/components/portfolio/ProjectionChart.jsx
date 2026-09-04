"use client";

/**
 * ProjectionChart — plain inline SVG, no charting library.
 *
 * TA Model Sprint 3, Task 1d: apps/web/package.json carries no charting
 * dependency (checked directly) and the standing rule is to reuse whatever's
 * established, never introduce one unilaterally — so this draws bars/line
 * with raw SVG instead.
 *
 * Bar/line COORDINATES are computed via chartFraction (lib/decimalString.js),
 * which does use `Number()` — that is fine here: pixel geometry has no
 * display-precision requirement. No text label in this component is derived
 * that way; every label goes through the exact string formatters.
 */

import { chartFraction, formatMoneyExact } from "@/lib/decimalString";

const HEIGHT = 220;
const PAD = { top: 12, right: 12, bottom: 24, left: 12 };

export default function ProjectionChart({ periods }) {
  if (!periods || periods.length === 0) {
    return (
      <div className="flex h-[220px] items-center justify-center text-xs text-[var(--2a-text-muted)]">
        No periods to chart.
      </div>
    );
  }

  const width = Math.max(periods.length * 14, 320);
  const innerW = width - PAD.left - PAD.right;
  const innerH = HEIGHT - PAD.top - PAD.bottom;

  // The max across ALL series determines the shared scale — a NUMBER used
  // only for axis scaling, never rendered as text (chartFraction handles the
  // float-vs-string boundary explicitly).
  const maxima = periods.flatMap((p) => [
    Number(p.contribution), Number(p.distribution), Number(p.nav),
  ]);
  const maxValue = Math.max(1, ...maxima.filter(Number.isFinite));

  const barWidth = Math.max(2, innerW / periods.length / 3);
  const navPoints = periods
    .map((p, i) => {
      const x = PAD.left + (i + 0.5) * (innerW / periods.length);
      const y = PAD.top + innerH * (1 - chartFraction(p.nav, maxValue));
      return `${x},${y}`;
    })
    .join(" ");

  return (
    <div className="overflow-x-auto">
      <svg
        role="img"
        aria-label="Projected contributions, distributions and NAV by period"
        width={width}
        height={HEIGHT}
        viewBox={`0 0 ${width} ${HEIGHT}`}
      >
        <line
          x1={PAD.left} y1={PAD.top + innerH} x2={width - PAD.right} y2={PAD.top + innerH}
          stroke="var(--2a-border)" strokeWidth="1"
        />
        {periods.map((p, i) => {
          const slotX = PAD.left + i * (innerW / periods.length);
          const contribH = innerH * chartFraction(p.contribution, maxValue);
          const distH = innerH * chartFraction(p.distribution, maxValue);
          return (
            <g key={p.period}>
              <title>
                {`Period ${p.period} — contribution ${formatMoneyExact(p.contribution)}, `
                  + `distribution ${formatMoneyExact(p.distribution)}, nav ${formatMoneyExact(p.nav)}`}
              </title>
              <rect
                x={slotX + barWidth * 0.4} y={PAD.top + innerH - contribH}
                width={barWidth} height={contribH} fill="var(--2a-navy)" opacity="0.75"
              />
              <rect
                x={slotX + barWidth * 1.6} y={PAD.top + innerH - distH}
                width={barWidth} height={distH} fill="var(--2a-gold)" opacity="0.9"
              />
            </g>
          );
        })}
        <polyline points={navPoints} fill="none" stroke="#334155" strokeWidth="1.5" />
      </svg>
      <div className="mt-2 flex gap-4 text-[10px] text-[var(--2a-text-muted)]">
        <span><span className="inline-block h-2 w-2 bg-[var(--2a-navy)] opacity-75" /> Contribution</span>
        <span><span className="inline-block h-2 w-2 bg-[var(--2a-gold)]" /> Distribution</span>
        <span><span className="inline-block h-2 w-[10px] border-t-2 border-[#334155]" /> NAV</span>
      </div>
    </div>
  );
}
