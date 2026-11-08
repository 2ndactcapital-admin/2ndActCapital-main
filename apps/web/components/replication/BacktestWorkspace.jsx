"use client";

/**
 * BacktestWorkspace — Replication → Backtest (REP10). A person runs a backtest
 * (nothing runs one automatically); the result is HYPOTHETICAL on every line
 * and carries the firm's approved disclosure — or "no hypothetical-performance
 * policy approved — staff use only". With too little price history the run is
 * stored "not estimated" with the coverage found and the missing securities:
 * no partial figures. Controls from lib/rep10.mjs (fail closed).
 */

import { useState } from "react";
import { backtestControls, disclosureOf, labelOf, money, pct, vocab } from "@/lib/rep10.mjs";
import { errorFromDetail } from "@/lib/apiError.mjs";
import { BusyButton, BusyError, useBusy } from "@/components/ui/Busy";

const card = "rounded-[6px] border border-[#ece8dd] bg-white";
const input = "rounded border border-[#E2E8F0] px-3 py-2";
const primary = "rounded bg-navy px-3 py-1 text-white disabled:opacity-50";

export function BacktestResultView({ envelope, backtest }) {
  if (!backtest) return null;
  const v = vocab(envelope);
  const d = disclosureOf(envelope, backtest);
  const s = backtest.result?.statistics;
  const h = backtest.result?.harvest;
  const cov = backtest.coverage || {};
  return (
    <div className={`${card} space-y-2 p-4 text-sm`}>
      <p className="font-semibold text-navy">HYPOTHETICAL — {labelOf(v.windows, backtest.window_preset)} ({backtest.start_date} to {backtest.end_date})</p>
      <p className="rounded bg-[#F5F1EB] p-2 text-navy">Disclosure: {d}</p>
      {backtest.status !== "computed" ? (
        <>
          <p className="text-[#9B2335]">{backtest.not_estimated_reason}</p>
          <p className="text-text-muted">Weight covered {pct(cov.weight_covered)} of {pct(cov.min_weight_coverage)} needed; missing: {(cov.missing || []).join(", ") || "—"}</p>
        </>
      ) : (
        <>
          <div className="flex flex-wrap gap-6">
            <p>Replica {pct(s?.replica_return)}</p><p>Fund {pct(s?.fund_return)}</p>
            <p>Tracking error {pct(s?.tracking_error)}</p>
            <p>Max drawdown {pct(s?.max_drawdown_replica)} / {pct(s?.max_drawdown_fund)}</p>
          </div>
          <p>Harvested losses {money(h?.losses_total)} ({h?.count} sale(s)); tax value {h?.tax_value ? `${money(h.tax_value)} (estimate)` : h?.tax_value_note}</p>
          {(backtest.result?.method_notes || []).map((n) => <p key={n} className="text-text-muted">{n}</p>)}
        </>
      )}
    </div>
  );
}

export function BacktestsView({ envelope, onRun, busy = false }) {
  const c = backtestControls(envelope);
  const v = vocab(envelope);
  const rows = Array.isArray(envelope?.rows) ? envelope.rows : [];
  const [f, setF] = useState({ model_id: "", fund_security_id: "", window: "2022", cadence: "quarterly" });
  return (
    <div className="space-y-4">
      <p className="rounded-[6px] border border-[#ece8dd] bg-white p-3 text-sm text-navy">Hypothetical performance. {disclosureOf(envelope)}</p>
      {c.run ? (
        <div className={`${card} flex flex-wrap items-end gap-2 p-4 text-sm`}>
          <input className={input} placeholder="Replica model id" value={f.model_id} onChange={(e) => setF({ ...f, model_id: e.target.value })} />
          <input className={input} placeholder="Fund security id" value={f.fund_security_id} onChange={(e) => setF({ ...f, fund_security_id: e.target.value })} />
          <select className={input} value={f.window} onChange={(e) => setF({ ...f, window: e.target.value })}>
            {(v.windows || []).filter((w) => w.key !== "custom").map((w) => <option key={w.key} value={w.key}>{w.label}</option>)}
          </select>
          <select className={input} value={f.cadence} onChange={(e) => setF({ ...f, cadence: e.target.value })}>
            {(v.cadences || []).map((w) => <option key={w.key} value={w.key}>{w.label}</option>)}
          </select>
          {/* CATCHALL1 busy — busy comes from BacktestWorkspace's useBusy */}
          <BusyButton busy={busy} busyLabel="Running…" className={primary} onClick={() => onRun?.(f)}>Run backtest</BusyButton>
        </div>
      ) : null}
      {rows.length ? rows.map((b) => <BacktestResultView key={b.id} envelope={envelope} backtest={b} />)
        : <div className={`${card} p-6 text-center text-sm text-text-muted`}>No backtest has been run.</div>}
    </div>
  );
}

export default function BacktestWorkspace({ initialEnvelope }) {
  const [env, setEnv] = useState(initialEnvelope);
  const running = useBusy(); // CATCHALL1 busy — Run backtest
  const onRun = (f) => running.run(async () => {
    if (!String(f.model_id || "").trim() || !String(f.fund_security_id || "").trim()) {
      throw new Error("Enter the replica model id and the fund security id to run a backtest.");
    }
    let res;
    try {
      res = await fetch("/api/replication/backtests", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(f) });
    } catch (e) {
      throw new Error(`The backtest request did not reach the server: ${e?.message || e}`);
    }
    const data = await res.json().catch(() => ({}));
    // BT-01: the server's reason (detail), never a bare "Request failed".
    if (!res.ok) throw new Error(`Backtest not run: ${data.error || errorFromDetail(data.detail, res.status)}`);
    const r2 = await fetch("/api/replication/backtests");
    if (r2.ok) setEnv(await r2.json());
  });
  return (
    <div className="space-y-3">
      <BusyError error={running.error} className="block text-sm text-[#9B2335]" />
      <BacktestsView envelope={env} onRun={onRun} busy={running.busy} />
    </div>
  );
}
