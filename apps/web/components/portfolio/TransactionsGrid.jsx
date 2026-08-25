"use client";

/**
 * TransactionsGrid — the Transactions screen. Portfolio UX sprint 2.
 *
 * The same shape PositionsGrid established: a dense sortable/filterable grid on
 * the left, a detail pane on the right, no navigation, no wizard for a routine
 * fix. `components/ui/DataGrid.jsx` owns sort, per-column filter, global
 * filter, column visibility, column reorder and pagination; nothing here
 * re-implements any of it, and inline editing is a `columnDefs[].cell`
 * renderer, not a grid feature.
 *
 * WHAT MAKES THIS SCREEN DIFFERENT FROM POSITIONS
 * ─────────────────────────────────────────────────────────────────────────
 * 1. **An edit is a CORRECTION, not an update.** portfolio.transactions is an
 *    append-only ledger — nothing in the backend has ever issued an UPDATE
 *    against it. So a change POSTs to
 *    /api/portfolio/transactions/{id}/corrections, which closes the original
 *    and mints a successor with a NEW id. The row is REPLACED in local state,
 *    never patched in place, and the selection follows the successor. A client
 *    that kept the id it sent would be reading history.
 *
 * 2. **Corporate-action adjustments must never look like ordinary trades**
 *    (Phase F). They are the rows a realized-gain report excludes, and a
 *    reader scanning this grid has to be able to see that without opening
 *    anything. Marked three ways at once, because one marker is a marker
 *    somebody misses: a gold row wash, an explicit "Corp. action" kind cell,
 *    and a gold pill next to the type label. The `is_corporate_action_adjustment`
 *    filter is a first-class control in the bar above, tri-state, because
 *    "only real trades" is a question people ask constantly.
 *
 * 3. **Almost nothing is inline-editable, and the server says which.** A
 *    correction runs back through `record_transaction`, whose type-existence,
 *    is_active and Phase-E market checks can all REFUSE it — and a refusal that
 *    surfaces as a cell snapping back is worse than no inline edit at all. The
 *    server publishes `vocabularies.inline_correctable` (settle date and the
 *    custodian reference, the only two fields nothing validates) and this
 *    component honours that list rather than keeping its own copy.
 *
 * 4. **Permissions come from the server too (UX 4).** That same
 *    `inline_correctable` list arrives EMPTY for a caller without
 *    `manage_portfolio`, and `permissions.can_correct` says so directly.
 *    Between them they are the only thing deciding whether a correction
 *    control is rendered here. There is no local fallback list — a
 *    `|| DEFAULTS` would put live settle-date and reference inputs back in
 *    front of a view-only member the moment the envelope went missing.
 *
 *    Not the enforcement: `POST /portfolio/transactions/{id}/corrections`
 *    re-checks and returns 403 naming the permission, and
 *    `verify_portfolioux4` asserts both independently.
 *
 * MONEY SORTS NUMERICALLY, NOT LEXICALLY
 * ─────────────────────────────────────────────────────────────────────────
 * Figures arrive as exact decimal STRINGS. Sorting those lexically puts "9"
 * above "10" and "-12.34" in a place of its own. Each row carries derived
 * Numbers for the sortable money columns — used ONLY for ordering, never
 * rendered.
 */

import { useCallback, useEffect, useMemo, useState } from "react";

import DataGrid from "@/components/ui/DataGrid";
import EntityPicker from "@/components/EntityPicker";
import TransactionDetailPane from "@/components/portfolio/TransactionDetailPane";

const CARD = { borderColor: "#ece8dd", boxShadow: "0 1px 3px rgba(0,0,0,0.06)" };
const CONTROL =
  "rounded border border-[var(--2a-border)] bg-white px-2 py-1 text-xs text-[var(--2a-text)] focus:outline-none focus:ring-1 focus:ring-[var(--2a-gold)]";
const EYEBROW =
  "block text-[10px] font-semibold uppercase tracking-[0.12em] text-[var(--2a-text-muted)]";

// The row wash for a corporate-action adjustment. Gold, at low alpha, so the
// row reads as "not an ordinary trade" at a glance without becoming unreadable
// or introducing a colour outside the brand set.
const ADJUSTMENT_WASH = "rgba(197, 168, 128, 0.10)";

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
    maximumFractionDigits: 2,
  });
}

function fmtNumber(value, digits = 4) {
  if (value == null || value === "") return "—";
  const n = Number(value);
  if (!Number.isFinite(n)) return value;
  return n.toLocaleString("en-US", { maximumFractionDigits: digits });
}

// A decimal STRING → Number, for sorting only. Non-numeric sorts as null rather
// than colliding with a real zero.
function num(value) {
  if (value == null || value === "") return null;
  const n = Number(value);
  return Number.isFinite(n) ? n : null;
}

// Row clicks open the detail pane. Every interactive element inside a cell has
// to stop propagation, or using an editor would also re-open the pane on top of
// the control the user is still in.
function stop(e) {
  e.stopPropagation();
}

// ─── The corporate-action marker ─────────────────────────────────────────────

function KindCell({ row }) {
  if (!row.is_corporate_action_adjustment) {
    return (
      <span className="text-[10px] uppercase tracking-wide text-[var(--2a-text-muted)]">
        trade
      </span>
    );
  }
  return (
    <span
      className="inline-flex items-center rounded-full px-2 py-0.5 text-[9px] font-semibold uppercase tracking-wide"
      style={{ backgroundColor: "var(--2a-gold-light)", color: "var(--2a-navy)" }}
      title={
        "Corporate-action adjustment. Recorded by the corporate-actions engine " +
        "to restate a holding after a split, spinoff or similar — it is not a " +
        "trade and is excluded from realized gain."
      }
    >
      corp. action
    </span>
  );
}

function TypeCell({ row }) {
  return (
    <span className="text-[var(--2a-text-secondary)]">
      {row.transaction_type_label}
      {row.transaction_type_is_active === false && (
        <span
          className="ml-1 text-[9px] text-[var(--2a-text-muted)]"
          title="This transaction type has been retired. The historical entry keeps it."
        >
          (retired)
        </span>
      )}
      {row.is_corporate_action_adjustment && (
        <span
          className="ml-1 rounded-full px-1.5 py-0.5 text-[9px]"
          style={{
            backgroundColor: "var(--2a-gold-light)",
            color: "var(--2a-navy)",
          }}
          title="Corporate-action adjustment — excluded from realized gain"
        >
          adj
        </span>
      )}
    </span>
  );
}

// ─── Inline editors — only what the server publishes as safe ────────────────

function SettleDateCell({ row, editable, onEdit, pending }) {
  if (!editable) {
    return (
      <span className="text-[var(--2a-text-muted)]">{fmtDate(row.settle_date)}</span>
    );
  }
  return (
    <input
      type="date"
      value={row.settle_date || ""}
      disabled={pending}
      onClick={stop}
      onMouseDown={stop}
      onChange={(e) => {
        e.stopPropagation();
        onEdit(row, "settle_date", e.target.value || null);
      }}
      aria-label="Settle date"
      className="w-full rounded border border-transparent bg-transparent px-1 py-0.5 text-xs text-[var(--2a-text-secondary)] hover:border-[var(--2a-border)] focus:border-[var(--2a-gold)] focus:outline-none disabled:opacity-50"
      title="Correcting a settle date closes this entry and records a successor."
    />
  );
}

function ExternalRefCell({ row, editable, onEdit, pending }) {
  const [value, setValue] = useState(row.external_ref || "");
  useEffect(() => {
    setValue(row.external_ref || "");
  }, [row.external_ref, row.id]);

  if (!editable) {
    return (
      <span className="text-[var(--2a-text-muted)]">{row.external_ref || "—"}</span>
    );
  }
  const commit = () => {
    const next = value.trim();
    if (next === (row.external_ref || "")) return;
    onEdit(row, "external_ref", next === "" ? null : next);
  };
  return (
    <input
      type="text"
      value={value}
      disabled={pending}
      onClick={stop}
      onMouseDown={stop}
      onChange={(e) => setValue(e.target.value)}
      onBlur={commit}
      onKeyDown={(e) => {
        if (e.key === "Enter") e.currentTarget.blur();
        if (e.key === "Escape") setValue(row.external_ref || "");
      }}
      aria-label="Custodian reference"
      placeholder="—"
      className="w-full rounded border border-transparent bg-transparent px-1 py-0.5 text-xs text-[var(--2a-text-secondary)] hover:border-[var(--2a-border)] focus:border-[var(--2a-gold)] focus:outline-none disabled:opacity-50"
    />
  );
}

// ─── The screen ──────────────────────────────────────────────────────────────

const EMPTY_FILTERS = {
  owner_entity_id: "",
  transaction_type_code: "",
  source_system: "",
  authority: "",
  trade_from: "",
  trade_to: "",
  // "" = both kinds, "true" = adjustments only, "false" = real trades only.
  is_corporate_action_adjustment: "",
  search: "",
};

export default function TransactionsGrid() {
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
        // `value` is a string; "" means unset. "false" is a REAL value here and
        // survives because the test is against the empty string, not
        // truthiness — a truthiness check would drop the "real trades only"
        // filter and silently widen the query back to everything.
        if (value !== "") params.set(key, value);
      }
      const res = await fetch(`/api/portfolio/transactions?${params}`, {
        cache: "no-store",
      });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(body.error || "Could not load transactions.");
      setRows(Array.isArray(body.transactions) ? body.transactions : []);
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

  const vocabularies = meta?.vocabularies;
  const permissions = meta?.permissions;
  const types = useMemo(() => meta?.transaction_types || [], [meta]);

  // THE ONLY THING THAT DECIDES WHETHER A CORRECTION CONTROL EXISTS.
  // Server-built, permission-aware, empty without manage_portfolio. No local
  // fallback — see the header note.
  const inlineCorrectable = useMemo(
    () => new Set(vocabularies?.inline_correctable || []),
    [vocabularies],
  );
  const canCorrect = !!permissions?.can_correct;

  // Rows the grid actually sorts on. The derived `_*` fields exist only so
  // TanStack sorts numerically on values that arrive as exact decimal strings.
  const gridRows = useMemo(
    () =>
      rows.map((r) => ({
        ...r,
        _net: num(r.net_amount),
        _quantity: num(r.quantity),
        // The headline figure, chosen by the TYPE's amount_basis (published by
        // the server from public.transaction_types — not re-derived here). A
        // units-basis type headlines its quantity; a currency-basis type
        // headlines its gross amount.
        _amount:
          r.amount_basis === "units" ? num(r.quantity) : num(r.gross_amount),
        _kind: r.is_corporate_action_adjustment ? "corp. action" : "trade",
      })),
    [rows],
  );

  // A correction round-trips through the real endpoint. Because that endpoint
  // closes the original and mints a successor, the response carries a NEW id —
  // the row is REPLACED in local state, not patched, and the selection follows.
  const handleInlineEdit = useCallback(
    async (row, field, value) => {
      setPendingId(row.id);
      setInlineError(null);
      try {
        const res = await fetch(
          `/api/portfolio/transactions/${row.id}/corrections`,
          {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ [field]: value }),
          },
        );
        const body = await res.json().catch(() => ({}));
        if (!res.ok) {
          throw new Error(
            body.error || body.detail || "The correction was refused.",
          );
        }
        const updated = body.transaction;
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
        field: "trade_date",
        headerName: "Trade date",
        cell: (v) => (
          <span className="text-[var(--2a-text-secondary)]">{fmtDate(v)}</span>
        ),
      },
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
        field: "transaction_type_label",
        headerName: "Type",
        enableColumnFilter: true,
        filterPlaceholder: "Filter type…",
        cell: (_v, row) => <TypeCell row={row} />,
      },
      {
        field: "_kind",
        headerName: "Kind",
        align: "center",
        enableColumnFilter: true,
        filterPlaceholder: "trade / corp…",
        cell: (_v, row) => <KindCell row={row} />,
      },
      {
        field: "_amount",
        headerName: "Quantity / amount",
        align: "right",
        // Rendered from the type's amount_basis so the figure always carries
        // its unit. A bare "1000" that could be 1,000 shares or $1,000 is not a
        // figure. A units-basis row shows quantity × price; a currency-basis
        // row shows the gross amount.
        cell: (_v, row) =>
          row.amount_basis === "units" ? (
            <span>
              {fmtNumber(row.quantity)}
              {row.price != null && row.price !== "" && (
                <span className="ml-1 text-[10px] text-[var(--2a-text-muted)]">
                  @ {fmtMoney(row.price, row.currency_code)}
                </span>
              )}
            </span>
          ) : (
            fmtMoney(row.gross_amount, row.currency_code)
          ),
      },
      {
        field: "_net",
        headerName: "Net amount",
        align: "right",
        cell: (_v, row) =>
          row.net_amount != null ? (
            <span className="font-medium text-[var(--2a-text)]">
              {fmtMoney(row.net_amount, row.currency_code)}
            </span>
          ) : (
            // An em-dash, never $0. A ledger entry with no recorded net and one
            // that genuinely netted to zero are different facts.
            <span
              className="cursor-help text-[var(--2a-text-muted)]"
              title="No net amount was recorded on this entry."
            >
              —
            </span>
          ),
      },
      {
        field: "settle_date",
        headerName: "Settles",
        align: "right",
        cell: (_v, row) => (
          <SettleDateCell
            row={row}
            editable={inlineCorrectable.has("settle_date")}
            onEdit={handleInlineEdit}
            pending={pendingId === row.id}
          />
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
        field: "authority",
        headerName: "Authority",
        align: "center",
        cell: (v) => (
          <span className="text-[10px] uppercase tracking-wide text-[var(--2a-text-muted)]">
            {v}
          </span>
        ),
      },
      {
        field: "external_ref",
        headerName: "Reference",
        cell: (_v, row) => (
          <ExternalRefCell
            row={row}
            editable={inlineCorrectable.has("external_ref")}
            onEdit={handleInlineEdit}
            pending={pendingId === row.id}
          />
        ),
      },
    ],
    [inlineCorrectable, handleInlineEdit, pendingId],
  );

  // Row-level, because "this row is not a trade" is a row-level fact and a cell
  // renderer can only mark the cell it owns.
  const getRowStyle = useCallback(
    (row) =>
      row.is_corporate_action_adjustment
        ? { backgroundColor: ADJUSTMENT_WASH }
        : undefined,
    [],
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
            <label className={EYEBROW} htmlFor="f-type">
              Type
            </label>
            <select
              id="f-type"
              className={`${CONTROL} mt-1 w-52`}
              value={filters.transaction_type_code}
              onChange={(e) => setFilter("transaction_type_code", e.target.value)}
            >
              <option value="">Any type</option>
              {/* Labels and codes both come from public.transaction_types via
                  the API (Rule 1). Nothing here hardcodes either. */}
              {types.map((t) => (
                <option key={t.code} value={t.code}>
                  {t.label}
                  {t.is_active ? "" : " (retired)"}
                </option>
              ))}
            </select>
          </div>

          <div>
            <label className={EYEBROW} htmlFor="f-kind">
              Kind
            </label>
            <select
              id="f-kind"
              className={`${CONTROL} mt-1 w-44`}
              value={filters.is_corporate_action_adjustment}
              onChange={(e) =>
                setFilter("is_corporate_action_adjustment", e.target.value)
              }
            >
              <option value="">All entries</option>
              {/* Not a cosmetic split. "Trades only" is the realized-gain
                  population — the exact predicate Phase F says a report must be
                  able to write without knowing the corporate-action machinery
                  exists. */}
              <option value="false">Trades only</option>
              <option value="true">Corporate-action adjustments only</option>
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
              Traded from
            </label>
            <input
              id="f-from"
              type="date"
              className={`${CONTROL} mt-1`}
              value={filters.trade_from}
              onChange={(e) => setFilter("trade_from", e.target.value)}
            />
          </div>

          <div>
            <label className={EYEBROW} htmlFor="f-to">
              Traded to
            </label>
            <input
              id="f-to"
              type="date"
              className={`${CONTROL} mt-1`}
              value={filters.trade_to}
              onChange={(e) => setFilter("trade_to", e.target.value)}
            />
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
            ? `${meta.returned} of ${meta.total} transaction${meta.total === 1 ? "" : "s"}`
            : "—"}
          {truncated && (
            <span className="ml-2 text-[var(--2a-gold)]">
              · showing the first {meta.limit}; narrow the filters above to sort
              across the whole set
            </span>
          )}
          {permissions && !canCorrect && (
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
            gridId="portfolio-transactions"
            columnDefs={columnDefs}
            rowData={gridRows}
            getRowId={(row) => row.id}
            getRowStyle={getRowStyle}
            onRowClick={(row) => setSelectedId(row.id)}
            selectedRowId={selectedId}
            quickFilterPlaceholder="Search loaded rows…"
            emptyMessage={
              loading
                ? "Loading transactions…"
                : "No transactions match these filters."
            }
            pageSize={50}
          />
        </div>

        <div
          className="col-span-2 overflow-hidden rounded-lg border bg-white"
          style={CARD}
        >
          {/* No `vocabularies` prop: the pane fetches the detail endpoint,
              which publishes its OWN permission-aware vocabularies. Threading
              the grid's copy down would give the pane a second answer that
              could disagree with the one it just fetched. `transactionTypes`
              stays a prop — it is label data, not a permission. */}
          <TransactionDetailPane
            transactionId={selectedId}
            transactionTypes={types}
            onClose={() => setSelectedId(null)}
            onCorrected={(detail) => {
              // The pane corrected the entry: adopt the successor's id and swap
              // the row, so the grid and the pane agree about which entry is
              // current without a full reload.
              const updated = detail.transaction;
              setRows((prev) =>
                prev.map((r) => (r.id === detail.corrected_from ? updated : r)),
              );
              setSelectedId(updated.id);
            }}
          />
        </div>
      </div>
    </div>
  );
}
