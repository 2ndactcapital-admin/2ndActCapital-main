"use client";

/**
 * Shared pieces of the Tax screens (REP6): an estimate (always labelled, the
 * rates and as-of on hover), a security picker that searches through the
 * Next.js API route, and the POST helper. Light theme, existing tokens.
 */

import { useState } from "react";
import { estimateText, ratesTitle } from "@/lib/tax.mjs";

export const card = "rounded-[6px] border border-[#ece8dd] bg-white";
export const input = "rounded border border-[#E2E8F0] px-3 py-2";
export const primary = "rounded bg-navy px-3 py-1 text-white disabled:opacity-50";
export const secondary = "rounded border border-navy px-3 py-1 text-navy disabled:opacity-50";

export async function post(path, body) {
  const res = await fetch(`/api/${path}`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `Request failed (${res.status})`);
  return data;
}

export async function get(path) {
  const res = await fetch(`/api/${path}`);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `Request failed (${res.status})`);
  return data;
}

/** A tax value: "$X (estimate)" with the rates and as-of on hover, or the reason it is not estimated. */
export function Estimate({ value, note, rates }) {
  const text = estimateText(value, note);
  const title = value === null || value === undefined ? (note || "") : `${note || ""}${rates ? ` — ${ratesTitle(rates)}` : ""}`;
  return <span title={title} className={value === null || value === undefined ? "text-text-muted" : ""}>{text}</span>;
}

export function SecurityPicker({ value, onChange, placeholder = "Search a security (name or ticker)" }) {
  const [q, setQ] = useState("");
  const [rows, setRows] = useState([]);
  const [err, setErr] = useState(null);
  async function search() {
    setErr(null);
    try {
      const d = await get(`tax/securities?q=${encodeURIComponent(q)}`);
      setRows(Array.isArray(d.rows) ? d.rows : []);
    } catch (e) { setErr(e.message); }
  }
  return (
    <div className="space-y-1">
      <div className="flex gap-2">
        <input className={input} value={q} placeholder={placeholder} onChange={(e) => setQ(e.target.value)} />
        <button type="button" className={secondary} onClick={search}>Search</button>
      </div>
      {value ? <p className="text-xs text-text-muted">Chosen: {value.label || value.id}</p> : null}
      {rows.length ? (
        <ul className="text-sm">{rows.map((r) => (
          <li key={r.id}><a className="text-navy underline" href="#" onClick={(e) => { e.preventDefault(); onChange({ id: r.id, label: r.ticker ? `${r.name} (${r.ticker})` : r.name }); setRows([]); }}>
            {r.name}{r.ticker ? ` (${r.ticker})` : ""}</a></li>
        ))}</ul>
      ) : null}
      {err ? <p className="text-xs text-[#9B2335]">{err}</p> : null}
    </div>
  );
}
