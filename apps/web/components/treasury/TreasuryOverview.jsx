"use client";

/**
 * TreasuryOverview — Household → Treasury → Overview (CASH05): the liquid balance over time (range chips; a step line —
 * the balance is the latest statement until the next one), transfer markers, a tooltip with the value and the pending
 * in / out; the blended net and after-tax yield with its method note; cash01's coverage card and per-entity table
 * passed through from the same read; and the server's actions (links into the existing screens). Read-only. Every
 * figure is the server's text (lib/cashTreasury.mjs fails closed). Light / cream.
 */

import { useState } from "react";
import {
  actionLinks, chartGeometry, coverageEnv, labelText, money, rangeOptions, readable, tooltipLines, yieldsView,
} from "@/lib/cashTreasury.mjs";
import { CoverageCard, EntityTable } from "@/components/treasury/CoverageCard";

const card = "rounded-[6px] border border-[#ece8dd] bg-white p-4";
const serif = { fontFamily: "'Spectral', Georgia, serif" };
const NAVY = "#1B2B4B";
const GOLD = "#C5A880";
const MUTED = "#64748B";
const BORDER = "#E2E8F0";

export function BalanceChart({ data, selected = null, width = 640, height = 200 }) {
  const [hover, setHover] = useState(selected);
  const g = chartGeometry(data?.points, data?.markers, { width, height });
  if (g.empty) return <p className="text-sm text-text-muted">No liquid balance is recorded for this range.</p>;
  const pt = hover !== null && hover !== undefined ? g.dots[Math.max(0, Math.min(hover, g.dots.length - 1))] : null;
  const onMove = (e) => {
    const box = e.currentTarget.getBoundingClientRect();
    const px = ((e.clientX - box.left) / box.width) * width;
    let best = 0;
    g.dots.forEach((d, i) => { if (Math.abs(d.x - px) < Math.abs(g.dots[best].x - px)) best = i; });
    setHover(best);
  };
  return (
    <figure aria-label="Liquid balance over time" className="relative">
      <svg viewBox={`0 0 ${width} ${height}`} className="w-full" role="img" onMouseMove={onMove} onMouseLeave={() => setHover(null)}>
        <rect x="0" y="0" width={width} height={height} fill="#FFFFFF" />
        {g.yTicks.map((t) => (
          <g key={t.label}><line x1="28" x2={width - 28} y1={t.y} y2={t.y} stroke={BORDER} strokeWidth="1" />
            <text x="30" y={t.y - 3} fontSize="10" fill={MUTED}>{t.label}</text></g>))}
        <path d={g.path} fill="none" stroke={NAVY} strokeWidth="2" />
        {g.markers.map((m) => (
          <g key={m.id} data-marker={m.direction}>
            <line x1={m.x} x2={m.x} y1="20" y2={height - 24} stroke={GOLD} strokeWidth="1" strokeDasharray="3 3" />
            <circle cx={m.x} cy="18" r="4" fill={m.direction === "in" ? GOLD : "#FFFFFF"} stroke={GOLD} strokeWidth="1.5" />
            <title>{`${m.date} · ${m.description} · ${money(m.change)}${m.pending ? " (pending)" : ""}`}</title>
          </g>))}
        {pt ? <circle cx={pt.x} cy={pt.y} r="4" fill={NAVY} /> : null}
      </svg>
      {pt ? (
        <div role="tooltip" className="absolute right-2 top-2 rounded-[6px] border border-[#ece8dd] bg-[#FAF9F6] px-3 py-2 text-xs text-[#334155]">
          {tooltipLines(pt).map((l) => <p key={l}>{l}</p>)}
        </div>) : null}
      <figcaption className="mt-1 text-xs text-text-muted">{data?.points_basis}</figcaption>
    </figure>
  );
}

export function YieldCard({ data }) {
  const y = yieldsView(data);
  const est = labelText(data, "cash.yield_estimate");
  const tax = labelText(data, "cash.tax_estimate");
  return (
    <section className={card} aria-label="Blended yield">
      <h3 className="font-semibold text-navy">Blended yield</h3>
      {y.estimated ? (
        <div className="mt-2 grid grid-cols-1 gap-3 sm:grid-cols-2">
          <div><p className="text-sm text-text-muted">Net yield</p><p className="text-2xl text-navy">{y.net}</p>
            <p className="text-xs text-text-muted">{y.gross} before the fee · {y.fee}</p></div>
          <div><p className="text-sm text-text-muted">After tax</p><p className="text-2xl text-navy">{y.afterTax || "—"}</p>
            <p className="text-xs text-text-muted">{y.afterTaxText}</p></div>
        </div>) : <p className="mt-2 text-sm text-text-muted">{y.text}</p>}
      {y.method ? <p className="mt-2 text-xs text-text-muted" data-note="method">{y.method}</p> : null}
      {y.without.length ? <p className="mt-1 text-xs text-text-muted">Not in the blend: {y.without.map((w) => `${w.name} (${w.why})`).join("; ")}</p> : null}
      {est ? <p className="mt-2 text-xs text-text-muted" data-label="cash.yield_estimate">{est}</p> : null}
      {tax && y.afterTax ? <p className="text-xs text-text-muted" data-label="cash.tax_estimate">{tax}</p> : null}
    </section>
  );
}

export default function TreasuryOverview({ data, range, onRange, selected = null }) {
  if (!readable(data)) return null;
  const ranges = rangeOptions(data);
  const cov = coverageEnv(data);
  const acts = actionLinks(data);
  return (
    <section className="space-y-3" aria-label="Treasury overview">
      <div className={card}>
        <div className="flex flex-wrap items-baseline gap-2">
          <h3 className="mr-auto text-lg font-semibold text-navy" style={serif}>Liquid balance {money(data.balance)}</h3>
          <div className="flex gap-1" role="group" aria-label="Range">
            {ranges.map((r) => (
              <button key={r.key} type="button" aria-pressed={r.key === (range || data.range?.key)}
                className={`rounded-full border px-3 py-0.5 text-xs ${r.key === (range || data.range?.key) ? "border-[#C5A880] bg-[#F5F1EB] text-navy" : "border-[#E2E8F0] text-text-muted"}`}
                onClick={() => onRange?.(r.key)}>{r.label}</button>))}
          </div>
        </div>
        <p className="text-xs text-text-muted">{data.range?.start} – {data.range?.end} · change {money(data.change_in_range)} · {data.series_basis}</p>
        <BalanceChart data={data} selected={selected} />
        {data.markers?.length ? <p className="mt-1 text-xs text-text-muted">Transfers: {data.markers.map((m) => `${m.date} ${money(m.change)}${m.pending ? " (pending)" : ""}`).join(" · ")}</p> : null}
      </div>
      <YieldCard data={data} />
      {cov.coverage ? <CoverageCard env={cov} /> : <p className={`${card} text-sm text-text-muted`}>This entity has no cash and no obligations recorded.</p>}
      {cov.entities.length ? <section className={card}><h3 className="mb-2 font-semibold text-navy">By entity</h3><EntityTable env={cov} /></section> : null}
      {acts.length ? (
        <nav aria-label="Treasury actions" className="flex flex-wrap gap-3 text-sm">
          {acts.map((a) => <a key={a.key} className="text-navy underline" href={a.href}>{a.label}</a>)}
        </nav>) : null}
    </section>
  );
}
