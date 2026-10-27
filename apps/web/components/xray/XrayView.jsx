"use client";

/**
 * XrayView — the household's "X-ray" tab (REP7): exposure by security and by
 * sector (table + simple bars), each fund with its look-through as-of,
 * overlap between funds and with a followed replica, concentration flags, and
 * the replicable-funds table (fee difference, transition tax, break-even —
 * each an estimate with its inputs and as-of on hover). "Open join request"
 * renders only for a MANAGED account's line the server marked
 * (lib/heldAway.mjs joinControl); a held-away line says "held away — moving
 * the assets comes first" and has no button.
 */

import { useState } from "react";
import {
  barWidth, breakEvenText, estimateText, inputsTitle, joinControl, labelOf, money, pct, runControl, vocab,
} from "@/lib/heldAway.mjs";
import { oneOffXrayControl } from "@/lib/authority.mjs";

const card = "rounded-[6px] border border-[#ece8dd] bg-white";
const secondary = "rounded border border-navy px-3 py-1 text-navy disabled:opacity-50";

async function call(path, body) {
  const res = await fetch(`/api/xray/${path}`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `Request failed (${res.status})`);
  return data;
}

function Bar({ weight }) {
  return (
    <span className="inline-block h-2 w-32 rounded bg-[#F5F1EB] align-middle">
      <span className="block h-2 rounded bg-[#C5A880]" style={{ width: `${barWidth(weight)}%` }} />
    </span>
  );
}

export function Exposure({ envelope }) {
  const x = envelope?.xray || {};
  const sec = Array.isArray(x.by_security) ? x.by_security : [];
  const sectors = Array.isArray(x.by_sector) ? x.by_sector : [];
  return (
    <div className="grid gap-5 md:grid-cols-2">
      <div>
        <h3 className="font-medium text-navy">By security</h3>
        <table className="mt-1 w-full">
          <tbody>{sec.slice(0, 40).map((e) => (
            <tr key={e.key} className="border-t border-[#ece8dd]">
              <td className="py-1" title={`via ${(e.via || []).join(", ")}`}>{e.label}{e.flag ? <span className="ml-1 text-xs text-[#9B2335]">concentrated</span> : null}</td>
              <td title="estimate: weight × the holding's market value">{money(e.exposure)}</td>
              <td>{pct(e.weight)}</td><td><Bar weight={e.weight} /></td>
            </tr>
          ))}</tbody>
        </table>
      </div>
      <div>
        <h3 className="font-medium text-navy">By sector</h3>
        <table className="mt-1 w-full">
          <tbody>{sectors.map((s) => (
            <tr key={s.sector} className="border-t border-[#ece8dd]">
              <td className="py-1">{s.sector}</td><td>{money(s.exposure)}</td><td>{pct(s.weight)}</td><td><Bar weight={s.weight} /></td>
            </tr>
          ))}</tbody>
        </table>
      </div>
    </div>
  );
}

export function Funds({ envelope, onJoin, onOneOff }) {
  const x = envelope?.xray || {};
  const v = vocab(envelope);
  const funds = Array.isArray(x.funds) ? x.funds : [];
  if (!funds.length) return <p className="text-text-muted">No fund in the household is in the Replication Studio registry.</p>;
  return (
    <table className="w-full">
      <thead className="text-left text-text-muted"><tr><th className="py-1">Fund</th><th>Verdict</th><th>Fee difference / yr</th><th>Embedded gain</th><th>Transition tax</th><th>Break-even</th><th /></tr></thead>
      <tbody>{funds.map((f) => (
        <tr key={`${f.account_id}:${f.fund_security_id}`} className="border-t border-[#ece8dd] align-top">
          <td className="py-1">{f.label} · {f.account}<span className="block text-xs text-text-muted">{money(f.market_value)} as of {f.as_of}{f.look_through_as_of ? ` · holdings as of ${f.look_through_as_of}` : " · no look-through"}</span></td>
          <td>{f.viability?.overall ? labelOf(v.viability_verdicts, f.viability.overall) : (f.viability?.note || "—")}
            <span className="block text-xs text-text-muted">{f.model ? f.model.name : (v.xray_status_lines ? labelOf(v.xray_status_lines, "no_model") : "no model yet")}</span></td>
          <td title={inputsTitle(f.fee)}>{estimateText(f.fee?.annual_difference, f.fee?.note)}</td>
          <td title={inputsTitle(f.embedded_gain)}>{estimateText(f.embedded_gain?.amount, f.embedded_gain?.note)}</td>
          <td title={inputsTitle(f.transition_tax)}>{estimateText(f.transition_tax?.amount, f.transition_tax?.note)}</td>
          <td title={f.break_even?.note || ""}>{breakEvenText(f.break_even)}</td>
          <td>{joinControl(envelope, f)
            ? <button type="button" className={secondary} onClick={() => onJoin?.(f)}>Open join request</button>
            : oneOffXrayControl(envelope, f)
              ? <button type="button" className={secondary} title={f.one_off?.reason || ""} onClick={() => onOneOff?.(f)}>Propose a one-off trade</button>
              : (f.held_away ? <span className="text-xs text-text-muted">{v.held_away_note || "held away — moving the assets comes first"}</span> : null)}</td>
        </tr>
      ))}</tbody>
    </table>
  );
}

export default function XrayView({ initialEnvelope }) {
  const [env, setEnv] = useState(initialEnvelope);
  const [msg, setMsg] = useState(null);
  const x = env?.xray;
  if (!x || typeof x !== "object") return null;
  const flags = Array.isArray(x.concentration?.flags) ? x.concentration.flags : [];
  const overlap = Array.isArray(x.overlap) ? x.overlap : [];
  const rov = Array.isArray(x.replica_overlap) ? x.replica_overlap : [];
  const holdings = Array.isArray(x.holdings) ? x.holdings : [];
  const hh = x.household || {};

  async function reload() {
    const res = await fetch(`/api/xray/households/${hh.id}`);
    const data = await res.json().catch(() => ({}));
    if (res.ok) setEnv(data);
  }
  async function join(f) {
    setMsg(null);
    try {
      await call(`households/${hh.id}/join`, { account_id: f.account_id, fund_security_id: f.fund_security_id });
      setMsg("Join request opened — a second person approves it on the account page.");
      await reload();
    } catch (e) { setMsg(e.message); }
  }
  async function oneOff(f) {
    setMsg(null);
    try {
      const out = await call(`households/${hh.id}/one-off`, { account_id: f.account_id, fund_security_id: f.fund_security_id });
      setMsg("Draft one-off proposal made — submit it from the proposal page; a second person approves it.");
      if (out?.proposal?.id) window.location.href = `/portfolio/proposals/${out.proposal.id}`;
    } catch (e) { setMsg(e.message); }
  }
  async function record() {
    setMsg(null);
    try { await call(`households/${hh.id}/runs`, {}); setMsg("Run recorded."); await reload(); } catch (e) { setMsg(e.message); }
  }

  return (
    <div className="space-y-5 text-sm">
      <div className={`${card} p-6`}>
        <h2 className="text-lg font-semibold text-navy">X-ray — {hh.name}</h2>
        <p className="mt-1 text-text-muted">Valued total {money(x.total_market_value)} as of {x.as_of} (estimate){x.unvalued_holdings ? ` · ${x.unvalued_holdings} holding(s) without a market value` : ""}</p>
        <ul className="mt-2 text-xs text-text-muted">{(Array.isArray(x.status_lines) ? x.status_lines : []).map((s, i) => <li key={i}>{s}</li>)}</ul>
        {runControl(env) ? <button type="button" className={`${secondary} mt-2`} onClick={record}>Record a run</button> : null}
      </div>
      <div className={`${card} p-6`}><h2 className="text-lg font-semibold text-navy">Exposure</h2><div className="mt-2"><Exposure envelope={env} /></div></div>
      <div className={`${card} p-6`}>
        <h2 className="text-lg font-semibold text-navy">Concentration</h2>
        {flags.length ? <ul className="mt-1">{flags.map((f) => <li key={f.key} className="text-[#9B2335]">{f.message}</li>)}</ul>
          : <p className="mt-1 text-text-muted">No single name at or above {pct(x.concentration?.threshold)}.</p>}
      </div>
      <div className={`${card} p-6`}>
        <h2 className="text-lg font-semibold text-navy">Overlap</h2>
        {overlap.length ? <ul className="mt-1">{overlap.map((o, i) => <li key={i}>{o.fund_a_label} and {o.fund_b_label}: {o.shared} shared holdings, overlap {pct(o.overlap_weight)}</li>)}</ul>
          : <p className="mt-1 text-text-muted">No two looked-through funds.</p>}
        {rov.length ? <ul className="mt-1">{rov.map((o) => <li key={o.model_id}>With {o.name} (followed): {o.shared} shared, overlap {pct(o.overlap_weight)}</li>)}</ul> : null}
      </div>
      <div className={`${card} p-6`}><h2 className="text-lg font-semibold text-navy">Replicable funds</h2><div className="mt-2"><Funds envelope={env} onJoin={join} onOneOff={oneOff} /></div></div>
      <div className={`${card} p-6`}>
        <h2 className="text-lg font-semibold text-navy">Holdings</h2>
        <table className="mt-2 w-full">
          <thead className="text-left text-text-muted"><tr><th className="py-1">Account</th><th>Holding</th><th>Market value</th><th>As of</th><th>Source</th></tr></thead>
          <tbody>{holdings.map((h, i) => (
            <tr key={i} className="border-t border-[#ece8dd]"><td className="py-1">{h.account}{h.held_away ? " (held away)" : ""}</td><td>{h.label}</td>
              <td>{money(h.market_value)}</td><td>{h.as_of}</td><td>{h.source}</td></tr>
          ))}</tbody>
        </table>
      </div>
      {msg ? <p className="text-[#9B2335]">{msg}</p> : null}
    </div>
  );
}
