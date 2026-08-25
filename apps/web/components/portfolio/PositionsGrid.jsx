"use client";

/**
 * PositionsGrid — the Positions screen. Portfolio UX sprint 1.
 *
 * The flagship pattern the Transactions and Securities screens will reuse: a
 * dense sortable/filterable grid on the left, a detail pane on the right, no
 * navigation, no wizards, no multi-step modal for a routine edit.
 *
 * BUILT ON THE EXISTING GRID, NOT A NEW ONE
 * ─────────────────────────────────────────────────────────────────────────
 * `components/ui/DataGrid.jsx` (TanStack Table + dnd-kit) already owns sort,
 * per-column filter, global filter, column visibility, column reorder and
 * pagination. Nothing here re-implements any of that. Inline editing is not a
 * DataGrid feature and did NOT need to become one: a `columnDefs[].cell` is an
 * arbitrary render function, so an editable cell is a renderer.
 *
 * WHAT IS INLINE AND WHAT IS NOT
 * ─────────────────────────────────────────────────────────────────────────
 * Inline: `taxonomy_key` and `is_reconciled`. Neither can be refused by the
 * ownership-basis contract, so neither needs room to explain a refusal.
 *
 * Not inline: quantity, ownership %, market value, ownership basis. Those go
 * through the right pane, because `portfolio_assets._validate_basis` — the
 * only thing enforcing the contract, since `portfolio.positions` has no CHECK
 * for it — can and does refuse them, and a refusal surfacing as a cell
 * silently reverting is worse than no inline edit at all.
 *
 * The server publishes both lists (`vocabularies.inline_editable` /
 * `.editable`) and this component honours them rather than keeping its own
 * copy that could drift.
 *
 * TWO THINGS ABOUT SORTING THAT ARE EASY TO GET WRONG
 * ─────────────────────────────────────────────────────────────────────────
 * 1. Money arrives as exact decimal STRINGS. Sorting those lexically puts
 *    "9" above "10". Each row carries a derived Number for the sort columns —
 *    used ONLY for ordering and never rendered.
 * 2. The taxonomy column sorts and filters on the LABEL, not the key, because
 *    the label is what the user can see. Labels are resolved server-side from
 *    `config` (Rule 1); nothing here hardcodes one.
 */

import { useCallback, useEffect, useMemo, useState } from "react";

import DataGrid from "@/components/ui/DataGrid";
import EntityPicker from "@/components/EntityPicker";
import PositionDetailPane from "@/components/portfolio/PositionDetailPane";

const CARD = { borderColor: "#ece8dd", boxShadow: "0 1px 3px rgba(0,0,0,0.06)" };
const CONTROL =
  "rounded border border-[var(--2a-border)] bg-white px-2 py-1 text-xs text-[var(--2a-text)] focus:outline-none focus:ring-1 focus:ring-[var(--2a-gold)]";
const EYEBROW =
  "block text-[10px] font-semibold uppercase tracking-[0.12em] text-[var(--2a-text-muted)]";

function fmtDate(value) {
  if (!value) return "—";
  const d = new Date(value.length === 10 ? `${value}T00:00:00` : value);
  if (Number.isNaN(d.getTime())) return value;
  return d.toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
    year: "2-digit",
  });
}

function fmtMoney(value, currency = "USD") {
  if (value == null || value === "") return "—";
  const n = Number(value);
  if (!Number.isFinite(n)) return value;
  return n.toLocaleString("en-US", {
    style: "currency",
    currency: currency || "USD",
    maximumFractionDigits: 0,
  });
}

function fmtNumber(value, digits = 4) {
  if (value == null || value === "") return "—";
  const n = Number(value);
  if (!Number.isFinite(n)) return value;
  return n.toLocaleString("en-US", { maximumFractionDigits: digits });
}

// A decimal STRING → Number, for sorting only. NaN sorts last rather than
// colliding with a real zero.
function num(value) {
  if (value == null || value === "") return null;
  const n = Number(value);
  return Number.isFinite(n) ? n : null;
}

// ─── Inline editors ──────────────────────────────────────────────────────────

// Row clicks open the detail pane. Every interactive element inside a cell has
// to stop propagation or picking a taxonomy value would also re-open the pane
// on top of the select the user is still using.
function stop(e) {
  e.stopPropagation();
}

function TaxonomyCell({ row, taxonomy, editable, onEdit, pending }) {
  const options = useMemo(
    () => Object.entries(taxonomy || {}).sort((a, b) => a[1].localeCompare(b[1])),
    [taxonomy],
  );

  if (!editable) {
    return (
      <span className="text-[var(--2a-text-secondary)]">
        {row.taxonomy_label || (
          <span className="text-[var(--2a-text-muted)]">
            {row.taxonomy_key || "—"}
          </span>
        )}
      </span>
    );
  }

  return (
    <select
      value={row.taxonomy_key || ""}
      disabled={pending}
      onClick={stop}
      onMouseDown={stop}
      onChange={(e) => {
        e.stopPropagation();
        onEdit(row, "taxonomy_key", e.target.value || null);
      }}
      className="w-full rounded border border-transparent bg-transparent px-1 py-0.5 text-xs text-[var(--2a-text-secondary)] hover:border-[var(--2a-border)] focus:border-[var(--2a-gold)] focus:outline-none disabled:opacity-50"
      title={
        row.taxonomy_key && !row.taxonomy_label
          ? `Key ${row.taxonomy_key} has no active config row — it will not resolve to a label.`
          : "Reassign taxonomy"
      }
    >
      <option value="">— unassigned —</option>
      {/* A key with no config row is still shown, flagged, so a stale
          assignment is visible rather than silently rendering as unassigned. */}
      {row.taxonomy_key && !taxonomy?.[row.taxonomy_key] && (
        <option value={row.taxonomy_key}>{row.taxonomy_key} (unknown key)</option>
      )}
      {options.map(([key, label]) => (
        <option key={key} value={key}>
          {label}
        </option>
      ))}
    </select>
  );
}

function ReconciledCell({ row, editable, onEdit, pending }) {
  if (!editable) {
    return (
      <span className="text-[var(--2a-text-muted)]">
        {row.is_reconciled ? "✓" : "—"}
      </span>
    );
  }
  return (
    <input
      type="checkbox"
      checked={!!row.is_reconciled}
      disabled={pending}
      onClick={stop}
      onMouseDown={stop}
      onChange={(e) => {
        e.stopPropagation();
        onEdit(row, "is_reconciled", e.target.checked);
      }}
      aria-label="Reconciled"
      className="accent-[var(--2a-navy)] disabled:opacity-50"
    />
  );
}

function SourceStatePill({ row }) {
  if (!row.is_superseded) {
    return <span className="text-[var(--2a-text-muted)]">—</span>;
  }
  return (
    <span
      className="inline-flex items-center rounded-full px-2 py-0.5 text-[10px] font-medium"
      style={{ backgroundColor: "var(--2a-bg-sidebar)", color: "var(--2a-text-muted)" }}
      title={`Outranked by ${row.superseded_by_source} under this org's source precedence`}
    >
      {row.superseded_by_source}
    </span>
  );
}

// ─── The screen ──────────────────────────────────────────────────────────────

const EMPTY_FILTERS = {
  owner_entity_id: "",
  taxonomy_key: "",
  source_system: "",
  authority: "",
  ownership_basis: "",
  as_of_from: "",
  as_of_to: "",
  superseded: "all",
  search: "",
};

export default function PositionsGrid() {
  const [rows, setRows] = useState([]);
  const [meta, setMeta] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  const [filters, setFilters] = useState(EMPTY_FILTERS);
  const [owner, setOwner] = useState(null); // EntityPicker value
  const [selectedId, setSelectedId] = useState(null);
  const [pendingId, setPendingId] = useState(null);
  const [inlineError, setInlineError] = useState(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const params = new URLSearchParams();
      for (const [key, value] of Object.entries(filters)) {
        if (value) params.set(key, value);
      }
      const res = await fetch(`/api/portfolio/positions?${params}`, {
        cache: "no-store",
      });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(body.error || "Could not load positions.");
      setRows(Array.isArray(body.positions) ? body.positions : []);
      setMeta(body);
    } catch (err) {
      setError(err.message);
      setRows([]);
    } finally {
      setLoading(false);
    }
  }, [filters]);

  useEffect(() => {
    load();
  }, [load]);

  const taxonomy = meta?.taxonomy || {};
  const vocabularies = meta?.vocabularies;
  const inlineEditable = useMemo(
    () => new Set(vocabularies?.inline_editable || []),
    [vocabularies],
  );

  // Rows the grid actually sorts on. The derived `_*` fields exist only so
  // TanStack sorts numerically on values that arrive as exact decimal strings.
  const gridRows = useMemo(
    () =>
      rows.map((r) => ({
        ...r,
        _measure:
          r.ownership_basis === "percent"
            ? num(r.ownership_pct)
            : r.ownership_basis === "units"
              ? num(r.quantity)
              : num(r.market_value),
        _value: num(r.current_value),
        _taxonomy: r.taxonomy_label || r.taxonomy_key || "",
      })),
    [rows],
  );

  // An inline edit round-trips through the real PATCH endpoint. Because that
  // endpoint restates rather than updates, the response carries a NEW id — the
  // row is REPLACED in local state, not patched in place, and the selection
  // follows it. A client that kept the old id would be pointing at history.
  const handleInlineEdit = useCallback(
    async (row, field, value) => {
      setPendingId(row.id);
      setInlineError(null);
      try {
        const res = await fetch(`/api/portfolio/positions/${row.id}`, {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ [field]: value }),
        });
        const body = await res.json().catch(() => ({}));
        if (!res.ok) {
          throw new Error(body.error || body.detail || "The edit was refused.");
        }
        const updated = body.position;
        setRows((prev) => prev.map((r) => (r.id === row.id ? updated : r)));
        setSelectedId((prev) => (prev === row.id ? updated.id : prev));
      } catch (err) {
        setInlineError(err.message);
        // Re-read rather than guessing at the server's state after a refusal.
        load();
      } finally {
        setPendingId(null);
      }
    },
    [load],
  );

  const columnDefs = useMemo(
    () => [
      {
        field: "asset_name",
        headerName: "Asset",
        enableColumnFilter: true,
        filterPlaceholder: "Filter asset…",
        cell: (v, row) => (
          <span className="font-medium text-[var(--2a-text)]" title={row.asset_type}>
            {v}
          </span>
        ),
      },
      {
        field: "owner_name",
        headerName: "Owner",
        enableColumnFilter: true,
        filterPlaceholder: "Filter owner…",
      },
      {
        field: "_taxonomy",
        headerName: "Taxonomy",
        enableColumnFilter: true,
        filterPlaceholder: "Filter taxonomy…",
        cell: (_v, row) => (
          <TaxonomyCell
            row={row}
            taxonomy={taxonomy}
            editable={inlineEditable.has("taxonomy_key")}
            onEdit={handleInlineEdit}
            pending={pendingId === row.id}
          />
        ),
      },
      {
        field: "ownership_basis",
        headerName: "Basis",
        align: "center",
        cell: (v) => (
          <span className="text-[10px] uppercase tracking-wide text-[var(--2a-text-muted)]">
            {v}
          </span>
        ),
      },
      {
        field: "_measure",
        headerName: "Quantity / % / Value",
        align: "right",
        // Rendered from the basis so the number always carries its unit. A
        // bare "25" that could mean 25 shares or 25 percent is not a figure.
        cell: (_v, row) =>
          row.ownership_basis === "percent"
            ? `${fmtNumber(row.ownership_pct, 4)}%`
            : row.ownership_basis === "units"
              ? fmtNumber(row.quantity)
              : fmtMoney(row.market_value, row.currency_code),
      },
      {
        field: "_value",
        headerName: "Current value",
        align: "right",
        cell: (_v, row) =>
          row.current_value != null ? (
            <span className="font-medium text-[var(--2a-text)]">
              {fmtMoney(row.current_value, row.currency_code)}
            </span>
          ) : (
            // An em-dash, never $0 — an unmeasured holding and a genuine zero
            // are different facts, and the reason travels all the way here.
            <span
              className="cursor-help text-[var(--2a-text-muted)]"
              title={row.current_value_reason || "No valuation resolved."}
            >
              —
            </span>
          ),
      },
      {
        field: "as_of_date",
        headerName: "As of",
        align: "right",
        cell: (v) => (
          <span className="text-[var(--2a-text-muted)]">{fmtDate(v)}</span>
        ),
      },
      {
        field: "authority",
        headerName: "Authority",
        enableColumnFilter: true,
        filterPlaceholder: "Filter…",
        align: "center",
        cell: (v) => (
          <span className="text-[10px] uppercase tracking-wide text-[var(--2a-text-muted)]">
            {v}
          </span>
        ),
      },
      {
        field: "source_system",
        headerName: "Source",
        enableColumnFilter: true,
        filterPlaceholder: "Filter…",
        cell: (v) => (
          <span className="text-[var(--2a-text-muted)]">
            {(v || "").replace(/_/g, " ")}
          </span>
        ),
      },
      {
        field: "superseded_by_source",
        headerName: "Outranked by",
        align: "center",
        cell: (_v, row) => <SourceStatePill row={row} />,
      },
      {
        field: "is_reconciled",
        headerName: "Rec.",
        align: "center",
        enableSorting: true,
        cell: (_v, row) => (
          <ReconciledCell
            row={row}
            editable={inlineEditable.has("is_reconciled")}
            onEdit={handleInlineEdit}
            pending={pendingId === row.id}
          />
        ),
      },
    ],
    [taxonomy, inlineEditable, handleInlineEdit, pendingId],
  );

  function setFilter(name, value) {
    setFilters((prev) => ({ ...prev, [name]: value }));
  }

  const truncated = meta && meta.total > meta.returned;

  return (
    <div className="flex flex-col gap-3">
      {/* ── Server-side filter bar ─────────────────────────────────────── */}
      <div
        className="rounded-lg border bg-white px-4 py-3"
        style={CARD}
      >
        <div className="flex flex-wrap items-end gap-3">
          <div className="w-56">
            <span className={EYEBROW}>Owner</span>
            <div className="mt-1">
              <EntityPicker
                value={owner}
                onChange={(entity) => {
                  setOwner(entity);
                  setFilter("owner_entity_id", entity?.id || "");
                }}
                placeholder="Any owner…"
                className={`${CONTROL} w-full`}
              />
            </div>
          </div>

          <div>
            <label className={EYEBROW} htmlFor="f-taxonomy">
              Taxonomy
            </label>
            <select
              id="f-taxonomy"
              className={`${CONTROL} mt-1 w-44`}
              value={filters.taxonomy_key}
              onChange={(e) => setFilter("taxonomy_key", e.target.value)}
            >
              <option value="">Any</option>
              {Object.entries(taxonomy)
                .sort((a, b) => a[1].localeCompare(b[1]))
                .map(([key, label]) => (
                  <option key={key} value={key}>
                    {label}
                  </option>
                ))}
            </select>
          </div>

          <div>
            <label className={EYEBROW} htmlFor="f-source">
              Source
            </label>
            <select
              id="f-source"
              className={`${CONTROL} mt-1 w-40`}
              value={filters.source_system}
              onChange={(e) => setFilter("source_system", e.target.value)}
            >
              <option value="">Any</option>
              {(vocabularies?.source_system || []).map((s) => (
                <option key={s} value={s}>
                  {s.replace(/_/g, " ")}
                </option>
              ))}
            </select>
          </div>

          <div>
            <label className={EYEBROW} htmlFor="f-authority">
              Authority
            </label>
            <select
              id="f-authority"
              className={`${CONTROL} mt-1 w-32`}
              value={filters.authority}
              onChange={(e) => setFilter("authority", e.target.value)}
            >
              <option value="">Any</option>
              {(vocabularies?.authority || []).map((a) => (
                <option key={a} value={a}>
                  {a}
                </option>
              ))}
            </select>
          </div>

          <div>
            <label className={EYEBROW} htmlFor="f-from">
              As of from
            </label>
            <input
              id="f-from"
              type="date"
              className={`${CONTROL} mt-1`}
              value={filters.as_of_from}
              onChange={(e) => setFilter("as_of_from", e.target.value)}
            />
          </div>

          <div>
            <label className={EYEBROW} htmlFor="f-to">
              As of to
            </label>
            <input
              id="f-to"
              type="date"
              className={`${CONTROL} mt-1`}
              value={filters.as_of_to}
              onChange={(e) => setFilter("as_of_to", e.target.value)}
            />
          </div>

          <div>
            <label className={EYEBROW} htmlFor="f-superseded">
              Source state
            </label>
            <select
              id="f-superseded"
              className={`${CONTROL} mt-1 w-36`}
              value={filters.superseded}
              onChange={(e) => setFilter("superseded", e.target.value)}
            >
              <option value="all">All rows</option>
              <option value="winners">Winners only</option>
              <option value="losers">Outranked only</option>
            </select>
          </div>

          <button
            type="button"
            onClick={() => {
              setFilters(EMPTY_FILTERS);
              setOwner(null);
            }}
            className="rounded border border-[var(--2a-border)] px-3 py-1.5 text-xs text-[var(--2a-text-secondary)] hover:bg-[var(--2a-bg)]"
          >
            Clear
          </button>
          <button
            type="button"
            onClick={load}
            disabled={loading}
            className="rounded px-3 py-1.5 text-xs font-medium text-white disabled:opacity-50"
            style={{ backgroundColor: "var(--2a-navy)" }}
          >
            {loading ? "Loading…" : "Refresh"}
          </button>
        </div>

        <p className="mt-2 text-[11px] text-[var(--2a-text-muted)]">
          {meta
            ? `${meta.returned} of ${meta.total} position${meta.total === 1 ? "" : "s"}`
            : "—"}
          {truncated && (
            <span className="ml-2 text-[var(--2a-gold)]">
              · showing the first {meta.limit}; narrow the filters above to sort
              across the whole set
            </span>
          )}
        </p>
      </div>

      {inlineError && (
        <div
          className="rounded-lg border bg-[#FEF3F2] px-4 py-2 text-xs text-[#9B2335]"
          style={CARD}
        >
          {inlineError}
          <button
            type="button"
            onClick={() => setInlineError(null)}
            className="ml-3 underline"
          >
            dismiss
          </button>
        </div>
      )}
      {error && (
        <div
          className="rounded-lg border bg-[#FEF3F2] px-4 py-2 text-xs text-[#9B2335]"
          style={CARD}
        >
          {error}
        </div>
      )}

      {/* ── Grid + detail pane. No navigation between them. ────────────── */}
      <div
        className="grid grid-cols-5 gap-3"
        style={{ minHeight: "calc(100vh - 300px)" }}
      >
        <div
          className="col-span-3 overflow-auto rounded-lg border bg-white p-3"
          style={CARD}
        >
          <DataGrid
            gridId="portfolio-positions"
            columnDefs={columnDefs}
            rowData={gridRows}
            getRowId={(row) => row.id}
            onRowClick={(row) => setSelectedId(row.id)}
            selectedRowId={selectedId}
            quickFilterPlaceholder="Search loaded rows…"
            emptyMessage={
              loading ? "Loading positions…" : "No positions match these filters."
            }
            pageSize={50}
          />
        </div>

        <div
          className="col-span-2 overflow-hidden rounded-lg border bg-white"
          style={CARD}
        >
          <PositionDetailPane
            positionId={selectedId}
            vocabularies={vocabularies}
            taxonomy={taxonomy}
            onClose={() => setSelectedId(null)}
            onSaved={(detail) => {
              // The pane restated the position: adopt the successor's id and
              // swap the row, so the grid and the pane agree about which row
              // is current without a full reload.
              const updated = detail.position;
              setRows((prev) =>
                prev.map((r) => (r.id === detail.restated_from ? updated : r)),
              );
              setSelectedId(updated.id);
            }}
          />
        </div>
      </div>
    </div>
  );
}
