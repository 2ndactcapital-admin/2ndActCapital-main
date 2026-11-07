"use client";

import { useState } from "react";
import { jobSwitch } from "@/lib/bugfix4.mjs";
import { can, labelOf, vocab } from "./sourceLabel.mjs";

// SECMASTER1 — Super Admin → Security data → Security master: the status panel, the platform jobs
// (schedule, last run, Run now, close a held run) and the review queue. Every control renders only
// when the server's envelope says so (can_run / can_close_held / can_decide AND the vocabulary lists
// the outcome) — a missing envelope fails CLOSED. Counts and labels are the server's.
const card = "rounded-[6px] border border-[#ece8dd] bg-white p-5";
const btn = "rounded border border-[#C5A880] px-2 py-1 text-xs text-navy hover:bg-[#F5F1EB]";

const entries = (o) => Object.entries(o || {});

export function StatusPanel({ envelope }) {
  const st = envelope?.status;
  if (!st) return null;
  const f13 = st.sec_13f || {};
  return (
    <section className={card} data-testid="status-panel">
      <h2 className="font-serif text-xl text-navy">Security master</h2>
      <div className="mt-3 grid gap-4 text-sm md:grid-cols-4">
        <div><p className="text-text-muted">Securities</p><p className="text-2xl text-navy">{st.securities_total}</p>
          <ul>{entries(st.securities_by_type).map(([k, v]) => <li key={k}>{k}: {v}</li>)}</ul></div>
        <div><p className="text-text-muted">By source</p>
          <ul>{entries(st.securities_by_source).map(([k, v]) => <li key={k}>{k}: {v}</li>)}</ul>
          <p className="mt-2 text-text-muted">Identifiers</p>
          <ul>{entries(st.identifiers_by_type).map(([k, v]) => <li key={k}>{k}: {v}</li>)}</ul></div>
        <div><p className="text-text-muted">Review queue</p><p className="text-2xl text-navy" data-testid="review-open">{st.review_open}</p>
          <ul>{entries(st.review_open_by_kind).map(([k, v]) => <li key={k}>{labelOf(vocab(envelope, "review_kinds"), k)}: {v}</li>)}</ul></div>
        <div><p className="text-text-muted">13(f) list {f13.quarter || ""}</p>
          <p className="text-2xl text-navy" data-testid="f13-unmapped">{f13.unmapped ?? 0}</p><p className="text-text-muted">unmapped</p>
          <ul>{entries(f13.unmapped_reasons).slice(0, 5).map(([k, v]) => <li key={k} className="text-xs">{k}: {v}</li>)}</ul></div>
      </div>
    </section>
  );
}

export function JobsView({ envelope, onRunNow, onClose, onSwitch }) {
  const jobs = envelope?.jobs || [];
  const run = can(envelope, "can_run");
  const close = can(envelope, "can_close_held");
  return (
    <section className={card} data-testid="platform-jobs">
      <h2 className="font-serif text-xl text-navy">Platform jobs</h2>
      {envelope?.price_gap_note ? <p className="mt-1 text-xs text-text-muted">{envelope.price_gap_note}</p> : null}
      <table className="mt-3 w-full text-sm">
        <thead className="text-left text-text-muted"><tr><th>Job</th><th>Schedule</th><th>Last run</th><th>Result</th>
          {run ? <th /> : null}</tr></thead>
        <tbody>
          {jobs.map((j) => {
            const last = j.last_run;
            return (
              <tr key={j.key} className="border-t border-[#E2E8F0]">
                <td title={j.description}>{j.title}{j.trigger_active ? <span className="block text-xs text-[#2D6A4F]">schedule on</span> : <span className="block text-xs text-text-muted">schedule not active</span>}
                  {j.fetches ? <span className="block text-xs text-text-muted">{j.fetches}</span> : null}
                  {j.blocked_note ? <span className="block text-xs text-[#9B2335]">{j.blocked_note}</span> : null}</td>
                <td className="text-xs">{j.schedule_note}</td>
                <td className="text-xs">{last ? last.started_at : "never"}</td>
                <td className="text-xs">{last ? last.status : "—"}{last?.error_detail ? <span className="block text-[#9B2335]">{String(last.error_detail).slice(0, 160)}</span> : null}</td>
                {run ? (
                  <td className="space-x-2 text-right">
                    <button type="button" className={btn} onClick={() => onRunNow?.(j.key)}>Run now</button>
                    {/* BUGFIX4 — the Super Admin's schedule switch (audited; the sprint switched nothing on) */}
                    {jobSwitch(envelope, j) && onSwitch
                      ? <button type="button" className={btn} onClick={() => onSwitch(j.key, jobSwitch(envelope, j).next)}>{jobSwitch(envelope, j).label}</button> : null}
                    {close && last?.status === "held"
                      ? <button type="button" className={btn} onClick={() => onClose?.(last.id)}>Close held run</button> : null}
                  </td>
                ) : null}
              </tr>
            );
          })}
        </tbody>
      </table>
    </section>
  );
}

export function ReviewQueueView({ envelope, onDecide }) {
  const rows = envelope?.rows || [];
  const outcomes = vocab(envelope, "outcomes");
  const decide = can(envelope, "can_decide") && outcomes.length > 0;
  return (
    <section className={card} data-testid="review-queue">
      <h2 className="font-serif text-xl text-navy">Review queue</h2>
      <p className="mt-1 text-xs text-text-muted">A source that disagrees with the master lands here. Nothing is overwritten:
        deciding an item records the decision; the correction itself is made on the security.</p>
      {rows.length === 0 ? <p className="mt-3 text-sm text-text-muted">Nothing to review.</p> : (
        <table className="mt-3 w-full text-sm">
          <thead className="text-left text-text-muted"><tr><th>Kind</th><th>Subject</th><th>Incoming</th><th>Existing</th>
            <th>Source</th>{decide ? <th /> : null}</tr></thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.id} className="border-t border-[#E2E8F0]" data-testid="review-row">
                <td>{r.kind_label || labelOf(vocab(envelope, "review_kinds"), r.kind)}</td>
                <td>{r.subject_key}{r.security_name ? <span className="block text-xs text-text-muted">{r.security_name}</span> : null}</td>
                <td>{r.incoming_value}</td><td>{r.existing_value || "—"}</td><td>{r.provider_key}</td>
                {decide ? (
                  <td className="space-x-2 text-right">
                    {outcomes.filter((o) => (r.actions || []).includes(o.key)).map((o) => (
                      <button key={o.key} type="button" className={btn} onClick={() => onDecide?.(r.id, o.key)}>{o.label}</button>
                    ))}
                  </td>
                ) : null}
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}

async function call(url, opts) {
  const res = await fetch(url, opts ? { ...opts, headers: { "Content-Type": "application/json" } } : undefined);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data?.error || "Request failed");
  return data;
}

export default function SecurityMasterAdmin({ initialStatus, initialJobs, initialReview }) {
  const [status, setStatus] = useState(initialStatus);
  const [jobs, setJobs] = useState(initialJobs);
  const [review, setReview] = useState(initialReview);
  const [msg, setMsg] = useState(null);
  const base = "/api/admin/security-data/master";
  const reload = async () => {
    const [s, j, r] = await Promise.all([call(`${base}/status`), call(`${base}/jobs`), call(`${base}/review`)]);
    setStatus(s); setJobs(j); setReview(r);
  };
  const act = async (fn, ok) => {
    try { await fn(); setMsg(ok); await reload(); } catch (e) { setMsg(e.message); }
  };
  return (
    <div className="space-y-6">
      {msg ? <p className="text-sm text-navy">{msg}</p> : null}
      <StatusPanel envelope={status} />
      <JobsView envelope={jobs}
        onRunNow={(k) => act(() => call(`${base}/jobs/${encodeURIComponent(k)}/run-now`, { method: "POST", body: "{}" }),
          "Started — it appears on the Runs screen.")}
        onClose={(id) => { const note = window.prompt("Why is this held run being closed?"); if (note) act(() => call(`${base}/runs/${id}/close`, { method: "POST", body: JSON.stringify({ note }) }), "Held run closed — the schedule resumes."); }}
        onSwitch={(k, on) => { if (window.confirm(on ? "Switch this job's schedule ON? It will run on its schedule from now on." : "Switch this job's schedule off?")) act(() => call(`${base}/jobs/${encodeURIComponent(k)}/schedule`, { method: "POST", body: JSON.stringify({ active: on }) }), on ? "Schedule switched on." : "Schedule switched off."); }}
      />
      <ReviewQueueView envelope={review}
        onDecide={(id, outcome) => { const note = window.prompt("Note for the record"); if (note) act(() => call(`${base}/review/${id}/decision`, { method: "POST", body: JSON.stringify({ outcome, note }) }), "Recorded."); }} />
    </div>
  );
}

// The Runs screen's "Run now" for the platform jobs (Super Admin): rendered from the jobs envelope the
// server returned — a 403 for anyone else leaves envelope null and nothing renders.
export function PlatformJobsPanel({ initialJobs }) {
  const [jobs, setJobs] = useState(initialJobs);
  const [msg, setMsg] = useState(null);
  if (!can(jobs, "can_run")) return null;
  const base = "/api/admin/security-data/master";
  const act = async (fn, ok) => {
    try { await fn(); setMsg(ok); setJobs(await call(`${base}/jobs`)); } catch (e) { setMsg(e.message); }
  };
  return (
    <div className="mt-6">
      {msg ? <p className="mb-2 text-sm text-navy">{msg}</p> : null}
      <JobsView envelope={jobs}
        onRunNow={(k) => act(() => call(`${base}/jobs/${encodeURIComponent(k)}/run-now`, { method: "POST", body: "{}" }),
          "Started — refresh the run list to follow it.")}
        onClose={(id) => { const note = window.prompt("Why is this held run being closed?"); if (note) act(() => call(`${base}/runs/${id}/close`, { method: "POST", body: JSON.stringify({ note }) }), "Held run closed — the schedule resumes."); }}
        onSwitch={(k, on) => { if (window.confirm(on ? "Switch this job's schedule ON? It will run on its schedule from now on." : "Switch this job's schedule off?")) act(() => call(`${base}/jobs/${encodeURIComponent(k)}/schedule`, { method: "POST", body: JSON.stringify({ active: on }) }), on ? "Schedule switched on." : "Schedule switched off."); }}
      />
    </div>
  );
}
