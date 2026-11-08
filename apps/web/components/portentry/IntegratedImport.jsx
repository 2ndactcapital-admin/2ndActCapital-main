"use client";

// PORTENTRY1 Task 6 — upload → preview (each line's account by mask, its resolution, totals per account) →
// approve / reject by a SECOND Super Admin. Approve / Reject render only from the server's own `actions` list
// (empty for the uploader). Column names and statuses come from the envelope.
import { useState } from "react";
import { BusyButton, BusyError, useBusy } from "@/components/ui/Busy";
import { importActions, money } from "@/lib/portentry.mjs";

const card = "rounded-[6px] border border-[#ece8dd] bg-white p-6 text-sm";
const button = "rounded-[6px] bg-[#1B2B4B] px-4 py-2 text-sm text-white disabled:opacity-50";
const quiet = "text-[#64748B]";

async function call(url, opts = {}) {
  const res = await fetch(url, { cache: "no-store", ...opts });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(typeof data.error === "string" ? data.error : `Request failed (${res.status})`);
  return data;
}

function Preview({ imp }) {
  const rows = Array.isArray(imp?.preview?.rows) ? imp.preview.rows : [];
  const rejects = Array.isArray(imp?.preview?.rejects) ? imp.preview.rejects : [];
  const accounts = Array.isArray(imp?.totals?.accounts) ? imp.totals.accounts : [];
  return (
    <div className="mt-3 space-y-3">
      <p>{imp.totals?.rows} lines · {money(imp.totals?.value)} · {imp.totals?.resolved_in_master} in the security master ·{" "}
        {imp.totals?.manual_assets} org manual assets · {imp.totals?.rejected} rejected</p>
      <table className="w-full text-left"><thead><tr className={quiet}><th>Account</th><th>Lines</th><th>Value</th></tr></thead>
        <tbody>{accounts.map((a) => <tr key={a.account_mask}><td>{a.account_mask}</td><td>{a.rows}</td><td>{money(a.value)}</td></tr>)}</tbody></table>
      <details><summary className={`${quiet} cursor-pointer`}>Every line</summary>
        <table className="mt-2 w-full text-left"><thead><tr className={quiet}><th>Line</th><th>Account</th><th>Holding</th><th>Value</th><th>Resolution</th></tr></thead>
          <tbody>{rows.map((r) => <tr key={r.line}><td>{r.line}</td><td>{r.account_mask}</td><td>{r.name}{r.ticker ? ` (${r.ticker})` : ""}</td><td>{money(r.market_value)}</td><td>{r.resolution}</td></tr>)}</tbody></table>
      </details>
      {rejects.length > 0 && <ul className="text-[#9B2335]">{rejects.map((r) => <li key={r.line}>Line {r.line}: {r.reason}</li>)}</ul>}
    </div>
  );
}

export default function IntegratedImport({ initialEnvelope }) {
  const [env, setEnv] = useState(initialEnvelope);
  const [file, setFile] = useState(null);
  const [asOf, setAsOf] = useState("");
  const [open, setOpen] = useState(null);
  const [note, setNote] = useState("");
  const [msg, setMsg] = useState(null);
  // CATCHALL1 busy — Preview (upload) and Approve / Reject each run under the shared busy state
  const uploading = useBusy();
  const approving = useBusy();
  const rejecting = useBusy();
  const deciding = approving.busy || rejecting.busy;
  const statuses = Array.isArray(env?.vocabularies?.statuses) ? env.vocabularies.statuses : [];
  const rows = Array.isArray(env?.rows) ? env.rows : [];

  async function reload() {
    setEnv(await call("/api/portentry/admin/integrated-imports"));
  }
  function upload() {
    // CATCHALL1 busy — call() throws on !res.ok; the error shows beside Preview
    return uploading.run(async () => {
      setMsg(null);
      if (!file) throw new Error("Choose the CSV file.");
      const fd = new FormData();
      fd.append("file", file);
      if (asOf) fd.append("as_of", asOf);
      const imp = await call("/api/portentry/admin/integrated-imports", { method: "POST", body: fd });
      await reload();
      setOpen(await call(`/api/portentry/admin/integrated-imports/${imp.id}`));
    });
  }
  async function show(id) {
    try { setOpen(await call(`/api/portentry/admin/integrated-imports/${id}`)); } catch (e) { setMsg(e.message); }
  }
  function decide(outcome) {
    // CATCHALL1 busy
    const hook = outcome === "approve" ? approving : rejecting;
    if (deciding) return undefined;
    return hook.run(async () => {
      setMsg(null);
      await call(`/api/portentry/admin/integrated-imports/${open.id}/${outcome}`, { method: "POST",
        headers: { "Content-Type": "application/json" }, body: JSON.stringify({ note: note || undefined }) });
      await reload();
      await show(open.id);
    });
  }

  return (
    <div className="space-y-6">
      <section className={card}>
        <h2 className="font-[Spectral,Georgia,serif] text-xl text-[#1B2B4B]">Upload a position file</h2>
        <p className={`mt-1 ${quiet}`}>Columns: {(env?.vocabularies?.columns || []).join(", ")}. Accounts by mask only.</p>
        <div className="mt-3 flex flex-wrap items-center gap-3">
          <input type="file" accept=".csv" onChange={(e) => setFile(e.target.files?.[0] || null)} />
          <input type="date" className="rounded border border-[#E2E8F0] px-3 py-2" value={asOf} onChange={(e) => setAsOf(e.target.value)} aria-label="As of (when the file has no date)" />
          {/* CATCHALL1 busy */}
          <BusyButton className={button} busy={uploading.busy} busyLabel="Uploading…" onClick={upload}>Preview</BusyButton>
          <BusyError error={uploading.error} />
        </div>
      </section>
      <section className={card}>
        <h2 className="font-[Spectral,Georgia,serif] text-xl text-[#1B2B4B]">Imports</h2>
        {rows.length === 0 ? <p className={`mt-2 ${quiet}`}>None yet.</p> : (
          <ul className="mt-2 divide-y divide-[#E2E8F0]">{rows.map((r) => (
            <li key={r.id} className="flex justify-between py-2">
              <span>{r.file_name || "file"} · as of {r.as_of} · {statuses.find((s) => s.value === r.status)?.label || r.status}</span>
              <button type="button" className="text-[#1B2B4B] hover:underline" onClick={() => show(r.id)}>Open</button>
            </li>))}</ul>
        )}
      </section>
      {open && (
        <section className={card}>
          <h2 className="font-[Spectral,Georgia,serif] text-xl text-[#1B2B4B]">{open.file_name || "Import"} · {statuses.find((s) => s.value === open.status)?.label || open.status}</h2>
          <Preview imp={open} />
          {importActions(open).length > 0 && (
            <div className="mt-4 flex flex-wrap items-center gap-3">
              <input className="rounded border border-[#E2E8F0] px-3 py-2" placeholder="Note (required to reject)" value={note} onChange={(e) => setNote(e.target.value)} />
              {/* CATCHALL1 busy */}
              {importActions(open).includes("approve") && <BusyButton className={button} busy={approving.busy} disabled={deciding} busyLabel="Approving…" onClick={() => decide("approve")}>Approve</BusyButton>}
              {importActions(open).includes("reject") && <BusyButton className="rounded-[6px] border border-[#1B2B4B] px-4 py-2 text-[#1B2B4B] disabled:opacity-50" busy={rejecting.busy} disabled={deciding} busyLabel="Rejecting…" onClick={() => decide("reject")}>Reject</BusyButton>}
              <BusyError error={approving.error || rejecting.error} />
            </div>
          )}
          {open.status === "draft" && importActions(open).length === 0 && <p className={`mt-3 ${quiet}`}>{open.maker_note || "A different Super Admin must approve this import."}</p>}
        </section>
      )}
      {msg && <p className="text-sm text-[#9B2335]">{msg}</p>}
    </div>
  );
}
