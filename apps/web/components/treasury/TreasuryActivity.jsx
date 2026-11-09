"use client";

/**
 * TreasuryActivity — Household → Treasury → Activity (CASH05): the ledger of the cash / sleeve / ladder holdings (date,
 * description, change, a pending marker with settlement guidance, type), filters by type and status, a clear empty
 * state, and a DETAIL DRAWER per entry (source record, account, security, settlement, and the linked instruction /
 * proposal / documents — only the links the server named with the API path that opens them). EarningsDialog: the
 * period's dividends per fund + ladder interest + cash interest − the advisory fee on cash = total, with how it
 * reconciles to the ledger. Read-only; lib/cashTreasury.mjs fails closed. Light / cream.
 */

import {
  activityEmpty, activityRows, drawerView, earningsView, labelText, money, periodOptions, readable, typeOptions,
} from "@/lib/cashTreasury.mjs";

const card = "rounded-[6px] border border-[#ece8dd] bg-white p-4";
const input = "rounded border border-[#E2E8F0] px-2 py-1 text-sm";
const serif = { fontFamily: "'Spectral', Georgia, serif" };

async function openDocument(href) {
  const res = await fetch(href);
  const data = await res.json().catch(() => ({}));
  if (res.ok && data.url) window.open(data.url, "_blank", "noopener");
}

export function EntryDrawer({ drawer, onClose }) {
  const d = drawerView(drawer);
  if (!d) return null;
  return (
    <aside role="dialog" aria-label="Activity detail" className={`${card} space-y-2 bg-[#FAF9F6]`}>
      <div className="flex items-baseline gap-2">
        <h4 className="mr-auto font-semibold text-navy">{d.title}</h4>
        {onClose ? <button type="button" className="text-sm text-navy underline" onClick={onClose}>Close</button> : null}
      </div>
      <dl className="grid grid-cols-1 gap-1 text-sm sm:grid-cols-2">
        <div><dt className="text-text-muted">Change</dt><dd>{d.change}</dd></div>
        <div><dt className="text-text-muted">Type</dt><dd>{d.type}</dd></div>
        <div><dt className="text-text-muted">Trade date</dt><dd>{d.date}</dd></div>
        <div><dt className="text-text-muted">Settlement</dt><dd>{d.settlement}</dd></div>
        <div><dt className="text-text-muted">Account</dt><dd>{d.account}</dd></div>
        <div><dt className="text-text-muted">Security</dt><dd>{d.security}</dd></div>
        <div><dt className="text-text-muted">Owner</dt><dd>{d.owner}</dd></div>
        <div><dt className="text-text-muted">Source record</dt><dd>{d.source}</dd></div>
      </dl>
      {d.links.length ? (
        <ul aria-label="Linked records" className="text-sm">
          {d.links.map((l) => <li key={`${l.kind}-${l.href}`}><a className="text-navy underline" data-link={l.kind} href={l.href}
            onClick={l.kind === "document" ? (e) => { e.preventDefault(); openDocument(l.href); } : undefined}>{l.label}</a></li>)}
        </ul>) : <p className="text-sm text-text-muted">{d.linksNote || "No linked record."}</p>}
      {d.documents.filter((x) => !x.has_file).map((x) => <p key={x.id} className="text-xs text-text-muted">{x.name}: no stored file yet</p>)}
    </aside>
  );
}

export function EarningsDialog({ data, period, onPeriod, onClose }) {
  const v = earningsView(data);
  if (!v) return null;
  const est = labelText(data, "cash.yield_estimate");
  return (
    <section role="dialog" aria-label="Earnings" className={`${card} space-y-2`}>
      <div className="flex flex-wrap items-baseline gap-2">
        <h4 className="mr-auto text-lg font-semibold text-navy" style={serif}>Earnings {v.period.start} – {v.period.end}</h4>
        <select className={input} value={period || v.period.key} onChange={(e) => onPeriod?.(e.target.value)} aria-label="Period">
          {periodOptions(data).filter((p) => p.key !== "custom").map((p) => <option key={p.key} value={p.key}>{p.label}</option>)}</select>
        {onClose ? <button type="button" className="text-sm text-navy underline" onClick={onClose}>Close</button> : null}
      </div>
      <table className="w-full text-sm" aria-label="Earnings breakdown"><tbody>
        <tr className="border-t border-[#ece8dd]"><td className="py-1">Dividends</td><td className="text-right">{money(v.dividends)}</td></tr>
        {v.funds.map((f) => <tr key={f.holding} className="text-xs text-text-muted"><td className="pl-4">{f.holding}</td><td className="text-right">{money(f.amount)}</td></tr>)}
        <tr className="border-t border-[#ece8dd]"><td className="py-1">Ladder interest<span className="block text-xs text-text-muted">coupons {money(v.coupons)} + discount realised {money(v.discount)}</span></td><td className="text-right">{money(v.ladder)}</td></tr>
        <tr className="border-t border-[#ece8dd]"><td className="py-1">Cash interest</td><td className="text-right">{money(v.cash)}</td></tr>
        <tr className="border-t border-[#ece8dd]"><td className="py-1">Advisory fee on cash<span className="block text-xs text-text-muted" data-fee={v.feeCharged === false ? "not_charged" : "charged"}>{v.feeText}</span></td>
          <td className="text-right">{v.fee === null ? "not charged" : `−${money(v.fee)}`}</td></tr>
        <tr className="border-t-2 border-[#1B2B4B] font-semibold"><td className="py-1">Total</td><td className="text-right">{money(v.total)}</td></tr>
      </tbody></table>
      <p className="text-xs text-text-muted">{v.formula}. {v.ladderMethod}.</p>
      <p className="text-xs text-text-muted" data-reconciles={v.reconciles ? "yes" : "no"}>{v.reconText}{v.reconciles ? " — reconciled to the activity ledger." : " — does NOT reconcile; see the API log."} Fee debits in the ledger: {money(v.ledgerFees)}.</p>
      {v.accreting.length ? <ul className="text-xs text-text-muted" aria-label="Accreting">{v.accreting.map((a, i) => <li key={i}>{a.holding}: {a.amount ? `${money(a.amount)} accreting, not yet realised` : a.text}</li>)}</ul> : null}
      {v.missing.length ? <ul className="text-xs text-[#9B2335]">{v.missing.map((m) => <li key={m.entry}>{m.text}</li>)}</ul> : null}
      {est ? <p className="text-xs text-text-muted" data-label="cash.yield_estimate">{est}</p> : null}
    </section>
  );
}

export default function TreasuryActivity({ data, filters = {}, onFilters, onOpen, drawer, onCloseDrawer, onEarnings }) {
  if (!readable(data)) return null;
  const rows = activityRows(data);
  const empty = activityEmpty(data);
  return (
    <section className="space-y-3" aria-label="Activity">
      <div className="flex flex-wrap items-center gap-2">
        <h3 className="mr-auto text-lg font-semibold text-navy" style={serif}>Activity</h3>
        <select className={input} aria-label="Type" value={filters.type || ""} onChange={(e) => onFilters?.({ ...filters, type: e.target.value })}>
          <option value="">All types</option>{typeOptions(data).map((t) => <option key={t.key} value={t.key}>{t.label}</option>)}</select>
        <select className={input} aria-label="Status" value={filters.status || ""} onChange={(e) => onFilters?.({ ...filters, status: e.target.value })}>
          <option value="">Pending and settled</option><option value="pending">Pending</option><option value="settled">Settled</option></select>
        {onEarnings ? <button type="button" className="rounded border border-navy px-3 py-1 text-sm text-navy" onClick={onEarnings}>Earnings</button> : null}
      </div>
      {empty ? <p className={`${card} text-sm text-text-muted`} data-empty="activity">{empty}</p> : (
        <table className={`${card} w-full text-sm`}>
          <thead className="text-left text-text-muted"><tr><th className="py-1">Date</th><th>Description</th><th>Type</th><th className="text-right">Change</th><th>Status</th></tr></thead>
          <tbody>{rows.map((r) => (
            <tr key={r.id} className="cursor-pointer border-t border-[#ece8dd] align-top" onClick={() => onOpen?.(r.id)}>
              <td className="py-1">{r.date}</td>
              <td><button type="button" className="text-left text-navy underline" onClick={() => onOpen?.(r.id)}>{r.description}</button>
                <span className="block text-xs text-text-muted">{r.owner}{r.account ? ` · ${r.account}` : ""}</span></td>
              <td>{r.type}</td>
              <td className={`text-right ${r.positive ? "text-[#2D6A4F]" : "text-[#334155]"}`}>{r.change}</td>
              <td className="text-xs">{r.pending ? <span className="rounded-full border border-[#C5A880] px-2 text-navy" data-pending="yes">Pending</span> : null}
                <span className="block text-text-muted">{r.status}</span></td>
            </tr>))}
          </tbody>
        </table>)}
      {drawer ? <EntryDrawer drawer={drawer} onClose={onCloseDrawer} /> : null}
    </section>
  );
}
