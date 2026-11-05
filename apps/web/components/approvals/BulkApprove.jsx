"use client";

/**
 * BulkApprove — Approvals → Bulk approve (BULKAPPROVE1). Tabs per item type
 * (from the envelope), a table of PENDING maker-checker items with filters,
 * select one / select all (on the filtered set), greyed rows with the server's
 * reason, Approve selected / Reject selected (a note to reject) and ONE
 * confirmation naming the count and listing the items. Held-away rows expand
 * to their lines and totals. Every control renders only from
 * lib/bulkApprove.mjs; the server decides each item through the single path.
 */

import { useState } from "react";
import { formatMoneyExact, formatNumberExact } from "@/lib/decimalString";
import {
  blockedReason, bulkControls, canDecide, confirmation, filterRows, itemSummary, labelOf, pruneSelection,
  requestBody, resultLines, rowsOf, selectAll, selectable, toggle, vocab,
} from "@/lib/bulkApprove.mjs";

const card = "rounded-[6px] border border-[#ece8dd] bg-white";
const input = "rounded border border-[#E2E8F0] px-3 py-2 text-sm";
const primary = "rounded bg-navy px-3 py-1 text-white disabled:opacity-50";
const secondary = "rounded border border-navy px-3 py-1 text-navy disabled:opacity-50";

function Lines({ item }) {
  const l = item.lines || {};
  const t = item.totals || {};
  return (
    <div className="bg-[#FAF9F6] px-4 py-3 text-sm">
      <table className="w-full">
        <thead className="text-left text-text-muted"><tr><th className="py-1">Line</th><th>Quantity</th><th>Market value</th></tr></thead>
        <tbody>
          {(l.positions || []).map((p, i) => (
            <tr key={`p${i}`} className="border-t border-[#ece8dd]"><td className="py-1">{p.label}</td>
              <td>{formatNumberExact(p.quantity)}</td><td>{p.market_value == null ? "—" : formatMoneyExact(p.market_value)}</td></tr>
          ))}
          {(l.transactions || []).map((x, i) => (
            <tr key={`t${i}`} className="border-t border-[#ece8dd]"><td className="py-1">{x.side} · {x.trade_date}</td>
              <td>{formatNumberExact(x.quantity)}</td><td>{x.price == null ? "—" : formatMoneyExact(x.price)}</td></tr>
          ))}
        </tbody>
      </table>
      <p className="mt-2 text-xs text-text-muted">
        {t.positions} position(s), {t.lots} lot(s), {t.transactions} transaction(s) · {t.rows_accepted} of {t.rows_read} rows accepted
        {t.rows_rejected ? `, ${t.rows_rejected} rejected` : ""} · total {t.market_value == null ? "not known for every line" : formatMoneyExact(t.market_value)}
      </p>
    </div>
  );
}

export function BulkApproveView({ envelope, selected, filters, expanded, onToggle, onSelectAll, onClear, onFilter,
  onExpand, onAsk }) {
  const v = vocab(envelope);
  const rows = filterRows(envelope, filters);
  const ctl = bulkControls(envelope, selected);
  const decide = canDecide(envelope);
  const held = envelope?.type === "held_away";
  const sel = new Set(selected);
  return (
    <div className={`${card} p-6`}>
      <div className="flex flex-wrap items-end gap-2" data-filters="1">
        <select className={input} aria-label="Household" value={filters.household || ""} onChange={(e) => onFilter("household", e.target.value)}>
          <option value="">Every household</option>
          {(v.households || []).map((o) => <option key={o.key} value={o.key}>{o.label}</option>)}
        </select>
        <select className={input} aria-label="Maker" value={filters.maker || ""} onChange={(e) => onFilter("maker", e.target.value)}>
          <option value="">Anyone</option>
          {(v.makers || []).map((o) => <option key={o.key} value={o.key}>{o.label}</option>)}
        </select>
        <select className={input} aria-label="Type" value={filters.subtype || ""} onChange={(e) => onFilter("subtype", e.target.value)}>
          <option value="">Every type</option>
          {(v.subtypes || []).map((o) => <option key={o.key} value={o.key}>{o.label}</option>)}
        </select>
        <select className={input} aria-label="Age" value={filters.age || "any"} onChange={(e) => onFilter("age", e.target.value)}>
          {(v.ages || []).map((o) => <option key={o.key} value={o.key}>{o.label}</option>)}
        </select>
      </div>
      {decide ? (
        <div className="mt-4 flex flex-wrap items-center gap-2 text-sm" data-actions="1">
          <button type="button" className={secondary} onClick={() => onSelectAll(rows)}>Select all ({selectAll(envelope, rows).length})</button>
          <button type="button" className={secondary} disabled={!selected.length} onClick={onClear}>Clear</button>
          <span className="text-text-muted">{ctl.count} selected</span>
          <button type="button" className={primary} disabled={!ctl.approve} onClick={() => onAsk("approve")}>Approve selected</button>
          <button type="button" className={secondary} disabled={!ctl.reject} onClick={() => onAsk("reject")}>Reject selected</button>
          {ctl.overCap ? <span className="text-[#9B2335]">At most {ctl.cap} per request</span> : null}
        </div>
      ) : null}
      {!rows.length ? (
        <p className="mt-6 text-sm text-text-muted">{rowsOf(envelope).length ? "Nothing matches these filters." : "Nothing is waiting for approval."}</p>
      ) : (
        <table className="mt-4 w-full text-sm">
          <thead className="text-left text-text-muted"><tr>
            <th className="w-8 py-1" /><th>Household</th><th>Account</th><th>Change</th><th>Maker</th><th>Requested</th>
          </tr></thead>
          <tbody>{rows.map((r) => {
            const can = selectable(envelope, r);
            const why = blockedReason(envelope, r);
            return [
              <tr key={r.id} data-item={r.id} data-selectable={can ? "1" : "0"}
                className={`border-t border-[#ece8dd] align-top ${can ? "" : "text-text-muted opacity-60"}`}>
                <td className="py-2">{decide ? (
                  <input type="checkbox" aria-label={`Select ${itemSummary(r)}`} disabled={!can} checked={sel.has(r.id)}
                    onChange={() => onToggle(r)} />) : null}</td>
                <td className="py-2">{r.household_name || "—"}</td>
                <td>{r.account_masked || "Whole household"}</td>
                <td>
                  <span className="block text-xs text-text-muted">{r.subtype_label}</span>
                  {r.from_label ? <>{r.from_label} → {r.to_label}</> : r.to_label}
                  {r.reason ? <span className="block text-xs text-text-muted">{r.reason}</span> : null}
                  {why ? <span className="block text-xs" data-blocked="1">{why}</span> : null}
                  {held ? <button type="button" className="mt-1 text-xs text-navy underline" onClick={() => onExpand(r.id)}>
                    {expanded === r.id ? "Hide lines" : "Show lines and totals"}</button> : null}
                </td>
                <td>{r.maker_name}</td>
                <td>{String(r.requested_at || "").slice(0, 10)}</td>
              </tr>,
              held && expanded === r.id ? <tr key={`${r.id}-lines`}><td /><td colSpan={5}><Lines item={r} /></td></tr> : null,
            ];
          })}</tbody>
        </table>
      )}
    </div>
  );
}

export function ConfirmView({ envelope, selected, decision, note, onNote, onConfirm, onCancel, busy }) {
  const c = confirmation(envelope, selected, decision, note);
  return (
    <div className={`${card} mt-4 p-6`} role="dialog" aria-label="Confirm">
      <h2 className="text-lg font-semibold text-navy">{c.title}</h2>
      <p className="mt-1 text-sm text-text-muted">Each item is decided on its own, under your name, by the same rules as a single approval.</p>
      <ul className="mt-3 max-h-64 overflow-auto text-sm">{c.items.map((i) => <li key={i.id} className="py-0.5">{i.text}</li>)}</ul>
      {c.noteRequired ? (
        <textarea className={`${input} mt-3 w-full`} placeholder="Why — the note goes on every rejected item" value={note}
          onChange={(e) => onNote(e.target.value)} />
      ) : (
        <input className={`${input} mt-3 w-full`} placeholder="Note (optional)" value={note} onChange={(e) => onNote(e.target.value)} />
      )}
      <div className="mt-3 flex gap-2">
        <button type="button" className={primary} disabled={!c.canConfirm || busy} onClick={onConfirm}>{labelOf(vocab(envelope).decisions, decision)} {c.items.length}</button>
        <button type="button" className={secondary} disabled={busy} onClick={onCancel}>Cancel</button>
      </div>
    </div>
  );
}

export default function BulkApprove({ initialEnvelope }) {
  const [env, setEnv] = useState(initialEnvelope);
  const [selected, setSelected] = useState([]);
  const [filters, setFilters] = useState({ age: "any" });
  const [expanded, setExpanded] = useState(null);
  const [asking, setAsking] = useState(null);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [results, setResults] = useState(null);
  const [msg, setMsg] = useState(null);
  if (!env || typeof env !== "object") return null;
  const tabs = Array.isArray(env.tabs) ? env.tabs.filter((t) => t && t.readable === true) : [];

  async function load(type) {
    setMsg(null);
    const res = await fetch(`/api/approvals/bulk?type=${encodeURIComponent(type)}`);
    const data = await res.json().catch(() => ({}));
    if (res.ok) { setEnv(data); setSelected([]); setFilters({ age: "any" }); setExpanded(null); }
    else setMsg(data.error || `Could not load (${res.status})`);
  }
  async function confirm() {
    setBusy(true); setMsg(null);
    try {
      const res = await fetch("/api/approvals/bulk", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(requestBody(env, selected, asking, note)),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.error || `Request failed (${res.status})`);
      setResults(resultLines(env, data));
      setAsking(null); setNote("");
      await load(env.type);
    } catch (e) { setMsg(e.message); } finally { setBusy(false); }
  }

  return (
    <div>
      {tabs.length > 1 ? (
        <div className="mb-4 flex gap-4 border-b border-[#ece8dd] text-sm">
          {tabs.map((t) => (
            <button key={t.key} type="button" onClick={() => load(t.key)}
              className={`pb-2 ${t.key === env.type ? "border-b-2 border-[#C5A880] font-medium text-navy" : "text-text-muted"}`}>{t.label}</button>
          ))}
        </div>
      ) : null}
      {msg ? <p className="mb-3 text-sm text-[#9B2335]">{msg}</p> : null}
      {results ? (
        <div className={`${card} mb-4 p-4 text-sm`} data-results="1">
          <ul>{results.map((r) => <li key={r.id}>{r.label} — {r.text}{r.reason ? <span className="text-text-muted"> ({r.reason})</span> : null}</li>)}</ul>
        </div>
      ) : null}
      <BulkApproveView envelope={env} selected={selected} filters={filters} expanded={expanded}
        onToggle={(r) => setSelected(toggle(env, selected, r))}
        onSelectAll={(rows) => setSelected(selectAll(env, rows))}
        onClear={() => setSelected([])}
        onFilter={(k, val) => { const f = { ...filters, [k]: val }; setFilters(f); setSelected(pruneSelection(selected, filterRows(env, f))); }}
        onExpand={(id) => setExpanded(expanded === id ? null : id)}
        onAsk={(d) => { setAsking(d); setResults(null); }} />
      {asking ? <ConfirmView envelope={env} selected={selected} decision={asking} note={note} onNote={setNote}
        onConfirm={confirm} onCancel={() => setAsking(null)} busy={busy} /> : null}
    </div>
  );
}
