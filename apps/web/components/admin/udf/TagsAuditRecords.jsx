"use client";

// UDFUI1 — Tags (a tags field's vocabulary + merge, POST /udf/tags/{id}/merge), Audit (GET
// /udf/definitions/{id}/audit: who changed what, when) and Records (GET /udf/records/{type} in a grid, Export =
// GET /udf/records/{type}/export). Every value shown is the route's own; nothing is recomputed here.
import { useEffect, useState } from "react";
import DataGrid from "@/components/ui/DataGrid";
import { BusyButton, BusyError, useBusy } from "@/components/ui/Busy";
import { auditRows, builderRequest, canWrite, label, recordColumns, recordRows } from "@/lib/udfBuilder.mjs";
import { Card, Notice, Select, btnCls, primaryCls, udfCall } from "./udfParts";

export function TagsPanel({ envelope, initialVocabulary }) {
  const tagFields = (envelope?.rows || []).filter((d) => d.data_type === "tags" && d.status !== "deleted");
  const [defId, setDefId] = useState(tagFields[0]?.id || "");
  const [vocab, setVocab] = useState(initialVocabulary || null);
  const [merge, setMerge] = useState({ from_code: "", into_code: "" });
  const [result, setResult] = useState(null);
  const [error, setError] = useState(null);
  const busy = useBusy();
  const def = tagFields.find((d) => d.id === defId);
  const write = canWrite(envelope) && (def?.owner_scope !== "platform" || envelope?.permissions?.is_super_admin === true);

  async function load(id) {
    if (!id) return;
    try { setVocab(await udfCall("tagVocabulary", { id })); setError(null); } catch (e) { setError(e.message); }
  }
  useEffect(() => { if (!initialVocabulary) load(defId); }, [defId]); // eslint-disable-line react-hooks/exhaustive-deps

  // GET /udf/tags/{id} rows: {tag_code, normalized_code, n}
  const codes = (vocab?.rows || []).map((t) => ({ value: t.tag_code, label: t.tag_code }));
  return (
    <Card title="Tags">
      {tagFields.length === 0 ? <Notice>No tags fields yet. Create a field of type “{label(envelope, "data_type", "tags")}”.</Notice> : (
        <>
          <Select id="udf-tags-field" label="Tags field" value={defId} onChange={(v) => { setDefId(v); setVocab(null); setResult(null); }}
            options={tagFields.map((d) => ({ value: d.id, label: `${d.label} (${label(envelope, "applies_to", d.applies_to)})` }))} />
          {error ? <Notice tone="error">{error}</Notice> : null}
          {vocab ? (
            codes.length ? (
              <ul aria-label="Tags in use" className="mb-3 flex flex-wrap gap-2">
                {(vocab.rows || []).map((t) => (
                  <li key={t.normalized_code || t.tag_code} className="rounded border border-border px-2 py-0.5 text-sm" data-tag={t.tag_code}>
                    {t.tag_code} · {t.n} record(s)
                  </li>
                ))}
              </ul>
            ) : <Notice>No tags have been used on this field yet.</Notice>
          ) : null}
          {write && codes.length > 1 ? (
            <form className="grid grid-cols-1 items-end gap-x-3 md:grid-cols-3" aria-label="Merge two tags"
              onSubmit={(e) => {
                e.preventDefault();
                if (!merge.from_code || !merge.into_code || merge.from_code === merge.into_code) { setError("Choose two different tags."); return; }
                busy.run(async () => {
                  const out = await udfCall("mergeTags", { id: defId }, { body: merge });
                  setResult(out.targets_repointed); setMerge({ from_code: "", into_code: "" }); await load(defId);
                });
              }}>
              <Select id="udf-merge-from" label="Merge this tag" value={merge.from_code} placeholder="Choose"
                onChange={(v) => setMerge((m) => ({ ...m, from_code: v }))} options={codes} />
              <Select id="udf-merge-into" label="Into this tag" value={merge.into_code} placeholder="Choose"
                onChange={(v) => setMerge((m) => ({ ...m, into_code: v }))} options={codes} />
              <div className="mb-3"><BusyButton type="submit" busy={busy.busy} className={primaryCls} data-action="mergeTags">Merge</BusyButton>
                <BusyError error={busy.error} /></div>
            </form>
          ) : null}
          {result != null ? <p role="status" className="text-sm text-[#2D6A4F]">Merged — {result} record(s) moved to the kept tag.</p> : null}
        </>
      )}
    </Card>
  );
}

export function AuditPanel({ envelope, initialRows, initialId }) {
  const defs = envelope?.rows || [];
  const [defId, setDefId] = useState(initialId || "");
  const [rows, setRows] = useState(initialRows || null);
  const [error, setError] = useState(null);
  useEffect(() => {
    if (!defId || initialRows) return;
    udfCall("definitionAudit", { id: defId }).then((d) => { setRows(d.rows || []); setError(null); }).catch((e) => setError(e.message));
  }, [defId]); // eslint-disable-line react-hooks/exhaustive-deps
  const shown = auditRows(rows, envelope);
  return (
    <Card title="Change history">
      <Select id="udf-audit-field" label="Field" value={defId} placeholder="Choose a field" onChange={(v) => { setDefId(v); setRows(null); }}
        options={defs.map((d) => ({ value: d.id, label: `${d.label} (${label(envelope, "status", d.status)})` }))} />
      {error ? <Notice tone="error">{error}</Notice> : null}
      {rows && shown.length === 0 ? <Notice>No recorded changes.</Notice> : null}
      {shown.length ? (
        <table className="w-full text-sm" aria-label="Change history">
          <thead><tr className="text-left text-text-muted"><th scope="col">When</th><th scope="col">Who</th><th scope="col">Change</th><th scope="col">Fields changed</th></tr></thead>
          <tbody>
            {shown.map((r) => (
              <tr key={r.id} className="border-t border-border" data-audit-row={r.id}>
                <td>{r.when ? new Date(r.when).toLocaleString() : ""}</td><td>{r.who}</td><td>{r.kind}</td><td>{r.fields || "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : null}
    </Card>
  );
}

const PAGE = 50;

export function RecordsView({ envelope, initialRecords, initialTarget }) {
  const targets = Array.isArray(envelope?.vocabularies?.applies_to) ? envelope.vocabularies.applies_to : [];
  const [target, setTarget] = useState(initialTarget || (targets.includes("entity") ? "entity" : targets[0] || ""));
  const [page, setPage] = useState(0);
  const [records, setRecords] = useState(initialRecords || null);
  const [error, setError] = useState(null);
  useEffect(() => {
    if (initialRecords && page === 0 && target === (initialTarget || target)) return;
    udfCall("records", { type: target }, { query: { limit: PAGE, offset: page * PAGE } })
      .then((d) => { setRecords(d); setError(null); }).catch((e) => { setRecords(null); setError(e.message); });
  }, [target, page]); // eslint-disable-line react-hooks/exhaustive-deps
  const exportHref = target ? builderRequest("exportRecords", { type: target }).url : null;
  const total = records?.total_count ?? 0;
  return (
    <Card title="Records" actions={exportHref ? (
      <a href={exportHref} className={btnCls} data-action="exportRecords" download>Export CSV</a>
    ) : null}>
      <div className="max-w-xs">
        <Select id="udf-rec-target" label="Record type" value={target} onChange={(v) => { setTarget(v); setPage(0); }}
          options={targets.map((k) => ({ value: k, label: label(envelope, "applies_to", k) }))} />
      </div>
      {error ? <Notice tone="error">{error}</Notice> : null}
      {records ? (
        <>
          <DataGrid gridId={`udf-records-${target}`} columnDefs={recordColumns(records)} rowData={recordRows(records)}
            getRowId={(r) => r.id} serverSide={{ totalRows: total, pageIndex: page, pageSize: PAGE, sorting: [],
              onSortingChange: () => {}, onPageChange: setPage, loading: false }} />
          <p className="mt-1 text-xs text-text-muted">{total} record(s)</p>
        </>
      ) : null}
    </Card>
  );
}
