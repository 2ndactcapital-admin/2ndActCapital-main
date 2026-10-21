"use client";

import { useMemo, useState } from "react";

// BUGFIX1 — the Bulk download "Institutions" picker, now a real table with a
// header row (Institution, Entities, one column per year shown, Total) and
// right-aligned numeric columns. Split out of EdgarBulkDownload.jsx (which
// imports server actions) so node tests render the real markup.
const CARD = "rounded-md border border-[var(--2a-border)] bg-[var(--2a-bg-card)]";
const CONTROL =
  "rounded border border-[var(--2a-border)] bg-[var(--2a-bg-card)] px-2 py-1.5 text-xs text-[var(--2a-text)] focus:outline-none focus:ring-1 focus:ring-[var(--2a-gold)]";
const GHOST =
  "rounded border border-[var(--2a-border)] px-3 py-1.5 text-xs text-[var(--2a-navy)] hover:bg-[var(--2a-bg)] disabled:opacity-50";
const TH = "px-2 py-1.5 text-[10px] font-semibold uppercase tracking-[0.12em] text-[var(--2a-text-muted)]";
const NUM = "w-20 px-2 py-1.5 text-right tabular-nums";

function fmtInt(n) {
  return n === null || n === undefined ? "" : Number(n).toLocaleString();
}

export function institutionYears(rows) {
  return [...new Set(rows.flatMap((r) => Object.keys(r.years || {})))].sort().slice(-3);
}

function rowTotal(r, years) {
  return r.total ?? years.reduce((s, y) => s + (r.years?.[y] ?? 0), 0);
}

export default function InstitutionPicker({ rows, selected, onToggle, onAll, onNone }) {
  const [search, setSearch] = useState("");
  const [open, setOpen] = useState(() => new Set());
  const needle = search.trim().toLowerCase();
  const shown = rows.filter(
    (r) =>
      !needle ||
      r.name.toLowerCase().includes(needle) ||
      r.filers.some((f) => f.name.toLowerCase().includes(needle) || f.cik.includes(needle)),
  );
  const years = useMemo(() => institutionYears(rows), [rows]);

  function toggleOpen(name) {
    setOpen((prev) => {
      const next = new Set(prev);
      if (next.has(name)) next.delete(name);
      else next.add(name);
      return next;
    });
  }

  return (
    <div className={`${CARD} p-4`}>
      <div className="mb-2 flex flex-wrap items-center gap-2">
        <h3 className="mr-auto text-sm font-semibold text-[var(--2a-navy)]">Institutions</h3>
        <input
          className={`${CONTROL} w-48`}
          placeholder="Search institutions, entities, CIKs"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
        <button type="button" className={GHOST} onClick={() => onAll(shown.map((r) => r.name))}>
          Select all
        </button>
        <button type="button" className={GHOST} onClick={onNone}>
          Select none
        </button>
      </div>
      <p className="mb-2 text-[11px] text-[var(--2a-text-muted)]">
        Selected filings still to fetch, by year.
      </p>
      <div className="max-h-80 overflow-y-auto">
        <table className="w-full text-xs" data-testid="institutions-table">
          <thead className="sticky top-0 bg-[var(--2a-bg-card)]">
            <tr className="border-b border-[var(--2a-border)]">
              <th className={`${TH} w-6`}><span className="sr-only">Select</span></th>
              <th className={`${TH} text-left`}>Institution</th>
              <th className={`${TH} w-20 text-right`}>Entities</th>
              {years.map((y) => (
                <th key={y} className={`${TH} w-20 text-right`}>{y}</th>
              ))}
              <th className={`${TH} w-20 text-right`}>Total</th>
            </tr>
          </thead>
          <tbody>
            {shown.flatMap((r) => {
              const out = [
                <tr key={r.name} className="border-t border-[var(--2a-border)]" data-testid="institution-row">
                  <td className="px-2 py-1.5">
                    <input
                      type="checkbox"
                      aria-label={`Select ${r.name}`}
                      checked={selected.has(r.name)}
                      onChange={() => onToggle(r.name)}
                    />
                  </td>
                  <td className="px-2 py-1.5">
                    <button
                      type="button"
                      className="text-left text-[var(--2a-text)] hover:text-[var(--2a-navy)]"
                      onClick={() => toggleOpen(r.name)}
                      aria-expanded={open.has(r.name)}
                    >
                      <span className="mr-1 inline-block w-3 text-[var(--2a-text-muted)]">
                        {open.has(r.name) ? "▾" : "▸"}
                      </span>
                      {r.name}
                    </button>
                  </td>
                  <td className={`${NUM} text-[var(--2a-text-muted)]`}>{fmtInt(r.filers.length)}</td>
                  {years.map((y) => (
                    <td key={y} className={`${NUM} text-[var(--2a-text-secondary)]`}>{fmtInt(r.years[y] ?? 0)}</td>
                  ))}
                  <td className={`${NUM} font-medium text-[var(--2a-text)]`}>{fmtInt(rowTotal(r, years))}</td>
                </tr>,
              ];
              if (open.has(r.name)) {
                for (const f of r.filers) {
                  out.push(
                    <tr key={`${r.name}:${f.cik}`} className="text-[11px]">
                      <td />
                      <td className="py-0.5 pl-6 pr-2 text-[var(--2a-text-secondary)]">
                        {f.name}
                        <span className="ml-2 tabular-nums text-[var(--2a-text-muted)]">CIK {f.cik}</span>
                        {f.role && <span className="ml-2 text-[var(--2a-text-muted)]">{f.role}</span>}
                      </td>
                      <td />
                      {years.map((y) => (
                        <td key={y} className={`${NUM} py-0.5 text-[var(--2a-text)]`}>{fmtInt(f.years?.[y] ?? 0)}</td>
                      ))}
                      <td className={`${NUM} py-0.5 text-[var(--2a-text)]`}>{fmtInt(rowTotal(f, years))}</td>
                    </tr>,
                  );
                }
              }
              return out;
            })}
            {!shown.length && (
              <tr>
                <td colSpan={years.length + 4} className="py-3 text-xs text-[var(--2a-text-muted)]">
                  No institution matches.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
