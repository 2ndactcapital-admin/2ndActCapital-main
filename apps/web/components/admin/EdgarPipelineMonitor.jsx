"use client";

/**
 * EdgarPipelineMonitor — the EDGAR pipeline monitoring screen (edgarpipelinea).
 *
 * Four tabs over global SEC reference data, Super Admin only (FastAPI
 * enforces it; the page shows a "not permitted" panel on its 403):
 *
 *   Filings  — the ~717K-row manifest through the shared DataGrid in its
 *              serverSide mode: filter, sort and paging all happen in FastAPI;
 *              only the current page is ever in the browser. Rows can be
 *              TICKED (ticks are kept by accession number, so they survive
 *              paging, sorting and filtering) and a COHORT built from the
 *              ticks or from everything matching the filters (edgarcohorts).
 *   Progress — counts by status per quarter, the recent pipeline runs, and
 *              "Run default policy now (newest first)" — the nightly
 *              behaviour. Everything targeted runs from the Cohorts tab.
 *   Cohorts  — saved, frozen cohorts: definition, members by status, runs,
 *              "Fetch this cohort", copy-and-edit, the template-study preset
 *              and its inventory results.
 *   Issuers  — the issuer table with inline edits, the unlisted 424B2 filers
 *              beside it, and an add form a filer can be promoted into.
 *
 * Every label (statuses, document kinds, include statuses, roles), every
 * filter vocabulary and every editable-field list comes from the response
 * envelope (Rule 1). Write controls render ONLY inside a
 * `permissions.can_write === true` check, and editable fields come only from
 * `vocabularies.editable` — no client-side default, no truthy fallback. A lost
 * envelope renders a read-only screen.
 */

import { useCallback, useEffect, useMemo, useState, useTransition } from "react";

import { CohortBuilder, CohortsTab } from "@/components/admin/EdgarCohorts";
import DataGrid from "@/components/ui/DataGrid";
import {
  addIssuerAction,
  loadFilingsAction,
  loadIssuersAction,
  loadProgressAction,
  runNowAction,
  updateIssuerAction,
} from "@/lib/edgarPipelineActions";

const CARD = "rounded-md border border-[var(--2a-border)] bg-[var(--2a-bg-card)]";
const CONTROL =
  "rounded border border-[var(--2a-border)] bg-[var(--2a-bg-card)] px-2 py-1.5 text-xs text-[var(--2a-text)] focus:outline-none focus:ring-1 focus:ring-[var(--2a-gold)]";
const EYEBROW =
  "block text-[10px] font-semibold uppercase tracking-[0.12em] text-[var(--2a-text-muted)]";
const BUTTON =
  "rounded bg-[var(--2a-navy)] px-3 py-1.5 text-xs font-medium text-white disabled:opacity-50";
const GHOST =
  "rounded border border-[var(--2a-border)] px-3 py-1.5 text-xs text-[var(--2a-navy)] hover:bg-[var(--2a-bg)] disabled:opacity-50";
const ERROR_INK = "#9B2335";

const TABS = [
  { key: "filings", label: "Filings" },
  { key: "progress", label: "Progress" },
  { key: "cohorts", label: "Cohorts" },
  { key: "issuers", label: "Issuers" },
];

const EMPTY_FILTERS = {
  issuer_group: "",
  form_type: "",
  status: "",
  document_kind: "",
  quarter: "",
  date_from: "",
  date_to: "",
};

function canWrite(payload) {
  return payload?.permissions?.can_write === true;
}

function fmtDate(v) {
  return v ? String(v).slice(0, 10) : "";
}

function fmtWhen(v) {
  if (!v) return "";
  const d = new Date(v);
  return Number.isNaN(d.getTime()) ? String(v) : d.toLocaleString();
}

function fmtBytes(n) {
  if (n === null || n === undefined) return "";
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  if (n < 1024 * 1024 * 1024) return `${(n / 1024 / 1024).toFixed(1)} MB`;
  return `${(n / 1024 / 1024 / 1024).toFixed(2)} GB`;
}

function ErrorLine({ text }) {
  if (!text) return null;
  return (
    <p className="mt-2 text-xs" style={{ color: ERROR_INK }}>
      {text}
    </p>
  );
}

function Select({ label, value, onChange, options, anyLabel = "Any" }) {
  return (
    <label className="flex flex-col gap-1">
      <span className={EYEBROW}>{label}</span>
      <select className={CONTROL} value={value} onChange={(e) => onChange(e.target.value)}>
        <option value="">{anyLabel}</option>
        {options.map((o) => (
          <option key={o.value} value={o.value}>
            {o.label}
          </option>
        ))}
      </select>
    </label>
  );
}

// ─── Filings tab ─────────────────────────────────────────────────────────────

function FilingsTab({ initial, onCohortSaved }) {
  const [payload, setPayload] = useState(initial);
  // Ticks are keyed by accession number and live outside the page, so they
  // survive paging, sorting and filter changes.
  const [ticked, setTicked] = useState(() => new Set());
  const [filters, setFilters] = useState(EMPTY_FILTERS);
  const [sorting, setSorting] = useState([{ id: "filing_date", desc: true }]);
  const [pageIndex, setPageIndex] = useState(0);
  const [error, setError] = useState(null);
  const [pending, startTransition] = useTransition();

  const vocab = payload?.vocabularies ?? {};
  const statuses = vocab.statuses ?? {};
  const kinds = vocab.document_kinds ?? {};
  const sortable = useMemo(() => new Set(vocab.sortable ?? []), [vocab.sortable]);
  const pageSize = payload?.page_size ?? 50;

  const load = useCallback(
    (nextFilters, nextSorting, nextPage) => {
      const sort = nextSorting[0] ?? { id: "filing_date", desc: true };
      const params = {
        ...Object.fromEntries(Object.entries(nextFilters).filter(([, v]) => v !== "")),
        sort: sort.id,
        direction: sort.desc ? "desc" : "asc",
        page: nextPage + 1,
        page_size: pageSize,
      };
      startTransition(async () => {
        const res = await loadFilingsAction(params);
        if (res.ok) {
          setPayload(res.payload);
          setError(null);
        } else {
          setError(res.error);
        }
      });
    },
    [pageSize],
  );

  function changeFilter(key, value) {
    const next = { ...filters, [key]: value };
    setFilters(next);
    setPageIndex(0);
    load(next, sorting, 0);
  }

  function toggle(acc) {
    setTicked((prev) => {
      const next = new Set(prev);
      if (next.has(acc)) next.delete(acc);
      else next.add(acc);
      return next;
    });
  }

  // A stable array: the builder clears its preview whenever this changes.
  const tickedList = useMemo(() => [...ticked], [ticked]);

  function tickPage() {
    setTicked((prev) => new Set([...prev, ...(payload?.rows ?? []).map((r) => r.accession_number)]));
  }

  const columnDefs = useMemo(
    () => [
      {
        field: "_tick",
        headerName: "Pick",
        enableSorting: false,
        cell: (_v, row) => (
          <input
            type="checkbox"
            aria-label={`Pick ${row.accession_number}`}
            checked={ticked.has(row.accession_number)}
            onChange={() => toggle(row.accession_number)}
          />
        ),
      },
      { field: "filing_date", headerName: "Filed", cell: (v) => fmtDate(v), enableSorting: sortable.has("filing_date") },
      {
        field: "accession_number",
        headerName: "Accession",
        enableSorting: sortable.has("accession_number"),
        cell: (v, row) => (
          <a
            href={row.sec_filing_url}
            target="_blank"
            rel="noopener noreferrer"
            className="text-[var(--2a-navy)] underline decoration-[var(--2a-gold)] underline-offset-2"
          >
            {v}
          </a>
        ),
      },
      { field: "form_type", headerName: "Form", enableSorting: sortable.has("form_type") },
      { field: "issuer_group", headerName: "Issuer group", enableSorting: false },
      { field: "primary_filer_name", headerName: "Primary filer", enableSorting: false },
      {
        field: "pipeline_status",
        headerName: "Status",
        enableSorting: sortable.has("pipeline_status"),
        cell: (v) => statuses[v] ?? v,
      },
      {
        field: "document_kind",
        headerName: "Document",
        enableSorting: sortable.has("document_kind"),
        cell: (v) => (v ? kinds[v] ?? v : ""),
      },
      { field: "status_reason", headerName: "Reason", enableSorting: false, minWidth: 220 },
      { field: "detected_cusip", headerName: "CUSIP", enableSorting: false },
      { field: "index_quarter", headerName: "Quarter", enableSorting: sortable.has("index_quarter") },
      { field: "attempt_count", headerName: "Attempts", align: "right", enableSorting: false },
    ],
    [sortable, statuses, kinds, ticked],
  );

  const groupOptions = [
    ...(vocab.issuer_groups ?? []).map((g) => ({ value: g, label: g })),
    ...(vocab.unlisted_group ? [{ value: vocab.unlisted_group, label: "No listed issuer" }] : []),
  ];

  return (
    <div className={`${CARD} mt-4 p-4`}>
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4 lg:grid-cols-7">
        <Select label="Issuer group" value={filters.issuer_group} onChange={(v) => changeFilter("issuer_group", v)} options={groupOptions} />
        <Select label="Form" value={filters.form_type} onChange={(v) => changeFilter("form_type", v)} options={(vocab.form_types ?? []).map((f) => ({ value: f, label: f }))} />
        <Select label="Status" value={filters.status} onChange={(v) => changeFilter("status", v)} options={Object.entries(statuses).map(([value, label]) => ({ value, label }))} />
        <Select label="Document" value={filters.document_kind} onChange={(v) => changeFilter("document_kind", v)} options={Object.entries(kinds).map(([value, label]) => ({ value, label }))} />
        <Select label="Quarter" value={filters.quarter} onChange={(v) => changeFilter("quarter", v)} options={(vocab.quarters ?? []).map((q) => ({ value: q, label: q }))} />
        <label className="flex flex-col gap-1">
          <span className={EYEBROW}>Filed from</span>
          <input type="date" className={CONTROL} value={filters.date_from} onChange={(e) => changeFilter("date_from", e.target.value)} />
        </label>
        <label className="flex flex-col gap-1">
          <span className={EYEBROW}>Filed to</span>
          <input type="date" className={CONTROL} value={filters.date_to} onChange={(e) => changeFilter("date_to", e.target.value)} />
        </label>
      </div>
      <ErrorLine text={error} />
      <div className="mt-3 flex flex-wrap items-center gap-3 text-xs text-[var(--2a-text-secondary)]">
        <span>{ticked.size.toLocaleString()} ticked</span>
        <button type="button" className={GHOST} onClick={tickPage} disabled={!(payload?.rows ?? []).length}>
          Tick this page
        </button>
        <button type="button" className={GHOST} onClick={() => setTicked(new Set())} disabled={!ticked.size}>
          Clear ticks
        </button>
      </div>
      <div className="mt-4">
        <DataGrid
          gridId="edgar-filings"
          columnDefs={columnDefs}
          rowData={payload?.rows ?? []}
          getRowId={(row) => row.accession_number}
          emptyMessage={pending ? "Loading…" : "No filings match these filters."}
          serverSide={{
            totalRows: payload?.total ?? 0,
            pageIndex,
            pageSize,
            sorting,
            loading: pending,
            onSortingChange: (next) => {
              setSorting(next);
              setPageIndex(0);
              load(filters, next, 0);
            },
            onPageChange: (next) => {
              setPageIndex(next);
              load(filters, sorting, next);
            },
          }}
        />
      </div>
      <CohortBuilder
        filters={filters}
        total={payload?.total ?? 0}
        ticked={tickedList}
        onSaved={onCohortSaved}
      />
    </div>
  );
}

// ─── Progress tab ────────────────────────────────────────────────────────────

function ProgressTab() {
  const [payload, setPayload] = useState(null);
  const [error, setError] = useState(null);
  const [cap, setCap] = useState("");
  const [runMessage, setRunMessage] = useState(null);
  const [pending, startTransition] = useTransition();

  const refresh = useCallback(() => {
    startTransition(async () => {
      const res = await loadProgressAction();
      if (res.ok) {
        setPayload(res.payload);
        setError(null);
        setCap((c) => (c === "" ? String(res.payload?.vocabularies?.run_now?.default_fetch_cap ?? "") : c));
      } else {
        setError(res.error);
      }
    });
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const statuses = payload?.vocabularies?.statuses ?? {};
  const statusKeys = Object.keys(statuses);
  const runNow = payload?.vocabularies?.run_now;

  function launch() {
    startTransition(async () => {
      const res = await runNowAction(cap);
      if (!res.ok) {
        setRunMessage({ error: true, text: res.error });
        return;
      }
      const run = res.result?.run;
      setRunMessage({
        error: run?.status !== "launched",
        text:
          run?.status === "launched"
            ? `Launched — Render job ${run.render_job_id}.`
            : `Not launched (${run?.status}): ${run?.stop_reason || run?.error || ""}`,
      });
      refresh();
    });
  }

  const runColumns = [
    { field: "requested_at", headerName: "Requested", cell: (v) => fmtWhen(v) },
    { field: "trigger_source", headerName: "Source" },
    { field: "status", headerName: "Status" },
    { field: "fetch_cap", headerName: "Cap", align: "right" },
    { field: "discovered_new", headerName: "New", align: "right" },
    { field: "selected", headerName: "Selected", align: "right" },
    { field: "fetched", headerName: "Fetched", align: "right" },
    { field: "fetch_failed", headerName: "Failed", align: "right" },
    { field: "ready_for_extraction", headerName: "Ready", align: "right" },
    { field: "bytes_uploaded", headerName: "Uploaded", align: "right", cell: (v) => fmtBytes(v) },
    { field: "finished_at", headerName: "Finished", cell: (v) => fmtWhen(v) },
    { field: "stop_reason", headerName: "Stop / error", cell: (v, row) => v || row.error || "" },
  ];

  return (
    <div className="mt-4 flex flex-col gap-4">
      <ErrorLine text={error} />
      {canWrite(payload) && runNow && (
        <div className={`${CARD} flex flex-wrap items-end gap-3 p-4`}>
          <label className="flex flex-col gap-1">
            <span className={EYEBROW}>Fetch cap</span>
            <input
              type="number"
              min={0}
              max={runNow.max_fetch_cap}
              className={`${CONTROL} w-32`}
              value={cap}
              onChange={(e) => setCap(e.target.value)}
            />
          </label>
          <button type="button" className={BUTTON} disabled={pending || cap === ""} onClick={launch}>
            {runNow.label}
          </button>
          <p className="text-xs text-[var(--2a-text-muted)]">{runNow.description}</p>
          {runMessage && (
            <p className="w-full text-xs" style={runMessage.error ? { color: ERROR_INK } : undefined}>
              {runMessage.text}
            </p>
          )}
        </div>
      )}

      <div className={`${CARD} p-4`}>
        <div className="mb-2 flex items-center justify-between">
          <h3 className="text-sm font-semibold text-[var(--2a-navy)]">Recent runs</h3>
          <button type="button" className={GHOST} onClick={refresh} disabled={pending}>
            Refresh
          </button>
        </div>
        {payload?.lease?.held === true && (
          <p className="mb-2 text-xs text-[var(--2a-text-muted)]">
            A job holds the lease until {fmtWhen(payload.lease.expires_at)}.
          </p>
        )}
        <DataGrid
          gridId="edgar-runs"
          columnDefs={runColumns}
          rowData={payload?.runs ?? []}
          getRowId={(row) => row.id}
          enableGlobalFilter={false}
          emptyMessage={pending ? "Loading…" : "No pipeline runs yet."}
          pageSize={10}
        />
      </div>

      <div className={`${CARD} overflow-x-auto p-4`}>
        <h3 className="mb-2 text-sm font-semibold text-[var(--2a-navy)]">Filings by status, per quarter</h3>
        <table className="w-full text-xs">
          <thead className="border-b border-[var(--2a-border)]">
            <tr>
              <th className="px-2 py-2 text-left font-semibold text-[var(--2a-text-muted)]">Quarter</th>
              {statusKeys.map((k) => (
                <th key={k} className="px-2 py-2 text-right font-semibold text-[var(--2a-text-muted)]">
                  {statuses[k]}
                </th>
              ))}
              <th className="px-2 py-2 text-right font-semibold text-[var(--2a-text-muted)]">Total</th>
            </tr>
          </thead>
          <tbody>
            {(payload?.by_quarter ?? []).map((q) => (
              <tr key={q.index_quarter} className="border-t border-[var(--2a-border)]">
                <td className="px-2 py-1.5 text-[var(--2a-text-secondary)]">{q.index_quarter}</td>
                {statusKeys.map((k) => (
                  <td key={k} className="px-2 py-1.5 text-right tabular-nums text-[var(--2a-text)]">
                    {q[k] ? q[k].toLocaleString() : ""}
                  </td>
                ))}
                <td className="px-2 py-1.5 text-right tabular-nums text-[var(--2a-text)]">
                  {q.total.toLocaleString()}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

// ─── Issuers tab ─────────────────────────────────────────────────────────────

const EMPTY_NEW = {
  filer_cik: "",
  filer_name: "",
  issuer_group: "",
  filer_role: "",
  credit_entity: "",
  include_status: "",
  notes: "",
};

function IssuersTab() {
  const [payload, setPayload] = useState(null);
  const [drafts, setDrafts] = useState({});
  const [error, setError] = useState(null);
  const [notice, setNotice] = useState(null);
  const [newIssuer, setNewIssuer] = useState(EMPTY_NEW);
  const [pending, startTransition] = useTransition();

  const refresh = useCallback(() => {
    startTransition(async () => {
      const res = await loadIssuersAction();
      if (res.ok) {
        setPayload(res.payload);
        setDrafts({});
        setError(null);
      } else {
        setError(res.error);
      }
    });
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const writable = canWrite(payload);
  const editable = new Set(payload?.vocabularies?.editable ?? []);
  const includeLabels = payload?.vocabularies?.include_status ?? {};
  const roleLabels = payload?.vocabularies?.filer_roles ?? {};

  function setDraft(cik, field, value) {
    setDrafts((d) => ({ ...d, [cik]: { ...(d[cik] ?? {}), [field]: value } }));
  }

  function save(cik) {
    const changes = drafts[cik];
    if (!changes) return;
    startTransition(async () => {
      const res = await updateIssuerAction(cik, changes);
      if (!res.ok) {
        setError(res.error);
        return;
      }
      setNotice(
        `Saved ${cik}. ${res.result?.reopened_for_selection ?? 0} filings re-opened for selection.`,
      );
      refresh();
    });
  }

  function promote(row) {
    setNewIssuer({ ...EMPTY_NEW, filer_cik: row.cik, filer_name: row.company_name });
  }

  function addIssuer() {
    startTransition(async () => {
      const res = await addIssuerAction({ ...newIssuer, notes: newIssuer.notes || null });
      if (!res.ok) {
        setError(res.error);
        return;
      }
      setNotice(
        `Added ${res.result?.issuer?.filer_cik}. ${res.result?.reopened_for_selection ?? 0} filings re-opened for selection.`,
      );
      setNewIssuer(EMPTY_NEW);
      refresh();
    });
  }

  function editCell(field, render) {
    return (v, row) => {
      if (!(writable && editable.has(field))) return render ? render(v) : v ?? "";
      const value = drafts[row.filer_cik]?.[field] ?? v ?? "";
      if (field === "include_status") {
        return (
          <select className={CONTROL} value={value} onChange={(e) => setDraft(row.filer_cik, field, e.target.value)}>
            {Object.entries(includeLabels).map(([k, label]) => (
              <option key={k} value={k}>
                {label}
              </option>
            ))}
          </select>
        );
      }
      return (
        <input
          className={`${CONTROL} w-full`}
          value={value}
          onChange={(e) => setDraft(row.filer_cik, field, e.target.value)}
        />
      );
    };
  }

  const issuerColumns = [
    { field: "issuer_group", headerName: "Group" },
    { field: "filer_cik", headerName: "CIK" },
    { field: "filer_name", headerName: "Filer" },
    { field: "filer_role", headerName: "Role", cell: (v) => roleLabels[v] ?? v },
    { field: "include_status", headerName: "Include", cell: editCell("include_status", (v) => includeLabels[v] ?? v) },
    { field: "credit_entity", headerName: "Credit entity", cell: editCell("credit_entity") },
    { field: "notes", headerName: "Notes", cell: editCell("notes") },
    { field: "primary_filings", headerName: "Filings", align: "right", cell: (v) => (v ?? 0).toLocaleString() },
    ...(writable
      ? [
          {
            field: "_save",
            headerName: "",
            enableSorting: false,
            cell: (_v, row) =>
              drafts[row.filer_cik] ? (
                <button type="button" className={BUTTON} disabled={pending} onClick={() => save(row.filer_cik)}>
                  Save
                </button>
              ) : null,
          },
        ]
      : []),
  ];

  const unlistedColumns = [
    { field: "company_name", headerName: "Filer" },
    { field: "cik", headerName: "CIK" },
    { field: "filings_424b2", headerName: "424B2s", align: "right" },
    { field: "last_filed", headerName: "Last filed", cell: (v) => fmtDate(v) },
    ...(writable
      ? [
          {
            field: "_promote",
            headerName: "",
            enableSorting: false,
            cell: (_v, row) => (
              <button type="button" className={GHOST} onClick={() => promote(row)}>
                Promote
              </button>
            ),
          },
        ]
      : []),
  ];

  const newReady =
    newIssuer.filer_cik && newIssuer.filer_name && newIssuer.issuer_group &&
    newIssuer.filer_role && newIssuer.credit_entity && newIssuer.include_status;

  return (
    <div className="mt-4 flex flex-col gap-4">
      <ErrorLine text={error} />
      {notice && <p className="text-xs text-[var(--2a-text-secondary)]">{notice}</p>}
      <div className="grid gap-4 xl:grid-cols-[3fr_2fr]">
        <div className={`${CARD} p-4`}>
          <h3 className="mb-2 text-sm font-semibold text-[var(--2a-navy)]">Issuers</h3>
          <DataGrid
            gridId="edgar-issuers"
            columnDefs={issuerColumns}
            rowData={payload?.rows ?? []}
            getRowId={(row) => row.filer_cik}
            emptyMessage={pending ? "Loading…" : "No issuers."}
            pageSize={50}
          />
        </div>
        <div className={`${CARD} p-4`}>
          <h3 className="mb-1 text-sm font-semibold text-[var(--2a-navy)]">Unlisted 424B2 filers</h3>
          <p className="mb-2 text-xs text-[var(--2a-text-muted)]">
            Filers of 424B2s with no listed issuer. Shown for review only — never included automatically.
          </p>
          <DataGrid
            gridId="edgar-unlisted-filers"
            columnDefs={unlistedColumns}
            rowData={payload?.unlisted_filers ?? []}
            getRowId={(row) => row.cik}
            emptyMessage={pending ? "Loading…" : "Every 424B2 filer is listed."}
            pageSize={15}
          />
        </div>
      </div>

      {writable && (
        <div className={`${CARD} p-4`}>
          <h3 className="mb-3 text-sm font-semibold text-[var(--2a-navy)]">Add an issuer</h3>
          <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
            {[
              ["filer_cik", "CIK"],
              ["filer_name", "Filer name"],
              ["issuer_group", "Issuer group"],
              ["credit_entity", "Credit entity"],
            ].map(([key, label]) => (
              <label key={key} className="flex flex-col gap-1">
                <span className={EYEBROW}>{label}</span>
                <input
                  className={CONTROL}
                  value={newIssuer[key]}
                  onChange={(e) => setNewIssuer((n) => ({ ...n, [key]: e.target.value }))}
                />
              </label>
            ))}
            <Select
              label="Role"
              value={newIssuer.filer_role}
              onChange={(v) => setNewIssuer((n) => ({ ...n, filer_role: v }))}
              options={Object.entries(roleLabels).map(([value, label]) => ({ value, label }))}
              anyLabel="Choose…"
            />
            <Select
              label="Include"
              value={newIssuer.include_status}
              onChange={(v) => setNewIssuer((n) => ({ ...n, include_status: v }))}
              options={Object.entries(includeLabels).map(([value, label]) => ({ value, label }))}
              anyLabel="Choose…"
            />
            <label className="col-span-2 flex flex-col gap-1">
              <span className={EYEBROW}>Notes</span>
              <input
                className={CONTROL}
                value={newIssuer.notes}
                onChange={(e) => setNewIssuer((n) => ({ ...n, notes: e.target.value }))}
              />
            </label>
          </div>
          <div className="mt-3">
            <button type="button" className={BUTTON} disabled={pending || !newReady} onClick={addIssuer}>
              Add issuer
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

// ─── Shell ───────────────────────────────────────────────────────────────────

export default function EdgarPipelineMonitor({ initialFilings }) {
  const [tab, setTab] = useState("filings");
  const [focusCohort, setFocusCohort] = useState(null);
  return (
    <div className="mt-6">
      <div className="flex gap-6 border-b border-[var(--2a-border)]">
        {TABS.map((t) => (
          <button
            key={t.key}
            type="button"
            onClick={() => setTab(t.key)}
            className={`-mb-px border-b-2 pb-2 text-sm ${
              tab === t.key
                ? "border-[var(--2a-gold)] font-semibold text-[var(--2a-navy)]"
                : "border-transparent text-[var(--2a-text-muted)] hover:text-[var(--2a-navy)]"
            }`}
          >
            {t.label}
          </button>
        ))}
      </div>
      {tab === "filings" && (
        <FilingsTab
          initial={initialFilings}
          onCohortSaved={(c) => {
            setFocusCohort(c?.id ?? null);
            setTab("cohorts");
          }}
        />
      )}
      {tab === "progress" && <ProgressTab />}
      {tab === "cohorts" && <CohortsTab focusId={focusCohort} />}
      {tab === "issuers" && <IssuersTab />}
    </div>
  );
}
