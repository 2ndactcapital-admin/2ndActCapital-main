"use client";

import { useState } from "react";
import { can, labelOf, vocab } from "./sourceLabel.mjs";

// MARKETDATA1 — org admin: rank overrides within the allowed providers, and file uploads
// (maker-checker; nothing counts until a second admin approves). Controls only when the
// server's envelope allows — a lost envelope renders read-only.
const card = "rounded-[6px] border border-[#ece8dd] bg-white p-5";

export function RankOverridesView({ envelope, onAct }) {
  const write = can(envelope, "can_write") && vocab(envelope, "rank_editable").includes("providers");
  const types = vocab(envelope, "data_types");
  return (
    <section className={card}>
      <h2 className="font-serif text-xl text-navy">Price sources</h2>
      <p className="text-sm text-text-muted">The order sources are asked in, per data type. An override may use only the providers the platform allows.</p>
      <ul className="mt-3 space-y-2 text-sm">
        {(envelope?.ranks || []).map((r) => (
          <li key={r.data_type} data-testid="org-rank-row">
            <span className="font-medium">{labelOf(types, r.data_type)}</span>: {r.effective.join(" → ") || "(no source)"}
            <span className="ml-1 text-xs text-text-muted">{r.origin === "org_override" ? "your order" : "platform default"}</span>
            {write && r.allowed.length ? (
              <>
                <button type="button" className="ml-2 text-navy underline"
                  onClick={() => {
                    const v = window.prompt(`Order for ${r.data_type} (from: ${r.allowed.join(", ")})`, r.effective.join(", "));
                    if (v !== null) onAct?.("org_rank", { data_type: r.data_type, providers: v.split(",").map((x) => x.trim()).filter(Boolean) });
                  }}>Change order</button>
                {r.origin === "org_override" ? (
                  <button type="button" className="ml-2 text-navy underline"
                    onClick={() => onAct?.("org_rank", { data_type: r.data_type, providers: [] })}>Use platform default</button>
                ) : null}
              </>
            ) : null}
          </li>
        ))}
      </ul>
    </section>
  );
}

export function UploadsView({ envelope, onAct }) {
  const canUpload = can(envelope, "can_write") && vocab(envelope, "upload_editable").includes("file");
  const types = vocab(envelope, "data_types");
  const uploadTypes = vocab(envelope, "upload_types");
  const md = vocab(envelope, "member_display");
  const ret = vocab(envelope, "retention");
  return (
    <section className={card}>
      <h2 className="font-serif text-xl text-navy">File uploads</h2>
      <p className="text-sm text-text-muted">Prices, corporate actions and index levels from a file. Each file states its own licence and counts only after a second admin approves it.</p>
      {canUpload ? (
        <form className="mt-3 grid grid-cols-2 gap-3 text-sm" data-testid="upload-form"
          onSubmit={(e) => { e.preventDefault(); onAct?.("upload", new FormData(e.currentTarget)); }}>
          <label>File <input name="file" type="file" accept=".csv,.xlsx,.xls" required /></label>
          <label>Data type <select name="data_type">{uploadTypes.map((t) => <option key={t} value={t}>{labelOf(types, t)}</option>)}</select></label>
          <label><input name="internal_use" type="checkbox" defaultChecked /> Internal use allowed</label>
          <label>Shown to members <select name="member_display">{md.filter((m) => m.key !== "own_holdings_only").map((m) => <option key={m.key} value={m.key}>{m.label}</option>)}</select></label>
          <label><input name="redistribution" type="checkbox" /> Redistribution allowed</label>
          <label>After cancellation <select name="retention_after_cancel">{ret.map((r) => <option key={r.key} value={r.key}>{r.label}</option>)}</select></label>
          <label className="col-span-2">Attribution <input name="attribution" type="text" className="w-full border border-[#E2E8F0]" /></label>
          <button type="submit" className="col-span-2 rounded-[6px] bg-[#1B2B4B] px-4 py-2 text-white">Upload for approval</button>
        </form>
      ) : null}
      <table className="mt-4 w-full text-sm">
        <thead className="text-left text-text-muted"><tr><th>File</th><th>Type</th><th>Rows</th><th>Licence</th><th>Status</th><th /></tr></thead>
        <tbody>
          {(envelope?.uploads || []).map((u) => (
            <tr key={u.id} className="border-t border-[#E2E8F0]" data-testid="upload-row">
              <td>{u.file_name}</td>
              <td>{labelOf(types, u.data_type)}</td>
              <td title={(u.rejects || []).map((r) => `row ${r.row}: ${r.reason}`).join("\n")}>{u.accepted_count} accepted of {u.row_count}</td>
              <td>{u.licence?.internal_use ? "internal" : "no internal use"} · {labelOf(md, u.licence?.member_display)}</td>
              <td>{u.status}{u.decision_note ? ` — ${u.decision_note}` : ""}</td>
              <td>
                {u.can_decide === true && can(envelope, "can_approve") ? (
                  <>
                    <button type="button" className="text-navy underline" onClick={() => onAct?.("decide", { id: u.id, approve: true })}>Approve</button>
                    <button type="button" className="ml-2 text-navy underline" onClick={() => {
                      const note = window.prompt("Why is this file rejected?");
                      if (note) onAct?.("decide", { id: u.id, approve: false, note });
                    }}>Reject</button>
                  </>
                ) : null}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}

export default function OrgSources({ initialRank, initialUploads }) {
  const [rank, setRank] = useState(initialRank);
  const [ups, setUps] = useState(initialUploads);
  const [error, setError] = useState(null);
  async function reload() {
    const [a, b] = await Promise.all([fetch("/api/security-data/rank"), fetch("/api/security-data/uploads")]);
    if (a.ok) setRank(await a.json());
    if (b.ok) setUps(await b.json());
  }
  async function onAct(kind, arg) {
    setError(null);
    let res;
    if (kind === "org_rank") {
      res = await fetch(`/api/security-data/rank/${encodeURIComponent(arg.data_type)}`, { method: "PUT", body: JSON.stringify({ providers: arg.providers }) });
    } else if (kind === "decide") {
      res = await fetch(`/api/security-data/uploads/${encodeURIComponent(arg.id)}/decision`, { method: "POST", body: JSON.stringify({ approve: arg.approve, note: arg.note }) });
    } else {
      const fd = new FormData();
      fd.set("file", arg.get("file"));
      fd.set("data_type", arg.get("data_type"));
      fd.set("licence", JSON.stringify({ internal_use: arg.get("internal_use") === "on", member_display: arg.get("member_display"),
        redistribution: arg.get("redistribution") === "on", retention_after_cancel: arg.get("retention_after_cancel"),
        attribution: arg.get("attribution") || null }));
      res = await fetch("/api/security-data/upload", { method: "POST", body: fd });
    }
    if (!res.ok) setError((await res.json().catch(() => ({}))).error || "Request failed");
    await reload();
  }
  return (
    <div className="space-y-6">
      {error ? <p className="text-sm text-[#9B2335]">{error}</p> : null}
      <RankOverridesView envelope={rank} onAct={onAct} />
      <UploadsView envelope={ups} onAct={onAct} />
    </div>
  );
}
