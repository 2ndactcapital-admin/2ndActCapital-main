"use client";

import { useMemo, useState } from "react";

import {
  DEFAULT_INSTITUTION_SORT,
  fmtAsOf,
  nextSort,
  sortInstitutions,
} from "@/lib/edgarBulkDownload.mjs";

// BUGFIX1 — the Bulk download "Institutions" picker, now a real table with a
// header row (Institution, Entities, one column per year shown, Total) and
// right-aligned numeric columns. Split out of EdgarBulkDownload.jsx (which
// imports server actions) so node tests render the real markup.
//
// BULKFETCHUI3 — every header sorts (asc/desc, aria-sort, an arrow on the
// sorted column; filer sub-rows sort the same way under their institution);
// Downloaded and Stored columns after Total (labels and their explanations from
// the envelope's vocabularies.institution_columns); the card fills the
// Institutions box (on xl it is absolutely positioned inside a box the grid row
// sizes, so the right-hand column sets the height and the table scrolls inside;
// below xl the table scrolls within 70vh); the header row carries Refresh (with
// "as of h:mm") and — only when writable — Refresh index with its "Index loaded
// through" note.
const CARD = "rounded-md border border-[var(--2a-border)] bg-[var(--2a-bg-card)]";
const CONTROL =
  "rounded border border-[var(--2a-border)] bg-[var(--2a-bg-card)] px-2 py-1.5 text-xs text-[var(--2a-text)] focus:outline-none focus:ring-1 focus:ring-[var(--2a-gold)]";
const GHOST =
  "rounded border border-[var(--2a-border)] px-3 py-1.5 text-xs text-[var(--2a-navy)] hover:bg-[var(--2a-bg)] disabled:opacity-50";
const TH = "px-2 py-1.5 text-[10px] font-semibold uppercase tracking-[0.12em] text-[var(--2a-text-muted)]";
const NUM = "w-20 px-2 py-1.5 text-right tabular-nums";
const MUTED_NOTE = "text-[11px] text-[var(--2a-text-muted)]";
const INDEX_LABELS = { running: "Index refreshing", queued: "Index refresh queued" };

function fmtInt(n) {
  return n === null || n === undefined ? "" : Number(n).toLocaleString();
}

export function institutionYears(rows) {
  return [...new Set(rows.flatMap((r) => Object.keys(r.years || {})))].sort().slice(-3);
}

function rowTotal(r, years) {
  return r.total ?? years.reduce((s, y) => s + (r.years?.[y] ?? 0), 0);
}

function SortHeader({ label, title, sortKey, sort, onSort, align = "right", className = "" }) {
  const active = sort.key === sortKey;
  const ariaSort = active ? (sort.dir === "asc" ? "ascending" : "descending") : "none";
  return (
    <th
      className={`${TH} ${align === "left" ? "text-left" : "w-20 text-right"} ${className}`}
      aria-sort={ariaSort}
      title={title}
      data-sort-key={sortKey}
    >
      <button
        type="button"
        className={`inline-flex items-center gap-1 uppercase tracking-[0.12em] hover:text-[var(--2a-navy)] ${active ? "text-[var(--2a-navy)]" : ""}`}
        onClick={() => onSort(sortKey)}
      >
        {label}
        {active && <span aria-hidden="true">{sort.dir === "asc" ? "▲" : "▼"}</span>}
      </button>
    </th>
  );
}

export default function InstitutionPicker({
  rows,
  selected,
  onToggle,
  onAll,
  onNone,
  columns = null,
  asOf = null,
  onRefresh = null,
  refreshing = false,
  writable = false,
  lastIndexed = null,
  onRefreshIndex = null,
  indexState = null,
  indexPending = false,
  indexMessage = null,
  initialSort = DEFAULT_INSTITUTION_SORT,
  initialOpen = null,
}) {
  const [search, setSearch] = useState("");
  const [open, setOpen] = useState(() => new Set(initialOpen ?? []));
  const [sort, setSort] = useState(initialSort);
  const needle = search.trim().toLowerCase();
  const years = useMemo(() => institutionYears(rows), [rows]);
  const shown = useMemo(
    () =>
      sortInstitutions(
        rows.filter(
          (r) =>
            !needle ||
            r.name.toLowerCase().includes(needle) ||
            r.filers.some((f) => f.name.toLowerCase().includes(needle) || f.cik.includes(needle)),
        ),
        sort,
        years,
      ),
    [rows, needle, sort, years],
  );
  const cols = columns ?? {};
  const onSort = (key) => setSort((s) => nextSort(s, key));
  const indexLabel = INDEX_LABELS[indexState] ?? "Refresh index";

  function toggleOpen(name) {
    setOpen((prev) => {
      const next = new Set(prev);
      if (next.has(name)) next.delete(name);
      else next.add(name);
      return next;
    });
  }

  return (
    <div className="relative min-w-0 xl:min-h-[20rem]" data-testid="institutions-box">
      <div className={`${CARD} flex min-w-0 flex-col p-4 xl:absolute xl:inset-0`} data-testid="institutions-card">
        <div className="mb-2 flex flex-wrap items-center gap-2" data-testid="institutions-header">
          <h3 className="mr-auto text-sm font-semibold text-[var(--2a-navy)]">Institutions</h3>
          <input
            className={`${CONTROL} w-48 min-w-0`}
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
          {onRefresh && (
            <span className="inline-flex flex-wrap items-center gap-1.5">
              <button type="button" className={GHOST} onClick={onRefresh} disabled={refreshing} data-testid="institutions-refresh">
                Refresh
              </button>
              {asOf && <span className={MUTED_NOTE} data-testid="institutions-as-of">as of {fmtAsOf(asOf)}</span>}
            </span>
          )}
          {writable === true && onRefreshIndex && (
            <span className="inline-flex flex-wrap items-center gap-1.5">
              <button
                type="button"
                className={GHOST}
                onClick={onRefreshIndex}
                disabled={indexPending || Boolean(INDEX_LABELS[indexState])}
                data-testid="refresh-index"
              >
                {indexLabel}
              </button>
              <span className={MUTED_NOTE} data-testid="index-loaded-through">
                Index loaded through {lastIndexed ?? "—"}.
              </span>
            </span>
          )}
        </div>
        {indexMessage && (
          <div className="mb-2 text-[11px]" data-testid="refresh-index-message">
            {indexMessage}
          </div>
        )}
        <p className={`mb-2 ${MUTED_NOTE}`}>Selected filings still to fetch, by year.</p>
        <div className="max-h-[70vh] min-h-0 flex-1 overflow-auto xl:max-h-none" data-testid="institutions-scroll">
          <table className="w-full text-xs" data-testid="institutions-table">
            <thead className="sticky top-0 z-[1] bg-[var(--2a-bg-card)]">
              <tr className="border-b border-[var(--2a-border)]">
                <th className={`${TH} w-6`}><span className="sr-only">Select</span></th>
                <SortHeader label="Institution" sortKey="name" sort={sort} onSort={onSort} align="left" />
                <SortHeader label="Entities" sortKey="entities" sort={sort} onSort={onSort} />
                {years.map((y) => (
                  <SortHeader key={y} label={y} sortKey={`year:${y}`} sort={sort} onSort={onSort} />
                ))}
                <SortHeader label="Total" sortKey="total" sort={sort} onSort={onSort} />
                <SortHeader
                  label={cols.downloaded?.label ?? ""}
                  title={cols.downloaded?.help}
                  sortKey="downloaded"
                  sort={sort}
                  onSort={onSort}
                />
                <SortHeader
                  label={cols.stored?.label ?? ""}
                  title={cols.stored?.help}
                  sortKey="stored"
                  sort={sort}
                  onSort={onSort}
                />
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
                    <td className={`${NUM} text-[var(--2a-text-secondary)]`} data-col="downloaded">{fmtInt(r.downloaded ?? 0)}</td>
                    <td className={`${NUM} text-[var(--2a-text-secondary)]`} data-col="stored">{fmtInt(r.stored ?? 0)}</td>
                  </tr>,
                ];
                if (open.has(r.name)) {
                  for (const f of r.filers) {
                    out.push(
                      <tr key={`${r.name}:${f.cik}`} className="text-[11px]" data-testid="filer-row" data-parent={r.name}>
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
                        <td className={`${NUM} py-0.5 text-[var(--2a-text)]`} data-col="downloaded">{fmtInt(f.downloaded ?? 0)}</td>
                        <td className={`${NUM} py-0.5 text-[var(--2a-text)]`} data-col="stored">{fmtInt(f.stored ?? 0)}</td>
                      </tr>,
                    );
                  }
                }
                return out;
              })}
              {!shown.length && (
                <tr>
                  <td colSpan={years.length + 6} className="py-3 text-xs text-[var(--2a-text-muted)]">
                    No institution matches.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
