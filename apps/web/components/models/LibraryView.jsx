"use client";

/**
 * LibraryView — Portfolio → Models → Load from library (REP3).
 *
 * The platform shelf (published by Hollisworks, read-only for every org) and
 * our shelf (published by this org's admins). Copy makes an independent org
 * model; Subscribe makes one that receives each new approved version as a
 * draft with a review task. An entry that requires an agreement needs an org
 * admin's "agreement on file" confirmation first. Controls render only from
 * `libraryControls(envelope, entry)`; labels are the envelope's vocabularies.
 */

import { useState } from "react";
import { labelOf, libraryControls, vocab } from "@/lib/models.mjs";

const card = "rounded-[6px] border border-[#ece8dd] bg-white";
const primary = "rounded bg-navy px-3 py-1 text-white disabled:opacity-50";
const secondary = "rounded border border-navy px-3 py-1 text-navy disabled:opacity-50";

async function post(path, body) {
  const res = await fetch(`/api/models/${path}`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `Request failed (${res.status})`);
  return data;
}

export function EntryCard({ envelope, entry, onChanged }) {
  const v = vocab(envelope);
  const c = libraryControls(envelope, entry);
  const [msg, setMsg] = useState(null);
  async function load(kind) {
    setMsg(null);
    try {
      const out = await post(`library/${entry.id}/${kind}`, {});
      window.location.href = `/portfolio/models/${out.model.id}`;
    } catch (e) { setMsg(e.message); }
  }
  async function agree() {
    setMsg(null);
    try { await post(`library/${entry.id}/agreement`, {}); onChanged?.(); } catch (e) { setMsg(e.message); }
  }
  return (
    <div className={`${card} space-y-2 p-5 text-sm`}>
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <p className="text-base font-semibold text-navy">{entry.title}</p>
        <p className="text-text-muted">{entry.provider}</p>
      </div>
      {entry.description ? <p>{entry.description}</p> : null}
      <p className="text-text-muted">
        {labelOf(v.cadences, entry.update_cadence)} updates · last updated {String(entry.last_updated || "—").slice(0, 10)}
        {entry.positions != null ? ` · ${entry.positions} positions` : ""} · used by {entry.usage_count}
      </p>
      {entry.licensing_note ? <p className="text-text-muted">Licensing: {entry.licensing_note}</p> : null}
      {entry.requires_agreement ? (
        <p className={entry.agreement_on_file ? "text-[#2D6A4F]" : "text-[#8a5a00]"}>
          {entry.agreement_on_file ? "Agreement on file." : "Requires an agreement on file before it can be loaded."}
        </p>
      ) : null}
      {Array.isArray(entry.loaded_as) && entry.loaded_as.length ? (
        <p className="text-text-muted">Loaded here as: {entry.loaded_as.map((m) => m.name).join(", ")}</p>
      ) : null}
      <div className="flex flex-wrap gap-2">
        {c.confirmAgreement ? <button type="button" className={secondary} onClick={agree}>Confirm agreement on file</button> : null}
        {c.copy ? <button type="button" className={primary} onClick={() => load("copy")}>Copy</button> : null}
        {c.subscribe ? <button type="button" className={secondary} onClick={() => load("subscribe")}>Subscribe</button> : null}
      </div>
      {msg ? <p className="text-[#9B2335]">{msg}</p> : null}
    </div>
  );
}

export default function LibraryView({ initialEnvelope }) {
  const [env, setEnv] = useState(initialEnvelope);
  const v = vocab(env);
  const rows = Array.isArray(env?.rows) ? env.rows : [];
  async function reload() {
    const res = await fetch("/api/models/library");
    const data = await res.json().catch(() => ({}));
    if (res.ok) setEnv(data);
  }
  return (
    <div className="space-y-8">
      {["platform", "org"].map((shelf) => {
        const list = rows.filter((r) => r.shelf === shelf);
        return (
          <section key={shelf} className="space-y-3">
            <h2 className="text-xl font-semibold text-navy">{labelOf(v.shelves, shelf)}</h2>
            {list.length ? list.map((e) => <EntryCard key={e.id} envelope={env} entry={e} onChanged={reload} />) : (
              <div className={`${card} p-8 text-center text-sm text-text-muted`}>Nothing on this shelf yet.</div>
            )}
          </section>
        );
      })}
    </div>
  );
}
