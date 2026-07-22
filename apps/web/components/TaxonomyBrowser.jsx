"use client";

import { useState } from "react";

// One accent color per super-class (index 0–7).
const SC_COLORS = [
  "var(--2a-navy)", // Navy        — Private Real Estate
  "var(--2a-gold)", // Gold        — Private Equity
  "#2C6FAC", // Blue        — Private Credit
  "#3A7D44", // Green       — Infrastructure / Energy
  "#7C3AED", // Purple      — Hedge Funds
  "#D97706", // Amber       — Commodities
  "#E11D48", // Rose        — Volatility / Derivatives
  "#0891B2", // Cyan        — Private Debt
];

function SuperClassSection({ sc, color }) {
  const [open, setOpen] = useState(true);

  return (
    <div
      className="overflow-hidden rounded-lg border border-border bg-bg-card"
      style={{ borderLeftWidth: "4px", borderLeftColor: color }}
    >
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center justify-between px-5 py-4 text-left"
      >
        <div>
          <span className="text-base font-semibold text-navy">{sc.label}</span>
          <span className="ml-3 text-xs text-text-muted">
            {sc.major_classes?.length || 0} classes
          </span>
        </div>
        <span className="text-text-muted">{open ? "▾" : "▸"}</span>
      </button>

      {open && (
        <div className="border-t border-border px-5 pb-5 pt-4 space-y-5">
          {(sc.major_classes || []).map((mc) => (
            <div key={mc.key}>
              <div className="mb-2 flex items-center gap-2">
                <h4 className="text-sm font-semibold text-text-primary">
                  {mc.label}
                </h4>
                {mc.label === "Volatility Strategies" && (
                  <span className="rounded-full bg-[#E11D48] px-2 py-0.5 text-[10px] font-bold uppercase tracking-wide text-white">
                    NEW
                  </span>
                )}
              </div>
              <div className="flex flex-wrap gap-2">
                {(mc.sub_categories || []).map((sub) => (
                  <span
                    key={sub.key}
                    className="rounded-full border border-border px-3 py-1 text-xs text-text-secondary"
                  >
                    {sub.label}
                  </span>
                ))}
                {(mc.sub_categories || []).length === 0 && (
                  <span className="text-xs italic text-text-muted">
                    No sub-categories
                  </span>
                )}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

export default function TaxonomyBrowser({ taxonomy }) {
  const [search, setSearch] = useState("");
  const superClasses = taxonomy?.super_classes || [];

  const filtered = search.trim()
    ? superClasses.filter((sc) => {
        const q = search.toLowerCase();
        if (sc.label.toLowerCase().includes(q)) return true;
        return (sc.major_classes || []).some(
          (mc) =>
            mc.label.toLowerCase().includes(q) ||
            (mc.sub_categories || []).some((sub) =>
              sub.label.toLowerCase().includes(q)
            )
        );
      })
    : superClasses;

  return (
    <div>
      <div className="mb-5">
        <input
          type="text"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="Search asset classes and sub-categories…"
          className="w-full max-w-lg rounded-md border border-border bg-bg-card px-3 py-2 text-sm text-text-primary outline-none focus:ring-2 focus:ring-navy"
        />
      </div>

      {filtered.length === 0 && search && (
        <p className="text-sm text-text-muted">No results for &ldquo;{search}&rdquo;</p>
      )}

      <div className="space-y-4">
        {filtered.map((sc) => {
          const originalIndex = superClasses.indexOf(sc);
          const color = SC_COLORS[originalIndex % SC_COLORS.length];
          return (
            <SuperClassSection key={sc.key} sc={sc} color={color} />
          );
        })}
      </div>
    </div>
  );
}
