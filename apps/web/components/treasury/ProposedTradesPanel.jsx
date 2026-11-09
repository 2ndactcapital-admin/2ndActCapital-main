"use client";

/**
 * ProposedTradesPanel — Household → Treasury → Proposed trades (CASH04). The household's cash proposals (a kind of
 * Replication Studio proposal): lines grouped by sleeve / rung with their reasons, early-sale warnings, the shortfall
 * and the after-tax effect, and the maturity waterfall's run history. Approve / route / the instruction sheet are LINKS
 * into the existing proposal and instruction screens — nothing here approves or routes. Every control renders only
 * from lib/cashProposals.mjs (fails closed); a member never sees this panel. Light / cream.
 */

import { useState } from "react";
import {
  panelControls, panelVisible, proposalRows, proposalsBlock, routeNotes, waterfallHistory,
} from "@/lib/cashProposals.mjs";

const card = "rounded-[6px] border border-[#ece8dd] bg-white p-4";
const primary = "rounded bg-navy px-3 py-1 text-white disabled:opacity-50";
const secondary = "rounded border border-[#E2E8F0] px-3 py-1 text-navy disabled:opacity-50";
const serif = { fontFamily: "'Spectral', Georgia, serif" };

export function ProposalCard({ row, onAdopt, busy }) {
  return (
    <section className={`${card} space-y-2`} aria-label={`Cash proposal ${row.account}`}>
      <div className="flex flex-wrap items-baseline gap-2">
        <h4 className="mr-auto font-semibold text-navy">{row.account} · {row.trigger}{row.period ? ` · ${row.period}` : ""}</h4>
        <span className="text-sm text-text-muted">{row.status}{row.policy !== null ? ` · policy v${row.policy}` : ""}</span>
        <a className="text-sm text-navy underline" href={row.link}>Open proposal (approve / route)</a>
        {row.actions.adopt ? <button type="button" className={secondary} disabled={busy} onClick={() => onAdopt(row.id)}>Adopt and submit</button> : null}
      </div>
      {row.groups.map((g) => (
        <div key={g.key} aria-label={g.title}>
          <p className="text-sm font-semibold text-[#334155]">{g.title}</p>
          <table className="w-full text-sm">
            <thead className="text-left text-text-muted"><tr><th className="py-1">#</th><th>Side</th><th>Security</th><th>Amount</th>
              <th>Price</th><th>After-tax</th><th>Why</th></tr></thead>
            <tbody>{g.lines.map((l) => (
              <tr key={l.key} className="border-t border-[#ece8dd] align-top">
                <td className="py-1">{l.lineNo}</td><td>{l.side}</td>
                <td>{l.security}{l.maturity ? <span className="block text-xs text-text-muted">matures {l.maturity}</span> : null}</td>
                <td>{l.amount}</td>
                <td>{l.price}{l.limitText ? <span className="block text-xs text-text-muted">{l.limitText}</span> : null}</td>
                <td>{l.afterTax}</td>
                <td className="text-xs">{l.step ? `${l.step}: ` : ""}{l.reason}{l.obligations ? ` (funds ${l.obligations})` : ""}
                  {l.warning ? <span className="block text-[#9B2335]" data-warning="early_sale">{l.warning}</span> : null}
                  {l.instruction ? <a className="block text-navy underline" href={l.instruction}>Instruction</a> : null}</td>
              </tr>))}
            </tbody>
          </table>
        </div>
      ))}
      {row.earlySale.length ? <ul className="text-xs text-[#9B2335]" aria-label="Early-sale warnings">{row.earlySale.map((w) => <li key={w}>{w}</li>)}</ul> : null}
      {row.shortfall ? <p className="text-sm text-[#9B2335]" data-shortfall="true">{row.shortfall}</p> : null}
      {row.afterTax ? <p className="text-xs text-text-muted" data-effect="after_tax">{row.afterTax}</p> : null}
      {row.untouched.length ? <p className="text-xs text-text-muted">Never sold (not cash instruments): {row.untouched.join(", ")}</p> : null}
      {row.notes.length ? <ul className="text-xs text-text-muted">{row.notes.map((n) => <li key={n}>{n}</li>)}</ul> : null}
      {row.instructions.length ? <p className="text-xs">{row.instructions.map((href, i) => <a key={href} className="mr-2 text-navy underline" href={href}>Instruction {i + 1}</a>)}</p> : null}
    </section>
  );
}

export function WaterfallHistory({ env }) {
  const rows = waterfallHistory(env);
  return (
    <section className={card} aria-label="Waterfall run history">
      <h4 className="mb-1 font-semibold text-navy">Maturity waterfall — run history</h4>
      {rows.length ? (
        <table className="w-full text-sm">
          <thead className="text-left text-text-muted"><tr><th className="py-1">Period</th><th>Account</th><th>Status</th><th>Maturities</th><th>Earnings</th></tr></thead>
          <tbody>{rows.map((r) => (
            <tr key={r.key} className="border-t border-[#ece8dd]"><td className="py-1"><a className="text-navy underline" href={r.link}>{r.period}</a></td>
              <td>{r.account}</td><td>{r.status}</td><td className="text-xs">{r.maturities || "—"}</td><td>{r.earnings || "—"}</td></tr>))}
          </tbody>
        </table>
      ) : <p className="text-sm text-text-muted">The waterfall has not run for this household (it ships switched off).</p>}
    </section>
  );
}

export default function ProposedTradesPanel({ env, onReload }) {
  const [msg, setMsg] = useState(null);
  const [busy, setBusy] = useState(false);
  if (!env || !env.household || !panelVisible(env)) return null;
  const block = proposalsBlock(env);
  if (block.error) return <div className={`${card} text-sm text-[#9B2335]`} role="alert">{block.error}</div>;
  const c = panelControls(env);
  const rows = proposalRows(env);
  const post = async (path, done) => {
    setBusy(true);
    try {
      const res = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.detail || data.error || `Request failed (${res.status})`);
      setMsg(done(data));
      await onReload?.();
    } catch (e) { setMsg(e.message); } finally { setBusy(false); }
  };
  const generate = () => post(`/api/cash/households/${env.household.id}/proposals`,
    (d) => d.empty || `${(d.made || []).length} draft proposal(s); ${(d.skipped || []).length} account(s) skipped.`);
  const adopt = (id) => post(`/api/cash/proposals/${id}/adopt`, () => "Adopted and submitted — a different person approves it.");
  return (
    <div className="space-y-3" aria-label="Proposed trades">
      <div className="flex flex-wrap items-center gap-2">
        <h3 className="mr-auto text-lg font-semibold text-navy" style={serif}>Proposed trades</h3>
        {c.generate ? <button type="button" className={primary} disabled={busy} onClick={generate}>Propose trades</button> : null}
      </div>
      <p className="text-xs text-text-muted">A proposal is a trade list for securities toward the active cash policy. A different person approves it on the proposal screen; instructions are carried out by people. Routes: {routeNotes(env).map((r) => `${r.label}${r.refused ? ` (refused: ${r.refused})` : ""}`).join("; ")}.</p>
      {rows.length ? rows.map((r) => <ProposalCard key={r.id} row={r} onAdopt={adopt} busy={busy} />)
        : <p className={`${card} text-sm text-text-muted`}>No cash proposals for this household yet.</p>}
      <WaterfallHistory env={env} />
      {msg ? <p className="text-sm text-[#334155]">{msg}</p> : null}
    </div>
  );
}
