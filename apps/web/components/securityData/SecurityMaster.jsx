"use client";

import { useState } from "react";
import { BusyButton, BusyError, useBusy } from "@/components/ui/Busy";
import { jobSwitch } from "@/lib/bugfix4.mjs";
import { can, labelOf, vocab } from "./sourceLabel.mjs";
import { describeFilter, listHref, pct, reviewHref } from "@/lib/secmaster2.mjs";
import { ESTIMATE_API, TRACK_API, UNTRACK_API, actionBody, canTrack, canUntrack, cleanTrackedFilter, confirmText,
  refusalText, selectionBody, trackedHref } from "@/lib/track1.mjs";

// SECMASTER1 — Super Admin → Security data → Security master: the status panel, the platform jobs
// (schedule, last run, Run now, close a held run) and the review queue. Every control renders only
// when the server's envelope says so (can_run / can_close_held / can_decide AND the vocabulary lists
// the outcome) — a missing envelope fails CLOSED. Counts and labels are the server's.
const card = "rounded-[6px] border border-[#ece8dd] bg-white p-5";
const btn = "rounded border border-[#C5A880] px-2 py-1 text-xs text-navy hover:bg-[#F5F1EB]";

const entries = (o) => Object.entries(o || {});
const MASTER_SUMMARY = "/admin/security-data/master";

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

// SECMASTER2 — what the master holds. Every number is the server's and links to the list of exactly those
// securities (the count's own `filter`, kept only for filter names the server lists). No envelope, or no
// can_read exactly true → nothing renders (fails closed).
function Count({ n, filter, allowed, testid }) {
  const href = listHref(filter, allowed);
  return href ? <a href={href} className="text-navy underline decoration-[#C5A880] underline-offset-2" data-testid={testid}>{n}</a>
    : <span data-testid={testid}>{n}</span>;
}

export function SummaryPanel({ envelope }) {
  const sm = envelope?.summary;
  if (!sm || !can(envelope, "can_read")) return null;
  const allowed = envelope?.list_filters;
  const kinds = vocab(envelope, "review_kinds");
  const total = sm.total || 0;
  const head = "text-left text-text-muted";
  return (
    <section className={card} data-testid="summary-panel">
      <h2 className="font-serif text-xl text-navy">What the master holds</h2>
      <p className="mt-1 text-xs text-text-muted">Every number links to the securities it counts.</p>
      <div className="mt-3 grid gap-6 text-sm md:grid-cols-2">
        <div>
          <p className="text-text-muted">Securities</p><p className="text-2xl text-navy" data-testid="summary-total">{total}</p>
          <table className="mt-2 w-full"><thead className={head}><tr><th>Type</th><th className="text-right">Securities</th></tr></thead>
            <tbody>{(sm.by_type || []).map((t) => (
              <tr key={t.security_type} className="border-t border-[#E2E8F0] align-top">
                <td>{t.security_type}
                  {(t.structures || []).map((x) => (
                    <span key={x.structure} className="block pl-3 text-xs text-text-muted">{x.structure.replace(/_/g, " ")}: <Count n={x.count} filter={x.filter} allowed={allowed} testid={`structure-${x.structure}`} /></span>
                  ))}</td>
                <td className="text-right"><Count n={t.count} filter={t.filter} allowed={allowed} testid={`type-${t.security_type}`} /></td>
              </tr>))}</tbody></table>
        </div>
        <div>
          <p className="text-text-muted">Identifiers</p>
          <table className="mt-2 w-full"><thead className={head}><tr><th>Type</th><th className="text-right">Securities</th><th className="text-right">Missing</th></tr></thead>
            <tbody>{(sm.identifiers || []).map((x) => (
              <tr key={x.id_type} className="border-t border-[#E2E8F0]"><td>{x.id_type}</td>
                <td className="text-right"><Count n={x.securities} filter={x.filter} allowed={allowed} testid={`id-${x.id_type}`} /></td>
                <td className="text-right"><Count n={x.missing} filter={x.missing_filter} allowed={allowed} testid={`id-missing-${x.id_type}`} /></td></tr>))}</tbody></table>
        </div>
        <div>
          <p className="text-text-muted">Sources</p>
          <table className="mt-2 w-full"><thead className={head}><tr><th>Provider</th><th className="text-right">Securities</th></tr></thead>
            <tbody>{(sm.providers || []).map((x) => (
              <tr key={x.provider_key} className="border-t border-[#E2E8F0]"><td>{x.provider_key}</td>
                <td className="text-right"><Count n={x.securities} filter={x.filter} allowed={allowed} testid={`provider-${x.provider_key}`} /></td></tr>))}</tbody></table>
          <p className="mt-4 text-text-muted">Prices</p>
          <table className="mt-2 w-full"><thead className={head}><tr><th>Coverage</th><th className="text-right">Securities</th><th className="text-right">With a price</th></tr></thead>
            <tbody>{(sm.prices || []).map((x) => (
              <tr key={x.price_coverage} className="border-t border-[#E2E8F0]"><td>{x.price_coverage.replace(/_/g, " ")}</td>
                <td className="text-right"><Count n={x.securities} filter={x.filter} allowed={allowed} testid={`price-${x.price_coverage}`} /></td>
                <td className="text-right"><Count n={x.with_price} filter={x.with_price_filter} allowed={allowed} testid={`priced-${x.price_coverage}`} /></td></tr>))}</tbody></table>
        </div>
        <div>
          <p className="text-text-muted">Attribute coverage</p>
          <table className="mt-2 w-full"><thead className={head}><tr><th>Field</th><th className="text-right">Securities</th><th className="text-right">Missing</th></tr></thead>
            <tbody>{(sm.attributes || []).map((x) => (
              <tr key={x.field} className="border-t border-[#E2E8F0]"><td>{x.field.replace(/_/g, " ")} <span className="text-xs text-text-muted">{pct(x.securities, total)}</span></td>
                <td className="text-right"><Count n={x.securities} filter={x.filter} allowed={allowed} testid={`field-${x.field}`} /></td>
                <td className="text-right"><Count n={x.missing} filter={x.missing_filter} allowed={allowed} testid={`field-missing-${x.field}`} /></td></tr>))}</tbody></table>
        </div>
        <div>
          <p className="text-text-muted">Review queue</p><p className="text-2xl text-navy" data-testid="summary-review-open">{sm.review?.open ?? 0}</p>
          <ul>{(sm.review?.by_kind || []).map((x) => {
            const href = reviewHref(x.review_filter, kinds);
            return <li key={x.kind}>{labelOf(kinds, x.kind)}: {href ? <a href={href} className="text-navy underline decoration-[#C5A880] underline-offset-2">{x.open}</a> : x.open}</li>;
          })}</ul>
        </div>
        <div>
          <p className="text-text-muted">Last run per job</p>
          <ul className="text-xs">{(sm.jobs || []).map((j) => (
            <li key={j.job}>{j.job.replace(/_/g, " ")}: <span className={j.status === "failed" ? "text-[#9B2335]" : "text-[#2D6A4F]"}>{j.status}</span> {j.finished_at || j.started_at || ""}</li>))}</ul>
        </div>
      </div>
    </section>
  );
}

export function SecuritiesList({ envelope }) {
  if (!envelope || !can(envelope, "can_read") || !Array.isArray(envelope.rows)) return null;
  const rows = envelope.rows;
  return (
    <section className={card} data-testid="securities-list">
      <div className="flex items-baseline justify-between">
        <h2 className="font-serif text-xl text-navy">Securities</h2>
        <a href="/admin/security-data/master" className="text-xs text-navy underline decoration-[#C5A880] underline-offset-2">Back to the summary</a>
      </div>
      <p className="mt-1 text-xs text-text-muted" data-testid="list-filter">{describeFilter(envelope.filters)} — {envelope.total} securities{envelope.total > rows.length ? `, first ${rows.length} shown` : ""}</p>
      {rows.length === 0 ? <p className="mt-3 text-sm text-text-muted">No securities match.</p> : (
        <table className="mt-3 w-full text-sm">
          <thead className="text-left text-text-muted"><tr><th>Name</th><th>Type</th><th>Identifiers</th><th>Price coverage</th></tr></thead>
          <tbody>{rows.map((r) => (
            <tr key={r.id} className="border-t border-[#E2E8F0]" data-testid="security-row">
              <td>{r.name}</td><td>{r.security_type}</td>
              <td className="text-xs">{Object.entries(r.identifiers || {}).map(([k, v]) => `${k} ${v}`).join(" · ")}</td>
              <td className="text-xs">{(r.price_coverage || "").replace(/_/g, " ")}</td>
            </tr>))}</tbody>
        </table>
      )}
    </section>
  );
}

// TRACK1 — the Tracked view: the named lists (Track list…), a filter over the whole master (the server's own
// predicates — q / type / exchange / category / prices / tracked / list), the matching securities with checkboxes,
// and the bulk actions. Every write control renders only when the envelope's can_track / can_untrack is exactly
// true; every bulk action asks the server for the count + estimate first and confirms with it; the request then
// carries that count and the server refuses if it changed. No envelope → nothing renders (fails closed).
function Select({ name, value, options, label }) {
  return (
    <label className="text-xs text-text-muted">{label}
      <select name={name} defaultValue={value || ""} className="mt-1 block w-full rounded border border-[#E2E8F0] bg-white px-2 py-1 text-sm text-navy">
        <option value="">any</option>
        {(options || []).map((o) => <option key={o.key} value={o.key}>{o.label}{o.count != null ? ` (${o.count})` : ""}</option>)}
      </select>
    </label>
  );
}

export function TrackedView({ envelope, list, filter = {}, selected = [], onToggle, onToggleAll, onBulk, onTrackList, busy = false }) {
  if (!envelope || !can(envelope, "can_read")) return null;
  const v = envelope.vocabularies || {};
  const f = cleanTrackedFilter(filter, envelope);
  const rows = Array.isArray(list?.rows) && can(list, "can_read") ? list.rows : [];
  const total = typeof list?.total === "number" ? list.total : null;
  const track = canTrack(envelope);
  const untrack = canUntrack(envelope);
  const pr = envelope.progress;
  const sel = new Set(selected);
  return (
    <section className={card} data-testid="tracked-view">
      <div className="flex items-baseline justify-between">
        <h2 className="font-serif text-xl text-navy">Tracked securities</h2>
        <a href={MASTER_SUMMARY} className="text-xs text-navy underline decoration-[#C5A880] underline-offset-2">Back to the summary</a>
      </div>
      <p className="mt-1 text-sm text-text-muted">Securities tracked here join the price coverage set (reason "tracked"): the prices backfill and the
        nightly prices job load their 5-year daily history within the source's daily budget.</p>
      {pr ? <p className="mt-2 text-sm text-navy" data-testid="tracked-progress">{pr.label}</p> : null}

      <h3 className="mt-5 font-serif text-lg text-navy">Named lists</h3>
      {(envelope.lists || []).length === 0 ? <p className="mt-1 text-sm text-text-muted" data-testid="no-lists">No list is loaded yet — the operator loads them (scripts/security_lists_load.py).</p> : (
        <table className="mt-2 w-full text-sm">
          <thead className="text-left text-text-muted"><tr><th>List</th><th>Source</th><th>As of</th><th className="text-right">Members</th>
            <th className="text-right">Tracked</th><th className="text-right">Not found</th>{track ? <th /> : null}</tr></thead>
          <tbody>{envelope.lists.map((l) => (
            <tr key={l.list_key} className="border-t border-[#E2E8F0]" data-testid="list-row">
              <td title={l.description || ""}><a className="text-navy underline decoration-[#C5A880] underline-offset-2" href={trackedHref({ in_list: l.list_key }, envelope)}>{l.name}</a></td>
              <td className="text-xs">{l.source_detail}</td><td className="text-xs">{l.as_of || "—"}</td>
              <td className="text-right">{l.member_count}</td><td className="text-right">{l.tracked}</td><td className="text-right">{l.miss_count}</td>
              {track ? <td className="text-right"><button type="button" className={btn} disabled={busy} onClick={() => onTrackList?.(l)} data-testid="track-list">Track list…</button></td> : null}
            </tr>))}</tbody>
        </table>
      )}

      <h3 className="mt-5 font-serif text-lg text-navy">Find securities</h3>
      <form method="get" action={MASTER_SUMMARY} className="mt-2 grid gap-3 md:grid-cols-4" data-testid="tracked-filter">
        <input type="hidden" name="view" value="tracked" />
        <label className="text-xs text-text-muted">Name, ticker or CUSIP
          <input name="q" defaultValue={f.q || ""} maxLength={100} className="mt-1 block w-full rounded border border-[#E2E8F0] bg-white px-2 py-1 text-sm text-navy" /></label>
        <Select name="security_type" label="Type" value={f.security_type} options={v.security_types} />
        <Select name="exchange" label="Exchange" value={f.exchange} options={v.exchanges} />
        <Select name="category" label="Fund category" value={f.category} options={v.categories} />
        <Select name="has_price" label="Prices" value={f.has_price} options={v.has_price} />
        <Select name="tracked" label="Tracked" value={f.tracked} options={v.tracked} />
        <Select name="in_list" label="In list" value={f.in_list} options={v.lists} />
        <div className="flex items-end"><button type="submit" className={btn}>Apply</button></div>
      </form>

      {total == null ? null : (
        <div className="mt-4">
          <p className="text-xs text-text-muted" data-testid="tracked-count">{describeFilter(list.filters)} — {total} securities{total > rows.length ? `, first ${rows.length} shown` : ""}</p>
          {(track || untrack) ? (
            <div className="mt-2 flex flex-wrap gap-2" data-testid="bulk-actions">
              {track ? <button type="button" className={btn} disabled={busy || sel.size === 0} onClick={() => onBulk?.("track", { ids: [...sel] })}>Track selected ({sel.size})</button> : null}
              {untrack ? <button type="button" className={btn} disabled={busy || sel.size === 0} onClick={() => onBulk?.("untrack", { ids: [...sel] })}>Untrack selected ({sel.size})</button> : null}
              {track && Object.keys(f).length ? <button type="button" className={btn} disabled={busy || total === 0} onClick={() => onBulk?.("track", { filters: f })} data-testid="track-all">Track all {total} matching…</button> : null}
              {untrack && Object.keys(f).length ? <button type="button" className={btn} disabled={busy || total === 0} onClick={() => onBulk?.("untrack", { filters: f })} data-testid="untrack-all">Untrack all {total} matching…</button> : null}
            </div>
          ) : null}
          {rows.length === 0 ? <p className="mt-3 text-sm text-text-muted">No securities match.</p> : (
            <table className="mt-3 w-full text-sm">
              <thead className="text-left text-text-muted"><tr>
                {track || untrack ? <th><input type="checkbox" aria-label="Select all shown" checked={rows.every((r) => sel.has(r.id))} onChange={() => onToggleAll?.(rows.map((r) => r.id))} /></th> : null}
                <th>Name</th><th>Type</th><th>Identifiers</th><th>Prices</th><th>Tracked</th></tr></thead>
              <tbody>{rows.map((r) => (
                <tr key={r.id} className="border-t border-[#E2E8F0]" data-testid="tracked-row">
                  {track || untrack ? <td><input type="checkbox" aria-label={r.name} checked={sel.has(r.id)} onChange={() => onToggle?.(r.id)} /></td> : null}
                  <td>{r.name}</td><td>{r.security_type}</td>
                  <td className="text-xs">{Object.entries(r.identifiers || {}).map(([k, x]) => `${k} ${x}`).join(" · ")}</td>
                  <td className="text-xs">{r.has_price ? "loaded" : "none yet"}</td>
                  <td className="text-xs">{r.tracked ? <span className="text-[#2D6A4F]">tracked</span> : "—"}</td>
                </tr>))}</tbody>
            </table>
          )}
        </div>
      )}
    </section>
  );
}

// The confirm-then-write flow of a bulk action (estimate → confirm with the server's numbers → note → write).
export async function runBulk({ verb, selection, envelope, ask = (m) => window.confirm(m), askNote = (m) => window.prompt(m),
  post = call }) {
  const sel = selectionBody(selection, envelope);
  if (!sel) return { error: "Nothing selected." };
  const est = await post(ESTIMATE_API, { method: "POST", body: JSON.stringify({ ...sel, removing: verb === "untrack" }) });
  const refused = refusalText(est);
  if (refused) return { error: refused };
  const text = confirmText(verb, est);
  if (!text || !ask(text)) return { cancelled: true };
  const note = askNote(verb === "untrack" ? "Why are these untracked? (required)" : (sel.list_key ? "Note (optional)" : "Why are these tracked? (required)"));
  if (note === null) return { cancelled: true };
  const body = actionBody(sel, est, note);
  const out = await post(verb === "untrack" ? UNTRACK_API : TRACK_API, { method: "POST", body: JSON.stringify(body) });
  return { result: out.result, progress: out.progress };
}

export function JobsView({ envelope, onRunNow, onClose, onSwitch, runningKey = null }) {
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
                    {/* CATCHALL1 busy — one Run now at a time; the row being started shows Working… */}
                    <BusyButton className={btn} busy={runningKey === j.key} disabled={runningKey != null}
                      onClick={() => onRunNow?.(j.key)}>Run now</BusyButton>
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

export function TrackedPanel({ initialTracked, initialList, filter }) {
  const [env, setEnv] = useState(initialTracked);
  const [selected, setSelected] = useState([]);
  // CATCHALL1 busy — the ONE shared indicator (no local busy copy)
  const bulk = useBusy();
  const [msg, setMsg] = useState(null);
  const done = async (res) => {
    if (res.error) setMsg(res.error);
    else if (res.result) {
      const r = res.result;
      setMsg(r.untracked != null ? `Untracked ${r.untracked}; ${r.not_tracked} were not tracked.`
        : `Tracked ${r.tracked}; ${r.already_tracked} already tracked${r.unknown?.length ? `; ${r.unknown.length} not found` : ""}. ${res.progress?.label || ""}`);
      setSelected([]);
      try { setEnv(await call("/api/admin/security-data/master/tracked")); } catch { /* the page reload shows it */ }
    }
  };
  const go = async (verb, selection) => {
    if (bulk.busy) return;
    setMsg(null);
    await bulk.run(async () => { await done(await runBulk({ verb, selection, envelope: env })); });
  };
  return (
    <div className="space-y-3">
      {msg ? <p className="text-sm text-navy" data-testid="tracked-msg">{msg}</p> : null}
      <BusyError error={bulk.error} className="block text-sm text-[#9B2335]" />
      <TrackedView envelope={env} list={initialList} filter={filter} selected={selected} busy={bulk.busy}
        onToggle={(id) => setSelected((s) => (s.includes(id) ? s.filter((x) => x !== id) : [...s, id]))}
        onToggleAll={(ids) => setSelected((s) => (ids.every((i) => s.includes(i)) ? s.filter((x) => !ids.includes(x)) : [...new Set([...s, ...ids])]))}
        onBulk={go} onTrackList={(l) => go("track", { listKey: l.list_key })} />
    </div>
  );
}

export default function SecurityMasterAdmin({ initialStatus, initialJobs, initialReview, initialSummary = null,
  initialList = null }) {
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
  // CATCHALL1 busy — Run now
  const runNow = useBusy();
  const [runningKey, setRunningKey] = useState(null);
  const onRunNow = async (k) => {
    if (runNow.busy) return;
    setMsg(null); setRunningKey(k);
    await runNow.run(async () => {
      await call(`${base}/jobs/${encodeURIComponent(k)}/run-now`, { method: "POST", body: "{}" });
      setMsg("Started — it appears on the Runs screen.");
      await reload();
    });
    setRunningKey(null);
  };
  return (
    <div className="space-y-6">
      {msg ? <p className="text-sm text-navy">{msg}</p> : null}
      <BusyError error={runNow.error} className="block text-sm text-[#9B2335]" />
      {can(initialSummary, "can_read") ? <p className="text-sm"><a href={`${MASTER_SUMMARY}?view=tracked&tracked=true`} className="text-navy underline decoration-[#C5A880] underline-offset-2" data-testid="tracked-link">Tracked securities — add any security to the price coverage set</a></p> : null}
      {initialList ? <SecuritiesList envelope={initialList} /> : <SummaryPanel envelope={initialSummary} />}
      <StatusPanel envelope={status} />
      <JobsView envelope={jobs} runningKey={runNow.busy ? runningKey : null} onRunNow={onRunNow}
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
  // CATCHALL1 busy — Run now (hooks before the fail-closed early return)
  const runNow = useBusy();
  const [runningKey, setRunningKey] = useState(null);
  if (!can(jobs, "can_run")) return null;
  const base = "/api/admin/security-data/master";
  const act = async (fn, ok) => {
    try { await fn(); setMsg(ok); setJobs(await call(`${base}/jobs`)); } catch (e) { setMsg(e.message); }
  };
  const onRunNow = async (k) => {
    if (runNow.busy) return;
    setMsg(null); setRunningKey(k);
    await runNow.run(async () => {
      await call(`${base}/jobs/${encodeURIComponent(k)}/run-now`, { method: "POST", body: "{}" });
      setMsg("Started — refresh the run list to follow it.");
      setJobs(await call(`${base}/jobs`));
    });
    setRunningKey(null);
  };
  return (
    <div className="mt-6">
      {msg ? <p className="mb-2 text-sm text-navy">{msg}</p> : null}
      <BusyError error={runNow.error} className="mb-2 block text-sm text-[#9B2335]" />
      <JobsView envelope={jobs} runningKey={runNow.busy ? runningKey : null} onRunNow={onRunNow}
        onClose={(id) => { const note = window.prompt("Why is this held run being closed?"); if (note) act(() => call(`${base}/runs/${id}/close`, { method: "POST", body: JSON.stringify({ note }) }), "Held run closed — the schedule resumes."); }}
        onSwitch={(k, on) => { if (window.confirm(on ? "Switch this job's schedule ON? It will run on its schedule from now on." : "Switch this job's schedule off?")) act(() => call(`${base}/jobs/${encodeURIComponent(k)}/schedule`, { method: "POST", body: JSON.stringify({ active: on }) }), on ? "Schedule switched on." : "Schedule switched off."); }}
      />
    </div>
  );
}
