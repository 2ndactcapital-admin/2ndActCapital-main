"use client";

/**
 * ModelList — Portfolio → Models (REP3).
 *
 * Every model in the org's ONE registry (uploaded, library copy or
 * subscription, replicated fund, composite) from GET /models. The Upload
 * model, Load from library and New composite controls render only from
 * `listControls(envelope)` — the envelope's `can_write === true` AND the
 * action in the server's `actions` list. Sources and statuses are the
 * envelope's vocabularies.
 */

import { useState } from "react";
import { labelOf, listControls, pctInput, sumPct, vocab } from "@/lib/models.mjs";

const card = "rounded-[6px] border border-[#ece8dd] bg-white";
const input = "rounded border border-[#E2E8F0] px-3 py-2";

export function ModelTable({ envelope }) {
  const rows = Array.isArray(envelope?.rows) ? envelope.rows : [];
  const v = vocab(envelope);
  if (!rows.length) {
    return <div className={`${card} p-10 text-center text-sm text-text-muted`}>No models match.</div>;
  }
  return (
    <div className={`${card} overflow-x-auto`}>
      <table className="w-full text-sm">
        <thead className="border-b border-[#ece8dd] text-left text-text-muted">
          <tr>
            <th className="px-4 py-3 font-medium">Model</th>
            <th className="px-4 py-3 font-medium">Source</th>
            <th className="px-4 py-3 font-medium">Live version</th>
            <th className="px-4 py-3 font-medium">Status</th>
            <th className="px-4 py-3 font-medium">Positions</th>
            <th className="px-4 py-3 font-medium">Sleeves</th>
            <th className="px-4 py-3 font-medium">Last change</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.id} className="border-b border-[#ece8dd] last:border-0">
              <td className="px-4 py-3">
                <a className="font-medium text-navy hover:underline" href={`/portfolio/models/${r.id}`}>{r.name}</a>
                {r.open_version_no ? (
                  <span className="ml-2 text-xs text-text-muted">
                    v{r.open_version_no} {labelOf(v.version_statuses, r.open_status).toLowerCase()}
                  </span>
                ) : null}
              </td>
              <td className="px-4 py-3">{labelOf(v.sources, r.source)}</td>
              <td className="px-4 py-3">{r.live_version_no ? `v${r.live_version_no}` : "—"}</td>
              <td className="px-4 py-3">{labelOf(v.model_statuses, r.status)}</td>
              <td className="px-4 py-3">{r.positions ?? "—"}</td>
              <td className="px-4 py-3">{r.sleeves ? r.sleeves : "—"}</td>
              <td className="px-4 py-3">{(r.last_change || "").slice(0, 10)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function Filters({ envelope, value, onChange }) {
  const v = vocab(envelope);
  const set = (k) => (e) => onChange({ ...value, [k]: e.target.value });
  return (
    <div className="flex flex-wrap items-end gap-3 text-sm">
      <label className="flex flex-col gap-1">Search
        <input className={input} value={value.q || ""} onChange={set("q")} placeholder="Model name" />
      </label>
      <label className="flex flex-col gap-1">Source
        <select className={input} value={value.source || ""} onChange={set("source")}>
          <option value="">Any</option>
          {(v.sources || []).map((s) => <option key={s.key} value={s.key}>{s.label}</option>)}
        </select>
      </label>
      <label className="flex flex-col gap-1">Status
        <select className={input} value={value.status || ""} onChange={set("status")}>
          <option value="">Any</option>
          {(v.model_statuses || []).map((s) => <option key={s.key} value={s.key}>{s.label}</option>)}
        </select>
      </label>
    </div>
  );
}

/** New composite: a name and sleeves (other models of the org, by weight). */
export function CompositeForm({ envelope, onCreated }) {
  const candidates = (Array.isArray(envelope?.rows) ? envelope.rows : []).filter((r) => r.live_version_no);
  const v = vocab(envelope);
  const [name, setName] = useState("");
  const [sleeves, setSleeves] = useState([{ child_model_id: "", weight_pct: "", follow: "live" }]);
  const [msg, setMsg] = useState(null);
  const total = sumPct(sleeves.map((s) => pctInput(s.weight_pct) ?? "x"));
  const setRow = (i, k, val) => setSleeves(sleeves.map((s, j) => (j === i ? { ...s, [k]: val } : s)));

  async function save() {
    setMsg(null);
    const res = await fetch("/api/models", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, sleeves: sleeves.filter((s) => s.child_model_id) }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) setMsg(data.error || `Could not create (${res.status})`);
    else onCreated?.(data);
  }

  return (
    <div className={`${card} space-y-3 p-5 text-sm`}>
      <p className="font-medium text-navy">New composite</p>
      <label className="flex flex-col gap-1">Name
        <input className={input} value={name} onChange={(e) => setName(e.target.value)} />
      </label>
      {sleeves.map((s, i) => (
        <div key={i} className="flex flex-wrap items-end gap-2">
          <select className={input} value={s.child_model_id} onChange={(e) => setRow(i, "child_model_id", e.target.value)}>
            <option value="">Choose a model</option>
            {candidates.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
          </select>
          <input className={`${input} w-28`} value={s.weight_pct} placeholder="Weight %"
            onChange={(e) => setRow(i, "weight_pct", e.target.value)} />
          <select className={input} value={s.follow} onChange={(e) => setRow(i, "follow", e.target.value)}>
            {(v.follow_modes || []).filter((f) => f.key === "live").map((f) => (
              <option key={f.key} value={f.key}>{f.label}</option>
            ))}
          </select>
        </div>
      ))}
      <div className="flex items-center gap-3">
        <button type="button" className="text-navy underline"
          onClick={() => setSleeves([...sleeves, { child_model_id: "", weight_pct: "", follow: "live" }])}>Add sleeve</button>
        <span className={total === "100" ? "text-[#2D6A4F]" : "text-text-muted"}>Total {total ?? "—"}%</span>
      </div>
      {msg ? <p className="text-[#9B2335]">{msg}</p> : null}
      <button type="button" className="rounded bg-navy px-4 py-2 text-white disabled:opacity-50"
        disabled={!name.trim() || total !== "100"} onClick={save}>Create composite</button>
    </div>
  );
}

export default function ModelList({ initialEnvelope }) {
  const [env, setEnv] = useState(initialEnvelope);
  const [filters, setFilters] = useState({});
  const [composing, setComposing] = useState(false);
  const [msg, setMsg] = useState(null);
  const controls = listControls(env);

  async function reload(next = filters) {
    setFilters(next);
    const params = new URLSearchParams();
    for (const [k, val] of Object.entries(next)) if (val) params.set(k, String(val));
    const res = await fetch(`/api/models?${params}`);
    const data = await res.json().catch(() => ({}));
    if (res.ok) setEnv(data); else setMsg(data.error || `Reload failed (${res.status})`);
  }

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <Filters envelope={env} value={filters} onChange={reload} />
        <div className="flex flex-wrap gap-2 text-sm">
          {controls.upload ? (
            <a className="rounded bg-navy px-4 py-2 text-white" href="/portfolio/models/upload">Upload model</a>
          ) : null}
          {controls.library ? (
            <a className="rounded border border-navy px-4 py-2 text-navy" href="/portfolio/models/library">Load from library</a>
          ) : null}
          {controls.composite ? (
            <button type="button" className="rounded border border-navy px-4 py-2 text-navy"
              onClick={() => setComposing(!composing)}>New composite</button>
          ) : null}
        </div>
      </div>
      {msg ? <p className="text-sm text-[#9B2335]">{msg}</p> : null}
      {controls.composite && composing ? (
        <CompositeForm envelope={env} onCreated={(out) => { window.location.href = `/portfolio/models/${out.model.id}`; }} />
      ) : null}
      <ModelTable envelope={env} />
      <p className="text-xs text-text-muted">
        <a className="underline" href="/portfolio/models/uploads">Upload log</a>
        {" · "}<a className="underline" href="/api/models/template?format=xlsx">Template (.xlsx)</a>
        {" · "}<a className="underline" href="/api/models/template?format=csv">Template (.csv)</a>
      </p>
    </div>
  );
}
