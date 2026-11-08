"use client";

/**
 * WorkflowAutonomy — S29B. Admin → Workflows → Autonomy: the organization switch and both kill switches, the limits
 * with today's use, every registered verb with its autonomy class, requests awaiting a decision (a DIFFERENT person
 * decides — services.sod on the server), and shadow runs awaiting the trigger owner's review. Controls from
 * lib/workflowAutonomy.mjs (fail closed); every key and class comes from the server's own response. Light / cream only.
 * Money movement, custody and trade submission are never run unattended (cash stage C needs counsel).
 */

import { useState } from "react";
import { canDecide, controls, limitRows, switches, verbsByClass } from "@/lib/workflowAutonomy.mjs";

const card = "rounded-[6px] border border-[#ece8dd] bg-white p-5";
const input = "rounded border border-[#E2E8F0] px-3 py-2";
const primary = "rounded bg-navy px-3 py-1 text-white disabled:opacity-50";
const secondary = "rounded border border-[#E2E8F0] px-3 py-1 text-navy disabled:opacity-50";
const th = "px-2 py-1 text-left font-medium text-text-muted";
const td = "px-2 py-1 align-top";
const words = (k) => String(k || "").replace(/[_.]/g, " ");

async function send(path, method = "GET", body) {
  const res = await fetch(`/api/workflow-autonomy${path ? `/${path}` : ""}`, {
    method, headers: body ? { "Content-Type": "application/json" } : undefined, body: body ? JSON.stringify(body) : undefined,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || "Request failed");
  return data;
}

export function SwitchesView({ env, onSet, busy }) {
  const c = controls(env);
  const s = switches(env);
  return (
    <div className={card}>
      <h2 className="font-serif text-xl text-navy">Switches</h2>
      {s.stopped && (
        <p className="mt-2 text-sm text-[#9B2335]">
          Unattended execution is stopped ({s.platformKill ? "platform kill switch" : "this organization's kill switch"}).
          Every unattended step waits for a person.
        </p>
      )}
      <table className="mt-3 w-full text-sm">
        <tbody>
          <tr className="border-t border-[#ece8dd]">
            <td className={td}>Unattended writes ({s.name})</td>
            <td className={td}>{s.autonomy ? "On" : "Off"}</td>
            <td className={td}>{c.autonomy && (
              <button className={secondary} disabled={busy}
                onClick={() => onSet("settings", { key: "autonomy", value: !s.autonomy })}>
                {s.autonomy ? "Turn off" : "Ask to turn on"}</button>)}</td>
          </tr>
          <tr className="border-t border-[#ece8dd]">
            <td className={td}>Organization kill switch</td>
            <td className={td}>{s.orgKill ? "Stopped" : "Not set"}</td>
            <td className={td}>{c.orgKill && (
              <button className={secondary} disabled={busy}
                onClick={() => onSet("settings", { key: "kill_switch", value: !s.orgKill })}>
                {s.orgKill ? "Clear" : "Stop all unattended steps"}</button>)}</td>
          </tr>
          <tr className="border-t border-[#ece8dd]">
            <td className={td}>Platform kill switch</td>
            <td className={td}>{s.platformKill ? "Stopped" : "Not set"}</td>
            <td className={td}>{c.platformKill && (
              <button className={secondary} disabled={busy}
                onClick={() => onSet("platform-kill-switch", { on: !s.platformKill })}>
                {s.platformKill ? "Clear" : "Stop everywhere"}</button>)}</td>
          </tr>
        </tbody>
      </table>
      <p className="mt-2 text-xs text-text-muted">Turning anything off, or stopping, applies at once. Turning unattended
        writes on, raising a limit or needing fewer shadow runs is a request a different person decides.</p>
    </div>
  );
}

export function LimitsView({ env }) {
  const rows = limitRows(env);
  return (
    <div className={card}>
      <h2 className="font-serif text-xl text-navy">Limits</h2>
      <p className="mt-1 text-xs text-text-muted">Over a limit the run holds and the trigger owner and administrators are
        told. {Number.isInteger(env?.shadow_runs_required) ? `${env.shadow_runs_required} clean shadow runs are required before a workflow version may run unattended.` : ""}</p>
      <table className="mt-3 w-full text-sm">
        <thead><tr><th className={th}>Limit</th><th className={th}>Value</th><th className={th}>Used today</th></tr></thead>
        <tbody>{rows.map((r) => (
          <tr key={r.key} className="border-t border-[#ece8dd]"><td className={td}>{words(r.key)}</td>
            <td className={td}>{r.value ?? "—"}</td><td className={td}>{r.used ?? "—"}</td></tr>))}</tbody>
      </table>
    </div>
  );
}

export function VerbsView({ env }) {
  const grouped = verbsByClass(env);
  return (
    <div className={card}>
      <h2 className="font-serif text-xl text-navy">Verbs by autonomy class</h2>
      {Object.entries(grouped).map(([cls, verbs]) => (
        <div key={cls} className="mt-4">
          <h3 className="text-sm font-medium text-navy">{words(cls)} ({verbs.length})</h3>
          <table className="mt-1 w-full text-sm">
            <tbody>{verbs.map((v) => (
              <tr key={v.key} className="border-t border-[#ece8dd]">
                <td className={td}><code>{v.key}</code></td>
                <td className={td}>{v.writes || "—"}</td>
                <td className={td}>{(v.why_never || []).join(", ") || (v.platform_job ? "platform job" : "")}</td>
              </tr>))}</tbody>
          </table>
        </div>
      ))}
    </div>
  );
}

export function RequestsView({ env, viewerId, onDecide, busy }) {
  const drafts = Array.isArray(env?.drafts) ? env.drafts : [];
  return (
    <div className={card}>
      <h2 className="font-serif text-xl text-navy">Awaiting a decision</h2>
      {drafts.length === 0 ? <p className="mt-2 text-sm text-text-muted">Nothing is awaiting a decision.</p> : (
        <table className="mt-3 w-full text-sm">
          <thead><tr><th className={th}>Setting</th><th className={th}>Asked for</th><th className={th}>Why</th>
            <th className={th} /></tr></thead>
          <tbody>{drafts.map((d) => (
            <tr key={d.id} className="border-t border-[#ece8dd]">
              <td className={td}>{words(d.setting_key)}</td>
              <td className={td}><code>{JSON.stringify(d.setting_value)}</code></td>
              <td className={td}>{d.reason}</td>
              <td className={td}>{canDecide(env, d, viewerId) && (
                <span className="flex gap-2">
                  <button className={primary} disabled={busy} onClick={() => onDecide(d.id, "approve")}>Approve</button>
                  <button className={secondary} disabled={busy} onClick={() => onDecide(d.id, "reject")}>Reject</button>
                </span>)}</td>
            </tr>))}</tbody>
        </table>
      )}
    </div>
  );
}

export function ShadowReviewsView({ env, onReview, busy }) {
  const c = controls(env);
  const rows = Array.isArray(env?.shadow_reviews_pending) ? env.shadow_reviews_pending : [];
  return (
    <div className={card}>
      <h2 className="font-serif text-xl text-navy">Shadow runs awaiting review</h2>
      <p className="mt-1 text-xs text-text-muted">A shadow run records every write it would have made and executes
        none. The owner of the trigger reviews each one.</p>
      {rows.length === 0 ? <p className="mt-2 text-sm text-text-muted">No shadow run is awaiting review.</p> : (
        <table className="mt-3 w-full text-sm">
          <tbody>{rows.map((r) => (
            <tr key={r.run_id} className="border-t border-[#ece8dd]">
              <td className={td}>{r.name} v{r.version_number}</td>
              <td className={td}><a className="text-gold hover:underline" href={`/admin/workflows/runs?run=${r.run_id}`}>Open run</a></td>
              <td className={td}>{c.reviewShadow && (
                <span className="flex gap-2">
                  <button className={primary} disabled={busy} onClick={() => onReview(r.run_id, "clean")}>Clean</button>
                  <button className={secondary} disabled={busy} onClick={() => onReview(r.run_id, "not_clean")}>Not clean</button>
                </span>)}</td>
            </tr>))}</tbody>
        </table>
      )}
    </div>
  );
}

export default function WorkflowAutonomy({ initial }) {
  const [env, setEnv] = useState(initial);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [reason, setReason] = useState("");

  async function act(fn) {
    setBusy(true);
    setError(null);
    try {
      await fn();
      setEnv(await send(""));
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }
  const why = () => reason.trim() || "set from Admin → Workflows → Autonomy";
  const onSet = (path, body) => act(() => send(path, "POST", { ...body, reason: why() }));
  const onDecide = (id, outcome) => act(() => send(`settings/${id}/decide`, "POST",
    outcome === "reject" ? { outcome, note: why() } : { outcome }));
  const onReview = (runId, outcome) => act(() => send(`runs/${runId}/shadow-review`, "POST",
    outcome === "not_clean" ? { outcome, note: why() } : { outcome }));
  const c = controls(env);
  const anyWrite = Object.values(c).some(Boolean);

  return (
    <div className="space-y-6">
      {error && <div className="rounded-[6px] border border-[#9B2335] bg-white p-3 text-sm text-[#9B2335]">{error}</div>}
      {anyWrite && (
        <div className={card}>
          <label className="text-sm text-text-secondary">Reason (recorded with every change)
            <input className={`${input} mt-1 block w-full`} value={reason} onChange={(e) => setReason(e.target.value)} />
          </label>
        </div>
      )}
      <SwitchesView env={env} onSet={onSet} busy={busy} />
      <RequestsView env={env} viewerId={env?.viewer_id} onDecide={onDecide} busy={busy} />
      <ShadowReviewsView env={env} onReview={onReview} busy={busy} />
      <LimitsView env={env} />
      <VerbsView env={env} />
    </div>
  );
}
