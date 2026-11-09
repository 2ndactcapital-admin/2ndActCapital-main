"use client";

/**
 * PolicyPanel — Household → Treasury → Cash policy (CASH02). Per scope (the household, and any entity that pays its
 * own obligations): the ACTIVE policy's three sleeves (Near-term / Reserve / Long-term) in months, $ and % of the
 * liquid total with their estimated yields; for an author the policy editor (two boundary sliders, rung count, preset
 * chips, buffer, objective, minimum coverage; the live derived view comes from the server's preview route; "draft —
 * not saved" until saved; the diff vs active), the 3-step questionnaire, and the approval status and history with
 * Approve / Reject only for a DIFFERENT eligible person. A member sees their family's active policy, read-only, with
 * the disclosure library's labels. Every control renders only from lib/cashPolicy.mjs (fails closed). Light / cream.
 * A policy is a target: nothing here moves money.
 */

import { useState } from "react";
import {
  bounds, coverageLabel, diffRows, initialForm, makersText, money, objectiveScale, policyBody, policyControls,
  presetOptions, recommendationBody, scopes, setBoundary, sleeveRows, statusLabel, yieldLabel, yieldText,
} from "@/lib/cashPolicy.mjs";
import { planBlendedFor } from "@/lib/cashPlan.mjs";

const card = "rounded-[6px] border border-[#ece8dd] bg-white p-4";
const input = "rounded border border-[#E2E8F0] px-3 py-2";
const primary = "rounded bg-navy px-3 py-1 text-white disabled:opacity-50";
const secondary = "rounded border border-navy px-3 py-1 text-navy disabled:opacity-50";
const chip = (on) => `rounded-full border px-3 py-1 text-sm ${on ? "border-[#C5A880] bg-[#E8D5A3] text-navy" : "border-[#E2E8F0] text-[#334155]"}`;
const serif = { fontFamily: "'Spectral', Georgia, serif" };

async function post(path, body) {
  const res = await fetch(`/api/cash/${path}`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || data.error || `Request failed (${res.status})`);
  return data;
}

export function SleeveTable({ derived, env, caption }) {
  const rows = sleeveRows(derived);
  if (!rows.length) return null;
  const yl = yieldLabel(env);
  return (
    <div>
      <table className="w-full text-sm" aria-label={caption || "Sleeves"}>
        <thead className="text-left text-text-muted"><tr><th className="py-1">Sleeve</th><th>Covers</th><th>Target</th>
          <th>% of liquid</th><th>Estimated yield</th><th>After-tax</th></tr></thead>
        <tbody>{rows.map((r) => (
          <tr key={r.key} className="border-t border-[#ece8dd] align-top">
            <td className="py-1 text-navy">{r.label}</td>
            <td>{r.window}{r.note ? <span className="block text-xs text-text-muted">{r.note}</span> : null}</td>
            <td>{r.amount}<span className="block text-xs text-text-muted">{r.count} {r.count === 1 ? "obligation" : "obligations"}</span></td>
            <td>{r.pct}</td>
            <td className="text-xs">{r.yield}</td>
            <td className="text-xs">{r.afterTax}</td>
          </tr>))}
        </tbody>
      </table>
      <p className="mt-2 text-xs text-text-muted">
        Liquid {money(derived?.liquid) !== "—" ? money(derived.liquid) : (derived?.liquid_text || "—")}; targets total {money(derived?.total)};
        unallocated {money(derived?.unallocated)}. Blended estimate: {yieldText(derived?.blended)}. As of {derived?.as_of}.
      </p>
      {derived?.coverage ? <p className="text-xs text-text-muted">Coverage {derived.coverage.months ?? derived.coverage.months_text} months against a minimum of {derived.coverage.min_months}{derived.coverage.below_minimum ? " — below the minimum" : ""}.</p> : null}
      {yl ? <p className="mt-1 text-xs text-text-muted" data-label="cash.yield_estimate">{yl}</p> : null}
    </div>
  );
}

export function PolicySummary({ version }) {
  if (!version) return null;
  return (
    <p className="text-sm text-[#334155]">
      v{version.version_no} · {version.preset_label || version.preset} · Near-term to month {version.near_months}, Reserve to
      month {version.reserve_months}, horizon {version.horizon_months} months · {version.rung_count} rungs · buffer {version.buffer_months} ·
      objective {version.objective} ({version.objective_score}) · minimum coverage {version.min_coverage_months} months
    </p>
  );
}

export function PolicyEditor({ env, scope, onSaved }) {
  const c = policyControls(env, scope);
  const [form, setForm] = useState(() => initialForm(env, scope));
  const [view, setView] = useState(scope?.open?.derived || scope?.active?.derived || null);
  const [diff, setDiff] = useState(scope?.open?.diff || []);
  const [dirty, setDirty] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState(null);
  if (!c.edit) return null;
  const hid = env.household.id;
  const refresh = async (f) => {
    try {
      const out = await post(`households/${hid}/policy/preview`, policyBody(f));
      setView(out.derived); setDiff(out.diff || []); setErr(null);
      setForm((x) => ({ ...x, near_months: out.fields.near_months, reserve_months: out.fields.reserve_months }));
    } catch (e) { setErr(e.message); }
  };
  const change = (f) => { setForm(f); setDirty(true); if (c.preview) refresh(f); };
  const save = async () => {
    setBusy(true); setErr(null);
    try { await post(`households/${hid}/policies`, policyBody(form)); setDirty(false); onSaved?.(); }
    catch (e) { setErr(e.message); } finally { setBusy(false); }
  };
  const nb = bounds(env, "near_months");
  const hb = bounds(env, "horizon_months");
  const max = Math.min(form.horizon_months || hb.max, nb.max);
  return (
    <div className={`${card} space-y-3`} aria-label="Policy editor">
      <div className="flex flex-wrap items-center gap-2">
        <h4 className="mr-auto font-semibold text-navy">Edit the policy</h4>
        {dirty ? <span className="text-xs text-[#9B2335]">draft — not saved</span> : null}
        {presetOptions(env).map((p) => (
          <button key={p.key} type="button" className={chip(form.preset === p.key)} title={p.note}
            onClick={() => change({ ...form, preset: p.key })}>{p.label}</button>))}
      </div>
      <label className="block text-sm">Near-term / Reserve boundary: month {form.near_months}
        <input className="w-full" type="range" min={nb.min} max={max} step={1} value={form.near_months ?? 0}
          onChange={(e) => change(setBoundary(form, "near_months", e.target.value, env))} /></label>
      <label className="block text-sm">Reserve / Long-term boundary: month {form.reserve_months}
        <input className="w-full" type="range" min={nb.min} max={max} step={1} value={form.reserve_months ?? 0}
          onChange={(e) => change(setBoundary(form, "reserve_months", e.target.value, env))} /></label>
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-5">
        <label className="text-sm">Horizon (months)<input className={input} type="number" min={hb.min} max={hb.max} value={form.horizon_months ?? ""}
          onChange={(e) => change({ ...form, horizon_months: e.target.value })} /></label>
        <label className="text-sm">Rungs<input className={input} type="number" min={1} max={52} value={form.rung_count ?? ""}
          onChange={(e) => change({ ...form, rung_count: e.target.value })} /></label>
        <label className="text-sm">Buffer (months)<input className={input} value={form.buffer_months ?? ""}
          onChange={(e) => change({ ...form, buffer_months: e.target.value })} /></label>
        <label className="text-sm">Objective<select className={input} value={form.objective_score ?? 3}
          onChange={(e) => change({ ...form, objective_score: Number(e.target.value) })}>
          {objectiveScale(env).map((o) => <option key={o.score} value={o.score}>{o.score} — {o.label}</option>)}</select></label>
        <label className="text-sm">Minimum coverage (months)<input className={input} value={form.min_coverage_months ?? ""}
          onChange={(e) => change({ ...form, min_coverage_months: e.target.value })} /></label>
      </div>
      <SleeveTable derived={view} env={env} caption="Derived sleeves (draft)" />
      {diffRows(diff).length ? <div aria-label="Changes from the active policy"><p className="text-sm font-semibold text-navy">Changes from the active policy</p>
        <ul className="text-sm">{diffRows(diff).map((d) => <li key={d}>{d}</li>)}</ul></div> : null}
      <div className="flex gap-2"><button type="button" className={primary} disabled={busy} onClick={save}>Save draft</button></div>
      <p className="text-xs text-text-muted">Saving creates a draft. It takes effect only when a different person approves it.</p>
      {err ? <p className="text-sm text-[#9B2335]">{err}</p> : null}
    </div>
  );
}

export function Questionnaire({ env, scope, onSaved }) {
  const c = policyControls(env, scope);
  const q = scope?.questionnaire || {};
  const [step, setStep] = useState(1);
  const [outflow, setOutflow] = useState(q.scheduled_12m || "");
  const [score, setScore] = useState(3);
  const [rec, setRec] = useState(null);
  const [warnings, setWarnings] = useState(q.warnings || []);
  const [err, setErr] = useState(null);
  if (!c.questionnaire) return null;
  const hid = env.household.id;
  const base = initialForm(env, scope);
  const recommend = async () => {
    try {
      const out = await post(`households/${hid}/policy/preview`, recommendationBody(base, score, outflow));
      setRec(out); setWarnings(out.questionnaire?.warnings || []); setStep(3); setErr(null);
    } catch (e) { setErr(e.message); }
  };
  const apply = async () => {
    try { await post(`households/${hid}/policies`, recommendationBody(base, score, outflow)); onSaved?.(); }
    catch (e) { setErr(e.message); }
  };
  return (
    <div className={`${card} space-y-2`} aria-label="Recommendation questionnaire">
      <h4 className="font-semibold text-navy">Recommendation — step {step} of 3</h4>
      {step === 1 ? (
        <div className="space-y-1 text-sm">
          <p>Outflows over the next 12 months — prefilled from the obligations schedule ({money(q.scheduled_12m)}).</p>
          <input className={input} value={outflow} onChange={(e) => setOutflow(e.target.value)} />
          <p className="text-xs text-text-muted">Trailing 3 months, actual: {q.trailing_3m ? money(q.trailing_3m) : "—"} ({q.trailing_basis})</p>
          {warnings.map((w) => <p key={w.key} className="text-xs text-[#9B2335]" data-warning={w.key}>{w.text}</p>)}
          <button type="button" className={secondary} onClick={() => setStep(2)}>Next</button>
        </div>) : null}
      {step === 2 ? (
        <div className="space-y-1 text-sm">
          <p>Objective — stability or yield:</p>
          <div className="flex flex-wrap gap-2">{objectiveScale(env).map((o) => (
            <button key={o.score} type="button" className={chip(score === o.score)} onClick={() => setScore(o.score)}>
              {o.score} — {o.label}</button>))}</div>
          <button type="button" className={secondary} onClick={recommend}>See the recommendation</button>
        </div>) : null}
      {step === 3 && rec ? (
        <div className="space-y-2 text-sm">
          <p>Recommended: Near-term to month {rec.fields.near_months}, Reserve to month {rec.fields.reserve_months}.</p>
          <SleeveTable derived={rec.derived} env={env} caption="Recommended sleeves" />
          {warnings.map((w) => <p key={w.key} className="text-xs text-[#9B2335]" data-warning={w.key}>{w.text}</p>)}
          <button type="button" className={primary} onClick={apply}>Apply as a draft</button>
          <p className="text-xs text-text-muted">Applying creates a policy draft that needs a second person.</p>
        </div>) : null}
      {err ? <p className="text-sm text-[#9B2335]">{err}</p> : null}
    </div>
  );
}

export function ApprovalStatus({ env, scope, onDecided }) {
  const c = policyControls(env, scope);
  const open = scope?.open;
  const [note, setNote] = useState("");
  const [err, setErr] = useState(null);
  const hid = env?.household?.id;
  const act = async (action) => {
    try { await post(`households/${hid}/policies/${open.id}/${action}`, action === "submit" ? {} : { note: note || null }); onDecided?.(); }
    catch (e) { setErr(e.message); }
  };
  const history = Array.isArray(scope?.history) ? scope.history : [];
  return (
    <div className="space-y-2 text-sm" aria-label="Approval status">
      {open ? (
        <div className="rounded-[6px] border border-[#ece8dd] bg-[#FAF9F6] p-3">
          <p className="text-navy">Version {open.version_no}: {statusLabel(env, open.status)}</p>
          <p className="text-xs text-text-muted">Made by {makersText(open)}{open.submitted_at ? `; submitted ${open.submitted_at}` : ""}</p>
          {open.decide_blocked ? <p className="text-xs text-text-muted">{open.decide_blocked}</p> : null}
          {c.submit ? <button type="button" className={primary} onClick={() => act("submit")}>Submit for approval</button> : null}
          {c.decide ? (
            <div className="mt-2 flex flex-wrap gap-2">
              <input className={input} placeholder="Note (a rejection needs one)" value={note} onChange={(e) => setNote(e.target.value)} />
              <button type="button" className={primary} onClick={() => act("approve")}>Approve</button>
              <button type="button" className={secondary} onClick={() => act("reject")}>Reject</button>
            </div>) : null}
        </div>) : null}
      {history.length ? (
        <div aria-label="Policy history"><p className="font-semibold text-navy">History</p>
          <ul>{history.map((h) => (
            <li key={h.id} className="text-xs text-[#334155]">v{h.version_no} · {statusLabel(env, h.status)} · made by {makersText(h)}
              {h.approved_by_name ? ` · approved by ${h.approved_by_name} ${h.approved_at}` : ""}{h.reject_reason ? ` · rejected: ${h.reject_reason}` : ""}</li>))}</ul></div>) : null}
      {err ? <p className="text-sm text-[#9B2335]">{err}</p> : null}
    </div>
  );
}

/** CASH03 — the instrument plan's blended yields for this scope, beside cash02's Treasury-curve estimate. */
export function PlanBlended({ env, scope }) {
  const b = planBlendedFor(env, scope?.scope_type, scope?.scope_id);
  if (!b) return null;
  return (
    <p className="text-xs text-[#334155]" aria-label="Instrument plan blended yields">
      Instrument plan: blended {b.net} net, {b.afterTax} after tax; ladder {b.ladder}.
    </p>
  );
}

export function ScopePolicy({ env, scope, onReload }) {
  const c = policyControls(env, scope);
  return (
    <section className={`${card} space-y-3`} aria-label={`Cash policy — ${scope.label}`}>
      <h3 className="text-lg font-semibold text-navy" style={serif}>Cash policy — {scope.label}</h3>
      {scope.active ? <>
        <PolicySummary version={scope.active} />
        <SleeveTable derived={scope.active.derived} env={env} caption="Active sleeves" />
        <PlanBlended env={env} scope={scope} />
      </> : <p className="text-sm text-text-muted">No active policy — coverage uses the default minimum.</p>}
      {env?.member_view ? null : <ApprovalStatus env={env} scope={scope} onDecided={onReload} />}
      {c.edit ? <PolicyEditor env={env} scope={scope} onSaved={onReload} /> : null}
      {c.questionnaire ? <Questionnaire env={env} scope={scope} onSaved={onReload} /> : null}
    </section>
  );
}

export default function PolicyPanel({ env, onReload }) {
  const list = scopes(env);
  if (!env || !env.household || !env.policy) return null;
  if (!list.length) {
    return env.member_view ? <div className={`${card} text-sm text-text-muted`}>No cash policy is in force for your family yet.</div> : null;
  }
  const cov = coverageLabel(env);
  return (
    <div className="space-y-4" aria-label="Cash policies">
      {list.map((s) => <ScopePolicy key={`${s.scope_type}:${s.scope_id}`} env={env} scope={s} onReload={onReload} />)}
      {env.member_view && cov ? <p className="text-xs text-text-muted" data-label="cash.coverage_estimate">{cov}</p> : null}
    </div>
  );
}
