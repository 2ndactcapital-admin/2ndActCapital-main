"use client";

/**
 * ProposalDetail — one rebalance proposal (REP5): its trades with reasons,
 * lots, prices (source and as-of), before / target / after weights and drift,
 * estimated short- and long-term gain, warnings, each trade's household
 * wash-sale result (REP6: clear / replaced / deferred / blocked, with the
 * matches; a deferred or blocked line is never routed — a pre-rep6 proposal
 * still shows its flag), the approval task, and the execution instructions
 * an approved proposal created. Submit / Approve / Reject / Execute render only
 * from `proposalControls(envelope)`; Approve / Reject never for the maker.
 */

import { useEffect, useState } from "react";
import { amountText, labelOf, moneyText, pctText, proposalControls, securityText, vocab, weightRows } from "@/lib/proposals.mjs";
import { matchesOf, washText } from "@/lib/tax.mjs";
import { saleMethodText } from "@/lib/lotMethod.mjs";
import ProposalLotMethod from "@/components/lotmethod/ProposalLotMethod";
import { proposalStale } from "@/lib/heldAway.mjs";
import { TaxGatePanelView } from "@/components/replication/OptionsTaxGate";
import { optionsApproveVisible } from "@/lib/replicationOptions.mjs";

const card = "rounded-[6px] border border-[#ece8dd] bg-white";
const input = "rounded border border-[#E2E8F0] px-3 py-2";
const primary = "rounded bg-navy px-3 py-1 text-white disabled:opacity-50";
const secondary = "rounded border border-navy px-3 py-1 text-navy disabled:opacity-50";

async function call(path, body) {
  const res = await fetch(`/api/proposals/${path}`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `Request failed (${res.status})`);
  return data;
}

export function Trades({ envelope }) {
  const p = envelope?.proposal || {};
  const v = vocab(envelope);
  const trades = Array.isArray(p.trades) ? p.trades : [];
  if (!trades.length) return <p className="text-sm text-text-muted">No trades.</p>;
  return (
    <table className="w-full text-sm">
      <thead className="text-left text-text-muted"><tr><th className="py-1">Trade</th><th>Price</th><th>Est. gain</th><th>Why</th></tr></thead>
      <tbody>{trades.map((t) => (
        <tr key={t.id} className="border-t border-[#ece8dd] align-top">
          <td className="py-2">{t.side === "buy" ? "Buy" : "Sell"} {amountText(t)}{t.option_leg ? " contracts" : ""} {securityText(t)}
            {t.option_leg ? <span className="block text-xs text-navy">{labelOf(v.leg_roles, t.option_leg.role)} — to {t.option_leg.position_effect}, strike {t.option_leg.strike}, expires {t.option_leg.expiry}{t.option_leg.is_flex ? " (FLEX)" : ""}, ×{t.option_leg.multiplier}</span> : null}
            {saleMethodText(t, envelope) ? <span className="block text-xs text-navy">Lots: {saleMethodText(t, envelope)}</span> : null}
            {(t.lots || []).map((l, i) => (
              <span key={l.lot_id || `avg-${i}`} className="block text-xs text-text-muted">lot {String(l.acquired_date || "—")}: {l.quantity} sh, cost {l.unit_cost ?? "—"}, {l.term}, est. {l.est_gain ?? "—"}{l.why ? ` — ${l.why}` : ""}</span>
            ))}
            {t.instruction_id ? <a className="block text-xs text-navy underline" href={`/portfolio/instructions/${t.instruction_id}`}>Instruction</a> : null}
          </td>
          <td title={t.price != null ? `${labelOf(v.price_sources, t.price_source)}${t.price_as_of ? `, as of ${t.price_as_of}` : ""}` : "not priced"}>{t.price ?? "—"}<span className="block text-xs text-text-muted">{labelOf(v.price_sources, t.price_source)}{t.price_as_of ? `, as of ${t.price_as_of}` : ""}</span></td>
          <td>{t.side === "sell" ? `ST ${t.est_st_gain} / LT ${t.est_lt_gain}` : "—"}</td>
          <td>{t.reason}
            {t.wash_sale_flag ? <span className="mt-1 block text-xs text-[#9B2335]">Wash-sale flag: {t.wash_sale_flag.note}</span> : null}
            {t.wash_sale ? (
              <span className={`mt-1 block text-xs ${t.wash_sale.outcome === "clear" ? "text-text-muted" : "text-[#9B2335]"}`}>
                Wash sale: {washText(t.wash_sale, v)}{t.wash_sale.outcome !== "clear" ? ` — ${t.wash_sale.reason}` : ""}
                {matchesOf(t.wash_sale).map((m, i) => <span key={i} className="block">· {m}</span>)}
              </span>
            ) : null}</td>
        </tr>
      ))}</tbody>
    </table>
  );
}

export function Weights({ envelope }) {
  const p = envelope?.proposal || {};
  const rows = weightRows(p);
  return (
    <div className="text-sm">
      <p className="text-text-muted">Drift {pctText(p.drift_before)} → {pctText(p.drift_after)} · account value {moneyText(p.account_value)} as of {p.valuation_as_of || "—"}</p>
      <table className="mt-2 w-full">
        <thead className="text-left text-text-muted"><tr><th className="py-1">Position</th><th>Before</th><th>Target</th><th>After</th></tr></thead>
        <tbody>{rows.map((r) => (
          <tr key={r.key} className="border-t border-[#ece8dd]"><td className="py-1">{r.label}</td>
            <td>{pctText(r.before)}</td><td>{pctText(r.target)}</td><td>{pctText(r.after)}</td></tr>
        ))}</tbody>
      </table>
    </div>
  );
}

export default function ProposalDetail({ initialEnvelope }) {
  const [env, setEnv] = useState(initialEnvelope);
  const [note, setNote] = useState("");
  const [msg, setMsg] = useState(null);
  const p = env?.proposal || {};
  const v = vocab(env);
  const c = proposalControls(env);
  const tasks = Array.isArray(p.tasks) ? p.tasks : [];
  const open = tasks.find((t) => t.status === "open");
  const warnings = Array.isArray(p.warnings) ? p.warnings : [];
  const instructions = Array.isArray(p.instructions) ? p.instructions : [];
  const stale = proposalStale(p);   // REP7 — approval waits for a confirmation of stale held-away data
  const isOptions = p.proposal_kind === "options";
  const [gate, setGate] = useState(null);   // REP8 — the options tax gate (Approve hidden while a rule blocks)
  const [gateBusy, setGateBusy] = useState(false);

  async function loadGate(rerun = false) {
    if (!isOptions || !p.id) return;
    setGateBusy(true);
    try {
      const res = await fetch(`/api/replication/options/proposals/${p.id}/gate`, rerun ? { method: "POST" } : undefined);
      const data = await res.json().catch(() => ({}));
      if (rerun && res.ok) {
        const again = await fetch(`/api/replication/options/proposals/${p.id}/gate`);
        setGate(again.ok ? await again.json() : null);
      } else setGate(res.ok ? data : null);
    } finally { setGateBusy(false); }
  }

  useEffect(() => { loadGate(); }, [p.id]); // eslint-disable-line react-hooks/exhaustive-deps
  const approveShown = isOptions ? optionsApproveVisible(c.approve, gate) : c.approve;

  async function act(path, body) {
    setMsg(null);
    try {
      await call(`${p.id}/${path}`, body);
      const res = await fetch(`/api/proposals/${p.id}`);
      const data = await res.json().catch(() => ({}));
      if (res.ok) setEnv(data);
    } catch (err) { setMsg(err.message); }
  }

  return (
    <div className="space-y-5">
      <div className={`${card} p-6 text-sm`}>
        <p><span className="font-medium text-navy">{labelOf(v.statuses, p.status)}</span> · {labelOf(v.triggers, p.trigger)} ·
          {" "}{p.account?.account_number_masked} · {p.model_name}{p.advice_only ? " · ADVICE ONLY — the member decides and executes" : ""}</p>
        <p className="mt-1 text-text-muted">Made by {p.maker_kind === "system" ? "the system" : "a colleague"} · expires {String(p.expires_at || "").slice(0, 16).replace("T", " ")}
          {p.status_reason ? ` · ${p.status_reason}` : ""}</p>
        <p className="mt-1">Estimated gain: short-term {moneyText(p.est_st_gain)}, long-term {moneyText(p.est_lt_gain)}
          {p.gain_budget ? ` · gain budget ${moneyText(p.gain_budget)} (already realized this year ${moneyText(p.gain_realized_ytd)})` : " · no gain budget"}</p>
      </div>
      {stale.lines.length ? (
        <div className={`${card} p-6 text-sm`}>
          <h2 className="text-lg font-semibold text-navy">Held-away data{stale.blocking ? <span className="ml-2 rounded bg-[#FAF9F6] px-2 text-xs text-[#9B2335]">Stale — confirm before approval</span> : null}</h2>
          <ul className="mt-2 space-y-1">{stale.lines.map((d) => (
            <li key={d.account_id}>{d.message}
              {d.blocking ? <a className="ml-2 text-navy underline" href={`/portfolio/accounts/${d.account_id}`}>Confirm the data (task: confirm_held_away_data)</a> : null}</li>
          ))}</ul>
        </div>
      ) : null}
      {warnings.length ? (
        <div className={`${card} p-6 text-sm`}>
          <h2 className="text-lg font-semibold text-navy">Warnings</h2>
          <ul className="mt-2 space-y-1">{warnings.map((w, i) => <li key={i}><span className="font-medium">{labelOf(v.warning_codes, w.code)}</span> — {w.message}</li>)}</ul>
        </div>
      ) : null}
      {isOptions ? <TaxGatePanelView gate={gate} busy={gateBusy} onRerun={() => loadGate(true)} /> : null}
      <div className={`${card} p-6`}><h2 className="text-lg font-semibold text-navy">Trades</h2><div className="mt-2"><Trades envelope={env} /></div>
        <div className="mt-3"><ProposalLotMethod envelope={env} /></div></div>
      <div className={`${card} p-6`}><h2 className="text-lg font-semibold text-navy">Weights</h2><div className="mt-2"><Weights envelope={env} /></div></div>
      {Array.isArray(p.sell_later) && p.sell_later.length ? (
        <div className={`${card} p-6 text-sm`}>
          <h2 className="text-lg font-semibold text-navy">Sell later</h2>
          <ul className="mt-2">{p.sell_later.map((x, i) => <li key={i}>{x.label}: {x.quantity} in {x.year || "later"}{x.on_date ? ` (from ${x.on_date})` : ""} — {x.reason}</li>)}</ul>
        </div>
      ) : null}
      <div className={`${card} p-6 text-sm`}>
        <h2 className="text-lg font-semibold text-navy">Approval</h2>
        {open ? <p className="mt-2">Awaiting a decision by a holder of {open.eligible_permission}{open.maker_id ? " — not the person who made it" : ""}.</p>
          : <p className="mt-2 text-text-muted">No approval is open.</p>}
        {p.decision_note ? <p className="mt-1 text-text-muted">Note: {p.decision_note}</p> : null}
        <div className="mt-3 flex flex-wrap items-center gap-2">
          {c.submit ? <button type="button" className={primary} onClick={() => act("submit")}>Submit for approval</button> : null}
          {c.approve || c.reject ? <input className={input} placeholder="Note (required to reject)" value={note} onChange={(e) => setNote(e.target.value)} /> : null}
          {approveShown && !stale.blocking ? <button type="button" className={primary} onClick={() => act("approve", { note: note || undefined })}>Approve</button> : null}
          {c.reject ? <button type="button" className={secondary} onClick={() => act("reject", { note })}>Reject</button> : null}
          {c.execute ? <button type="button" className={secondary} onClick={() => act("execute")}>Create instructions</button> : null}
          {c.refresh ? <button type="button" className={secondary} onClick={() => act("refresh")}>Check instructions</button> : null}
        </div>
        {msg ? <p className="mt-2 text-[#9B2335]">{msg}</p> : null}
      </div>
      {instructions.length ? (
        <div className={`${card} p-6 text-sm`}>
          <h2 className="text-lg font-semibold text-navy">Execution instructions</h2>
          <ul className="mt-2">{instructions.map((i) => (
            <li key={i.id}><a className="text-navy underline" href={`/portfolio/instructions/${i.id}`}>{i.side} {i.quantity || i.notional}</a> — {i.status}</li>
          ))}</ul>
        </div>
      ) : null}
    </div>
  );
}
