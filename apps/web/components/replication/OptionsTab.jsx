"use client";

/**
 * OptionsTab — REP8, the "Options" tab of a Replication Studio entry: the
 * options sleeve of a buffer (defined-outcome) or covered-call / income ETF.
 *
 * Sleeve: the package's legs, outcome period, cap / buffer / floor (computed
 * from the legs, next to the fund's stated values), the minimum account (exact
 * from the multiplier × an adviser's level quote, or "not priced"), the quotes
 * with their age, and the viability note ("manual execution only — … options
 * access unconfirmed"; never a block on the core). Payoff lab: the at-expiry
 * replica payoff against the fund's stated outcome (always available — strikes
 * only), a light chart and an accessible table of the same points; today's
 * mark-to-market and the cap a new package would set only from quotes,
 * labelled estimate; a structured-note slot marked phase 3, empty. Quotes:
 * instrument, kind, value, source, as-of — a new row each time.
 *
 * Every write control renders only from `optionsControls(envelope)` (the
 * server's own flags and actions); every label comes from the envelope's
 * vocabularies. A missing envelope renders no control.
 */

import { useEffect, useState } from "react";
import {
  capRepriceText, chartPaths, labelOf, legText, markText, minimumText, optionsControls, pct, quoteLine, vocab,
} from "@/lib/replicationOptions.mjs";

const card = "rounded-[6px] border border-[#ece8dd] bg-white";
const input = "rounded border border-[#E2E8F0] px-2 py-1";
const primary = "rounded bg-navy px-3 py-1 text-white disabled:opacity-50";
const secondary = "rounded border border-navy px-3 py-1 text-navy disabled:opacity-50";

async function call(method, path, body, base = "replication") {
  const res = await fetch(`/api/${base}/${path}`, {
    method, headers: body ? { "Content-Type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const missing = Array.isArray(data?.detail?.missing) && data.detail.missing.length ? ` (${data.detail.missing.join("; ")})` : "";
    throw new Error(`${data.error || `Request failed (${res.status})`}${missing}`);
  }
  return data;
}

export function PackageSummary({ envelope }) {
  const d = envelope?.current;
  const v = vocab(envelope);
  if (!d) return <p className="text-sm text-text-muted">No options sleeve has been built for this fund yet.</p>;
  const p = d.package || {};
  return (
    <div className={`${card} p-5 text-sm`}>
      <p><span className="font-medium text-navy">Package #{p.package_no}</span> · {labelOf(v.package_kinds, p.kind)} · {labelOf(v.package_statuses, p.status)}
        {p.status_reason ? ` — ${p.status_reason}` : ""}</p>
      <p className="mt-1 text-text-muted">Outcome period {p.outcome_start} → {p.outcome_end} · core: {labelOf(v.core_sources, p.core_source)}
        {" · "}start level {p.start_level ?? "not priced — enter a level quote"}</p>
      {p.kind === "buffer" ? (
        <p className="mt-1">Cap {p.cap_pct != null ? pct(p.cap_pct) : (p.cap_text || "not priced")} (stated {pct(p.stated_cap_pct)})
          {" · "}buffer {pct(p.buffer_pct)} (stated {pct(p.stated_buffer_pct)}) · floor {pct(p.floor_pct)} (worst at expiry)</p>
      ) : (
        <p className="mt-1">Calls: {labelOf(v.call_rules, p.call_rule)}{p.call_otm_pct ? ` ${pct(p.call_otm_pct)}` : ""}
          {p.call_delta ? ` delta ${p.call_delta}` : ""} · tenor {p.tenor_days} days · coverage {pct(p.coverage_pct)}</p>
      )}
      <table className="mt-3 w-full">
        <caption className="sr-only">Legs of the options package</caption>
        <thead className="text-left text-text-muted"><tr><th className="py-1">Leg</th><th>Contract</th></tr></thead>
        <tbody>{(d.legs || []).map((l) => (
          <tr key={l.id} className="border-t border-[#ece8dd] align-top">
            <td className="py-1">{legText(l, envelope)}</td>
            <td className="text-xs text-text-muted">{l.contract ? l.contract.name : (l.role === "underlying" ? "—" : "not yet defined (set the start level)")}</td>
          </tr>
        ))}</tbody>
      </table>
      {Array.isArray(d.needs) && d.needs.length ? <p className="mt-2 text-[#9B2335]">Still needed before approval: {d.needs.join("; ")}.</p> : null}
      <p className="mt-2">Minimum account: {minimumText(d.minimum_account)}</p>
      {d.minimum_account?.mini?.length ? d.minimum_account.mini.map((m, i) => (
        <p key={i} className="text-xs text-text-muted">Mini contract (×{m.multiplier}, {m.mini_ratio} of the level): ${m.minimum_account_cents}</p>
      )) : null}
      <p className="mt-1 text-xs text-text-muted">Basis risk (basket vs index): tracking error {d.basis_risk?.tracking_error}.</p>
    </div>
  );
}

export function QuotesList({ envelope }) {
  const rows = envelope?.current?.quotes || [];
  if (!rows.length) return <p className="text-sm text-text-muted">No quotes entered. Nothing is priced without one.</p>;
  return (
    <ul className="space-y-1 text-sm">{rows.map((q) => <li key={q.id}>{q.security_name}: {quoteLine(q, envelope)}</li>)}</ul>
  );
}

export function PayoffChart({ points }) {
  const g = chartPaths(points);
  if (!g.replica) return null;
  return (
    <svg viewBox={`0 0 ${g.width} ${g.height}`} role="img" aria-label="At-expiry payoff: replica against the fund's stated outcome"
      className="w-full max-w-[560px] bg-[#FAF9F6]">
      <line x1={g.pad} x2={g.width - g.pad} y1={g.zero} y2={g.zero} stroke="#E2E8F0" />
      {g.stated ? <path d={g.stated} fill="none" stroke="#C5A880" strokeWidth="3" /> : null}
      <path d={g.replica} fill="none" stroke="#1B2B4B" strokeWidth="1.5" strokeDasharray="5 3" />
      <text x={g.pad} y={g.pad - 10} fontSize="11" fill="#64748B">Replica (dashed) · fund's stated outcome (gold)</text>
    </svg>
  );
}

export function PayoffLabView({ payoff }) {
  if (!payoff) return <p className="text-sm text-text-muted">Build a package to see its payoff.</p>;
  const pts = Array.isArray(payoff.points) ? payoff.points : [];
  return (
    <div className={`${card} space-y-3 p-5 text-sm`}>
      <h3 className="text-base font-semibold text-navy">Payoff lab</h3>
      <p className="text-xs text-text-muted">{payoff.at_expiry_note}</p>
      <PayoffChart points={pts} />
      <table className="w-full">
        <caption className="sr-only">At-expiry payoff points</caption>
        <thead className="text-left text-text-muted"><tr><th className="py-1">Underlying return</th><th>Replica</th><th>Fund's stated outcome</th><th>Difference</th></tr></thead>
        <tbody>{pts.map((p) => (
          <tr key={p.underlying_return} className="border-t border-[#ece8dd]">
            <td className="py-1">{pct(p.underlying_return)}</td><td>{pct(p.replica_return)}</td>
            <td>{p.stated_return == null ? "—" : pct(p.stated_return)}</td><td>{p.difference == null ? "—" : pct(p.difference)}</td>
          </tr>
        ))}</tbody>
      </table>
      <p>Today's mark-to-market: {markText(payoff.mark_to_market)}</p>
      {(payoff.mark_to_market?.lines || []).filter((l) => l.quote).map((l) => (
        <p key={l.role} className="text-xs text-text-muted">{l.role}: {l.value} — premium {l.quote.value}, {l.quote.source}, as of {String(l.quote.as_of).slice(0, 16).replace("T", " ")} UTC ({l.quote.age})</p>
      ))}
      <p>Cap a new package would set: {capRepriceText(payoff.reprice)}</p>
      <div className="rounded-[6px] border border-dashed border-[#ece8dd] p-3 text-xs text-text-muted">
        {payoff.note_comparison?.label} — {payoff.note_comparison?.status}
      </div>
    </div>
  );
}

export function OptionsSleeveView({ envelope, payoff, onAct, busy }) {
  const c = optionsControls(envelope);
  const v = vocab(envelope);
  const [q, setQ] = useState({ global_security_id: "", kind: "", value: "", source: "", as_of: "" });
  const [price, setPrice] = useState({ start_level_quote_id: "", cap_strike_pct: "", call_strike_pct: "" });
  const [note, setNote] = useState("");
  const [acct, setAcct] = useState("");
  const [und, setUnd] = useState("");
  if (!envelope) return null;
  if (envelope.is_options_class !== true) {
    return <p className="text-sm text-text-muted">This fund's instrument class has no options sleeve.</p>;
  }
  const d = envelope.current;
  const pkgId = d?.package?.id;
  const instruments = [d?.underlying, ...(d?.legs || []).map((l) => l.contract && { id: l.contract.global_security_id, name: l.contract.name })]
    .filter(Boolean);
  return (
    <div className="space-y-4">
      {envelope.viability ? <p className={`${card} p-4 text-sm`}>{envelope.viability.note}</p> : null}
      {Array.isArray(envelope.terms?.missing) && envelope.terms.missing.length ? (
        <p className="text-sm text-[#9B2335]">Terms still needed: {envelope.terms.missing.join("; ")}.</p>
      ) : null}
      {c.build ? (
        <div className={`${card} flex flex-wrap items-center gap-2 p-4 text-sm`}>
          <input className={input} placeholder="Underlying security id (the index or ETF the options settle on)" value={und}
            onChange={(e) => setUnd(e.target.value)} />
          <button type="button" className={primary} disabled={busy || !und}
            onClick={() => onAct("build", null, { underlying_global_security_id: und })}>Build the options sleeve</button>
        </div>
      ) : null}
      <PackageSummary envelope={envelope} />
      {c.price ? (
        <div className={`${card} flex flex-wrap items-center gap-2 p-4 text-sm`}>
          <select className={input} value={price.start_level_quote_id} onChange={(e) => setPrice({ ...price, start_level_quote_id: e.target.value })}>
            <option value="">Start level quote…</option>
            {(d?.quotes || []).filter((x) => x.kind === "index_level" || x.kind === "underlying_price").map((x) => <option key={x.id} value={x.id}>{quoteLine(x, envelope)}</option>)}
          </select>
          {d?.package?.kind === "buffer" ? <input className={input} placeholder="Cap call strike, % of start" value={price.cap_strike_pct} onChange={(e) => setPrice({ ...price, cap_strike_pct: e.target.value })} /> : null}
          {d?.package?.kind === "income" ? <input className={input} placeholder="Call strike, % of start" value={price.call_strike_pct} onChange={(e) => setPrice({ ...price, call_strike_pct: e.target.value })} /> : null}
          <button type="button" className={secondary} disabled={busy} onClick={() => onAct("price", pkgId, Object.fromEntries(Object.entries(price).filter(([, x]) => x)))}>Price the draft</button>
        </div>
      ) : null}
      <div className="flex flex-wrap items-center gap-2">
        {c.submit ? <button type="button" className={primary} disabled={busy} onClick={() => onAct("submit", pkgId)}>Submit for approval</button> : null}
        {c.approve || c.reject ? <input className={input} placeholder="Note (required to reject)" value={note} onChange={(e) => setNote(e.target.value)} /> : null}
        {c.approve ? <button type="button" className={primary} disabled={busy} onClick={() => onAct("approve", pkgId, { note: note || undefined })}>Approve package</button> : null}
        {c.reject ? <button type="button" className={secondary} disabled={busy} onClick={() => onAct("reject", pkgId, { note })}>Reject</button> : null}
      </div>
      {c.propose ? (
        <div className={`${card} flex flex-wrap items-center gap-2 p-4 text-sm`}>
          <select className={input} value={acct} onChange={(e) => setAcct(e.target.value)}>
            <option value="">Account following this model…</option>
            {envelope.accounts_following.map((a) => <option key={a.id} value={a.id}>{a.account_number_masked}</option>)}
          </select>
          <button type="button" className={secondary} disabled={busy || !acct} onClick={() => onAct("propose", acct, { package_id: pkgId, action: "open" })}>{labelOf(v.option_actions, "open")}</button>
          {c.roll ? <button type="button" className={secondary} disabled={busy || !acct} onClick={() => onAct("propose", acct, { package_id: pkgId, action: "roll" })}>{labelOf(v.option_actions, "roll")}</button> : null}
        </div>
      ) : null}
      <div className={`${card} p-5`}>
        <h3 className="text-base font-semibold text-navy">Quotes</h3>
        <p className="text-xs text-text-muted">Adviser-entered only — nothing is fetched or defaulted. A new quote is a new row.</p>
        <div className="mt-2"><QuotesList envelope={envelope} /></div>
        {c.enterQuote ? (
          <div className="mt-3 flex flex-wrap items-center gap-2 text-sm">
            <select className={input} value={q.global_security_id} onChange={(e) => setQ({ ...q, global_security_id: e.target.value })}>
              <option value="">Instrument…</option>
              {instruments.map((x) => <option key={x.id} value={x.id}>{x.name}</option>)}
            </select>
            <select className={input} value={q.kind} onChange={(e) => setQ({ ...q, kind: e.target.value })}>
              <option value="">Kind…</option>
              {(v.quote_kinds || []).map((k) => <option key={k.key} value={k.key}>{k.label}</option>)}
            </select>
            <input className={input} placeholder="Value" value={q.value} onChange={(e) => setQ({ ...q, value: e.target.value })} />
            <input className={input} placeholder="Source" value={q.source} onChange={(e) => setQ({ ...q, source: e.target.value })} />
            <input className={input} type="datetime-local" value={q.as_of} onChange={(e) => setQ({ ...q, as_of: e.target.value })} />
            <button type="button" className={secondary} disabled={busy || !q.global_security_id || !q.kind || !q.value || !q.source || !q.as_of}
              onClick={() => onAct("quote", null, { ...q, as_of: new Date(q.as_of).toISOString() })}>Enter quote</button>
          </div>
        ) : null}
      </div>
      <PayoffLabView payoff={payoff} />
    </div>
  );
}

export default function OptionsTab({ entryId }) {
  const [env, setEnv] = useState(null);
  const [payoff, setPayoff] = useState(null);
  const [msg, setMsg] = useState(null);
  const [busy, setBusy] = useState(false);

  async function load() {
    try {
      const data = await call("GET", `options/entries/${entryId}`);
      setEnv(data);
      const pid = data?.current?.package?.id;
      setPayoff(pid ? await call("GET", `options/packages/${pid}/payoff`) : null);
    } catch (err) { setMsg(err.message); }
  }

  useEffect(() => { if (entryId) load(); }, [entryId]); // eslint-disable-line react-hooks/exhaustive-deps

  async function onAct(action, id, body) {
    setMsg(null);
    setBusy(true);
    try {
      if (action === "build") {
        await call("POST", `options/entries/${entryId}/build`, body);
      } else if (action === "quote") {
        await call("POST", "options/quotes", body);
      } else if (action === "propose") {
        const res = await call("POST", `options/accounts/${id}/proposals`, body);
        if (res?.proposal?.id) window.location.href = `/portfolio/proposals/${res.proposal.id}`;
      } else {
        await call("POST", `options/packages/${id}/${action}`, body || {});
      }
      await load();
    } catch (err) { setMsg(err.message); } finally { setBusy(false); }
  }

  return (
    <div className="space-y-3">
      {msg ? <p className="text-sm text-[#9B2335]">{msg}</p> : null}
      {env ? <OptionsSleeveView envelope={env} payoff={payoff} onAct={onAct} busy={busy} /> : <p className="text-sm text-text-muted">Loading…</p>}
    </div>
  );
}
