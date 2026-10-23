"use client";

/**
 * NewInstruction — the New instruction form (REP2). Rendered by
 * InstructionList only when the envelope says can_write === true.
 *
 * Security search (security master), lot picker from THIS account's open lots
 * in THAT security (sells only), price guidance, executor and the execute-by
 * policy. Quantities and prices are sent as decimal strings. The server routes
 * the trade (in this build: always to a draft execution instruction) and says
 * why; nothing is sent to a custodian.
 */

import { useState } from "react";
import { tradeListBody, vocab } from "@/lib/execution.mjs";

const card = "rounded-[6px] border border-[#ece8dd] bg-white";
const input = "rounded border border-[#E2E8F0] px-3 py-2";

export default function NewInstruction({ envelope, accounts = [], onCreated }) {
  const v = vocab(envelope);
  const [form, setForm] = useState({
    accountId: accounts[0]?.id || "", side: "buy", amountMode: "quantity", amount: "", priceKind: "market",
    executorType: v.executor_types?.[0]?.key || "", ifNotFilled: "ask", executeBy: "", lots: [],
  });
  const [query, setQuery] = useState("");
  const [results, setResults] = useState([]);
  const [security, setSecurity] = useState(null);
  const [lots, setLots] = useState([]);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState(null);
  const set = (k) => (e) => setForm({ ...form, [k]: e.target.value });

  async function search(e) {
    e.preventDefault();
    setMsg(null);
    const res = await fetch(`/api/execution/securities?q=${encodeURIComponent(query)}`);
    const data = await res.json().catch(() => ({}));
    if (res.ok) setResults(data.rows || []); else setMsg(data.error || "Search failed");
  }

  async function pick(sec) {
    setSecurity(sec);
    setResults([]);
    setForm((f) => ({ ...f, securityId: sec.id, lots: [] }));
    if (form.accountId) {
      const res = await fetch(`/api/execution/accounts/${form.accountId}/lots?security_id=${sec.id}`);
      const data = await res.json().catch(() => ({}));
      setLots(res.ok ? data.rows || [] : []);
    }
  }

  function setLotQty(id, quantity) {
    const others = (form.lots || []).filter((l) => l.id !== id);
    setForm({ ...form, lots: quantity ? [...others, { id, quantity }] : others });
  }

  async function submit(e) {
    e.preventDefault();
    setBusy(true); setMsg(null);
    try {
      const res = await fetch("/api/execution/instructions", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(tradeListBody(form)),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.error || `Could not create the instruction (${res.status})`);
      onCreated?.(data);
    } catch (err) {
      setMsg(err.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className={`${card} p-6`}>
      <h2 className="text-lg font-semibold text-navy">New instruction</h2>
      <p className="mt-1 text-xs text-text-muted">Every trade becomes an instruction a person carries out. It starts as a draft and needs a second person to approve it.</p>
      <form onSubmit={search} className="mt-4 flex flex-wrap items-end gap-3 text-sm">
        <label className="flex flex-col gap-1">Account
          <select className={input} value={form.accountId} onChange={set("accountId")}>
            {accounts.map((a) => (
              <option key={a.id} value={a.id}>{a.account_number_masked}{a.is_held_away ? " (held away)" : ""}</option>
            ))}
          </select>
        </label>
        <label className="flex flex-col gap-1">Security
          <input className={input} value={query} placeholder="Name, ticker or CUSIP" onChange={(e) => setQuery(e.target.value)} />
        </label>
        <button type="submit" className="rounded border border-navy px-3 py-2 text-navy">Search</button>
        {security ? <span className="text-navy">{security.name}{security.ticker ? ` (${security.ticker})` : ""}</span> : null}
      </form>
      {results.length ? (
        <ul className="mt-2 text-sm">
          {results.map((r) => (
            <li key={r.id}><button type="button" className="text-navy underline" onClick={() => pick(r)}>
              {r.name}{r.ticker ? ` (${r.ticker})` : ""}{r.cusip ? ` · ${r.cusip}` : ""}
            </button></li>
          ))}
        </ul>
      ) : null}
      <form onSubmit={submit} className="mt-4 grid grid-cols-1 gap-3 text-sm md:grid-cols-3">
        <label className="flex flex-col gap-1">Side
          <select className={input} value={form.side} onChange={set("side")}>
            {(v.sides || []).map((s) => <option key={s.key} value={s.key}>{s.label}</option>)}
          </select>
        </label>
        <label className="flex flex-col gap-1">Amount
          <div className="flex gap-2">
            <select className={input} value={form.amountMode} onChange={set("amountMode")}>
              <option value="quantity">Shares</option>
              <option value="notional">Notional</option>
            </select>
            <input className={`${input} w-full`} inputMode="decimal" value={form.amount} onChange={set("amount")} />
          </div>
        </label>
        <label className="flex flex-col gap-1">Price guidance
          <select className={input} value={form.priceKind} onChange={set("priceKind")}>
            {(v.price_kinds || []).map((s) => <option key={s.key} value={s.key}>{s.label}</option>)}
          </select>
        </label>
        {form.priceKind === "limit" ? (
          <label className="flex flex-col gap-1">Limit price
            <input className={input} inputMode="decimal" value={form.limitPrice || ""} onChange={set("limitPrice")} />
          </label>
        ) : null}
        {form.priceKind === "range" ? (
          <>
            <label className="flex flex-col gap-1">Low
              <input className={input} inputMode="decimal" value={form.rangeLow || ""} onChange={set("rangeLow")} />
            </label>
            <label className="flex flex-col gap-1">High
              <input className={input} inputMode="decimal" value={form.rangeHigh || ""} onChange={set("rangeHigh")} />
            </label>
            <label className="flex flex-col gap-1">Reference price
              <input className={input} inputMode="decimal" value={form.referencePrice || ""} onChange={set("referencePrice")} />
            </label>
            <label className="flex flex-col gap-1">Reference time
              <input type="datetime-local" className={input} value={form.referenceAt || ""} onChange={set("referenceAt")} />
            </label>
          </>
        ) : null}
        <label className="flex flex-col gap-1">Carried out by
          <select className={input} value={form.executorType} onChange={set("executorType")}>
            {(v.executor_types || []).map((s) => <option key={s.key} value={s.key}>{s.label}</option>)}
          </select>
        </label>
        <label className="flex flex-col gap-1">Name
          <input className={input} value={form.executorName || ""} onChange={set("executorName")} />
        </label>
        <label className="flex flex-col gap-1">Contact
          <input className={input} value={form.executorContact || ""} onChange={set("executorContact")} />
        </label>
        <label className="flex flex-col gap-1">Execute by
          <input type="date" className={input} value={form.executeBy} onChange={set("executeBy")} />
        </label>
        <label className="flex flex-col gap-1">If not filled
          <select className={input} value={form.ifNotFilled} onChange={set("ifNotFilled")}>
            {(v.if_not_filled || []).map((s) => <option key={s.key} value={s.key}>{s.label}</option>)}
          </select>
        </label>
        <label className="flex flex-col gap-1 md:col-span-3">Wash-sale notes
          <textarea className={input} rows={2} value={form.washSaleNotes || ""} onChange={set("washSaleNotes")} />
        </label>
        {form.side === "sell" && lots.length ? (
          <div className="md:col-span-3">
            <p className="font-medium text-navy">Specific lots</p>
            <table className="mt-2 w-full text-sm">
              <thead className="text-left text-text-muted"><tr><th className="py-1">Acquired</th><th>Holds</th><th>Term</th><th>Sell</th></tr></thead>
              <tbody>
                {lots.map((l) => (
                  <tr key={l.id}>
                    <td className="py-1">{l.acquired_date || "—"}</td><td>{l.quantity}</td><td>{l.term || "—"}</td>
                    <td><input className={`${input} w-28`} inputMode="decimal"
                      value={(form.lots || []).find((x) => x.id === l.id)?.quantity || ""}
                      onChange={(e) => setLotQty(l.id, e.target.value)} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : null}
        <div className="md:col-span-3">
          <button type="submit" disabled={busy || !form.securityId || !form.accountId}
            className="rounded bg-navy px-4 py-2 text-white disabled:opacity-50">Create draft</button>
          {msg ? <span className="ml-3 text-[#9B2335]">{msg}</span> : null}
        </div>
      </form>
    </div>
  );
}
