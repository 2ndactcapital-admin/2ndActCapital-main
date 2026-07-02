"use client";

import { useEffect, useState } from "react";

const STATE_COLORS = {
  none:     "#F4F1E9",
  under:    "#C9A24B",
  on:       "#2F6B4F",
  over:     "#7E2B2B",
  off_plan: "#3A3A3C",
};

const STATE_GLYPHS = { none: "", under: "↓", on: "✓", over: "↑", off_plan: "!" };
const STATE_LABELS = {
  none: "Unallocated",
  under: "Under",
  on: "On target",
  over: "Over",
  off_plan: "Off plan",
};

export default function AllocationSunburstCompact({ selector_type, entity_id }) {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    const p = new URLSearchParams({ selector_type: selector_type || "entity" });
    if (entity_id) p.set("entity_id", entity_id);
    fetch(`/api/allocation-lens?${p}`, { cache: "no-store" })
      .then((r) => (r.ok ? r.json() : Promise.reject(r.status)))
      .then(setData)
      .catch(() => setError(true));
  }, [selector_type, entity_id]);

  if (error) return <p className="text-sm text-[#64748B]">Could not load allocation data.</p>;
  if (!data) return <p className="text-sm text-[#64748B]">Loading allocation…</p>;

  const scs = data.super_classes || [];

  return (
    <div className="mt-2 rounded border border-[#ece8dd] bg-white overflow-hidden">
      <div className="px-3 py-2 border-b border-[#F5F1EB] flex items-baseline justify-between">
        <span className="text-xs font-semibold uppercase tracking-wide text-[#C5A880]">
          Allocation
        </span>
        <a
          href="/portfolio/allocation"
          className="text-xs text-[#64748B] hover:text-[#1B2B4B] transition-colors"
        >
          Full view →
        </a>
      </div>
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-[#F5F1EB]">
            <th className="px-3 py-1.5 text-left text-[11px] font-semibold uppercase tracking-wide text-[#64748B]">Class</th>
            <th className="px-3 py-1.5 text-right text-[11px] font-semibold uppercase tracking-wide text-[#64748B]">Actual</th>
            <th className="px-3 py-1.5 text-right text-[11px] font-semibold uppercase tracking-wide text-[#64748B]">Target</th>
            <th className="px-3 py-1.5 text-right text-[11px] font-semibold uppercase tracking-wide text-[#64748B]">State</th>
          </tr>
        </thead>
        <tbody>
          {scs.filter((sc) => sc.actual_pct > 0 || sc.target_pct > 0).map((sc) => (
            <tr key={sc.key} className="border-b border-[#F5F1EB] last:border-0">
              <td className="px-3 py-1.5 text-[#0F172A]">{sc.label}</td>
              <td className="px-3 py-1.5 text-right tabular-nums text-[#334155]">
                {sc.actual_pct.toFixed(1)}%
              </td>
              <td className="px-3 py-1.5 text-right tabular-nums text-[#64748B]">
                {sc.target_pct.toFixed(1)}%
              </td>
              <td className="px-3 py-1.5 text-right">
                <span
                  style={{
                    display: "inline-flex",
                    alignItems: "center",
                    gap: 3,
                    background: STATE_COLORS[sc.state],
                    color: ["on", "over", "off_plan"].includes(sc.state) ? "#FFFFFF" : "#1B2B4B",
                    borderRadius: 4,
                    padding: "1px 6px",
                    fontSize: 11,
                    fontWeight: 500,
                  }}
                >
                  {STATE_GLYPHS[sc.state]} {STATE_LABELS[sc.state]}
                </span>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
