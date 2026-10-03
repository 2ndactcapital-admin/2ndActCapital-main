"use client";

/**
 * EDGAR cohorts (edgarcohorts) — the cohort builder that sits under the
 * Filings tab, and the Cohorts tab.
 *
 * A cohort is a named, FROZEN list of filings. It is built from the Filings
 * tab's own filters (everything matching) or from ticked rows, then sampled
 * (all / newest N / oldest N / random N with a seed / stratified N per
 * stratum). The builder always PREVIEWS first — the count and members per
 * stratum — and only then offers Save.
 *
 * Every label and vocabulary (sampling methods, strata, eras, run kinds,
 * statuses) comes from the API envelope (Rule 1). Write controls render only
 * inside `permissions.can_write === true`, with no fallback: a lost envelope
 * renders read-only. A saved cohort has no edit control at all — only
 * "Copy and edit", which creates a new one.
 */

import { useCallback, useEffect, useMemo, useState, useTransition } from "react";

import DataGrid from "@/components/ui/DataGrid";
import {
  copyCohortAction,
  createCohortAction,
  createTemplateStudyAction,
  loadCohortAction,
  loadCohortMembersAction,
  loadCohortsAction,
  loadInventoryRunAction,
  previewCohortAction,
  previewTemplateStudyAction,
  runCohortAction,
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

function canWrite(payload) {
  return payload?.permissions?.can_write === true;
}

function fmtWhen(v) {
  if (!v) return "";
  const d = new Date(v);
  return Number.isNaN(d.getTime()) ? String(v) : d.toLocaleString();
}

function ErrorLine({ text }) {
  if (!text) return null;
  return (
    <p className="mt-2 text-xs" style={{ color: ERROR_INK }}>
      {text}
    </p>
  );
}

function StrataTable({ strata }) {
  if (!strata?.length) return null;
  return (
    <table className="mt-2 w-full max-w-xl text-xs">
      <thead className="border-b border-[var(--2a-border)]">
        <tr>
          <th className="px-2 py-1.5 text-left font-semibold text-[var(--2a-text-muted)]">Stratum</th>
          <th className="px-2 py-1.5 text-right font-semibold text-[var(--2a-text-muted)]">Members</th>
        </tr>
      </thead>
      <tbody>
        {strata.map((s) => (
          <tr key={s.stratum} className="border-t border-[var(--2a-border)]">
            <td className="px-2 py-1 text-[var(--2a-text-secondary)]">{s.stratum}</td>
            <td className="px-2 py-1 text-right tabular-nums">{s.members.toLocaleString()}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

// ─── Builder (rendered under the Filings grid) ──────────────────────────────

/**
 * props:
 *   filters   — the Filings tab's current filters (only non-empty values are sent)
 *   total     — rows matching those filters
 *   ticked    — array of ticked accession numbers (survives paging)
 *   onSaved   — called with the saved cohort
 */
export function CohortBuilder({ filters, total, ticked, onSaved }) {
  const [envelope, setEnvelope] = useState(null);
  const [source, setSource] = useState("filter");
  const [method, setMethod] = useState("all");
  const [n, setN] = useState("");
  const [seed, setSeed] = useState("1");
  const [perStratum, setPerStratum] = useState("");
  const [stratifyBy, setStratifyBy] = useState("");
  const [name, setName] = useState("");
  const [purpose, setPurpose] = useState("");
  const [preview, setPreview] = useState(null);
  const [error, setError] = useState(null);
  const [notice, setNotice] = useState(null);
  const [pending, startTransition] = useTransition();

  useEffect(() => {
    startTransition(async () => {
      const res = await loadCohortsAction();
      if (res.ok) setEnvelope(res.payload);
      else setError(res.error);
    });
  }, []);

  // Any change to what would be frozen invalidates the preview.
  useEffect(() => {
    setPreview(null);
  }, [filters, ticked, source, method, n, seed, perStratum, stratifyBy]);

  const vocab = envelope?.vocabularies ?? {};
  const methods = vocab.sampling_methods ?? {};
  const strataVocab = vocab.stratify_by ?? {};

  function definition() {
    const sampling = { method };
    if (["newest", "oldest", "random"].includes(method)) sampling.n = Number(n);
    if (["random", "stratified"].includes(method)) sampling.seed = Number(seed);
    if (method === "stratified") {
      sampling.per_stratum = Number(perStratum);
      if (stratifyBy) sampling.stratify_by = stratifyBy;
    }
    if (source === "hand") return { source: "hand", accessions: ticked, sampling };
    const f = Object.fromEntries(Object.entries(filters).filter(([, v]) => v !== ""));
    return { source: "filter", filters: f, sampling };
  }

  function runPreview() {
    startTransition(async () => {
      const res = await previewCohortAction(definition());
      if (res.ok) {
        setPreview(res.payload);
        setError(null);
      } else {
        setPreview(null);
        setError(res.error);
      }
    });
  }

  function save() {
    startTransition(async () => {
      const res = await createCohortAction({ name, purpose, definition: definition() });
      if (!res.ok) {
        setError(res.error);
        return;
      }
      const c = res.payload?.cohort;
      setNotice(`Saved cohort "${c?.name}" — ${c?.member_count?.toLocaleString()} filings, frozen.`);
      setPreview(null);
      setName("");
      setPurpose("");
      onSaved?.(c);
    });
  }

  if (!canWrite(envelope)) {
    return error ? <ErrorLine text={error} /> : null;
  }

  return (
    <div className={`${CARD} mt-4 p-4`}>
      <h3 className="text-sm font-semibold text-[var(--2a-navy)]">Build a cohort</h3>
      <p className="mt-1 text-xs text-[var(--2a-text-muted)]">
        A cohort is a named, frozen list of filings — for fetching now and extraction later. Once
        saved it never changes; copy it to make another.
      </p>
      <div className="mt-3 grid grid-cols-2 gap-3 md:grid-cols-4 lg:grid-cols-6">
        <label className="flex flex-col gap-1">
          <span className={EYEBROW}>From</span>
          <select className={CONTROL} value={source} onChange={(e) => setSource(e.target.value)}>
            <option value="filter">Everything matching the filters ({(total ?? 0).toLocaleString()})</option>
            <option value="hand" disabled={!ticked.length}>
              Ticked rows ({ticked.length.toLocaleString()})
            </option>
          </select>
        </label>
        <label className="flex flex-col gap-1">
          <span className={EYEBROW}>Sampling</span>
          <select className={CONTROL} value={method} onChange={(e) => setMethod(e.target.value)}>
            {Object.entries(methods).map(([k, label]) => (
              <option key={k} value={k}>
                {label}
              </option>
            ))}
          </select>
        </label>
        {["newest", "oldest", "random"].includes(method) && (
          <label className="flex flex-col gap-1">
            <span className={EYEBROW}>N</span>
            <input type="number" min={1} max={vocab.max_size} className={CONTROL} value={n} onChange={(e) => setN(e.target.value)} />
          </label>
        )}
        {method === "stratified" && (
          <>
            <label className="flex flex-col gap-1">
              <span className={EYEBROW}>Strata</span>
              <select className={CONTROL} value={stratifyBy} onChange={(e) => setStratifyBy(e.target.value)}>
                <option value="">Choose…</option>
                {Object.entries(strataVocab).map(([k, label]) => (
                  <option key={k} value={k}>
                    {label}
                  </option>
                ))}
              </select>
            </label>
            <label className="flex flex-col gap-1">
              <span className={EYEBROW}>N per stratum</span>
              <input type="number" min={1} className={CONTROL} value={perStratum} onChange={(e) => setPerStratum(e.target.value)} />
            </label>
          </>
        )}
        {["random", "stratified"].includes(method) && (
          <label className="flex flex-col gap-1">
            <span className={EYEBROW}>Seed</span>
            <input type="number" min={0} className={CONTROL} value={seed} onChange={(e) => setSeed(e.target.value)} />
          </label>
        )}
      </div>
      {method === "stratified" && vocab.eras && (
        <p className="mt-2 text-xs text-[var(--2a-text-muted)]">Eras: {vocab.eras.join(", ")}.</p>
      )}
      <div className="mt-3 flex flex-wrap items-center gap-3">
        <button type="button" className={GHOST} disabled={pending} onClick={runPreview}>
          Preview
        </button>
        {preview && !preview.too_large && (
          <>
            <input className={`${CONTROL} w-56`} placeholder="Cohort name" value={name} onChange={(e) => setName(e.target.value)} />
            <input className={`${CONTROL} w-72`} placeholder="Purpose (optional)" value={purpose} onChange={(e) => setPurpose(e.target.value)} />
            <button type="button" className={BUTTON} disabled={pending || !name.trim()} onClick={save}>
              Save cohort ({preview.count.toLocaleString()})
            </button>
          </>
        )}
      </div>
      <ErrorLine text={error} />
      {notice && <p className="mt-2 text-xs text-[var(--2a-text-secondary)]">{notice}</p>}
      {preview && (
        <div className="mt-3">
          {preview.too_large ? (
            <p className="text-xs" style={{ color: ERROR_INK }}>
              More than {preview.max_size.toLocaleString()} filings — narrow the filters or sample.
            </p>
          ) : (
            <p className="text-xs text-[var(--2a-text-secondary)]">
              {preview.count.toLocaleString()} filings would be frozen (of {preview.matched.toLocaleString()} matching).
            </p>
          )}
          <StrataTable strata={preview.strata} />
        </div>
      )}
    </div>
  );
}

// ─── Cohorts tab ────────────────────────────────────────────────────────────

function TemplateStudyPreset({ vocab, onSaved }) {
  const [preview, setPreview] = useState(null);
  const [error, setError] = useState(null);
  const [pending, startTransition] = useTransition();
  const preset = vocab?.template_study;
  if (!preset) return null;

  function runPreview() {
    startTransition(async () => {
      const res = await previewTemplateStudyAction({});
      if (res.ok) {
        setPreview(res.payload);
        setError(null);
      } else setError(res.error);
    });
  }

  function save() {
    startTransition(async () => {
      const res = await createTemplateStudyAction({});
      if (!res.ok) {
        setError(res.error);
        return;
      }
      setPreview(null);
      onSaved?.(res.payload?.cohort);
    });
  }

  return (
    <div className={`${CARD} p-4`}>
      <h3 className="text-sm font-semibold text-[var(--2a-navy)]">{preset.label}</h3>
      <p className="mt-1 text-xs text-[var(--2a-text-muted)]">{preset.description}</p>
      <div className="mt-3 flex gap-3">
        <button type="button" className={GHOST} disabled={pending} onClick={runPreview}>
          Preview
        </button>
        {preview && !preview.too_large && (
          <button type="button" className={BUTTON} disabled={pending} onClick={save}>
            Save template study ({preview.count.toLocaleString()})
          </button>
        )}
      </div>
      <ErrorLine text={error} />
      {preview && <StrataTable strata={preview.strata} />}
    </div>
  );
}

function CohortMembers({ cohortId, statuses, kinds }) {
  const [payload, setPayload] = useState(null);
  const [pageIndex, setPageIndex] = useState(0);
  const [status, setStatus] = useState("");
  const [error, setError] = useState(null);
  const [pending, startTransition] = useTransition();

  const load = useCallback(
    (nextPage, nextStatus) => {
      startTransition(async () => {
        const params = { page: nextPage + 1, page_size: 50 };
        if (nextStatus) params.status = nextStatus;
        const res = await loadCohortMembersAction(cohortId, params);
        if (res.ok) {
          setPayload(res.payload);
          setError(null);
        } else setError(res.error);
      });
    },
    [cohortId],
  );

  useEffect(() => {
    setPageIndex(0);
    setStatus("");
    load(0, "");
  }, [load]);

  const columns = [
    { field: "position", headerName: "#", align: "right", enableSorting: false },
    { field: "stratum", headerName: "Stratum", enableSorting: false },
    {
      field: "accession_number",
      headerName: "Accession",
      enableSorting: false,
      cell: (v, row) => (
        <a href={row.sec_filing_url} target="_blank" rel="noopener noreferrer" className="text-[var(--2a-navy)] underline decoration-[var(--2a-gold)] underline-offset-2">
          {v}
        </a>
      ),
    },
    { field: "filing_date", headerName: "Filed", enableSorting: false, cell: (v) => (v ? String(v).slice(0, 10) : "") },
    { field: "form_type", headerName: "Form", enableSorting: false },
    { field: "issuer_group", headerName: "Issuer group", enableSorting: false },
    { field: "pipeline_status", headerName: "Status", enableSorting: false, cell: (v) => statuses[v] ?? v },
    { field: "document_kind", headerName: "Document", enableSorting: false, cell: (v) => (v ? kinds[v] ?? v : "") },
    {
      field: "selected_by_cohort_id",
      headerName: "Selected by",
      enableSorting: false,
      cell: (v, row) => (v ? "Cohort" : row.selection_policy_version ? `Policy v${row.selection_policy_version}` : ""),
    },
  ];

  return (
    <div className={`${CARD} p-4`}>
      <div className="mb-2 flex items-end justify-between gap-3">
        <h3 className="text-sm font-semibold text-[var(--2a-navy)]">Members</h3>
        <label className="flex flex-col gap-1">
          <span className={EYEBROW}>Status</span>
          <select
            className={CONTROL}
            value={status}
            onChange={(e) => {
              setStatus(e.target.value);
              setPageIndex(0);
              load(0, e.target.value);
            }}
          >
            <option value="">Any</option>
            {Object.entries(statuses).map(([k, label]) => (
              <option key={k} value={k}>
                {label}
              </option>
            ))}
          </select>
        </label>
      </div>
      <ErrorLine text={error} />
      <DataGrid
        gridId="edgar-cohort-members"
        columnDefs={columns}
        rowData={payload?.rows ?? []}
        getRowId={(row) => row.accession_number}
        emptyMessage={pending ? "Loading…" : "No members."}
        serverSide={{
          totalRows: payload?.total ?? 0,
          pageIndex,
          pageSize: payload?.page_size ?? 50,
          sorting: [],
          loading: pending,
          onSortingChange: () => {},
          onPageChange: (next) => {
            setPageIndex(next);
            load(next, status);
          },
        }}
      />
    </div>
  );
}

function InventoryResults({ runId }) {
  const [payload, setPayload] = useState(null);
  const [error, setError] = useState(null);
  const [pending, startTransition] = useTransition();

  useEffect(() => {
    startTransition(async () => {
      const res = await loadInventoryRunAction(runId);
      if (res.ok) {
        setPayload(res.payload);
        setError(null);
      } else setError(res.error);
    });
  }, [runId]);

  const columns = [
    { field: "display_label", headerName: "Concept" },
    { field: "labels", headerName: "Labels as written", cell: (v) => (v ?? []).join("; "), minWidth: 220 },
    { field: "issuers", headerName: "Issuers", cell: (v) => (v ?? []).join(", ") },
    { field: "frequency", headerName: "Items", align: "right" },
    { field: "document_count", headerName: "Docs", align: "right" },
    { field: "mapped_field_key", headerName: "Existing field" },
    { field: "proposed_field_key", headerName: "Proposed new field" },
    { field: "example_values", headerName: "Examples", cell: (v) => (v ?? []).slice(0, 3).join("; ") },
    {
      field: "misleading_flags",
      headerName: "Misleading label",
      cell: (v) => (v ?? []).map((f) => `${f.label}: ${f.note ?? ""}`).join("; "),
    },
  ];
  const dictionary = payload?.label_dictionary ?? {};

  return (
    <div className="flex flex-col gap-3">
      <ErrorLine text={error} />
      {payload?.run && (
        <p className="text-xs text-[var(--2a-text-secondary)]">
          {payload.run.status} — {payload.run.documents_done} of {payload.run.documents_planned} documents,{" "}
          {payload.run.items_accepted} items, {payload.run.items_rejected} rejected (quote not found), model{" "}
          {payload.run.deployment_name}, grouping {payload.run.grouping_method ?? "—"}.
        </p>
      )}
      <DataGrid
        gridId="edgar-inventory-concepts"
        columnDefs={columns}
        rowData={payload?.rows ?? []}
        getRowId={(row) => row.id}
        emptyMessage={pending ? "Loading…" : "No concepts."}
        pageSize={25}
      />
      {Object.keys(dictionary).length > 0 && (
        <details className="text-xs">
          <summary className="cursor-pointer text-[var(--2a-navy)]">Per-issuer label dictionary</summary>
          {Object.entries(dictionary).map(([issuer, labels]) => (
            <div key={issuer} className="mt-2">
              <div className="font-semibold text-[var(--2a-text-secondary)]">{issuer}</div>
              <ul className="ml-4 list-disc">
                {Object.entries(labels).map(([label, concept]) => (
                  <li key={label}>
                    {label} → <code>{concept}</code>
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </details>
      )}
    </div>
  );
}

function CohortDetail({ cohortId, onCopied }) {
  const [payload, setPayload] = useState(null);
  const [error, setError] = useState(null);
  const [notice, setNotice] = useState(null);
  const [cap, setCap] = useState("");
  const [runKind, setRunKind] = useState("fetch");
  const [copy, setCopy] = useState({ name: "", add: "", remove: "" });
  const [inventoryRun, setInventoryRun] = useState(null);
  const [pending, startTransition] = useTransition();

  const refresh = useCallback(() => {
    startTransition(async () => {
      const res = await loadCohortAction(cohortId);
      if (res.ok) {
        setPayload(res.payload);
        setError(null);
        setInventoryRun((r) => r ?? res.payload?.inventory_runs?.[0]?.id ?? null);
      } else setError(res.error);
    });
  }, [cohortId]);

  useEffect(() => {
    setInventoryRun(null);
    refresh();
  }, [refresh]);

  const vocab = payload?.vocabularies ?? {};
  const statuses = vocab.statuses ?? {};
  const kinds = vocab.document_kinds ?? {};
  const runKinds = vocab.run_kinds ?? {};
  const available = new Set(vocab.run_kinds_available ?? []);
  const cohort = payload?.cohort;
  const counts = payload?.status_counts ?? {};
  const writable = canWrite(payload);

  function launch() {
    startTransition(async () => {
      const body = { run_kind: runKind };
      if (cap !== "") body.fetch_cap = Number(cap);
      const res = await runCohortAction(cohortId, body);
      if (!res.ok) {
        setNotice({ error: true, text: res.error });
        return;
      }
      const run = res.payload?.run;
      setNotice({
        error: run?.status !== "launched",
        text:
          run?.status === "launched"
            ? `Launched — Render job ${run.render_job_id}.`
            : `Not launched (${run?.status}): ${run?.stop_reason || run?.error || ""}`,
      });
      refresh();
    });
  }

  function makeCopy() {
    const split = (s) => s.split(/[\s,]+/).map((x) => x.trim()).filter(Boolean);
    startTransition(async () => {
      const res = await copyCohortAction(cohortId, {
        name: copy.name,
        add: split(copy.add),
        remove: split(copy.remove),
      });
      if (!res.ok) {
        setNotice({ error: true, text: res.error });
        return;
      }
      setCopy({ name: "", add: "", remove: "" });
      onCopied?.(res.payload?.cohort);
    });
  }

  const runColumns = [
    { field: "requested_at", headerName: "Requested", cell: (v) => fmtWhen(v) },
    { field: "run_kind", headerName: "Kind", cell: (v) => runKinds[v] ?? v },
    { field: "status", headerName: "Status" },
    { field: "fetch_cap", headerName: "Cap", align: "right" },
    { field: "fetch_attempted", headerName: "Attempted", align: "right" },
    { field: "fetched", headerName: "Fetched", align: "right" },
    { field: "fetch_failed", headerName: "Failed", align: "right" },
    { field: "finished_at", headerName: "Finished", cell: (v) => fmtWhen(v) },
    { field: "stop_reason", headerName: "Stop / error", cell: (v, row) => v || row.error || "" },
  ];

  if (!cohort) return <ErrorLine text={error} />;

  return (
    <div className="flex flex-col gap-4">
      <ErrorLine text={error} />
      <div className={`${CARD} p-4`}>
        <h3 className="text-base font-semibold text-[var(--2a-navy)]">{cohort.name}</h3>
        {cohort.purpose && <p className="mt-1 text-xs text-[var(--2a-text-secondary)]">{cohort.purpose}</p>}
        <p className="mt-1 text-xs text-[var(--2a-text-muted)]">
          {cohort.member_count.toLocaleString()} filings · frozen {fmtWhen(cohort.sealed_at)}
          {cohort.copied_from ? ` · copied from ${cohort.copied_from}` : ""}
        </p>
        <div className="mt-3 grid gap-4 lg:grid-cols-2">
          <div>
            <span className={EYEBROW}>Definition</span>
            <pre className="mt-1 max-h-56 overflow-auto rounded bg-[var(--2a-bg)] p-2 text-[11px] text-[var(--2a-text-secondary)]">
              {JSON.stringify(cohort.definition, null, 2)}
            </pre>
          </div>
          <div>
            <span className={EYEBROW}>Members by status</span>
            <table className="mt-1 w-full text-xs">
              <tbody>
                {Object.entries(counts).map(([k, v]) => (
                  <tr key={k} className="border-t border-[var(--2a-border)]">
                    <td className="px-2 py-1 text-[var(--2a-text-secondary)]">{statuses[k] ?? k}</td>
                    <td className="px-2 py-1 text-right tabular-nums">{v.toLocaleString()}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <StrataTable strata={payload?.strata} />
          </div>
        </div>
      </div>

      {writable && (
        <div className={`${CARD} flex flex-wrap items-end gap-3 p-4`}>
          <label className="flex flex-col gap-1">
            <span className={EYEBROW}>Run</span>
            <select className={CONTROL} value={runKind} onChange={(e) => setRunKind(e.target.value)}>
              {Object.entries(runKinds).map(([k, label]) => (
                <option key={k} value={k} disabled={!available.has(k)}>
                  {label}
                </option>
              ))}
            </select>
          </label>
          <label className="flex flex-col gap-1">
            <span className={EYEBROW}>Cap (optional)</span>
            <input type="number" min={1} className={`${CONTROL} w-32`} value={cap} onChange={(e) => setCap(e.target.value)} />
          </label>
          <button type="button" className={BUTTON} disabled={pending || !available.has(runKind)} onClick={launch}>
            {runKinds[runKind] ?? runKind}
          </button>
          <p className="text-xs text-[var(--2a-text-muted)]">
            Exactly this cohort's members, in cohort order — including filings the default policy did not
            select. Same lease, rate limit and runtime cap as the nightly run.
          </p>
          {notice && (
            <p className="w-full text-xs" style={notice.error ? { color: ERROR_INK } : undefined}>
              {notice.text}
            </p>
          )}
        </div>
      )}

      <div className={`${CARD} p-4`}>
        <h3 className="mb-2 text-sm font-semibold text-[var(--2a-navy)]">Runs</h3>
        <DataGrid gridId="edgar-cohort-runs" columnDefs={runColumns} rowData={payload?.runs ?? []} getRowId={(r) => r.id} enableGlobalFilter={false} emptyMessage="No runs yet." pageSize={10} />
      </div>

      <CohortMembers cohortId={cohortId} statuses={statuses} kinds={kinds} />

      {writable && (
        <div className={`${CARD} p-4`}>
          <h3 className="text-sm font-semibold text-[var(--2a-navy)]">Copy and edit</h3>
          <p className="mt-1 text-xs text-[var(--2a-text-muted)]">
            Creates a new cohort from this one&apos;s members; this cohort is not changed.
          </p>
          <div className="mt-3 grid gap-3 md:grid-cols-3">
            <input className={CONTROL} placeholder="New cohort name" value={copy.name} onChange={(e) => setCopy((c) => ({ ...c, name: e.target.value }))} />
            <textarea className={CONTROL} rows={2} placeholder="Accessions to add" value={copy.add} onChange={(e) => setCopy((c) => ({ ...c, add: e.target.value }))} />
            <textarea className={CONTROL} rows={2} placeholder="Accessions to remove" value={copy.remove} onChange={(e) => setCopy((c) => ({ ...c, remove: e.target.value }))} />
          </div>
          <button type="button" className={`${BUTTON} mt-3`} disabled={pending || !copy.name.trim()} onClick={makeCopy}>
            Create copy
          </button>
        </div>
      )}

      {(payload?.inventory_runs ?? []).length > 0 && (
        <div className={`${CARD} p-4`}>
          <div className="mb-2 flex items-end justify-between gap-3">
            <h3 className="text-sm font-semibold text-[var(--2a-navy)]">Template study — inventory</h3>
            <select className={CONTROL} value={inventoryRun ?? ""} onChange={(e) => setInventoryRun(e.target.value)}>
              {payload.inventory_runs.map((r) => (
                <option key={r.id} value={r.id}>
                  {fmtWhen(r.started_at)} — {r.status}
                </option>
              ))}
            </select>
          </div>
          {inventoryRun && <InventoryResults runId={inventoryRun} />}
        </div>
      )}
    </div>
  );
}

export function CohortsTab({ focusId }) {
  const [payload, setPayload] = useState(null);
  const [selected, setSelected] = useState(focusId ?? null);
  const [error, setError] = useState(null);
  const [pending, startTransition] = useTransition();

  const refresh = useCallback(
    (select) => {
      startTransition(async () => {
        const res = await loadCohortsAction();
        if (res.ok) {
          setPayload(res.payload);
          setError(null);
          if (select) setSelected(select);
        } else setError(res.error);
      });
    },
    [],
  );

  useEffect(() => {
    refresh();
  }, [refresh]);

  const columns = useMemo(
    () => [
      {
        field: "name",
        headerName: "Cohort",
        cell: (v, row) => (
          <button type="button" className="text-left text-[var(--2a-navy)] underline decoration-[var(--2a-gold)] underline-offset-2" onClick={() => setSelected(row.id)}>
            {v}
          </button>
        ),
      },
      { field: "kind", headerName: "Kind" },
      { field: "member_count", headerName: "Filings", align: "right", cell: (v) => (v ?? 0).toLocaleString() },
      { field: "runs", headerName: "Runs", align: "right" },
      { field: "purpose", headerName: "Purpose" },
      { field: "created_at", headerName: "Created", cell: (v) => fmtWhen(v) },
    ],
    [],
  );

  return (
    <div className="mt-4 flex flex-col gap-4">
      <ErrorLine text={error} />
      <div className="grid gap-4 xl:grid-cols-[3fr_2fr]">
        <div className={`${CARD} p-4`}>
          <h3 className="mb-2 text-sm font-semibold text-[var(--2a-navy)]">Cohorts</h3>
          <DataGrid gridId="edgar-cohorts" columnDefs={columns} rowData={payload?.rows ?? []} getRowId={(r) => r.id} emptyMessage={pending ? "Loading…" : "No cohorts yet — build one on the Filings tab."} pageSize={15} />
        </div>
        {canWrite(payload) && <TemplateStudyPreset vocab={payload?.vocabularies} onSaved={(c) => refresh(c?.id)} />}
      </div>
      {selected && <CohortDetail key={selected} cohortId={selected} onCopied={(c) => refresh(c?.id)} />}
    </div>
  );
}
