"use client";

// CRMDETAIL1 — the record's Liabilities tab: the live liabilities, their history, add / edit (a new version) / end,
// and net worth as three numbers (gross, liabilities, net). Kinds, rate types and line labels come from the API
// envelope; money arrives and leaves as strings (exact Decimal). Write controls only on can_write === true.
import { useEffect, useState } from "react";
import EntityPicker from "@/components/EntityPicker";
import { canWrite, formatMoney, liabilityPayload, netWorthLines } from "@/lib/crmDetail.mjs";

const card = "rounded-[6px] border border-[#ece8dd] bg-white p-6";
const input = "w-full rounded-[6px] border border-[#E2E8F0] bg-white px-3 py-2 text-sm text-[#0F172A]";
const button = "rounded-[6px] bg-[#1B2B4B] px-4 py-2 text-sm text-white disabled:opacity-50";
const quiet = "text-sm text-[#64748B]";

async function call(url, options = {}) {
  const res = await fetch(url, { cache: "no-store", headers: { "Content-Type": "application/json" }, ...options });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error(typeof data.detail === "string" ? data.detail : data.error || `Request failed (${res.status})`);
  }
  return data;
}

export function NetWorthLines({ netWorth, vocabularies }) {
  const lines = netWorthLines(netWorth, vocabularies);
  if (!lines.length) return null;
  return (
    <dl className="grid grid-cols-3 gap-4">
      {lines.map((l) => (
        <div key={l.key}>
          <dt className="text-xs font-medium uppercase tracking-wide text-[#64748B]">{l.label}</dt>
          <dd className={`mt-0.5 font-[Spectral,Georgia,serif] text-lg ${l.key === "net" ? "text-[#1B2B4B]" : "text-[#334155]"}`}>
            {l.text}
          </dd>
        </div>
      ))}
    </dl>
  );
}

const EMPTY = { name: "", kind: "", lender_name: "", original_amount: "", current_balance: "", as_of: "",
  rate_type: "", rate_text: "", maturity: "", collateral_text: "", notes: "", currency_code: "USD" };

function LiabilityForm({ vocab, initial, onSubmit, onCancel, submitLabel }) {
  const [form, setForm] = useState({ ...EMPTY, ...(initial || {}) });
  const [lender, setLender] = useState(initial?.lender_entity_id
    ? { id: initial.lender_entity_id, display_name: initial.lender_display } : null);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  const set = (k) => (e) => setForm({ ...form, [k]: e.target.value });
  async function submit(e) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await onSubmit({ ...form, lender_entity_id: lender?.id || (initial ? null : undefined) });
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <form onSubmit={submit} className="mt-4 grid gap-3 sm:grid-cols-2">
      <input value={form.name} onChange={set("name")} placeholder="Name (e.g. Coastal Bank — mortgage)" required className={input} />
      <select value={form.kind} onChange={set("kind")} required className={input} aria-label="Kind">
        <option value="">Kind…</option>
        {(vocab?.liability_kinds || []).map((k) => <option key={k.value} value={k.value}>{k.label}</option>)}
      </select>
      <EntityPicker value={lender} onChange={setLender} placeholder="Lender (a CRM record)…" />
      <input value={form.lender_name || ""} onChange={set("lender_name")} placeholder="…or lender name" className={input} />
      <input value={form.current_balance} onChange={set("current_balance")} inputMode="decimal" placeholder="Current balance" required className={input} />
      <input value={form.original_amount || ""} onChange={set("original_amount")} inputMode="decimal" placeholder="Original amount" className={input} />
      <label className={quiet}>As of <input type="date" value={form.as_of} onChange={set("as_of")} required className={input} /></label>
      <label className={quiet}>Maturity <input type="date" value={form.maturity || ""} onChange={set("maturity")} className={input} /></label>
      <select value={form.rate_type || ""} onChange={set("rate_type")} className={input} aria-label="Rate type">
        <option value="">Rate type…</option>
        {(vocab?.rate_types || []).map((k) => <option key={k.value} value={k.value}>{k.label}</option>)}
      </select>
      <input value={form.rate_text || ""} onChange={set("rate_text")} placeholder="Rate (e.g. SOFR + 1.10%)" className={input} />
      <input value={form.collateral_text || ""} onChange={set("collateral_text")} placeholder="Collateral" className={`${input} sm:col-span-2`} />
      <textarea value={form.notes || ""} onChange={set("notes")} placeholder="Notes" rows={2} className={`${input} sm:col-span-2`} />
      {error && <p className="text-sm text-[#9B2335] sm:col-span-2">{error}</p>}
      <div className="flex gap-2 sm:col-span-2">
        <button type="submit" disabled={busy} className={button}>{busy ? "Saving…" : submitLabel}</button>
        <button type="button" onClick={onCancel} className="rounded-[6px] border border-[#E2E8F0] px-4 py-2 text-sm text-[#334155]">Cancel</button>
      </div>
    </form>
  );
}

function LiabilityRow({ l, vocab, writable, onChanged }) {
  const [editing, setEditing] = useState(false);
  const [error, setError] = useState(null);
  async function end() {
    if (!window.confirm(`End "${l.name}"? It stays in the history.`)) return;
    try {
      await call(`/api/liabilities/${encodeURIComponent(l.id)}/end`, { method: "POST", body: "{}" });
      onChanged();
    } catch (err) {
      setError(err.message);
    }
  }
  return (
    <li className="border-b border-[#E2E8F0] py-3 last:border-0">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <span className="text-sm text-[#0F172A]">
          <span className="font-medium">{l.name}</span>
          <span className={quiet}> · {l.kind_label}{l.lender_display ? ` · ${l.lender_display}` : ""}</span>
        </span>
        <span className="font-[Spectral,Georgia,serif] text-[#1B2B4B]">{formatMoney(l.current_balance, l.currency_code)}</span>
      </div>
      <p className="text-xs text-[#64748B]">
        as of {l.as_of}{l.rate_type_label ? ` · ${l.rate_type_label}` : ""}{l.rate_text ? ` ${l.rate_text}` : ""}
        {l.maturity ? ` · matures ${l.maturity}` : ""}{l.collateral_text ? ` · collateral: ${l.collateral_text}` : ""}
        {l.ended ? ` · ended ${String(l.valid_to).slice(0, 10)}` : ""}
      </p>
      {writable && !l.ended && !editing && (
        <div className="mt-1 flex gap-3">
          <button type="button" onClick={() => setEditing(true)} className="text-xs text-[#1B2B4B] hover:underline">Edit</button>
          <button type="button" onClick={end} className="text-xs text-[#9B2335] hover:underline">End</button>
        </div>
      )}
      {editing && (
        <LiabilityForm vocab={vocab} initial={l} submitLabel="Save new version" onCancel={() => setEditing(false)}
          onSubmit={async (form) => {
            await call(`/api/liabilities/${encodeURIComponent(l.id)}`, { method: "PATCH",
              body: JSON.stringify(liabilityPayload(form, { editing: true })) });
            setEditing(false);
            onChanged();
          }} />
      )}
      {error && <p className="text-xs text-[#9B2335]">{error}</p>}
    </li>
  );
}

export default function LiabilitiesTab({ entityId }) {
  const [env, setEnv] = useState(null);
  const [error, setError] = useState(null);
  const [adding, setAdding] = useState(false);
  const [showHistory, setShowHistory] = useState(false);
  async function load() {
    try {
      setEnv(await call(`/api/entities/${encodeURIComponent(entityId)}/liabilities`));
      setError(null);
    } catch (err) {
      setError(err.message);
    }
  }
  useEffect(() => {
    load();
  }, [entityId]); // eslint-disable-line react-hooks/exhaustive-deps
  const vocab = env?.vocabularies;
  const writable = canWrite(env);
  const current = env?.rows?.current || [];
  const history = env?.rows?.history || [];
  return (
    <div className="space-y-6">
      <section className={card}>
        <h2 className="font-[Spectral,Georgia,serif] text-xl text-[#1B2B4B]">Net worth</h2>
        <div className="mt-3"><NetWorthLines netWorth={env?.net_worth} vocabularies={vocab} /></div>
        {vocab?.net_worth_note && <p className={`mt-2 ${quiet}`}>{vocab.net_worth_note}</p>}
      </section>
      <section className={card}>
        <div className="flex items-center justify-between">
          <h2 className="font-[Spectral,Georgia,serif] text-xl text-[#1B2B4B]">Liabilities</h2>
          {writable && !adding && (
            <button type="button" onClick={() => setAdding(true)} className="text-sm text-[#1B2B4B] hover:underline">
              Add liability
            </button>
          )}
        </div>
        {error && <p className="mt-2 text-sm text-[#9B2335]">{error}</p>}
        {env && current.length === 0 && <p className={`mt-3 ${quiet}`}>No liabilities recorded.</p>}
        <ul className="mt-2">{current.map((l) => <LiabilityRow key={l.id} l={l} vocab={vocab} writable={writable} onChanged={load} />)}</ul>
        {adding && writable && (
          <LiabilityForm vocab={vocab} submitLabel="Add liability" onCancel={() => setAdding(false)}
            onSubmit={async (form) => {
              await call(`/api/entities/${encodeURIComponent(entityId)}/liabilities`, { method: "POST",
                body: JSON.stringify(liabilityPayload(form)) });
              setAdding(false);
              load();
            }} />
        )}
        {history.length > 0 && (
          <div className="mt-4">
            <button type="button" className="text-sm text-[#1B2B4B] hover:underline" onClick={() => setShowHistory(!showHistory)}>
              {showHistory ? "Hide" : "Show"} history ({history.length})
            </button>
            {showHistory && <ul>{history.map((l) => <LiabilityRow key={l.id} l={l} vocab={vocab} writable={false} />)}</ul>}
          </div>
        )}
      </section>
    </div>
  );
}
