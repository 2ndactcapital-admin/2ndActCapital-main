"use client";

/**
 * SecuritiesGrid — the Securities & Assets screen. Portfolio UX sprint 3.
 *
 * The same shape as PositionsGrid and TransactionsGrid: a dense
 * sortable/filterable grid on the left, a detail pane on the right, no
 * navigation, no wizard for a routine edit. Built on the existing
 * `components/ui/DataGrid.jsx`, which already owns sort, per-column filter,
 * global filter, column visibility, column reorder and pagination. Nothing here
 * re-implements any of that; an editable cell is a `columnDefs[].cell`
 * renderer, which is what it was on the previous two screens too.
 *
 * WHAT IS GENUINELY NEW HERE: TWO SCOPES IN ONE ROW
 * ─────────────────────────────────────────────────────────────────────────
 * Every row joins the tenant's own `portfolio.assets` row to the
 * platform-wide `portfolio.securities_global` row it is linked to. Those two
 * halves answer to different authorities:
 *
 *   org-owned   → editable with `manage_portfolio`
 *   global      → NOT editable from this screen by anyone, including a super
 *                 admin. The global write path is a different endpoint.
 *
 * The component decides NOTHING about that. It renders an editable control for
 * a field if and only if the server put that field in
 * `vocabularies.inline_editable`, which the API builds from the caller's real
 * permissions and which never contains a global-sourced key. A view-only user
 * gets an empty list and therefore no write controls at all — not disabled
 * ones, absent ones — and there is no client-side branch that could disagree
 * with the server, because there is no client-side copy of the rule.
 *
 * That is the UI half of the boundary. It is not the enforcement: every write
 * endpoint re-checks server-side, and a caller with curl gets a 403 from
 * FastAPI whatever this file renders. Both halves are asserted independently in
 * verify_portfolioux3.
 *
 * TWO THINGS ABOUT SORTING THAT ARE EASY TO GET WRONG
 * ─────────────────────────────────────────────────────────────────────────
 * 1. Money arrives as exact decimal STRINGS. Sorting those lexically puts "9"
 *    above "10". Each row carries a derived Number for the money columns — used
 *    ONLY for ordering and never rendered.
 * 2. The taxonomy column sorts and filters on the LABEL, not the key, because
 *    the label is what the user can see. Labels are resolved server-side from
 *    `config` (Rule 1); nothing here hardcodes one.
 */

import { useCallback, useEffect, useMemo, useState } from "react";

import DataGrid from "@/components/ui/DataGrid";
import AssetDetailPane from "@/components/portfolio/AssetDetailPane";

const CARD = { borderColor: "#ece8dd", boxShadow: "0 1px 3px rgba(0,0,0,0.06)" };
const CONTROL =
  "rounded border border-[var(--2a-border)] bg-white px-2 py-1 text-xs text-[var(--2a-text)] focus:outline-none focus:ring-1 focus:ring-[var(--2a-gold)]";
const EYEBROW =
  "block text-[10px] font-semibold uppercase tracking-[0.12em] text-[var(--2a-text-muted)]";

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

function fmtPrice(value, currency = "USD") {
  if (value == null || value === "") return "—";
  const n = Number(value);
  if (!Number.isFinite(n)) return value;
  return n.toLocaleString("en-US", {
    style: "currency",
    currency: currency || "USD",
    maximumFractionDigits: 4,
  });
}

// A decimal STRING → Number, for sorting only. NaN sorts last rather than
// colliding with a real zero.
function num(value) {
  if (value == null || value === "") return null;
  const n = Number(value);
  return Number.isFinite(n) ? n : null;
}

function titleise(value) {
  return (value || "").replace(/_/g, " ");
}

// Row clicks open the detail pane. Every interactive element inside a cell has
// to stop propagation, or picking a taxonomy value would also re-open the pane
// on top of the select the user is still using.
function stop(e) {
  e.stopPropagation();
}

// ─── Cells ───────────────────────────────────────────────────────────────────

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
            {row.default_taxonomy_key || "—"}
          </span>
        )}
      </span>
    );
  }

  return (
    <select
      value={row.default_taxonomy_key || ""}
      disabled={pending}
      onClick={stop}
      onMouseDown={stop}
      onChange={(e) => {
        e.stopPropagation();
        onEdit(row, "default_taxonomy_key", e.target.value || null);
      }}
      className="w-full rounded border border-transparent bg-transparent px-1 py-0.5 text-xs text-[var(--2a-text-secondary)] hover:border-[var(--2a-border)] focus:border-[var(--2a-gold)] focus:outline-none disabled:opacity-50"
      title={
        row.default_taxonomy_key && !row.taxonomy_label
          ? `Key ${row.default_taxonomy_key} has no active config row — it will not resolve to a label.`
          : "Reassign taxonomy"
      }
    >
      <option value="">— unassigned —</option>
      {/* A key with no config row is still shown, flagged, so a stale
          assignment is visible rather than silently rendering as unassigned. */}
      {row.default_taxonomy_key && !taxonomy?.[row.default_taxonomy_key] && (
        <option value={row.default_taxonomy_key}>
          {row.default_taxonomy_key} (unknown key)
        </option>
      )}
      {options.map(([key, label]) => (
        <option key={key} value={key}>
          {label}
        </option>
      ))}
    </select>
  );
}

function PerformanceCell({ row, editable, onEdit, pending }) {
  if (!editable) {
    return (
      <span className="text-[var(--2a-text-muted)]">
        {row.include_in_performance ? "✓" : "—"}
      </span>
    );
  }
  return (
    <input
      type="checkbox"
      checked={!!row.include_in_performance}
      disabled={pending}
      onClick={stop}
      onMouseDown={stop}
      onChange={(e) => {
        e.stopPropagation();
        onEdit(row, "include_in_performance", e.target.checked);
      }}
      aria-label="Include in performance"
      className="accent-[var(--2a-navy)] disabled:opacity-50"
    />
  );
}

/**
 * The linked global identifier. READ-ONLY for every caller, always — there is
 * no `editable` prop and no branch that could grow one.
 *
 * The lock glyph is not decoration. `name`, `short_name` and `currency_code`
 * exist on BOTH portfolio.assets and portfolio.securities_global, so a screen
 * that showed the two halves in the same visual register would be one where the
 * difference between a legal edit and an illegal one is which identical-looking
 * box the user clicked. Platform-sourced values are marked, everywhere.
 */
function GlobalIdentifierCell({ row }) {
  if (!row.global_security_id) {
    return (
      <span
        className="cursor-help text-[var(--2a-text-muted)]"
        title="This asset is not linked to a global security. That is a legitimate permanent state for a property, a private interest or a collectible — not missing data."
      >
        — unlinked —
      </span>
    );
  }
  if (!row.global_identifier_value) {
    return (
      <span
        className="cursor-help text-[var(--2a-text-muted)]"
        title={`Linked to “${row.global_name}”, which carries no identifier in the platform master yet.`}
      >
        {row.global_name}
      </span>
    );
  }
  return (
    <span
      className="inline-flex items-center gap-1 font-mono text-[11px] text-[var(--2a-text-secondary)]"
      title={`${row.global_identifier_type?.toUpperCase()} ${row.global_identifier_value} · ${row.global_name} · platform-sourced, not editable here`}
    >
      <span aria-hidden="true" className="text-[var(--2a-text-muted)]">
        🔒
      </span>
      <span className="uppercase text-[9px] tracking-wide text-[var(--2a-text-muted)]">
        {row.global_identifier_type}
      </span>
      {row.global_identifier_value}
    </span>
  );
}

// ─── The screen ──────────────────────────────────────────────────────────────

const EMPTY_FILTERS = {
  search: "",
  asset_class: "",
  valuation_method: "",
  taxonomy_key: "",
  security_type: "",
  linked: "all",
  include_inactive: "",
};

export default function SecuritiesGrid() {
  const [rows, setRows] = useState([]);
  const [meta, setMeta] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  const [filters, setFilters] = useState(EMPTY_FILTERS);
  const [selectedId, setSelectedId] = useState(null);
  const [pendingId, setPendingId] = useState(null);
  const [inlineError, setInlineError] = useState(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const params = new URLSearchParams();
      for (const [key, value] of Object.entries(filters)) {
        if (value !== "") params.set(key, value);
      }
      const res = await fetch(`/api/portfolio/securities?${params}`, {
        cache: "no-store",
      });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(body.error || body.detail || "Could not load assets.");
      setRows(Array.isArray(body.assets) ? body.assets : []);
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
  const permissions = meta?.permissions;

  // THE ONLY THING THAT DECIDES WHETHER A WRITE CONTROL EXISTS. Server-built,
  // permission-aware, and empty for a caller without manage_portfolio. There is
  // deliberately no local fallback list — a `|| DEFAULTS` here would silently
  // restore write controls for a view-only user the first time the envelope was
  // missing for an unrelated reason.
  const inlineEditable = useMemo(
    () => new Set(vocabularies?.inline_editable || []),
    [vocabularies],
  );
  const canWrite = !!permissions?.can_write;

  // Rows the grid actually sorts on. The derived `_*` fields exist only so
  // TanStack sorts numerically on values that arrive as exact decimal strings.
  const gridRows = useMemo(
    () =>
      rows.map((r) => ({
        ...r,
        _value: num(r.current_value),
        _price: num(r.latest_price),
        _taxonomy: r.taxonomy_label || r.default_taxonomy_key || "",
        _identifier: r.global_identifier_value || "",
      })),
    [rows],
  );

  // An inline edit round-trips through the real PATCH endpoint. The asset's id
  // does NOT change (the outgoing version is archived on the system axis), so
  // the row is patched in place rather than swapped — the opposite of the
  // Positions and Transactions grids, and the response is trusted rather than
  // the optimistic value.
  const handleInlineEdit = useCallback(
    async (row, field, value) => {
      setPendingId(row.id);
      setInlineError(null);
      try {
        const res = await fetch(`/api/portfolio/securities/${row.id}`, {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ [field]: value }),
        });
        const body = await res.json().catch(() => ({}));
        if (!res.ok) {
          throw new Error(body.error || body.detail || "The edit was refused.");
        }
        const updated = body.asset;
        setRows((prev) => prev.map((r) => (r.id === row.id ? updated : r)));
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
        field: "name",
        headerName: "Asset",
        enableColumnFilter: true,
        filterPlaceholder: "Filter asset…",
        cell: (v, row) => (
          <span className="font-medium text-[var(--2a-text)]" title={row.short_name || v}>
            {v}
            {!row.is_active && (
              <span className="ml-2 text-[10px] uppercase tracking-wide text-[var(--2a-text-muted)]">
                inactive
              </span>
            )}
          </span>
        ),
      },
      {
        field: "asset_type",
        headerName: "Type",
        enableColumnFilter: true,
        filterPlaceholder: "Filter type…",
        cell: (v) => (
          <span className="text-[var(--2a-text-secondary)]">{titleise(v)}</span>
        ),
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
            editable={inlineEditable.has("default_taxonomy_key")}
            onEdit={handleInlineEdit}
            pending={pendingId === row.id}
          />
        ),
      },
      {
        field: "valuation_method",
        headerName: "Valuation",
        enableColumnFilter: true,
        filterPlaceholder: "Filter…",
        cell: (v) => (
          <span className="text-[10px] uppercase tracking-wide text-[var(--2a-text-muted)]">
            {titleise(v)}
          </span>
        ),
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
            // An em-dash, never $0 — an unmeasured asset and a genuine zero are
            // different facts, and the reason travels all the way here.
            <span
              className="cursor-help text-[var(--2a-text-muted)]"
              title={row.current_value_reason || "No valuation resolved."}
            >
              —
            </span>
          ),
      },
      {
        field: "_identifier",
        headerName: "Global identifier",
        enableColumnFilter: true,
        filterPlaceholder: "CUSIP / ticker…",
        cell: (_v, row) => <GlobalIdentifierCell row={row} />,
      },
      {
        field: "_price",
        headerName: "Latest price",
        align: "right",
        cell: (_v, row) =>
          row.latest_price != null ? (
            <span
              className="tabular-nums text-[var(--2a-text-secondary)]"
              title={`${row.latest_price_date} · ${row.latest_price_type} · platform-sourced`}
            >
              {fmtPrice(row.latest_price, row.latest_price_currency)}
            </span>
          ) : (
            <span
              className="cursor-help text-[var(--2a-text-muted)]"
              title={row.latest_price_reason || "No platform price."}
            >
              —
            </span>
          ),
      },
      {
        field: "org_name",
        headerName: "Org",
        enableColumnFilter: true,
        filterPlaceholder: "Filter org…",
        cell: (v) => (
          <span className="text-[11px] text-[var(--2a-text-muted)]">{v}</span>
        ),
      },
      {
        field: "include_in_performance",
        headerName: "Perf.",
        align: "center",
        cell: (_v, row) => (
          <PerformanceCell
            row={row}
            editable={inlineEditable.has("include_in_performance")}
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
      <div className="rounded-lg border bg-white px-4 py-3" style={CARD}>
        <div className="flex flex-wrap items-end gap-3">
          <div>
            <label className={EYEBROW} htmlFor="f-search">
              Search
            </label>
            <input
              id="f-search"
              type="search"
              className={`${CONTROL} mt-1 w-56`}
              placeholder="Name, CUSIP, ticker…"
              value={filters.search}
              onChange={(e) => setFilter("search", e.target.value)}
            />
          </div>

          <div>
            <label className={EYEBROW} htmlFor="f-class">
              Class
            </label>
            <select
              id="f-class"
              className={`${CONTROL} mt-1 w-36`}
              value={filters.asset_class}
              onChange={(e) => setFilter("asset_class", e.target.value)}
            >
              <option value="">Any</option>
              {(vocabularies?.asset_class || []).map((c) => (
                <option key={c} value={c}>
                  {titleise(c)}
                </option>
              ))}
            </select>
          </div>

          <div>
            <label className={EYEBROW} htmlFor="f-valuation">
              Valuation method
            </label>
            <select
              id="f-valuation"
              className={`${CONTROL} mt-1 w-40`}
              value={filters.valuation_method}
              onChange={(e) => setFilter("valuation_method", e.target.value)}
            >
              <option value="">Any</option>
              {(vocabularies?.valuation_method || []).map((m) => (
                <option key={m} value={m}>
                  {titleise(m)}
                </option>
              ))}
            </select>
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
            <label className={EYEBROW} htmlFor="f-security-type">
              Security type
            </label>
            <select
              id="f-security-type"
              className={`${CONTROL} mt-1 w-40`}
              value={filters.security_type}
              onChange={(e) => setFilter("security_type", e.target.value)}
            >
              <option value="">Any</option>
              {(vocabularies?.security_type || []).map((t) => (
                <option key={t} value={t}>
                  {titleise(t)}
                </option>
              ))}
            </select>
          </div>

          <div>
            <label className={EYEBROW} htmlFor="f-linked">
              Global link
            </label>
            <select
              id="f-linked"
              className={`${CONTROL} mt-1 w-36`}
              value={filters.linked}
              onChange={(e) => setFilter("linked", e.target.value)}
            >
              <option value="all">All assets</option>
              <option value="linked">Linked only</option>
              <option value="unlinked">Unlinked only</option>
            </select>
          </div>

          <label className="flex items-center gap-1.5 pb-1 text-xs text-[var(--2a-text-secondary)]">
            <input
              type="checkbox"
              checked={filters.include_inactive === "true"}
              onChange={(e) =>
                setFilter("include_inactive", e.target.checked ? "true" : "")
              }
              className="accent-[var(--2a-navy)]"
            />
            Include inactive
          </label>

          <button
            type="button"
            onClick={() => setFilters(EMPTY_FILTERS)}
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
            ? `${meta.returned} of ${meta.total} asset${meta.total === 1 ? "" : "s"}`
            : "—"}
          {truncated && (
            <span className="ml-2 text-[var(--2a-gold)]">
              · showing the first {meta.limit}; narrow the filters above to sort
              across the whole set
            </span>
          )}
          {permissions && !canWrite && (
            <span className="ml-2 text-[var(--2a-text-muted)]">
              · read-only — you hold {permissions.read_permission} but not{" "}
              {permissions.write_permission}
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
            gridId="portfolio-securities"
            columnDefs={columnDefs}
            rowData={gridRows}
            getRowId={(row) => row.id}
            onRowClick={(row) => setSelectedId(row.id)}
            selectedRowId={selectedId}
            quickFilterPlaceholder="Search loaded rows…"
            emptyMessage={
              loading ? "Loading assets…" : "No assets match these filters."
            }
            pageSize={50}
          />
        </div>

        <div
          className="col-span-2 overflow-hidden rounded-lg border bg-white"
          style={CARD}
        >
          <AssetDetailPane
            assetId={selectedId}
            taxonomy={taxonomy}
            onClose={() => setSelectedId(null)}
            onSaved={(detail) => {
              // The asset kept its id — patch the row in place. Nothing to swap.
              const updated = detail.asset;
              setRows((prev) =>
                prev.map((r) => (r.id === updated.id ? updated : r)),
              );
            }}
          />
        </div>
      </div>
    </div>
  );
}
