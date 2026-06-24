"use client";

import { formatPercent } from "@/lib/format";

export default function AllocationHeatMap({ allocations = [], taxonomy = null }) {
  if (allocations.length === 0) {
    return (
      <div className="rounded-lg border border-border bg-bg-card p-10 text-center text-sm text-text-muted">
        No allocation data available.
        {" "}Set targets for this entity to enable the heat map.
      </div>
    );
  }

  // Build display list: prefer taxonomy tree order, fallback to raw allocations
  const byKey = Object.fromEntries(allocations.map((a) => [a.taxonomy_key, a]));
  let rows = [];

  const scs = taxonomy?.super_classes || [];
  if (scs.length > 0) {
    for (const sc of scs) {
      const mcs = sc.major_classes || [];
      if (mcs.length > 0) {
        for (const mc of mcs) {
          const alloc = byKey[mc.key];
          if (alloc) rows.push({ ...alloc, super_class_label: sc.label });
        }
      } else {
        const alloc = byKey[sc.key];
        if (alloc) rows.push({ ...alloc, super_class_label: sc.label });
      }
    }
    // Append any keys not in taxonomy tree
    const treeKeys = new Set(rows.map((r) => r.taxonomy_key));
    for (const a of allocations) {
      if (!treeKeys.has(a.taxonomy_key)) rows.push(a);
    }
  } else {
    rows = allocations;
  }

  return (
    <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
      {rows.map((row) => {
        const actual = row.actual_pct ?? 0;
        const target = row.target_pct;
        const gap = row.gap_pct;
        const fillPct = Math.min(actual, 100);

        let barColor = "bg-gold";
        if (target != null) {
          if (actual >= target) barColor = "bg-green-500";
          else if (actual >= target * 0.7) barColor = "bg-yellow-400";
          else barColor = "bg-red-400";
        }

        return (
          <div
            key={row.taxonomy_key}
            className="rounded-lg border border-border bg-bg-card p-4"
          >
            {row.super_class_label && (
              <p className="text-[10px] font-semibold uppercase tracking-wide text-text-muted">
                {row.super_class_label}
              </p>
            )}
            <p className="mt-0.5 font-medium text-navy">
              {row.taxonomy_label || row.taxonomy_key}
            </p>

            <div className="mt-3 h-2 w-full overflow-hidden rounded-full bg-border">
              <div
                className={`h-full rounded-full transition-all ${barColor}`}
                style={{ width: `${fillPct}%` }}
              />
            </div>

            <div className="mt-2 flex items-center justify-between text-xs">
              <span className="text-text-secondary">
                Actual:{" "}
                <span className="font-semibold tabular-nums">
                  {formatPercent(actual)}
                </span>
              </span>
              {target != null && (
                <span className="text-text-muted">
                  Target:{" "}
                  <span className="font-medium tabular-nums">
                    {formatPercent(target)}
                  </span>
                </span>
              )}
            </div>

            {gap != null && (
              <p
                className={`mt-1 text-xs font-semibold tabular-nums ${
                  gap >= 0 ? "text-green-600" : "text-red-500"
                }`}
              >
                Gap: {gap >= 0 ? "+" : ""}
                {formatPercent(gap)}
              </p>
            )}
          </div>
        );
      })}
    </div>
  );
}
