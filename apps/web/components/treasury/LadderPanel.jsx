"use client";

/**
 * LadderPanel — Household → Treasury → Ladder and Instrument plan (CASH03). Per scope with an ACTIVE cash policy:
 * the obligation-matched ladder as a timeline (rungs against the Long-term obligations they fund; targeted vs filler;
 * indicative maturities marked) and its rung table, then the instrument plan per sleeve × account (chosen, why, net /
 * after-tax yield with basis and as-of, runner-up and the bp gap) with the blended yields. A member sees their family's
 * plan read-only, with the disclosure library's labels (cash.yield_estimate, cash.tax_estimate). Every control renders
 * only from lib/cashPlan.mjs (fails closed). Light / cream. A plan is a target: nothing here moves money.
 */

import { useState } from "react";
import {
  isMemberPlan, planBlock, planControls, planRows, planScopes, providerNotes, rungRows, sleeveBlends, taxLabel,
  timeline, yieldEstimateLabel, yieldLine,
} from "@/lib/cashPlan.mjs";

const card = "rounded-[6px] border border-[#ece8dd] bg-white p-4";
const primary = "rounded bg-navy px-3 py-1 text-white disabled:opacity-50";
const serif = { fontFamily: "'Spectral', Georgia, serif" };

export function LadderTimeline({ scope }) {
  const tl = timeline(scope);
  if (!tl.rungs.length && !tl.obligations.length) {
    return <p className="text-sm text-text-muted">No Long-term obligations — no ladder.</p>;
  }
  return (
    <div aria-label="Ladder timeline">
      <div className="relative h-16 rounded-[6px] border border-[#ece8dd] bg-[#FAF9F6]">
        {tl.obligations.map((o) => (
          <span key={`o-${o.key}`} title={`${o.name} · ${o.amount} · ${o.date}`} data-kind="obligation"
            className={`absolute top-1 h-5 w-0.5 ${o.large ? "bg-[#9B2335]" : "bg-[#64748B]"}`} style={{ left: `${o.at}%` }} />
        ))}
        {tl.rungs.map((r) => (
          <span key={`r-${r.key}`} title={`Rung ${r.index} · ${r.par} · ${r.date}${r.indicative ? " · indicative" : ""}`}
            data-kind={r.targeted ? "targeted" : "filler"} data-indicative={r.indicative ? "true" : "false"}
            className={`absolute bottom-1 h-6 w-2 rounded-sm ${r.targeted ? "bg-navy" : "bg-[#C5A880]"} ${r.indicative ? "opacity-60" : ""}`}
            style={{ left: `${r.at}%` }} />
        ))}
      </div>
      <p className="mt-1 text-xs text-text-muted">{tl.from} → {tl.until}. Navy: targeted rung; gold: monthly filler; faded: indicative maturity. Red tick: a large obligation; grey: a smaller one.</p>
      {tl.unfunded.length ? (
        <ul className="mt-1 text-xs text-[#9B2335]">{tl.unfunded.map((u) => <li key={u.key}>{u.date} · {u.amount} — {u.text}</li>)}</ul>
      ) : null}
    </div>
  );
}

export function RungTable({ env, scope }) {
  const rows = rungRows(env, scope);
  if (!rows.length) return null;
  return (
    <table className="w-full text-sm" aria-label="Ladder rungs">
      <thead className="text-left text-text-muted"><tr><th className="py-1">#</th><th>Rung</th><th>Maturity</th><th>Instrument</th>
        <th>Par</th><th>Covers</th><th>Yield</th></tr></thead>
      <tbody>{rows.map((r) => (
        <tr key={r.key} className="border-t border-[#ece8dd] align-top">
          <td className="py-1">{r.index}</td>
          <td>{r.kindLabel}</td>
          <td>{r.maturity}{r.indicative ? <span className="block text-xs text-[#9B2335]" data-indicative="true">{r.indicativeLabel}</span> : null}
            {r.adjustment ? <span className="block text-xs text-text-muted">{r.adjustment}</span> : null}</td>
          <td>{r.type}{r.issue ? <span className="block text-xs text-text-muted">{r.issue}{r.cusip ? ` · ${r.cusip}` : ""}</span> : null}</td>
          <td>{r.par}</td>
          <td>{r.covers}{r.funds ? <span className="block text-xs text-text-muted">+ {r.funds} smaller</span> : null}</td>
          <td className="text-xs">{r.yield}</td>
        </tr>))}
      </tbody>
    </table>
  );
}

export function InstrumentPlan({ env, scope }) {
  const rows = planRows(env, scope);
  const blends = sleeveBlends(scope);
  return (
    <div className="space-y-2" aria-label="Instrument plan">
      {rows.length ? (
        <table className="w-full text-sm">
          <thead className="text-left text-text-muted"><tr><th className="py-1">Sleeve</th><th>Account</th><th>Chosen</th>
            <th>Net</th><th>After-tax</th><th>Runner-up</th><th>Allocated</th></tr></thead>
          <tbody>{rows.map((r) => (
            <tr key={r.key} className="border-t border-[#ece8dd] align-top">
              <td className="py-1">{r.sleeve}</td>
              <td>{r.account}<span className="block text-xs text-text-muted">{r.treatment}</span></td>
              <td>{r.chosen}<span className="block text-xs text-text-muted">{r.why}</span>
                {r.cdHurdle ? <span className="block text-xs text-text-muted">{r.cdHurdle}</span> : null}</td>
              <td className="text-xs">{r.net}</td>
              <td className="text-xs">{r.afterTax}</td>
              <td className="text-xs">{r.runnerUp} · {r.gap}</td>
              <td className="text-xs">{r.value}</td>
            </tr>))}
          </tbody>
        </table>
      ) : <p className="text-sm text-text-muted">No open account in this scope — no instrument plan.</p>}
      <ul className="text-xs text-[#334155]" aria-label="Blended yields">{blends.map((b) => (
        <li key={b.key}>{b.label} ({b.amount}): instruments {b.net} net, {b.afterTax} after tax · in effect {b.effective}</li>))}</ul>
    </div>
  );
}

export function ScopePlan({ env, scope }) {
  return (
    <section className={`${card} space-y-3`} aria-label={`Ladder and instrument plan — ${scope.label}`}>
      <h3 className="text-lg font-semibold text-navy" style={serif}>Ladder and instrument plan — {scope.label}</h3>
      <p className="text-xs text-text-muted">Policy v{scope.version_no} · {scope.rung_count} rungs · {scope.ladder?.threshold}.
        Ladder yield {yieldLine(scope.ladder?.yield)}.</p>
      <LadderTimeline scope={scope} />
      <RungTable env={env} scope={scope} />
      <InstrumentPlan env={env} scope={scope} />
      {scope.sources?.issues ? <p className="text-xs text-text-muted">{scope.sources.issues}</p> : null}
    </section>
  );
}

export default function LadderPanel({ env, onReload }) {
  const [msg, setMsg] = useState(null);
  const [busy, setBusy] = useState(false);
  if (!env || !env.household || !env.plan) return null;
  const list = planScopes(env);
  const c = planControls(env);
  if (planBlock(env).error) {
    return <div className={`${card} text-sm text-[#9B2335]`} role="alert">{planBlock(env).error}</div>;
  }
  const tl = taxLabel(env);
  const yl = yieldEstimateLabel(env);
  if (!list.length) {
    return isMemberPlan(env) ? null : <div className={`${card} text-sm text-text-muted`}>No active cash policy — the ladder and instrument plan appear once a policy is approved.</div>;
  }
  const regenerate = async () => {
    setBusy(true);
    try {
      const res = await fetch(`/api/cash/households/${env.household.id}/plan/regenerate`, { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.detail || data.error || `Request failed (${res.status})`);
      setMsg("Plan regenerated.");
      await onReload?.();
    } catch (e) { setMsg(e.message); } finally { setBusy(false); }
  };
  return (
    <div className="space-y-4" aria-label="Ladders and instrument plans">
      {c.regenerate ? <div><button type="button" className={primary} disabled={busy} onClick={regenerate}>Regenerate plan</button></div> : null}
      {list.map((s) => <ScopePlan key={`${s.scope_type}:${s.scope_id}`} env={env} scope={s} />)}
      <ul className="text-xs text-text-muted" aria-label="Data sources">{providerNotes(env).map((p) => <li key={p.key}>{p.text}{p.attribution ? ` — ${p.attribution}` : ""}</li>)}</ul>
      {tl ? <p className="text-xs text-text-muted" data-label="cash.tax_estimate">{tl}</p> : null}
      {yl ? <p className="text-xs text-text-muted" data-label="cash.yield_estimate">{yl}</p> : null}
      {msg ? <p className="text-sm text-[#334155]">{msg}</p> : null}
      {planBlock(env).purpose === "member_display" ? <p className="text-xs text-text-muted">Read-only.</p> : null}
    </div>
  );
}
