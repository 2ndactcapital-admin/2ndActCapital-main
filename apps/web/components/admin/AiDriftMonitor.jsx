"use client";

import { useState } from "react";
import {
  STATUS_TONE, alertStatusLabel, canWrite, editableFields, fmtInt, fmtMs, fmtPct, fmtUsd, ruleOf, rules,
  sparkPath, statusLabel, thresholdDraft,
} from "@/lib/aiDrift.mjs";

// S28 — Super Admin → AI → Drift monitor (org admins: the same page scoped to their org, thresholds read-only).
// Every label comes from the server's envelope; the threshold editor renders ONLY when permissions.can_write is
// exactly true and the server listed the editable fields — a lost envelope fails closed.
const card = "rounded-[6px] border border-[#ece8dd] bg-white p-5";
const btn = "rounded border border-[#C5A880] px-2 py-1 text-xs text-navy hover:bg-[#F5F1EB]";
const th = "px-2 py-1 text-left text-xs font-medium text-text-muted";
const td = "px-2 py-1 align-top";

function Status({ envelope, value }) {
  const label = statusLabel(envelope, value);
  if (!label) return null;
  return <span className={`text-xs font-medium ${STATUS_TONE[value] || ""}`} data-status={value}>{label}</span>;
}

function Pair({ recent, baseline, fmt }) {
  return (
    <span>
      {fmt(recent)}
      <span className="block text-xs text-text-muted">baseline {fmt(baseline)}</span>
    </span>
  );
}

export function Spark({ values, label }) {
  const d = sparkPath(values);
  return (
    <figure className="inline-block">
      <svg width="120" height="28" role="img" aria-label={label} className="overflow-visible">
        {d ? <path d={d} fill="none" stroke="#1B2B4B" strokeWidth="1.25" /> : null}
      </svg>
      <figcaption className="text-xs text-text-muted">{label}</figcaption>
    </figure>
  );
}

export function TaskTable({ envelope }) {
  const tasks = envelope?.tasks;
  if (!Array.isArray(tasks)) return null;
  const scope = envelope?.permissions?.scope;
  return (
    <section className={card} data-testid="drift-tasks">
      <h2 className="font-serif text-xl text-navy">AI tasks</h2>
      <p className="mt-1 text-sm text-text-muted">
        Each task's last 24 hours against its own previous 28 days{scope === "org" ? " — your organization only" : " — every organization"}.
      </p>
      {tasks.length === 0 ? (
        <p className="mt-4 text-sm text-text-muted">No AI calls in the last 29 days.</p>
      ) : (
        <div className="mt-4 overflow-x-auto">
          <table className="min-w-full text-sm">
            <thead><tr>
              <th className={th}>Task</th><th className={th}>Status</th><th className={th}>Calls 24 h / 7 d</th>
              <th className={th}>Cost per call (median)</th><th className={th}>Latency p95</th>
              <th className={th}>Fallback</th><th className={th}>Failure</th><th className={th}>Bypass</th>
              <th className={th}>Models (7 d)</th><th className={th}>Open alerts</th>
            </tr></thead>
            <tbody>
              {tasks.map((t) => (
                <tr key={t.task_type} className="border-t border-[#ece8dd]" data-task={t.task_type}>
                  <td className={td}><a className="text-navy underline" href={t.link}>{t.task_type}</a></td>
                  <td className={td}><Status envelope={envelope} value={t.status} /></td>
                  <td className={td}>{fmtInt(t.recent_24h?.calls)} / {fmtInt(t.view_7d?.calls)}</td>
                  <td className={td}><Pair recent={t.recent_24h?.cost_median} baseline={t.baseline?.cost_median} fmt={fmtUsd} /></td>
                  <td className={td}><Pair recent={t.recent_24h?.latency_p95} baseline={t.baseline?.latency_p95} fmt={fmtMs} /></td>
                  <td className={td}><Pair recent={t.recent_24h?.fallback_rate} baseline={t.baseline?.fallback_rate} fmt={fmtPct} /></td>
                  <td className={td}><Pair recent={t.recent_24h?.failure_rate} baseline={t.baseline?.failure_rate} fmt={fmtPct} /></td>
                  <td className={td}><Pair recent={t.recent_24h?.bypass_rate} baseline={t.baseline?.bypass_rate} fmt={fmtPct} /></td>
                  <td className={td}>{(t.view_7d?.models || []).map((m) => <span key={m.model} className="block text-xs">{m.model}</span>)}</td>
                  <td className={td}>{t.open_alerts ? <span className="text-[#9B2335]">{t.open_alerts}</span> : "0"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

export function TaskDetail({ envelope }) {
  if (!envelope?.task_type || !Array.isArray(envelope?.series)) return null;
  const s = envelope.series;
  const col = (k) => s.map((p) => p[k]);
  const ruleLabel = (k) => ruleOf(envelope, k)?.label || "";
  return (
    <section className={card} data-testid="drift-detail">
      <h2 className="font-serif text-xl text-navy">{envelope.task_type}</h2>
      <p className="mt-1 text-sm text-text-muted">Daily, last {s.length} days (UTC).</p>
      <div className="mt-3 flex flex-wrap gap-6">
        <Spark values={col("calls")} label="Calls" />
        <Spark values={col("cost_median")} label="Cost per call (median)" />
        <Spark values={col("latency_p95")} label="Latency p95" />
        <Spark values={col("fallback_rate")} label="Fallback rate" />
        <Spark values={col("failure_rate")} label="Failure rate" />
        <Spark values={col("bypass_rate")} label="Bypass rate" />
      </div>
      {(envelope.orgs || []).map((o) => (
        <div key={o.org_id} className="mt-5" data-org={o.org_id}>
          <h3 className="text-sm font-medium text-navy">{o.org} <Status envelope={envelope} value={o.status} /></h3>
          <ul className="mt-1 space-y-0.5 text-sm">
            {(o.findings || []).map((f) => (
              <li key={f.rule}><Status envelope={envelope} value={f.status} /> <span className="text-text-muted">{ruleLabel(f.rule)}:</span> {f.sentence}</li>
            ))}
          </ul>
        </div>
      ))}
      <h3 className="mt-6 text-sm font-medium text-navy">Alerts</h3>
      {(envelope.alerts || []).length === 0 ? <p className="text-sm text-text-muted">None.</p> : (
        <ul className="mt-1 space-y-2 text-sm" data-testid="drift-alerts">
          {envelope.alerts.map((a) => (
            <li key={a.id} data-alert={a.status}>
              <span className="font-medium">{alertStatusLabel(envelope, a.status)}</span> · {ruleLabel(a.rule_key)} · {a.org_name}
              <span className="block text-text-muted">{a.detail}</span>
              <span className="block text-xs text-text-muted">
                opened {a.opened_at}{a.closed_at ? ` · closed ${a.closed_at} (${a.close_reason})` : ""} · {a.evaluations} evaluations
              </span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

export function ThresholdsPanel({ envelope, taskType = null, onSave = null }) {
  const live = envelope?.live;
  const ruleList = rules(envelope);
  const fields = editableFields(envelope);
  const writable = canWrite(envelope) && fields.length > 0 && typeof onSave === "function";
  const [draft, setDraft] = useState(null);
  const [err, setErr] = useState(null);
  if (!Array.isArray(live) || ruleList.length === 0) return null;
  const effective = (key) => live.find((t) => t.rule_key === key && taskType && t.task_type === taskType)
    || live.find((t) => t.rule_key === key && !t.task_type);
  const start = (rule) => {
    const t = effective(rule.key) || {};
    setErr(null);
    setDraft({ rule: rule.key, task_type: taskType, threshold: t.threshold ?? "", min_recent: t.min_recent ?? "",
      min_baseline: t.min_baseline ?? "", enabled: t.enabled === true, note: "" });
  };
  const save = async () => {
    const out = thresholdDraft(ruleOf(envelope, draft.rule), draft);
    if (out.error) { setErr(out.error); return; }
    try { await onSave(out.body); setDraft(null); } catch (e) { setErr(e.message); }
  };
  return (
    <section className={card} data-testid="drift-thresholds">
      <h2 className="font-serif text-xl text-navy">Thresholds{taskType ? ` — ${taskType}` : " — platform defaults"}</h2>
      <p className="mt-1 text-sm text-text-muted">
        {writable ? "Every change is a new version; the history is kept." : "Set by Hollisworks. Read-only."}
      </p>
      <table className="mt-3 min-w-full text-sm">
        <thead><tr><th className={th}>Rule</th><th className={th}>Threshold</th><th className={th}>Min recent / baseline</th>
          <th className={th}>On</th><th className={th}>Version</th>{writable ? <th className={th} /> : null}</tr></thead>
        <tbody>
          {ruleList.map((r) => {
            const t = effective(r.key);
            if (!t) return null;
            return (
              <tr key={r.key} className="border-t border-[#ece8dd]" data-rule={r.key}>
                <td className={td}>{r.label}{t.task_type ? <span className="block text-xs text-text-muted">task override</span> : null}</td>
                <td className={td}>{r.has_threshold ? `${t.threshold} ${r.unit_label || ""}` : "always an alert"}</td>
                <td className={td}>{t.min_recent} / {t.min_baseline}</td>
                <td className={td}>{t.enabled ? "Yes" : "No"}</td>
                <td className={td}>v{t.version}</td>
                {writable ? <td className={td}><button type="button" className={btn} onClick={() => start(r)}>Edit</button></td> : null}
              </tr>
            );
          })}
        </tbody>
      </table>
      {writable && draft ? (
        <div className="mt-4 space-y-2 text-sm" data-testid="threshold-editor">
          <p className="font-medium text-navy">{ruleOf(envelope, draft.rule)?.label}{taskType ? ` (override for ${taskType})` : " (platform default)"}</p>
          {ruleOf(envelope, draft.rule)?.has_threshold && fields.includes("threshold") ? (
            <label className="block">Threshold <input className="ml-2 rounded border border-border px-2 py-1" value={draft.threshold}
              onChange={(e) => setDraft({ ...draft, threshold: e.target.value })} /></label>) : null}
          {fields.includes("min_recent") ? <label className="block">Minimum recent <input className="ml-2 w-20 rounded border border-border px-2 py-1"
            value={draft.min_recent} onChange={(e) => setDraft({ ...draft, min_recent: e.target.value })} /></label> : null}
          {fields.includes("min_baseline") ? <label className="block">Minimum baseline <input className="ml-2 w-20 rounded border border-border px-2 py-1"
            value={draft.min_baseline} onChange={(e) => setDraft({ ...draft, min_baseline: e.target.value })} /></label> : null}
          {fields.includes("enabled") ? <label className="block"><input type="checkbox" checked={draft.enabled}
            onChange={(e) => setDraft({ ...draft, enabled: e.target.checked })} /> On</label> : null}
          {fields.includes("note") ? <label className="block">Why <input className="ml-2 w-96 rounded border border-border px-2 py-1"
            value={draft.note} onChange={(e) => setDraft({ ...draft, note: e.target.value })} /></label> : null}
          {err ? <p className="text-[#9B2335]">{err}</p> : null}
          <button type="button" className={btn} onClick={save}>Save new version</button>
          <button type="button" className={`${btn} ml-2`} onClick={() => setDraft(null)}>Cancel</button>
        </div>
      ) : null}
      <details className="mt-4 text-sm">
        <summary className="cursor-pointer text-navy">History</summary>
        <ul className="mt-2 space-y-1">
          {(envelope.history || []).filter((h) => !taskType || !h.task_type || h.task_type === taskType).map((h) => (
            <li key={h.id} className="text-xs text-text-muted">
              {ruleOf(envelope, h.rule_key)?.label} {h.task_type ? `(${h.task_type})` : "(platform)"} v{h.version}:
              {" "}{h.threshold ?? "—"} · {h.min_recent}/{h.min_baseline} · {h.enabled ? "on" : "off"} · from {h.valid_from}
              {h.valid_to ? ` to ${h.valid_to}` : " (live)"}{h.change_note ? ` — ${h.change_note}` : ""}
            </li>
          ))}
        </ul>
      </details>
    </section>
  );
}

async function call(url, opts) {
  const res = await fetch(url, opts ? { ...opts, headers: { "Content-Type": "application/json" } } : undefined);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data?.error || "Request failed");
  return data;
}

export default function AiDriftMonitor({ initialOverview, initialThresholds, initialDetail = null }) {
  const [thresholds, setThresholds] = useState(initialThresholds);
  const [msg, setMsg] = useState(null);
  const base = "/api/admin/ai-drift";
  const save = async (body) => {
    await call(`${base}/thresholds`, { method: "PUT", body: JSON.stringify(body) });
    setThresholds(await call(`${base}/thresholds`));
    setMsg("Saved as a new version.");
  };
  return (
    <div className="space-y-6">
      {msg ? <p className="text-sm text-navy">{msg}</p> : null}
      {initialDetail ? <TaskDetail envelope={initialDetail} /> : <TaskTable envelope={initialOverview} />}
      <ThresholdsPanel envelope={thresholds} taskType={initialDetail?.task_type || null}
        onSave={canWrite(thresholds) ? save : null} />
    </div>
  );
}
