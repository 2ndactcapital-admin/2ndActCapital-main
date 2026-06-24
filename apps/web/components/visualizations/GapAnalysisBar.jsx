"use client";

import { formatPercent } from "@/lib/format";

export default function GapAnalysisBar({ allocations = [] }) {
  const items = allocations.filter(
    (a) => a.target_pct != null || a.actual_pct > 0,
  );

  if (items.length === 0) {
    return (
      <div className="rounded-lg border border-border bg-bg-card p-10 text-center text-sm text-text-muted">
        No target allocations configured.{" "}
        <span className="block mt-1 text-xs">
          Have an advisor set targets for this entity in the CRM to see gap analysis.
        </span>
      </div>
    );
  }

  return (
    <div className="rounded-lg border border-border bg-bg-card p-6">
      <div className="space-y-5">
        {items.map((item) => {
          const actual = item.actual_pct ?? 0;
          const target = item.target_pct ?? 0;
          const maxPct = Math.max(actual, target, 5);
          const actualW = Math.round((actual / maxPct) * 100);
          const targetW = Math.round((target / maxPct) * 100);
          const gap = item.gap_pct;

          return (
            <div key={item.taxonomy_key}>
              <div className="flex items-center justify-between mb-1">
                <span className="text-sm font-medium text-text-secondary">
                  {item.taxonomy_label || item.taxonomy_key}
                </span>
                {gap != null && (
                  <span
                    className={`text-xs font-semibold tabular-nums ${
                      gap >= 0 ? "text-green-600" : "text-red-500"
                    }`}
                  >
                    {gap >= 0 ? "+" : ""}
                    {formatPercent(gap)}
                  </span>
                )}
              </div>

              <div className="relative h-6 w-full rounded overflow-hidden bg-border">
                {/* Actual fill */}
                <div
                  className="absolute top-0 left-0 h-full bg-gold transition-all"
                  style={{ width: `${actualW}%` }}
                />
                {/* Target marker */}
                {target > 0 && (
                  <div
                    className="absolute top-0 h-full w-0.5 bg-navy z-10"
                    style={{ left: `${targetW}%` }}
                  />
                )}
                {/* Label overlay */}
                <div className="absolute inset-0 flex items-center px-2">
                  <span className="text-[10px] font-semibold text-navy">
                    {formatPercent(actual)}
                  </span>
                </div>
              </div>

              <div className="flex items-center gap-4 mt-1 text-xs text-text-muted">
                <span>
                  Actual:{" "}
                  <strong className="tabular-nums">{formatPercent(actual)}</strong>
                </span>
                {item.target_pct != null && (
                  <span>
                    Target:{" "}
                    <strong className="tabular-nums">
                      {formatPercent(item.target_pct)}
                    </strong>
                  </span>
                )}
              </div>
            </div>
          );
        })}
      </div>

      {/* Legend */}
      <div className="mt-6 flex items-center gap-5 border-t border-border pt-4 text-xs text-text-muted">
        <span className="flex items-center gap-1.5">
          <span className="inline-block h-3 w-8 rounded bg-gold" />
          Actual allocation
        </span>
        <span className="flex items-center gap-1.5">
          <span className="inline-block h-3 w-0.5 bg-navy" />
          Target
        </span>
      </div>
    </div>
  );
}
