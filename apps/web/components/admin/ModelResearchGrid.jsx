"use client";

/**
 * ModelResearchGrid — every model LiteLLM prices (modelresearch.structural).
 *
 * READ-ONLY. This component renders no write control of any kind; the
 * envelope's `editable` / `inline_editable` vocabularies are empty and nothing
 * here reads them for a write. Refresh only re-reads the sources server-side.
 *
 * Rows arrive from GET /api/admin/model-research (forwarded to FastAPI, which
 * reads the LiteLLM proxy and both catalog tables server-side; the browser
 * never calls the proxy or GitHub). Null fields are omitted from each row, so a
 * missing key means "unknown". `false` is sent explicitly and means "no".
 *
 * ~4,450 rows are held client-side. The filters below narrow them in one
 * useMemo, and the shared DataGrid sorts and paginates. Only one page of rows is
 * ever in the DOM, so no server-side paging is needed (Task 1d).
 *
 * Labels for kinds, capability flags and catalog states, and the default token
 * profile, come from the response's `vocabularies` (Rule 1). Providers and
 * modes are the distinct values present in the data itself.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import DataGrid from "@/components/ui/DataGrid";

const CARD = "rounded-md border border-[var(--2a-border)] bg-[var(--2a-bg-card)]";
const CONTROL =
  "rounded border border-[var(--2a-border)] bg-[var(--2a-bg-card)] px-2 py-1.5 text-xs text-[var(--2a-text)] focus:outline-none focus:ring-1 focus:ring-[var(--2a-gold)]";
const EYEBROW =
  "block text-[10px] font-semibold uppercase tracking-[0.12em] text-[var(--2a-text-muted)]";

const TRI = [
  { value: "", label: "Any" },
  { value: "yes", label: "Yes" },
  { value: "no", label: "No" },
];

function fmtUsd(v) {
  if (v === undefined || v === null) return "";
  if (v === 0) return "$0";
  if (Math.abs(v) < 0.01) return `$${Number(v.toPrecision(2))}`;
  if (Math.abs(v) < 1) return `$${v.toFixed(4).replace(/0+$/, "").replace(/\.$/, "")}`;
  return `$${v.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

function fmtInt(v) {
  return v === undefined || v === null ? "" : v.toLocaleString();
}

function fmtWhen(iso) {
  if (!iso) return null;
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString();
}

function Flag({ value }) {
  if (value === true) return <span className="text-[var(--2a-navy)]">Yes</span>;
  if (value === false) return <span className="text-[var(--2a-text-muted)]">No</span>;
  return null;
}

function triMatch(want, value) {
  if (!want) return true;
  return want === "yes" ? value === true : value !== true;
}

function inRange(value, min, max) {
  if (min === "" && max === "") return true;
  if (value === undefined || value === null) return false;
  if (min !== "" && value < Number(min)) return false;
  if (max !== "" && value > Number(max)) return false;
  return true;
}

function typicalCost(row, inTok, outTok) {
  if (row.input_per_1m === undefined) return undefined;
  const out = row.output_per_1m ?? 0;
  return (inTok * row.input_per_1m + outTok * out) / 1_000_000;
}

// ─── Provider multi-select ──────────────────────────────────────────────────

function ProviderPicker({ providers, selected, onChange }) {
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState("");
  const ref = useRef(null);

  useEffect(() => {
    if (!open) return;
    function onDown(e) {
      if (ref.current && !ref.current.contains(e.target)) setOpen(false);
    }
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, [open]);

  const shown = providers.filter((p) => p.toLowerCase().includes(q.toLowerCase()));
  const toggle = (p) => {
    const next = new Set(selected);
    if (next.has(p)) next.delete(p);
    else next.add(p);
    onChange(next);
  };

  return (
    <div className="relative" ref={ref}>
      <button type="button" onClick={() => setOpen((v) => !v)} className={`${CONTROL} w-full text-left`}>
        {selected.size === 0 ? "All providers" : `${selected.size} selected`}
      </button>
      {open && (
        <div className={`${CARD} absolute z-20 mt-1 w-64 p-2`}>
          <input
            type="text"
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="Find a provider…"
            className={`${CONTROL} mb-2 w-full`}
          />
          <div className="max-h-64 overflow-y-auto">
            {shown.map((p) => (
              <label
                key={p}
                className="flex cursor-pointer items-center gap-2 rounded px-2 py-1 text-xs text-[var(--2a-text-secondary)] hover:bg-[var(--2a-bg)]"
              >
                <input
                  type="checkbox"
                  checked={selected.has(p)}
                  onChange={() => toggle(p)}
                  className="accent-[var(--2a-navy)]"
                />
                {p}
              </label>
            ))}
          </div>
          {selected.size > 0 && (
            <button
              type="button"
              onClick={() => onChange(new Set())}
              className="mt-2 text-[10px] uppercase tracking-[0.12em] text-[var(--2a-text-muted)] hover:text-[var(--2a-navy)]"
            >
              Clear
            </button>
          )}
        </div>
      )}
    </div>
  );
}

function Labeled({ label, children }) {
  return (
    <label className="flex flex-col gap-1">
      <span className={EYEBROW}>{label}</span>
      {children}
    </label>
  );
}

function TriSelect({ label, value, onChange }) {
  return (
    <Labeled label={label}>
      <select value={value} onChange={(e) => onChange(e.target.value)} className={CONTROL}>
        {TRI.map((o) => (
          <option key={o.value} value={o.value}>
            {o.label}
          </option>
        ))}
      </select>
    </Labeled>
  );
}

const EMPTY_FILTERS = {
  search: "",
  mode: "",
  kind: "",
  live: "",
  catalog: "",
  inMin: "",
  inMax: "",
  outMin: "",
  outMax: "",
  flags: {},
};

// ─── Main ───────────────────────────────────────────────────────────────────

export default function ModelResearchGrid() {
  const [data, setData] = useState(null);
  const [status, setStatus] = useState("loading"); // loading | ready | forbidden | error
  const [error, setError] = useState(null);
  const [refreshing, setRefreshing] = useState(false);
  const [providers, setProviders] = useState(() => new Set());
  const [filters, setFilters] = useState(EMPTY_FILTERS);
  const [inTok, setInTok] = useState(null);
  const [outTok, setOutTok] = useState(null);

  const load = useCallback(async (refresh) => {
    if (refresh) setRefreshing(true);
    try {
      const res = await fetch(`/api/admin/model-research${refresh ? "?refresh=true" : ""}`, {
        cache: "no-store",
      });
      const body = await res.json().catch(() => ({}));
      if (res.status === 403) {
        setStatus("forbidden");
        setData(null);
        return;
      }
      if (!res.ok) {
        setError(typeof body.detail === "string" ? body.detail : body.error || "Could not load models.");
        setStatus("error");
        return;
      }
      // The grid renders only on a real envelope — a response without one is
      // treated as a failure, never as permission to show data.
      if (body?.permissions?.can_read !== true || !Array.isArray(body.rows)) {
        setError("The server's response had no permission envelope.");
        setStatus("error");
        setData(null);
        return;
      }
      setData(body);
      setError(null);
      setStatus("ready");
    } catch (e) {
      setError(e.message);
      setStatus("error");
    } finally {
      setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    load(false);
  }, [load]);

  const vocab = data?.vocabularies;
  const meta = data?.meta;
  const flagDefs = useMemo(() => vocab?.capability_flags ?? [], [vocab]);
  const profile = vocab?.default_token_profile;
  const inputTokens = inTok ?? profile?.input_tokens ?? 0;
  const outputTokens = outTok ?? profile?.output_tokens ?? 0;

  const allRows = useMemo(() => data?.rows ?? [], [data]);
  const providerOptions = useMemo(
    () => [...new Set(allRows.map((r) => r.provider).filter(Boolean))].sort(),
    [allRows],
  );
  const modeOptions = useMemo(
    () => [...new Set(allRows.map((r) => r.mode).filter(Boolean))].sort(),
    [allRows],
  );

  const rows = useMemo(() => {
    const q = filters.search.trim().toLowerCase();
    return allRows
      .filter((r) => {
        if (q && !`${r.model} ${r.display_name ?? ""}`.toLowerCase().includes(q)) return false;
        if (providers.size > 0 && !providers.has(r.provider)) return false;
        if (filters.mode && r.mode !== filters.mode) return false;
        if (filters.kind && r.kind !== filters.kind) return false;
        if (!triMatch(filters.live, r.live_on_proxy)) return false;
        if (!triMatch(filters.catalog, r.in_catalog)) return false;
        if (!inRange(r.input_per_1m, filters.inMin, filters.inMax)) return false;
        if (!inRange(r.output_per_1m, filters.outMin, filters.outMax)) return false;
        for (const f of flagDefs) {
          if (!triMatch(filters.flags[f.field], r[f.field])) return false;
        }
        return true;
      })
      .map((r) => {
        const cost = typicalCost(r, inputTokens, outputTokens);
        return {
          ...r,
          typical_call_cost: cost,
          cost_per_million_calls: cost === undefined ? undefined : cost * 1_000_000,
        };
      });
  }, [allRows, filters, providers, flagDefs, inputTokens, outputTokens]);

  const columnDefs = useMemo(
    () => [
      { field: "provider", headerName: "Provider" },
      {
        field: "model",
        headerName: "Model",
        minWidth: 220,
        cell: (value, row) => (
          <span className="font-medium text-[var(--2a-navy)]">
            {value}
            {row.display_name && (
              <span className="ml-2 font-normal text-[var(--2a-text-muted)]">{row.display_name}</span>
            )}
          </span>
        ),
      },
      { field: "version", headerName: "Version" },
      { field: "mode", headerName: "Mode" },
      { field: "kind", headerName: "Kind" },
      { field: "input_per_1m", headerName: "Input $/1M", align: "right", cell: fmtUsd },
      { field: "output_per_1m", headerName: "Output $/1M", align: "right", cell: fmtUsd },
      { field: "batch_input_per_1m", headerName: "Batch in $/1M", align: "right", cell: fmtUsd },
      { field: "batch_output_per_1m", headerName: "Batch out $/1M", align: "right", cell: fmtUsd },
      { field: "cached_input_per_1m", headerName: "Cached in $/1M", align: "right", cell: fmtUsd },
      { field: "typical_call_cost", headerName: "Typical call", align: "right", cell: fmtUsd },
      { field: "cost_per_million_calls", headerName: "Per 1M calls", align: "right", cell: fmtUsd },
      { field: "max_input_tokens", headerName: "Max input", align: "right", cell: fmtInt },
      { field: "max_output_tokens", headerName: "Max output", align: "right", cell: fmtInt },
      ...flagDefs.map((f) => ({
        field: f.field,
        headerName: f.label,
        align: "center",
        cell: (value) => <Flag value={value} />,
      })),
      { field: "deprecation_date", headerName: "Deprecation" },
      {
        field: "live_on_proxy",
        headerName: "Live on our proxy",
        align: "center",
        cell: (value, row) =>
          value === true ? (
            <span className="text-[var(--2a-navy)]">
              Yes
              {row.proxy_aliases?.length ? (
                <span className="ml-1 text-[var(--2a-text-muted)]">({row.proxy_aliases.join(", ")})</span>
              ) : null}
            </span>
          ) : null,
      },
      {
        field: "in_catalog",
        headerName: "In platform catalog",
        align: "center",
        cell: (value, row) => {
          if (value === true) return <span className="text-[var(--2a-navy)]">{row.catalog_availability}</span>;
          if (row.system_one_availability) {
            return <span className="text-[var(--2a-navy)]">{row.system_one_availability} (System One)</span>;
          }
          return null;
        },
      },
    ],
    [flagDefs],
  );

  if (status === "loading") {
    return <div className={`${CARD} mt-6 p-10 text-center text-sm text-[var(--2a-text-muted)]`}>Loading models…</div>;
  }
  if (status === "forbidden") {
    return (
      <div className={`${CARD} mt-6 p-10 text-center text-sm text-[var(--2a-text-muted)]`}>
        Model Research is available to organization administrators and Super Admins.
      </div>
    );
  }
  if (status === "error" || !data) {
    return (
      <div className={`${CARD} mt-6 p-10 text-center text-sm text-[var(--2a-text)]`}>
        {error || "Could not load models."}
        <div className="mt-4">
          <button type="button" onClick={() => load(true)} className={CONTROL}>
            Try again
          </button>
        </div>
      </div>
    );
  }

  const setF = (patch) => setFilters((prev) => ({ ...prev, ...patch }));
  const setFlag = (field, value) => setFilters((prev) => ({ ...prev, flags: { ...prev.flags, [field]: value } }));

  return (
    <div className="mt-6 flex flex-col gap-4">
      {/* Source + freshness */}
      <div className={`${CARD} flex flex-wrap items-start justify-between gap-4 p-4`}>
        <div className="grid grid-cols-1 gap-x-8 gap-y-2 text-xs text-[var(--2a-text-secondary)] sm:grid-cols-3">
          <div>
            <span className={EYEBROW}>Source</span>
            <span className="text-[var(--2a-text)]">{meta.source_label}</span>
            {meta.source_detail?.loaded_from_url && (
              <span className="block break-all text-[10px] text-[var(--2a-text-muted)]">
                {meta.source_detail.loaded_from_url}
              </span>
            )}
          </div>
          <div>
            <span className={EYEBROW}>Prices as of</span>
            <span className="text-[var(--2a-text)]">{fmtWhen(meta.prices_as_of) || "Not reported"}</span>
            {meta.prices_as_of_note && (
              <span className="block text-[10px] text-[var(--2a-text-muted)]">{meta.prices_as_of_note}</span>
            )}
          </div>
          <div>
            <span className={EYEBROW}>Last refreshed</span>
            <span className="text-[var(--2a-text)]">{fmtWhen(meta.last_refreshed)}</span>
            <span className="block text-[10px] text-[var(--2a-text-muted)]">
              {meta.counts?.source_models?.toLocaleString()} priced models
              {meta.counts?.system_one_models ? ` + ${meta.counts.system_one_models} System One` : ""}
            </span>
          </div>
        </div>
        <button
          type="button"
          onClick={() => load(true)}
          disabled={refreshing}
          className="rounded border border-[var(--2a-navy)] px-4 py-1.5 text-xs font-medium text-[var(--2a-navy)] hover:bg-[var(--2a-bg)] disabled:opacity-50"
        >
          {refreshing ? "Refreshing…" : "Refresh"}
        </button>
        {meta.deployments_error && (
          <p className="w-full text-xs text-[var(--2a-text)]">
            Proxy registrations could not be read, so no model is flagged live: {meta.deployments_error}
          </p>
        )}
      </div>

      {/* Token profile */}
      <div className={`${CARD} flex flex-wrap items-end gap-4 p-4`}>
        <Labeled label="Typical call — input tokens">
          <input
            type="number"
            min="0"
            value={inputTokens}
            onChange={(e) => setInTok(Math.max(0, Number(e.target.value) || 0))}
            className={`${CONTROL} w-36`}
          />
        </Labeled>
        <Labeled label="Typical call — output tokens">
          <input
            type="number"
            min="0"
            value={outputTokens}
            onChange={(e) => setOutTok(Math.max(0, Number(e.target.value) || 0))}
            className={`${CONTROL} w-36`}
          />
        </Labeled>
        <p className="text-xs text-[var(--2a-text-muted)]">
          Typical call = input tokens × input price + output tokens × output price. Per 1M calls is the same, a million
          times over.
        </p>
      </div>

      {/* Filters */}
      <div className={`${CARD} grid grid-cols-2 gap-3 p-4 md:grid-cols-4 lg:grid-cols-6`}>
        <Labeled label="Model name">
          <input
            type="text"
            value={filters.search}
            onChange={(e) => setF({ search: e.target.value })}
            placeholder="Search…"
            className={CONTROL}
          />
        </Labeled>
        <div className="flex flex-col gap-1">
          <span className={EYEBROW}>Provider</span>
          <ProviderPicker providers={providerOptions} selected={providers} onChange={setProviders} />
        </div>
        <Labeled label="Mode">
          <select value={filters.mode} onChange={(e) => setF({ mode: e.target.value })} className={CONTROL}>
            <option value="">Any</option>
            {modeOptions.map((m) => (
              <option key={m} value={m}>
                {m}
              </option>
            ))}
          </select>
        </Labeled>
        <Labeled label="Kind">
          <select value={filters.kind} onChange={(e) => setF({ kind: e.target.value })} className={CONTROL}>
            <option value="">Any</option>
            {(vocab?.kinds ?? []).map((k) => (
              <option key={k} value={k}>
                {k}
              </option>
            ))}
          </select>
        </Labeled>
        <TriSelect label="Live on our proxy" value={filters.live} onChange={(v) => setF({ live: v })} />
        <TriSelect label="In platform catalog" value={filters.catalog} onChange={(v) => setF({ catalog: v })} />
        {flagDefs.map((f) => (
          <TriSelect
            key={f.field}
            label={f.label}
            value={filters.flags[f.field] ?? ""}
            onChange={(v) => setFlag(f.field, v)}
          />
        ))}
        <Labeled label="Input $/1M — min / max">
          <div className="flex gap-1">
            <input type="number" min="0" value={filters.inMin} onChange={(e) => setF({ inMin: e.target.value })} className={`${CONTROL} w-1/2`} />
            <input type="number" min="0" value={filters.inMax} onChange={(e) => setF({ inMax: e.target.value })} className={`${CONTROL} w-1/2`} />
          </div>
        </Labeled>
        <Labeled label="Output $/1M — min / max">
          <div className="flex gap-1">
            <input type="number" min="0" value={filters.outMin} onChange={(e) => setF({ outMin: e.target.value })} className={`${CONTROL} w-1/2`} />
            <input type="number" min="0" value={filters.outMax} onChange={(e) => setF({ outMax: e.target.value })} className={`${CONTROL} w-1/2`} />
          </div>
        </Labeled>
        <div className="flex items-end">
          <button
            type="button"
            onClick={() => {
              setFilters(EMPTY_FILTERS);
              setProviders(new Set());
            }}
            className="text-[10px] uppercase tracking-[0.12em] text-[var(--2a-text-muted)] hover:text-[var(--2a-navy)]"
          >
            Clear filters
          </button>
        </div>
      </div>

      <div className={`${CARD} p-4`}>
        <p className="mb-2 text-xs text-[var(--2a-text-muted)]">
          Showing {rows.length.toLocaleString()} of {allRows.length.toLocaleString()} models
        </p>
        <DataGrid
          gridId="model-research"
          columnDefs={columnDefs}
          rowData={rows}
          getRowId={(r) => r.id}
          enableGlobalFilter={false}
          pageSize={50}
          emptyMessage="No models match these filters."
        />
      </div>
    </div>
  );
}
