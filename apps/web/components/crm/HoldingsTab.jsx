"use client";

// PORTENTRY1 Task 5 — the record's direct assets (real estate, collectibles, operating businesses, insurance cash
// value, other) with their valuation history, and its private investments (fund / direct deal / SPV / SAFE / note)
// with the commitment, called, distributed, unfunded (computed by the API) and latest NAV. Kinds, methods and flow
// types come from the API envelope; money arrives and leaves as strings. Write controls only when the envelope
// says so (lib/portentry.mjs canDo — no truthy fallback).
import { useEffect, useState } from "react";
import {
  canDo, commitmentRule, directAssetPayload, directEditForm, directEditPayload, directEndPayload, directRowControls, money,
  options, privateEditPayload, privatePayload,
} from "@/lib/portentry.mjs";
import { BusyButton, BusyError, useBusy } from "@/components/ui/Busy";
import { privateRowControls } from "@/lib/bugfix4.mjs";
import { listView } from "@/lib/listState.mjs";

const card = "rounded-[6px] border border-[#ece8dd] bg-white p-6";
const input = "w-full rounded-[6px] border border-[#E2E8F0] bg-white px-3 py-2 text-sm text-[#0F172A]";
const button = "rounded-[6px] bg-[#1B2B4B] px-4 py-2 text-sm text-white disabled:opacity-50";
const quiet = "text-sm text-[#64748B]";
const label = "block text-xs font-medium uppercase tracking-wide text-[#64748B]";

async function call(url, options_ = {}) {
  const res = await fetch(url, { cache: "no-store", headers: { "Content-Type": "application/json" }, ...options_ });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(typeof data.error === "string" ? data.error : `Request failed (${res.status})`);
  return data;
}

function Field({ name, text, form, setForm, type = "text" }) {
  return (
    <div>
      <label className={label}>{text}</label>
      <input type={type} className={input} value={form[name] || ""} onChange={(e) => setForm({ ...form, [name]: e.target.value })} />
    </div>
  );
}

function Choice({ name, text, list, form, setForm }) {
  return (
    <div>
      <label className={label}>{text}</label>
      <select className={input} value={form[name] || ""} onChange={(e) => setForm({ ...form, [name]: e.target.value })}>
        <option value="">Select…</option>
        {list.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
      </select>
    </div>
  );
}

export default function HoldingsTab({ entityId }) {
  const base = `/api/portentry/entities/${encodeURIComponent(entityId)}`;
  const [assets, setAssets] = useState(null);
  const [priv, setPriv] = useState(null);
  const [error, setError] = useState(null);
  const [adding, setAdding] = useState(null);
  const [form, setForm] = useState({});
  const save = useBusy();   // CATCHALL1 busy — the shared indicator
  const busy = save.busy;
  const [history, setHistory] = useState(null);   // CATCHALL1 — {asset_id, data} of the open History view

  async function load() {
    try {
      const [a, p] = await Promise.all([call(`${base}/direct-assets`), call(`${base}/private-investments`)]);
      setAssets(a);
      setPriv(p);
      setError(null);
    } catch (e) {
      setError(e.message);
    }
  }
  useEffect(() => { load(); }, [entityId]); // eslint-disable-line react-hooks/exhaustive-deps

  async function showHistory(assetId) {
    if (history?.asset_id === assetId) { setHistory(null); return; }
    try {
      setHistory({ asset_id: assetId, data: await call(`/api/portentry/direct-assets/${assetId}/history`) });
    } catch (err) { setError(err.message); }
  }

  async function submit(e) {
    e.preventDefault();
    await save.run(async () => {
      if (adding === "asset") await call(`${base}/direct-assets`, { method: "POST", body: JSON.stringify(directAssetPayload(form)) });
      if (adding === "private") await call(`${base}/private-investments`, { method: "POST", body: JSON.stringify(privatePayload(form, commitmentRule(priv, form.kind))) });
      if (adding?.startsWith("value:")) {
        await call(`/api/portentry/direct-assets/${adding.slice(6)}/valuations`, { method: "POST",
          body: JSON.stringify({ value: form.value, date: form.valuation_date, method: form.method, source: form.source || undefined }) });
      }
      if (adding?.startsWith("flow:")) {
        await call(`/api/portentry/private-investments/${adding.slice(5)}/flows`, { method: "POST",
          body: JSON.stringify({ type: form.flow_type, amount: form.amount, date: form.flow_date }) });
      }
      if (adding?.startsWith("pnav:")) {
        // BUGFIX3 — a no-commitment investment's new value: a NAV valuation on its asset (no commitment id exists)
        await call(`/api/portentry/direct-assets/${adding.slice(5)}/valuations`, { method: "POST",
          body: JSON.stringify({ value: form.nav, date: form.nav_date, method: "nav" }) });
      }
      if (adding?.startsWith("edit:")) {
        // BUGFIX4 — a new version of the private investment (notes / dates / vintage); the prior one is kept
        const row = (priv?.rows || []).find((r) => r.asset_id === adding.slice(5));
        await call(`/api/portentry/private-investments/assets/${adding.slice(5)}`, { method: "PATCH",
          body: JSON.stringify(privateEditPayload(form, row)) });
      }
      if (adding?.startsWith("dedit:")) {
        // CATCHALL1 — a new version of the direct asset (name / kind / notes) and / or a new valuation
        const row = (assets?.rows || []).find((r) => r.asset_id === adding.slice(6));
        const body = directEditPayload(form, row);
        if (!Object.keys(body).length) throw new Error("Nothing changed");
        await call(`/api/portentry/direct-assets/${adding.slice(6)}`, { method: "PATCH", body: JSON.stringify(body) });
      }
      if (adding?.startsWith("dend:")) {
        await call(`/api/portentry/direct-assets/${adding.slice(5)}/end`, { method: "POST",
          body: JSON.stringify(directEndPayload(form)) });
      }
      if (adding?.startsWith("nav:")) {
        await call(`/api/portentry/private-investments/${adding.slice(4)}/nav`, { method: "POST",
          body: JSON.stringify({ value: form.nav, date: form.nav_date }) });
      }
      setAdding(null);
      setForm({});
      setHistory(null);
      await load();
    });
  }

  // BUGFIX3 (1e) — null = still loading: say so, never the empty sentence first
  const viewA = listView(assets);
  const viewP = listView(priv);
  const rowsA = viewA.rows;
  const rowsP = viewP.rows;
  const rule = commitmentRule(priv, form.kind);
  const methods = options(assets, "valuation_methods");
  const form_ = adding && (
    <form onSubmit={submit} className="mt-4 grid gap-3 sm:grid-cols-2">
      {adding === "asset" && (
        <>
          <Choice name="kind" text="Kind" list={options(assets, "direct_asset_kinds")} form={form} setForm={setForm} />
          <Field name="name" text="Name" form={form} setForm={setForm} />
          <Field name="taxonomy_key" text="Asset class (taxonomy key)" form={form} setForm={setForm} />
          <Field name="sub_class" text="Sub-class" form={form} setForm={setForm} />
          <Field name="ownership_pct" text="Ownership %" form={form} setForm={setForm} />
          <Field name="cost_basis" text="Cost basis" form={form} setForm={setForm} />
        </>
      )}
      {(adding === "asset" || adding.startsWith("value:")) && (
        <>
          <Field name="value" text="Value" form={form} setForm={setForm} />
          <Field name="valuation_date" text="Valuation date" type="date" form={form} setForm={setForm} />
          <Choice name="method" text="Method" list={methods} form={form} setForm={setForm} />
          <Field name="source" text="Source" form={form} setForm={setForm} />
        </>
      )}
      {adding === "asset" && <div className="sm:col-span-2"><Field name="notes" text="Notes" form={form} setForm={setForm} /></div>}
      {adding === "private" && (
        <>
          <Choice name="kind" text="Kind" list={options(priv, "private_kinds")} form={form} setForm={setForm} />
          <Field name="name" text="Name" form={form} setForm={setForm} />
          {/* BUGFIX3 — commitment fields only for a kind that has one (fund required, SPV optional) */}
          {rule !== "none" && (
            <>
              <Field name="amount" text={rule === "optional" ? "Commitment (optional)" : "Commitment"} form={form} setForm={setForm} />
              <Field name="commitment_date" text="Commitment date" type="date" form={form} setForm={setForm} />
              <Field name="vintage_year" text="Vintage" form={form} setForm={setForm} />
              <Field name="called_to_date" text="Called to date" form={form} setForm={setForm} />
              <Field name="distributed_to_date" text="Distributed to date" form={form} setForm={setForm} />
            </>
          )}
          {(rule === "none" || (rule === "optional" && !(form.amount || "").trim())) && (
            <>
              <Field name="amount_invested" text="Amount invested" form={form} setForm={setForm} />
              {/* BUGFIX4 — a simplified kind's own vintage and investment date */}
              <Field name="investment_date" text="Investment date" type="date" form={form} setForm={setForm} />
              <Field name="vintage_year" text="Vintage" form={form} setForm={setForm} />
            </>
          )}
          <Field name="taxonomy_key" text="Asset class (taxonomy key)" form={form} setForm={setForm} />
          <div className="sm:col-span-2"><Field name="notes" text="Note" form={form} setForm={setForm} /></div>
        </>
      )}
      {(adding === "private" || adding.startsWith("nav:") || adding.startsWith("pnav:")) && (
        <>
          <Field name="nav" text={adding === "private" && rule === "none" ? "Latest value" : "Latest NAV"} form={form} setForm={setForm} />
          <Field name="nav_date" text="NAV date" type="date" form={form} setForm={setForm} />
        </>
      )}
      {adding.startsWith("edit:") && (() => {
        const row = (priv?.rows || []).find((r) => r.asset_id === adding.slice(5)) || {};
        return (
          <>
            {row.has_commitment === false
              ? <Field name="investment_date" text="Investment date" type="date" form={form} setForm={setForm} />
              : <Field name="commitment_date" text="Commitment date" type="date" form={form} setForm={setForm} />}
            <Field name="vintage_year" text="Vintage" form={form} setForm={setForm} />
            <div className="sm:col-span-2"><Field name="notes" text="Note" form={form} setForm={setForm} /></div>
          </>
        );
      })()}
      {adding.startsWith("dedit:") && (
        <>
          <Field name="name" text="Name" form={form} setForm={setForm} />
          <Choice name="kind" text="Kind" list={options(assets, "direct_asset_kinds")} form={form} setForm={setForm} />
          <Field name="value" text="Value" form={form} setForm={setForm} />
          <Field name="as_of" text="As of" type="date" form={form} setForm={setForm} />
          <Choice name="method" text="Valuation method" list={methods} form={form} setForm={setForm} />
          <div className="sm:col-span-2"><Field name="notes" text="Notes" form={form} setForm={setForm} /></div>
          <p className={`${quiet} sm:col-span-2`}>Saved as a new version; the prior one stays in History.</p>
        </>
      )}
      {adding.startsWith("dend:") && (
        <>
          <Choice name="end_reason" text="Reason" list={options(assets, "end_reasons")} form={form} setForm={setForm} />
          {form.end_reason !== "entered_in_error" && (
            <>
              <Field name="end_date" text="End date" type="date" form={form} setForm={setForm} />
              <Field name="proceeds" text="Proceeds (optional)" form={form} setForm={setForm} />
            </>
          )}
          <div className="sm:col-span-2"><Field name="end_notes" text="Note" form={form} setForm={setForm} /></div>
          <p className={`${quiet} sm:col-span-2`}>{form.end_reason === "entered_in_error"
            ? "Ends it from its start: it will never have counted in net worth. Its history is kept."
            : "It stops counting in net worth from the end date. Nothing is deleted; its history is kept."}</p>
        </>
      )}
      {adding.startsWith("flow:") && (
        <>
          <Choice name="flow_type" text="Type" list={options(priv, "flow_types")} form={form} setForm={setForm} />
          <Field name="amount" text="Amount" form={form} setForm={setForm} />
          <Field name="flow_date" text="Date" type="date" form={form} setForm={setForm} />
        </>
      )}
      <div className="flex gap-2 sm:col-span-2">
        <BusyButton type="submit" busy={busy} busyLabel="Saving…" className={button}>Save</BusyButton>
        <button type="button" className="text-sm text-[#64748B]" onClick={() => { setAdding(null); setForm({}); }}>Cancel</button>
        <BusyError error={save.error} />
      </div>
    </form>
  );

  return (
    <div className="space-y-6">
      {error && <p className="text-sm text-[#9B2335]">{error}</p>}
      <section className={card}>
        <div className="flex items-center justify-between">
          <h2 className="font-[Spectral,Georgia,serif] text-xl text-[#1B2B4B]">Direct assets</h2>
          {canDo(assets, "add") && !adding && <button type="button" className="text-sm text-[#1B2B4B] hover:underline" onClick={() => setAdding("asset")}>Add asset</button>}
        </div>
        {adding && (adding === "asset" || adding.startsWith("value:") || adding.startsWith("dedit:") || adding.startsWith("dend:")) && form_}
        {viewA.state === "loading" ? <p className={`mt-3 ${quiet}`}>Loading…</p> : rowsA.length === 0 ? <p className={`mt-3 ${quiet}`}>No direct assets.</p> : (
          <ul className="mt-3 divide-y divide-[#E2E8F0]">
            {rowsA.map((a) => (
              <li key={a.position_id} className="py-3 text-sm">
                <div className="flex justify-between">
                  <span className="text-[#0F172A]">{a.name} <span className={quiet}>· {options(assets, "direct_asset_kinds").find((k) => k.value === a.kind)?.label || a.kind} · {a.ownership_pct}%</span></span>
                  <span className="text-[#1B2B4B]">{money(a.owner_value)}</span>
                </div>
                <p className={quiet}>Asset value {money(a.asset_value)} · cost basis {money(a.cost_basis)}</p>
                <details className="mt-1"><summary className={`${quiet} cursor-pointer`}>Valuation history ({a.valuations.length})</summary>
                  <ul className="mt-1">{a.valuations.map((v) => <li key={v.id} className={quiet}>{v.date} · {money(v.value)} · {methods.find((m) => m.value === v.method)?.label || v.method}{v.source ? ` · ${v.source}` : ""}</li>)}</ul>
                </details>
                {a.ended && (
                  <p className="text-xs text-[#64748B]" data-ended="1">Ended {a.ended.end_date} · {a.ended.reason_label}
                    {a.ended.proceeds ? ` · proceeds ${money(a.ended.proceeds)}` : ""}
                    {a.ended.counting ? " · counts until then" : " · no longer counted in net worth"}</p>
                )}
                {(() => {
                  const ctl = directRowControls(assets, a);   // CATCHALL1 — none for a member (fails closed)
                  if (adding) return null;
                  return (
                    <div className="mt-1 flex gap-3 text-xs" data-direct-controls="1">
                      {ctl.revalue && <button type="button" className="text-[#1B2B4B] hover:underline" onClick={() => setAdding(`value:${a.asset_id}`)}>New valuation</button>}
                      {ctl.edit && <button type="button" className="text-[#1B2B4B] hover:underline" onClick={() => { setForm(directEditForm(a)); setAdding(`dedit:${a.asset_id}`); }}>Edit</button>}
                      {ctl.end && <button type="button" className="text-[#1B2B4B] hover:underline" onClick={() => { setForm({}); setAdding(`dend:${a.asset_id}`); }}>End</button>}
                      {ctl.history && <button type="button" className="text-[#1B2B4B] hover:underline" onClick={() => showHistory(a.asset_id)}>{history?.asset_id === a.asset_id ? "Hide history" : "History"}</button>}
                    </div>
                  );
                })()}
                {history?.asset_id === a.asset_id && <DirectHistory data={history.data} methods={methods} />}
              </li>
            ))}
          </ul>
        )}
      </section>
      <section className={card}>
        <div className="flex items-center justify-between">
          <h2 className="font-[Spectral,Georgia,serif] text-xl text-[#1B2B4B]">Private investments</h2>
          {canDo(priv, "add") && !adding && <button type="button" className="text-sm text-[#1B2B4B] hover:underline" onClick={() => setAdding("private")}>Add private investment</button>}
        </div>
        {adding && (adding === "private" || adding.startsWith("flow:") || adding.startsWith("nav:") || adding.startsWith("pnav:") || adding.startsWith("edit:")) && form_}
        {viewP.state === "loading" ? <p className={`mt-3 ${quiet}`}>Loading…</p> : rowsP.length === 0 ? <p className={`mt-3 ${quiet}`}>No private investments.</p> : (
          <ul className="mt-3 divide-y divide-[#E2E8F0]">
            {rowsP.map((p) => (
              <li key={p.commitment_id || p.position_id} className="py-3 text-sm">
                <div className="flex justify-between">
                  <span className="text-[#0F172A]">{p.name} <span className={quiet}>· {options(priv, "private_kinds").find((k) => k.value === p.kind)?.label || p.kind}{p.vintage_year ? ` · ${p.vintage_year}` : ""}</span></span>
                  <span className="text-[#1B2B4B]">NAV {money(p.nav)}</span>
                </div>
                {p.has_commitment === false ? (
                  // BUGFIX3 — no commitment by design: amount invested + latest value; unfunded is not computed
                  <p className={quiet}>Amount invested {money(p.amount_invested)} · unfunded —</p>
                ) : (
                  <p className={quiet}>Commitment {money(p.commitment_amount)} · called {money(p.called_to_date)} · distributed {money(p.distributed_to_date)} · unfunded {money(p.unfunded)}</p>
                )}
                {p.notes && <p className="text-xs text-[#334155]">{p.notes}</p>}
                {(p.commitment_date || p.investment_date) && <p className={quiet}>{p.has_commitment === false ? `Invested ${p.investment_date}` : `Committed ${p.commitment_date}`}</p>}
                <span className="mt-1 flex gap-3">
                  {/* BUGFIX4 — the commitment opens its capital-call projection; a saved row can be edited */}
                  {privateRowControls(priv, p).projection && <a className="text-xs text-[#1B2B4B] underline" href={p.projection_path}>Capital-call projection</a>}
                  {privateRowControls(priv, p).edit && !adding && (
                    <button type="button" className="text-xs text-[#1B2B4B] hover:underline" onClick={() => {
                      setForm({ notes: p.notes || "", vintage_year: p.vintage_year ? String(p.vintage_year) : "", commitment_date: p.commitment_date || "", investment_date: p.investment_date || "" });
                      setAdding(`edit:${p.asset_id}`);
                    }}>Edit</button>
                  )}
                </span>
                {p.has_commitment === false && canDo(priv, "nav") && !adding && (
                  <button type="button" className="mt-1 text-xs text-[#1B2B4B] hover:underline" onClick={() => setAdding(`pnav:${p.asset_id}`)}>New value</button>
                )}
                {p.has_commitment !== false && canDo(priv, "flows") && !adding && (
                  <span className="mt-1 flex gap-3">
                    <button type="button" className="text-xs text-[#1B2B4B] hover:underline" onClick={() => setAdding(`flow:${p.commitment_id}`)}>Record call / distribution</button>
                    {canDo(priv, "nav") && <button type="button" className="text-xs text-[#1B2B4B] hover:underline" onClick={() => setAdding(`nav:${p.commitment_id}`)}>New NAV</button>}
                  </span>
                )}
              </li>
            ))}
          </ul>
        )}
        {priv?.vocabularies?.unfunded_formula && <p className={`mt-2 ${quiet}`}>Unfunded = {priv.vocabularies.unfunded_formula}</p>}
      </section>
    </div>
  );
}

// CATCHALL1 — a direct asset's History: its versions (current first), every valuation and its end
export function DirectHistory({ data, methods = [] }) {
  const v = Array.isArray(data?.versions) ? data.versions : [];
  const vals = Array.isArray(data?.valuations) ? data.valuations : [];
  const ends = Array.isArray(data?.ends) ? data.ends : [];
  return (
    <div className="mt-2 rounded-[6px] border border-[#ece8dd] bg-[#FAF9F6] p-3 text-xs text-[#334155]" data-history="1">
      <p className="font-medium text-[#1B2B4B]">Versions</p>
      <ul>{v.map((x) => <li key={x.id} data-version={x.current ? "current" : "prior"}>{x.current ? "Current" : `Until ${String(x.system_to).slice(0, 10)}`} · {x.name} · {x.kind_label}{x.notes ? ` · ${x.notes}` : ""}</li>)}</ul>
      <p className="mt-2 font-medium text-[#1B2B4B]">Valuations</p>
      <ul>{vals.map((x) => <li key={x.id}>{x.date} · {money(x.value)} · {methods.find((m) => m.value === x.method)?.label || x.method}</li>)}</ul>
      {ends.length > 0 && <p className="mt-2 font-medium text-[#1B2B4B]">End</p>}
      <ul>{ends.map((x) => <li key={x.id}>{x.end_date} · {x.reason_label}{x.proceeds ? ` · proceeds ${money(x.proceeds)}` : ""}{x.notes ? ` · ${x.notes}` : ""}</li>)}</ul>
    </div>
  );
}
