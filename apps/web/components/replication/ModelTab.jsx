"use client";

/**
 * ModelTab — REP4, the "Model" tab of a Replication Studio entry: build a
 * "Holdings of <TICKER>" model from the fund.
 *
 * Viability: the five questions as a vertical checklist (yes / no / unknown,
 * the server's plain-language reason and what is missing), custodian and
 * account-size pickers, an "as of" stamp and the N-PORT staleness warning.
 * Cost comparison: fund vs replica, every unknown shown as unknown, the tax
 * line never a number. Registry: the existing model for the chosen key is
 * offered FIRST ("Use this model"); otherwise Build, after a preview of the
 * positions against the fund's holdings (dropped rows, departures).
 *
 * Every write control renders only from `modelControls(envelope)` — which
 * needs the server's own `can_build` flag — and every label comes from the
 * envelope's vocabularies. A missing envelope renders no control.
 */

import { useEffect, useState } from "react";
import {
  buildBody, costRows, findOffer, formatWeight, labelOf, modelControls, offerText, viabilityRows,
} from "@/lib/replication.mjs";

const card = "rounded-[6px] border border-[#ece8dd] bg-white";
const input = "rounded border border-[#E2E8F0] px-2 py-1";

async function call(method, path, body) {
  const res = await fetch(`/api/replication/${path}`, {
    method, headers: body ? { "Content-Type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const missing = Array.isArray(data?.detail?.missing) ? ` (${data.detail.missing.join("; ")})` : "";
    throw new Error(`${data.error || `Request failed (${res.status})`}${missing}`);
  }
  return data;
}

export function SourcesLine({ envelope }) {
  const s = envelope?.sources || {};
  return (
    <div className={`${card} p-4 text-sm`}>
      <p className="text-text-muted">
        {s.terms_version ? `Approved terms v${s.terms_version.version_no}` : "No approved terms version"}
        {" · "}
        {s.snapshot ? `Live holdings as of ${s.snapshot.as_of} (${s.snapshot.age_days} days old, ${s.snapshot.row_count} rows)`
          : "No live holdings snapshot"}
      </p>
      {s.snapshot?.nport_stale ? (
        <p className="mt-1 text-[#9B2335]">These holdings come from an N-PORT filing {s.snapshot.age_days} days old — the fund may have moved since.</p>
      ) : null}
      {Array.isArray(s.missing) && s.missing.length ? (
        <p className="mt-1 text-[#9B2335]">A model cannot be built yet: {s.missing.join(" and ")}.</p>
      ) : null}
    </div>
  );
}

export function ViabilityChecklist({ report, vocab }) {
  const rows = viabilityRows(report, vocab);
  if (!report) return <p className="text-sm text-text-muted">No viability check yet for this fund.</p>;
  return (
    <div className="space-y-3">
      <p className="text-xs text-text-muted">
        As of {String(report.computed_at || "").slice(0, 16).replace("T", " ")} UTC · {labelOf(vocab?.custodians, report.custodian_code)}
        {" · "}${Number(report.account_size).toLocaleString("en-US")} · {labelOf(vocab?.answer_statuses, report.overall)} overall
      </p>
      <ol className={`${card} divide-y divide-[#ece8dd]`}>
        {rows.map((r, i) => (
          <li key={r.key} className="p-4 text-sm">
            <div className="flex items-baseline gap-3">
              <span className="w-6 text-text-muted">{i + 1}</span>
              <span className="flex-1 font-medium text-navy">{r.question}</span>
              <span className={`font-semibold ${r.tone}`}>{r.statusLabel}</span>
            </div>
            <p className="mt-1 pl-9 text-[#334155]">{r.reason}</p>
            {r.missing.length ? (
              <ul className="mt-1 pl-9 text-xs text-text-muted">
                {r.missing.slice(0, 12).map((m) => <li key={m}>Missing: {m}</li>)}
              </ul>
            ) : null}
          </li>
        ))}
      </ol>
    </div>
  );
}

export function CostTable({ report, vocab }) {
  const rows = costRows(report, vocab);
  if (!rows.length) return null;
  return (
    <div className={`${card} overflow-x-auto`}>
      <table className="w-full text-sm">
        <thead className="border-b border-[#ece8dd] text-left text-text-muted">
          <tr><th className="px-4 py-2 font-medium">Fund versus replica, a year</th><th className="px-4 py-2 font-medium">Amount</th><th className="px-4 py-2 font-medium">How</th></tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.key} className="border-b border-[#ece8dd] last:border-0">
              <td className="px-4 py-2">{r.label}</td>
              <td className={`px-4 py-2 ${r.unknown ? "text-text-muted" : "text-navy"}`}>{r.amount}</td>
              <td className="px-4 py-2 text-xs text-text-muted">{r.key === "tax_benefit" ? "" : r.reason}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function PreviewTable({ preview, vocab }) {
  if (!preview) return null;
  const c = preview.construction || {};
  const fundRows = Array.isArray(c.fund_rows) ? c.fund_rows : [];
  return (
    <div className="space-y-3 text-sm">
      <p className="text-text-muted">
        {preview.name ? `Will create "${preview.name}"` : "Matches an existing model"} · {c.counts?.positions ?? 0} positions
        {" · "}{c.counts?.dropped ?? 0} fund rows not held · from the {preview.snapshot?.as_of} holdings, terms v{preview.terms_version?.version_no}
      </p>
      {(preview.departures || []).length ? (
        <div className={`${card} p-4`}>
          {(preview.departures || []).map((d) => <p key={d.kind} className="text-navy">Departs from the fund: {d.text}</p>)}
        </div>
      ) : null}
      <div className={`${card} max-h-96 overflow-auto`}>
        <table className="w-full">
          <thead className="border-b border-[#ece8dd] text-left text-text-muted">
            <tr><th className="px-4 py-2 font-medium">Holding</th><th className="px-4 py-2 font-medium">Fund</th><th className="px-4 py-2 font-medium">Model</th><th className="px-4 py-2 font-medium">Note</th></tr>
          </thead>
          <tbody>
            {fundRows.map((r) => (
              <tr key={r.row_num} className="border-b border-[#ece8dd] last:border-0">
                <td className="px-4 py-2">{r.name || r.label}{r.ticker ? ` (${r.ticker})` : ""}</td>
                <td className="px-4 py-2">{formatWeight(r.fund_weight)}</td>
                <td className="px-4 py-2">{r.model_weight == null ? "—" : formatWeight(r.model_weight)}</td>
                <td className="px-4 py-2 text-xs text-text-muted">
                  {r.dropped_reason ? labelOf(vocab?.drop_reasons, r.dropped_reason)
                    : r.resolution_state === "unresolved" ? "Unresolved — blocks approval until resolved" : ""}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

export function ModelTabView({ envelope, form, onForm, report, preview, busy, msg, onCheck, onPreview, onBuild }) {
  const vocab = envelope?.vocabularies || {};
  const controls = modelControls(envelope);
  const f = form || {};
  const offer = findOffer(envelope, f);
  const shown = report || (Array.isArray(envelope?.reports) ? envelope.reports[0] : null);
  const set = (k) => (e) => onForm?.({ ...f, [k]: e.target.value });
  const models = Array.isArray(envelope?.models) ? envelope.models : [];
  const events = Array.isArray(envelope?.monitoring) ? envelope.monitoring : [];
  return (
    <div className="space-y-5">
      <p className="text-xs text-text-muted">{envelope?.entry?.disclosure}</p>
      <SourcesLine envelope={envelope} />

      <section className="space-y-3">
        <h2 className="text-lg font-semibold text-navy">Viability</h2>
        <div className="flex flex-wrap items-end gap-3 text-sm">
          <label className="flex flex-col gap-1">Custodian
            <select className={input} value={f.custodian_code || ""} onChange={set("custodian_code")}>
              {(vocab.custodians || []).map((c) => <option key={c.key} value={c.key}>{c.label}</option>)}
            </select>
          </label>
          <label className="flex flex-col gap-1">Account size ($)
            <input className={input} value={f.account_size || ""} onChange={set("account_size")} />
          </label>
          {controls.viability ? (
            <button type="button" disabled={busy} className="rounded bg-navy px-3 py-1 text-white disabled:opacity-50" onClick={onCheck}>Check viability</button>
          ) : null}
        </div>
        <ViabilityChecklist report={shown} vocab={vocab} />
        <CostTable report={shown} vocab={vocab} />
      </section>

      <section className="space-y-3">
        <h2 className="text-lg font-semibold text-navy">Model</h2>
        {offer ? (
          <div className={`${card} flex flex-wrap items-center gap-3 p-4 text-sm`}>
            <span className="flex-1">
              {offer.live_version ? "An approved model already exists: " : "A model for this choice is already being built: "}
              <span className="font-medium text-navy">{offerText(offer, vocab)}</span>
            </span>
            {controls.use ? (
              <a className="rounded bg-navy px-3 py-1 text-white" href={`/portfolio/models/${offer.model.id}`}>Use this model</a>
            ) : null}
          </div>
        ) : null}
        {!offer && controls.build ? (
          <div className={`${card} space-y-3 p-4 text-sm`}>
            <div className="flex flex-wrap items-end gap-3">
              <label className="flex flex-col gap-1">Method
                <select className={input} value={f.method || "full"} onChange={set("method")}>
                  {(vocab.methods || []).map((m) => <option key={m.key} value={m.key}>{m.label}</option>)}
                </select>
              </label>
              {f.method === "top_n" ? (
                <label className="flex flex-col gap-1">Top N
                  <input className={`${input} w-24`} value={f.top_n || ""} onChange={set("top_n")} />
                </label>
              ) : null}
              <label className="flex flex-col gap-1">Minimum weight (%)
                <input className={`${input} w-28`} value={f.min_weight_pct || ""} onChange={set("min_weight_pct")} placeholder="0" />
              </label>
              <label className="flex flex-col gap-1">Cash target (%)
                <input className={`${input} w-28`} value={f.cash_target_pct || ""} onChange={set("cash_target_pct")} placeholder="0" />
              </label>
              <label className="flex flex-col gap-1">Unmatched holdings
                <select className={input} value={f.unresolved_policy || "block"} onChange={set("unresolved_policy")}>
                  {(vocab.unresolved_policies || []).map((p) => <option key={p.key} value={p.key}>{p.label}</option>)}
                </select>
              </label>
              <label className="flex flex-col gap-1">Rebalance
                <select className={input} value={f.rebalance_frequency || ""} onChange={set("rebalance_frequency")}>
                  <option value="">As the fund does</option>
                  {(vocab.rebalance_frequencies || []).map((r) => <option key={r.key} value={r.key}>{r.label}</option>)}
                </select>
              </label>
              {f.rebalance_frequency ? (
                <label className="flex min-w-[16rem] flex-1 flex-col gap-1">Reason for departing from the fund
                  <input className={input} value={f.rebalance_reason || ""} onChange={set("rebalance_reason")} />
                </label>
              ) : null}
            </div>
            <div className="flex gap-2">
              {controls.preview ? (
                <button type="button" disabled={busy} className="rounded border border-navy px-3 py-1 text-navy disabled:opacity-50" onClick={onPreview}>Preview</button>
              ) : null}
              {controls.build && preview && !preview.existing ? (
                <button type="button" disabled={busy} className="rounded bg-navy px-3 py-1 text-white disabled:opacity-50" onClick={onBuild}>Build model</button>
              ) : null}
            </div>
            <PreviewTable preview={preview} vocab={vocab} />
          </div>
        ) : null}
        {!offer && !controls.build ? <p className="text-sm text-text-muted">No model has been built from this fund for this choice yet.</p> : null}
        {msg ? <p className="text-sm text-[#9B2335]">{msg}</p> : null}
      </section>

      {models.length ? (
        <section className={`${card} p-4 text-sm`}>
          <h3 className="font-semibold text-navy">Models built from this fund</h3>
          <ul className="mt-2 space-y-1">
            {models.map((m) => (
              <li key={m.model.id}>
                {controls.openModel ? <a className="text-navy underline" href={`/portfolio/models/${m.model.id}`}>{m.model.name}</a> : m.model.name}
                {" — "}{m.live_version ? `v${m.live_version.version_no} approved` : "no approved version"}
                {m.open_version ? ` · v${m.open_version.version_no} open` : ""} · {labelOf(vocab.custodians, m.key.custodian_code)}
                {" · "}{m.accounts_using} accounts
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      {events.length ? (
        <section className={`${card} p-4 text-sm`}>
          <h3 className="font-semibold text-navy">Monitoring</h3>
          <ul className="mt-2 space-y-1">
            {events.map((e, i) => (
              <li key={`${e.created_at}-${i}`} className="text-[#334155]">
                {String(e.created_at).slice(0, 16).replace("T", " ")} · {e.model_name} ·{" "}
                {e.action === "holdings_monitor" ? labelOf(vocab.monitor_outcomes, e.detail?.outcome)
                  : e.action === "terms_monitor" ? `Fund terms changed: ${(e.detail?.changed || []).map((c) => c.field_key).join(", ")}`
                    : labelOf(vocab.events, e.action) || e.action}
                {e.detail?.turnover ? ` (turnover ${formatWeight(e.detail.turnover)})` : ""}
              </li>
            ))}
          </ul>
        </section>
      ) : null}
    </div>
  );
}

export default function ModelTab({ entryId, initialEnvelope = null }) {
  const [env, setEnv] = useState(initialEnvelope);
  const [form, setForm] = useState(null);
  const [report, setReport] = useState(null);
  const [preview, setPreview] = useState(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState(null);

  async function reload() {
    try {
      const data = await call("GET", `registry/${entryId}/model`);
      setEnv(data);
      setForm((f) => f || { custodian_code: data.defaults?.custodian_code, method: data.defaults?.method,
        unresolved_policy: data.defaults?.unresolved_policy, account_size: data.defaults?.account_size });
    } catch (err) { setMsg(err.message); }
  }
  useEffect(() => { reload(); }, [entryId]); // eslint-disable-line react-hooks/exhaustive-deps

  async function act(fn) {
    setBusy(true); setMsg(null);
    try { await fn(); } catch (err) { setMsg(err.message); } finally { setBusy(false); }
  }
  const onForm = (next) => { setForm(next); setPreview(null); };
  const onCheck = () => act(async () => {
    setReport(await call("POST", `registry/${entryId}/viability`, {
      custodian_code: form.custodian_code, account_size: form.account_size || null,
      method: form.method || "full", ...(form.method === "top_n" ? { top_n: Number(form.top_n) } : {}) }));
  });
  const onPreview = () => act(async () => setPreview(await call("POST", `registry/${entryId}/models/preview`, buildBody(form))));
  const onBuild = () => act(async () => { await call("POST", `registry/${entryId}/models`, buildBody(form)); setPreview(null); await reload(); });

  if (!env) return <p className="text-sm text-text-muted">{msg || "Loading…"}</p>;
  return <ModelTabView envelope={env} form={form} onForm={onForm} report={report} preview={preview} busy={busy}
    msg={msg} onCheck={onCheck} onPreview={onPreview} onBuild={onBuild} />;
}
