"use client";

/**
 * Section351 — REP14. Replication → Section 351: the candidates (eligible accounts with their metrics and the
 * "harvesting has run dry" flag, ineligible accounts with reasons, the thresholds), the sponsor profiles, and a
 * plan (contributing accounts with their 25 / 50 test, the consents, the tax adviser's confirmation, the disclosure
 * acknowledgement, readiness, the control test, basis records, the project tracker and its timeline, and the in-kind
 * transfer instructions). Controls from lib/section351.mjs (fail closed); labels from the envelope's vocabularies;
 * figures exactly as the server states them, with its label. Light / cream only.
 */

import { useState } from "react";
import {
  adapterRefusal, addableAccounts, allReady, basisLinks, candidateControls, disclosureLine, estimateLabel, fixupProfiles,
  gateLines, labelOf, planControls, profileControls, sheetLink, stepControls, thresholdControls, timelineBars, vocab,
} from "@/lib/section351.mjs";

const card = "rounded-[6px] border border-[#ece8dd] bg-white p-5";
const input = "rounded border border-[#E2E8F0] px-3 py-2";
const primary = "rounded bg-navy px-3 py-1 text-white disabled:opacity-50";
const secondary = "rounded border border-[#E2E8F0] px-3 py-1 text-navy disabled:opacity-50";
const th = "px-2 py-1 text-left font-medium text-text-muted";
const td = "px-2 py-1 align-top";
const ok = (b) => (b ? "text-[#2D6A4F]" : "text-[#9B2335]");

export async function send(path, method = "GET", body) {
  const res = await fetch(`/api/section351/${path}`, {
    method, headers: body ? { "Content-Type": "application/json" } : undefined, body: body ? JSON.stringify(body) : undefined,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const err = new Error(data.error || "Request failed");
    err.status = res.status;
    err.body = data;
    throw err;
  }
  return data;
}

function Label({ envelope }) {
  const l = estimateLabel(envelope);
  return l ? <p className="mt-1 text-xs text-text-muted">{l}</p> : null;
}

// ── Candidates ──────────────────────────────────────────────────────────────
function RunDry({ rd }) {
  if (!rd) return null;
  return (
    <div className="text-xs">
      <span className={ok(rd.run_dry)}>{rd.run_dry ? "Harvesting has run dry" : "Not run dry"}</span>
      <ul className="mt-1 space-y-0.5 text-text-muted">
        {(rd.checks || []).map((c) => (
          <li key={c.key}><span className={ok(c.met)}>{c.met ? "Met" : "Not met"}</span> — {c.rule}: {c.value_pct ?? c.value ?? "unknown"}
            {" "}(threshold {c.threshold_pct ?? c.threshold})</li>
        ))}
      </ul>
    </div>
  );
}

function ThresholdForm({ thresholds, onSave }) {
  const [f, setF] = useState({ max_harvestable_loss_pct: thresholds?.max_harvestable_loss_pct || "",
    min_unrealized_gain_pct: thresholds?.min_unrealized_gain_pct || "", min_years_since_inception: thresholds?.min_years_since_inception || "",
    note: "" });
  const field = (k, l) => <label className="block text-sm">{l}<input className={`${input} mt-1 w-full`} value={f[k]} onChange={(e) => setF({ ...f, [k]: e.target.value })} /></label>;
  return (
    <div className="mt-3 grid gap-3 sm:grid-cols-2">
      {field("max_harvestable_loss_pct", "Harvestable loss remaining at most (fraction of the account)")}
      {field("min_unrealized_gain_pct", "Unrealised gain at least (fraction of the account)")}
      {field("min_years_since_inception", "Years since the model link began, at least")}
      {field("note", "Note")}
      <div><button type="button" className={primary} onClick={() => onSave?.({ ...f, note: f.note || null })}>Save a new version</button></div>
    </div>
  );
}

function FixupForm({ account, profiles, onFixup }) {
  const [f, setF] = useState({ sponsor_profile_id: "", valuation_date: "" });
  return (
    <div className="mt-1 flex flex-col gap-1 text-xs">
      <select className={input} value={f.sponsor_profile_id} onChange={(e) => setF({ ...f, sponsor_profile_id: e.target.value })}>
        <option value="">Sponsor profile</option>{profiles.map((r) => <option key={r.id} value={r.id}>{r.name} v{r.version_no}</option>)}
      </select>
      <input className={input} placeholder="Valuation date (YYYY-MM-DD)" value={f.valuation_date} onChange={(e) => setF({ ...f, valuation_date: e.target.value })} />
      <button type="button" className={secondary} disabled={!f.sponsor_profile_id || !f.valuation_date}
        onClick={() => onFixup?.(account, f)}>Fix-up proposal</button>
    </div>
  );
}

export function CandidatesView({ envelope, thresholdsEnvelope, profilesEnvelope, onSaveThresholds, onFixup }) {
  const c = candidateControls(envelope);
  const profiles = fixupProfiles(profilesEnvelope);
  const tc = thresholdControls(thresholdsEnvelope);
  const th_ = envelope?.thresholds || {};
  return (
    <div className="space-y-4">
      <section className={card}>
        <h2 className="font-serif text-xl text-navy">Candidates</h2>
        <p className="mt-1 text-sm text-text-muted">{envelope?.rule}</p>
        <Label envelope={envelope} />
        <table className="mt-3 w-full text-sm">
          <thead><tr><th className={th}>Account</th><th className={th}>Appreciated value</th><th className={th}>Unrealised gain</th>
            <th className={th}>Harvestable loss remaining</th><th className={th}>Benefit decay</th><th className={th}>Run dry</th><th className={th} /></tr></thead>
          <tbody>
            {(envelope?.eligible || []).map((s) => (
              <tr key={s.account_id} className="border-t border-[#ece8dd]">
                <td className={td}>{s.account}<div className="text-xs text-text-muted">valued {s.valuation_as_of || "—"}</div></td>
                <td className={td}>{s.metrics?.appreciated_value}</td>
                <td className={td}>{s.metrics?.unrealized_gain}</td>
                <td className={td}>{s.metrics?.harvestable_loss}</td>
                <td className={td}>{s.metrics?.decay?.estimated
                  ? <>Year 1 ({s.metrics.decay.year_1}): {s.metrics.decay.year_1_harvested}; {s.metrics.decay.this_year}: {s.metrics.decay.this_year_harvested}; decay {s.metrics.decay.decay_pct}</>
                  : <span className="text-text-muted">{s.metrics?.decay?.note}</span>}</td>
                <td className={td}><RunDry rd={s.run_dry} /></td>
                <td className={td}>{c.fixup && profiles.length ? <FixupForm account={s} profiles={profiles} onFixup={onFixup} /> : null}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {(envelope?.eligible || []).length === 0 ? <p className="mt-3 text-sm text-text-muted">No eligible account.</p> : null}
      </section>
      <section className={card}>
        <h2 className="font-serif text-xl text-navy">Not eligible</h2>
        <ul className="mt-2 space-y-1 text-sm">
          {(envelope?.ineligible || []).map((s) => (
            <li key={s.account_id}>{s.account} — <span className="text-text-muted">{(s.reasons || []).map((r) => r.reason).join("; ")}</span></li>
          ))}
        </ul>
      </section>
      <section className={card}>
        <h2 className="font-serif text-xl text-navy">"Run dry" thresholds</h2>
        <p className="mt-1 text-sm">Version {th_.version_no ?? "—"} ({th_.source}): harvestable loss at most {th_.max_harvestable_loss_pct},
          unrealised gain at least {th_.min_unrealized_gain_pct}, years at least {th_.min_years_since_inception}.</p>
        {tc.save && c.saveThresholds ? <ThresholdForm thresholds={th_} onSave={onSaveThresholds} /> : null}
      </section>
    </div>
  );
}

// ── Sponsor profiles ────────────────────────────────────────────────────────
function ProfileForm({ envelope, base, onSave }) {
  const v = vocab(envelope);
  const d = base || envelope?.defaults || {};
  const [f, setF] = useState({ name: base?.name || "", adviser_name: base?.adviser_name || "", distributor_name: base?.distributor_name || "",
    contact: base?.contact || "", eligible_owner_types: d.eligible_owner_types || [], review_owner_types: d.review_owner_types || [],
    ineligible_assets: d.ineligible_assets || [], cash_rule: d.cash_rule || "not_contributed", us_persons_only: d.us_persons_only !== false,
    note: "" });
  const toggle = (k, key) => setF({ ...f, [k]: f[k].includes(key) ? f[k].filter((x) => x !== key) : [...f[k], key] });
  const checks = (k, list) => (
    <div className="flex flex-wrap gap-3 text-sm">{(list || []).map((x) => (
      <label key={x.key}><input type="checkbox" checked={f[k].includes(x.key)} onChange={() => toggle(k, x.key)} /> {x.label}</label>))}</div>
  );
  const text = (k, l) => <label className="block text-sm">{l}<input className={`${input} mt-1 w-full`} value={f[k]} onChange={(e) => setF({ ...f, [k]: e.target.value })} /></label>;
  const ownerChoices = (v.owner_types || []).filter((x) => ["individual", "joint", "trust", "s_corp", "partnership"].includes(x.key));
  return (
    <div className="mt-3 space-y-3">
      <div className="grid gap-3 sm:grid-cols-2">{text("name", "Sponsor")}{text("adviser_name", "Adviser")}{text("distributor_name", "Distributor")}{text("contact", "Contact")}</div>
      <p className="text-sm text-navy">Eligible owner types</p>{checks("eligible_owner_types", ownerChoices)}
      <p className="text-sm text-navy">Needs the sponsor's review</p>{checks("review_owner_types", ownerChoices)}
      <p className="text-sm text-navy">Ineligible assets</p>{checks("ineligible_assets", (v.asset_classes || []).filter((x) => x.key !== "government"))}
      <label className="block text-sm">Cash
        <select className={`${input} mt-1 w-full`} value={f.cash_rule} onChange={(e) => setF({ ...f, cash_rule: e.target.value })}>
          {(v.cash_rules || []).map((x) => <option key={x.key} value={x.key}>{x.label}</option>)}
        </select></label>
      <label className="text-sm"><input type="checkbox" checked={f.us_persons_only} onChange={(e) => setF({ ...f, us_persons_only: e.target.checked })} /> U.S. persons only</label>
      {text("note", "Note")}
      <button type="button" className={primary} onClick={() => onSave?.({ ...f, note: f.note || null })}>{base ? "Save a new version" : "Create the profile"}</button>
    </div>
  );
}

export function ProfilesView({ envelope, onSave }) {
  const c = profileControls(envelope);
  const v = vocab(envelope);
  const [editing, setEditing] = useState(null);
  return (
    <section className={card}>
      <h2 className="font-serif text-xl text-navy">Sponsor profiles</h2>
      <p className="mt-1 text-sm text-text-muted">The third-party ETF sponsor runs the fund and its own final compliance review; each edit is a new version.</p>
      <ul className="mt-3 space-y-3 text-sm">
        {(envelope?.rows || []).map((r) => (
          <li key={r.id} className="border-t border-[#ece8dd] pt-2">
            <span className="font-medium text-navy">{r.name}</span> — version {r.version_no}{r.status === "retired" ? " (retired)" : ""}
            <div className="text-text-muted">Eligible: {r.eligible_owner_types.map((k) => labelOf(v.owner_types, k)).join(", ") || "—"};
              review: {r.review_owner_types.map((k) => labelOf(v.owner_types, k)).join(", ") || "—"};
              ineligible assets: {r.ineligible_assets.map((k) => labelOf(v.asset_classes, k)).join(", ") || "—"}; {r.cash_rule_label}</div>
            {c.newVersion ? <button type="button" className={`${secondary} mt-1`} onClick={() => setEditing(r)}>New version</button> : null}
          </li>
        ))}
      </ul>
      {c.newVersion && editing ? <ProfileForm envelope={envelope} base={editing} onSave={(b) => onSave?.(b, editing.profile_key)} /> : null}
      {c.create && !editing ? <ProfileForm envelope={envelope} onSave={(b) => onSave?.(b, null)} /> : null}
    </section>
  );
}

// ── A plan ──────────────────────────────────────────────────────────────────
function TestDetail({ test, v }) {
  if (!test || !test.verdict) return null;
  return (
    <div className="mt-2 text-xs">
      <p>{labelOf(v.verdicts, test.verdict)} — largest issuer {test.largest_issuer_pct} (margin {test.margin_one_pct}),
        five largest {test.top_five_pct} (margin {test.margin_five_pct}); total {test.total_assets}, cash excluded {test.cash_excluded}</p>
      {(test.reasons || []).concat(test.blocked || []).map((r) => <p key={r} className="text-[#9B2335]">{r}</p>)}
      <table className="mt-1 w-full"><thead><tr><th className={th}>Issuer</th><th className={th}>Weight</th><th className={th}>Value</th><th className={th}>Direct / inside funds</th></tr></thead>
        <tbody>{(test.issuers || []).map((i) => (
          <tr key={i.issuer_key}><td className={td}>{i.label}</td><td className={td}>{i.weight_pct}</td><td className={td}>{i.value}</td>
            <td className={td}>{i.direct} / {i.look_through}{i.via_funds?.length ? ` (${i.via_funds.join(", ")})` : ""}</td></tr>))}</tbody></table>
      <p className="mt-1 text-text-muted">Valuation per line: {(test.lines || []).map((l) => `${l.label}: ${l.valuation_source || "—"} as of ${l.valuation_as_of || "—"}${l.holdings_as_of ? `, holdings as of ${l.holdings_as_of}` : ""}`).join("; ")}</p>
      {(test.excluded || []).length ? <p className="mt-1">Excluded: {test.excluded.map((x) => `${x.label} — ${x.reason}`).join("; ")}</p> : null}
      {test.label ? <p className="text-text-muted">{test.label}</p> : null}
    </div>
  );
}

function AccountForms({ a, c, onAct }) {
  const [f, setF] = useState({ document_id: "", signed_on: "", signer: "", adviser_name: "", confirmed_on: "", us_source: "", us_as_of: "",
    boot: a.cash_to_member || "0", review_note: "", review_on: "" });
  const t = (k, l) => <input className={`${input} w-40 text-xs`} placeholder={l} value={f[k]} onChange={(e) => setF({ ...f, [k]: e.target.value })} />;
  const d = disclosureLine(a);
  return (
    <div className="mt-2 space-y-2 text-xs">
      {c.accountFields ? (
        <>
          <div className="flex flex-wrap gap-2">{t("document_id", "Chancery document id")}{t("signed_on", "Signed on (YYYY-MM-DD)")}{t("signer", "Signer")}
            <button type="button" className={secondary} onClick={() => onAct(`plan-accounts/${a.id}/consent`, { document_id: f.document_id, signed_on: f.signed_on, signer: f.signer })}>Record consent</button></div>
          <div className="flex flex-wrap gap-2">{t("adviser_name", "Tax adviser")}{t("confirmed_on", "Confirmed on")}
            <button type="button" className={secondary} onClick={() => onAct(`plan-accounts/${a.id}/tax-adviser`, { adviser_name: f.adviser_name, confirmed_on: f.confirmed_on })}>Record tax adviser confirmation</button></div>
          <div className="flex flex-wrap gap-2">{t("us_source", "Source")}{t("us_as_of", "As of")}
            <button type="button" className={secondary} onClick={() => onAct(`plan-accounts/${a.id}/us-person`, { us_person: "yes", source: f.us_source, as_of: f.us_as_of })}>U.S. person — yes</button>
            <button type="button" className={secondary} onClick={() => onAct(`plan-accounts/${a.id}/us-person`, { us_person: "no", source: f.us_source, as_of: f.us_as_of })}>U.S. person — no</button></div>
          {a.eligibility?.decision === "needs_sponsor_review" ? (
            <div className="flex flex-wrap gap-2">{t("review_note", "Sponsor's note")}{t("review_on", "Reviewed on")}
              <button type="button" className={secondary} onClick={() => onAct(`plan-accounts/${a.id}/sponsor-review`, { decision: "accepted", note: f.review_note, reviewed_on: f.review_on })}>Sponsor accepted</button>
              <button type="button" className={secondary} onClick={() => onAct(`plan-accounts/${a.id}/sponsor-review`, { decision: "declined", note: f.review_note, reviewed_on: f.review_on })}>Sponsor declined</button></div>
          ) : null}
          <div className="flex flex-wrap gap-2">{t("boot", "Cash to the member")}
            <button type="button" className={secondary} onClick={() => onAct(`plan-accounts/${a.id}/boot`, { amount: f.boot })}>Set cash (boot — taxable)</button></div>
        </>
      ) : null}
      <div className="rounded-[6px] border border-[#ece8dd] bg-[#FAF9F6] p-3">
        {d.inForce ? (
          <>
            <p className="text-navy">{d.title} — version {d.version}</p>
            <p className="mt-1 whitespace-pre-line">{d.text}</p>
            {d.current ? <p className="mt-1 text-[#2D6A4F]">Acknowledged at the version in force.</p> : <p className="mt-1 text-[#9B2335]">{d.note}</p>}
            {c.acknowledge && !d.current ? <button type="button" className={`${primary} mt-2`} onClick={() => onAct(`plan-accounts/${a.id}/acknowledge`, { version_id: d.versionId })}>Acknowledge this version</button> : null}
          </>
        ) : <p className="text-[#9B2335]">{d.note}</p>}
      </div>
    </div>
  );
}

function StepEdit({ step, onAct }) {
  const [f, setF] = useState({ owner_id: "", due_on: step.due_on || "", note: "" });
  const body = () => ({ ...(f.owner_id ? { owner_id: f.owner_id } : {}), ...(f.due_on && f.due_on !== step.due_on ? { due_on: f.due_on } : {}),
    ...(f.note ? { note: f.note } : {}) });
  return (
    <span className="ml-2 inline-flex flex-wrap gap-1 text-xs">
      <input className={input} placeholder="Owner (user id)" value={f.owner_id} onChange={(e) => setF({ ...f, owner_id: e.target.value })} />
      <input className={input} placeholder="Due on" value={f.due_on} onChange={(e) => setF({ ...f, due_on: e.target.value })} />
      <input className={input} placeholder="Note" value={f.note} onChange={(e) => setF({ ...f, note: e.target.value })} />
      <button type="button" className={secondary} onClick={() => onAct(`steps/${step.id}`, body(), null, "PATCH")}>Save step</button>
    </span>
  );
}

function Timeline({ timeline }) {
  const t = timelineBars(timeline);
  if (!t.bars.length) return null;
  return (
    <div className="mt-3">
      <div className="relative h-4 text-[10px] text-text-muted">{t.months.map((m) => <span key={m.month} className="absolute" style={{ left: `${m.left}%` }}>{m.month}m</span>)}</div>
      {t.bars.map((b) => (
        <div key={b.id} className="mt-1 flex items-center gap-2 text-xs">
          <span className="w-56 shrink-0">{b.label}</span>
          <div className="relative h-3 flex-1 rounded bg-[#F5F1EB]">
            <div className={`absolute h-3 rounded ${b.status === "done" ? "bg-[#2D6A4F]" : "bg-[#C5A880]"}`} style={{ left: `${b.left}%`, width: `${b.width}%` }} />
          </div>
          <span className="w-24 shrink-0 text-text-muted">{b.due_on}</span>
        </div>
      ))}
      <p className="mt-2 text-xs text-text-muted">{timeline?.note}</p>
    </div>
  );
}

export function PlanView({ envelope, onAct, routeError }) {
  const c = planControls(envelope);
  const v = vocab(envelope);
  const p = envelope?.plan || {};
  const accts = envelope?.accounts || [];
  const names = Object.fromEntries(accts.map((a) => [a.account_id, a.account || a.account_id]));
  const ctrl = envelope?.readiness?.control || {};
  const [addId, setAddId] = useState("");
  const [channel, setChannel] = useState("");
  const [confirm, setConfirm] = useState({ plan_account_id: "", received_on: "", etf_global_security_id: "", etf_shares: "" });
  const [approveNote, setApproveNote] = useState("");
  const [reason, setReason] = useState("");
  const addable = addableAccounts(envelope);
  const sheet = sheetLink(p.id);
  return (
    <div className="space-y-4">
      <section className={card}>
        <h2 className="font-serif text-xl text-navy">{p.name}</h2>
        <p className="mt-1 text-sm">{p.target_etf} — {labelOf(v.plan_statuses, p.status)}; valuation date {p.valuation_date};
          sponsor profile version {p.sponsor_profile_version}{p.sponsor_trust ? `; trust ${p.sponsor_trust}` : ""}</p>
        <Label envelope={envelope} />
        <div className="mt-3 flex flex-wrap gap-2">
          {c.markReady ? <button type="button" className={primary} onClick={() => onAct(`plans/${p.id}/ready`)}>Mark ready</button> : null}
          {c.reopen ? <button type="button" className={secondary} onClick={() => onAct(`plans/${p.id}/reopen`)}>Reopen as draft</button> : null}
          {c.submit ? <button type="button" className={primary} onClick={() => onAct(`plans/${p.id}/submit`)}>Submit for approval</button> : null}
          {c.approve ? <input className={`${input} text-sm`} placeholder="Approval note (optional)" value={approveNote} onChange={(e) => setApproveNote(e.target.value)} /> : null}
          {c.approve ? <button type="button" className={primary} onClick={() => onAct(`plans/${p.id}/approve`, { note: approveNote || null })}>Approve</button> : null}
          {c.reject || c.cancel ? <input className={`${input} text-sm`} placeholder="Reason (reject / cancel)" value={reason} onChange={(e) => setReason(e.target.value)} /> : null}
          {c.reject ? <button type="button" className={secondary} disabled={!reason.trim()} onClick={() => onAct(`plans/${p.id}/reject`, { reason })}>Reject</button> : null}
          {c.cancel ? <button type="button" className={secondary} disabled={!reason.trim()} onClick={() => onAct(`plans/${p.id}/cancel`, { reason })}>Cancel the plan</button> : null}
        </div>
      </section>

      <section className={card}>
        <h2 className="font-serif text-xl text-navy">Contributing accounts</h2>
        {addable.length ? (
          <div className="mt-2 flex gap-2 text-sm">
            <select className={input} value={addId} onChange={(e) => setAddId(e.target.value)}>
              <option value="">Account to add</option>{addable.map((a) => <option key={a.account_id} value={a.account_id}>{a.account}{a.custodian_code ? ` (${a.custodian_code})` : ""}</option>)}
            </select>
            <button type="button" className={secondary} disabled={!addId} onClick={() => onAct(`plans/${p.id}/accounts`, { account_id: addId })}>Add account</button></div>
        ) : null}
        {accts.filter((a) => a.status !== "removed").map((a) => (
          <div key={a.id} className="mt-3 border-t border-[#ece8dd] pt-3 text-sm">
            <p className="font-medium text-navy">{a.account || a.account_id} — {labelOf(v.owner_types, a.owner_type)} ({a.eligibility?.reason})</p>
            <p className="text-xs">Contributed {a.contributed_value ?? "—"}; cash to the member {a.cash_to_member}; U.S. person: {a.us_person};
              consent: {a.consent ? `${a.consent.signer}, ${a.consent.signed_on}` : "missing"};
              tax adviser: {a.tax_adviser ? `${a.tax_adviser.name}, ${a.tax_adviser.confirmed_on}` : "missing"}</p>
            <TestDetail test={a.test_result} v={v} />
            <AccountForms a={a} c={c} onAct={onAct} />
          </div>
        ))}
      </section>

      <section className={card}>
        <h2 className="font-serif text-xl text-navy">Readiness</h2>
        <p className={`mt-1 text-sm ${ok(allReady(envelope?.readiness))}`}>{allReady(envelope?.readiness) ? "Every gate is met." : "Not ready."}</p>
        <ul className="mt-2 space-y-0.5 text-xs">
          {gateLines(envelope?.readiness, names).map((g, i) => (
            <li key={`${g.scope}-${g.gate}-${i}`}><span className={ok(g.ok)}>{g.ok ? "Met" : "Open"}</span> — {g.scope}: {g.gate} — {g.detail}</li>
          ))}
        </ul>
        <p className="mt-3 text-sm">Control: contributors {ctrl.contributors_share_pct} of the ETF (minimum {ctrl.minimum}); contributed {ctrl.contributed},
          cash to contributors {ctrl.cash_to_contributors}, seed {ctrl.seed_capital}, ETF total {ctrl.etf_total}.</p>
        <p className={`text-xs ${ctrl.boot_taxable ? "text-[#9B2335]" : "text-text-muted"}`}>{ctrl.boot_note}</p>
        {ctrl.label ? <p className="text-xs text-text-muted">{ctrl.label}</p> : null}
      </section>

      <section className={card}>
        <h2 className="font-serif text-xl text-navy">Basis records</h2>
        {c.exportBasis ? <button type="button" className={`${secondary} mt-2`} onClick={() => onAct(`plans/${p.id}/basis-record`)}>Export a new version</button> : null}
        <ul className="mt-2 space-y-1 text-sm">
          {(envelope?.basis_records || []).map((b) => {
            const l = basisLinks(b);
            return (
              <li key={b.id}>Version {b.version_no}{b.live ? "" : " (replaced)"} — value {b.totals?.fmv}, basis {b.totals?.basis}, ETF basis {b.totals?.etf_basis};
                reconciles: {b.totals?.reconciles_to_plan ? "yes" : "no"}; delivered {b.delivered_on || "—"}
                {l ? <> — <a className="text-navy underline" href={l.csv}>CSV</a> · <a className="text-navy underline" href={l.pdf}>PDF</a></> : null}
                {c.exportBasis && b.live && !b.delivered_on ? <button type="button" className={`${secondary} ml-2`} onClick={() => onAct(`basis-records/${b.id}/delivered`, { delivered_on: new Date().toISOString().slice(0, 10) })}>Delivered today</button> : null}
              </li>
            );
          })}
        </ul>
      </section>

      <section className={card}>
        <h2 className="font-serif text-xl text-navy">Project tracker</h2>
        <ul className="mt-2 space-y-1 text-sm">
          {(envelope?.timeline?.steps || []).map((s) => {
            const sc = stepControls(envelope, s);
            return (
              <li key={s.id}>{s.seq}. {s.label} — due {s.due_on}; {s.status}{s.done_at ? ` ${s.done_at.slice(0, 10)}` : ""}{s.note ? ` — ${s.note}` : ""}
                {sc.done ? <button type="button" className={`${secondary} ml-2`} onClick={() => onAct(`steps/${s.id}/done`, { note: null })}>Mark done</button> : null}
                {sc.edit ? <StepEdit step={s} onAct={onAct} /> : null}</li>
            );
          })}
        </ul>
        <Timeline timeline={envelope?.timeline} />
      </section>

      <section className={card}>
        <h2 className="font-serif text-xl text-navy">In-kind transfer instructions</h2>
        {sheet && c.sheet ? <p className="mt-1 text-sm"><a className="text-navy underline" href={sheet}>Instruction sheet (PDF)</a></p> : null}
        <table className="mt-2 w-full text-xs"><thead><tr><th className={th}>Account</th><th className={th}>Lot</th><th className={th}>Quantity</th><th className={th}>Basis</th>
          <th className={th}>Acquired</th><th className={th}>Status</th><th className={th}>ETF shares</th><th className={th}>Realised gain</th></tr></thead>
          <tbody>{(envelope?.transfers || []).map((x) => (
            <tr key={x.id}><td className={td}>{names[x.account_id] || x.account_id}</td><td className={td}>{x.tax_lot_id.slice(0, 8)}</td><td className={td}>{x.quantity}</td>
              <td className={td}>{x.basis}</td><td className={td}>{x.acquired_date}</td><td className={td}>{labelOf(v.transfer_statuses, x.status)}</td>
              <td className={td}>{x.etf_shares ?? "—"}</td><td className={td}>{x.realized_gain ?? "—"}</td></tr>))}</tbody></table>
        {c.routeManual || c.routeAdapter ? (
          <div className="mt-3 space-y-2 text-sm">
            <input className={input} placeholder="How the sheet was sent" value={channel} onChange={(e) => setChannel(e.target.value)} />
            {accts.filter((a) => a.status === "active").map((a) => (
              <div key={a.id} className="flex gap-2">
                <span className="w-40">{a.account || a.account_id}</span>
                {c.routeAdapter ? <button type="button" className={secondary} onClick={() => onAct(`plans/${p.id}/route`, { account_id: a.account_id, route: "adapter" }, "route")}>Send to the custodian</button> : null}
                {c.routeManual ? <button type="button" className={primary} onClick={() => onAct(`plans/${p.id}/route`, { account_id: a.account_id, route: "manual", channel })}>Mark sent (manual)</button> : null}
              </div>
            ))}
            {routeError ? <p className="text-[#9B2335]">{routeError.message}{routeError.manualOffered ? " The manual route is offered." : ""}</p> : null}
          </div>
        ) : null}
        {c.confirm ? (
          <div className="mt-3 flex flex-wrap gap-2 text-xs">
            <select className={input} value={confirm.plan_account_id} onChange={(e) => setConfirm({ ...confirm, plan_account_id: e.target.value })}>
              <option value="">Account</option>{accts.filter((a) => a.status === "active").map((a) => <option key={a.id} value={a.id}>{a.account || a.account_id}</option>)}
            </select>
            {["received_on", "etf_global_security_id", "etf_shares"].map((k) => (
              <input key={k} className={input} placeholder={{ received_on: "Received on", etf_global_security_id: "ETF security id", etf_shares: "ETF shares" }[k]}
                value={confirm[k]} onChange={(e) => setConfirm({ ...confirm, [k]: e.target.value })} />))}
            <button type="button" className={primary} onClick={() => onAct(`plan-accounts/${confirm.plan_account_id}/confirm`, {
              received_on: confirm.received_on, etf_global_security_id: confirm.etf_global_security_id, etf_shares: confirm.etf_shares })}>Record the custodian's confirmation</button>
          </div>
        ) : null}
      </section>

      <section className={card}>
        <h2 className="font-serif text-xl text-navy">For counsel</h2>
        <ul className="mt-2 list-disc pl-5 text-xs text-text-muted">{(envelope?.open_items || []).map((x) => <li key={x}>{x}</li>)}</ul>
      </section>
    </div>
  );
}

// ── Client wrappers ─────────────────────────────────────────────────────────
export function Section351Home({ initial }) {
  const [cand, setCand] = useState(initial.candidates);
  const [thr, setThr] = useState(initial.thresholds);
  const [prof, setProf] = useState(initial.profiles);
  const [plans, setPlans] = useState(initial.plans);
  const [msg, setMsg] = useState(null);
  const [form, setForm] = useState({ name: "", target_etf: "", sponsor_profile_id: "", valuation_date: "", seed_capital: "0" });
  const canCreate = candidateControls(plans).createPlan;
  const run = async (fn) => { setMsg(null); try { await fn(); } catch (e) { setMsg(e.message); } };
  const reload = async () => {
    setCand(await send("candidates")); setThr(await send("thresholds"));
    setProf(await send("sponsor-profiles")); setPlans(await send("plans"));
  };
  return (
    <div className="space-y-4">
      {msg ? <div className="rounded-[6px] border border-[#ece8dd] bg-white p-3 text-sm text-[#9B2335]">{msg}</div> : null}
      <section className={card}>
        <h2 className="font-serif text-xl text-navy">Plans</h2>
        <ul className="mt-2 space-y-1 text-sm">{(plans?.rows || []).map((p) => (
          <li key={p.id}><a className="text-navy underline" href={`/portfolio/replication/section351/${p.id}`}>{p.name}</a> — {p.target_etf}; {labelOf(vocab(plans).plan_statuses, p.status)}</li>))}</ul>
        {canCreate ? (
          <div className="mt-3 flex flex-wrap gap-2 text-sm">
            <input className={input} placeholder="Plan name" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
            <input className={input} placeholder="Target ETF (as text)" value={form.target_etf} onChange={(e) => setForm({ ...form, target_etf: e.target.value })} />
            <select className={input} value={form.sponsor_profile_id} onChange={(e) => setForm({ ...form, sponsor_profile_id: e.target.value })}>
              <option value="">Sponsor profile</option>{(prof?.rows || []).filter((r) => r.live && r.status === "active").map((r) => <option key={r.id} value={r.id}>{r.name} v{r.version_no}</option>)}
            </select>
            <input className={input} placeholder="Valuation date" value={form.valuation_date} onChange={(e) => setForm({ ...form, valuation_date: e.target.value })} />
            <input className={input} placeholder="Outside seed capital" value={form.seed_capital} onChange={(e) => setForm({ ...form, seed_capital: e.target.value })} />
            <button type="button" className={primary} onClick={() => run(async () => {
              const d = await send("plans", "POST", form); window.location.href = `/portfolio/replication/section351/${d.plan.id}`; })}>Create plan</button>
          </div>
        ) : null}
      </section>
      <CandidatesView envelope={cand} thresholdsEnvelope={thr}
        onSaveThresholds={(b) => run(async () => { await send("thresholds", "PUT", b); await reload(); })}
        profilesEnvelope={prof}
        onFixup={(s, f) => run(async () => {
          const out = await send(`accounts/${s.account_id}/fixup`, "POST", { valuation_date: f.valuation_date, sponsor_profile_id: f.sponsor_profile_id });
          setMsg(`Fix-up proposal ${out.fixup.proposal_id} made (${out.fixup.after.verdict} after; tax ${out.fixup.tax.tax ?? out.fixup.tax.note}) — it goes through the normal approval.`);
        })} />
      <ProfilesView envelope={prof} onSave={(b, key) => run(async () => {
        await send(key ? `sponsor-profiles/${key}/versions` : "sponsor-profiles", "POST", b); await reload(); })} />
    </div>
  );
}

export function Section351Plan({ planId, initialEnvelope }) {
  const [env, setEnv] = useState(initialEnvelope);
  const [msg, setMsg] = useState(null);
  const [routeError, setRouteError] = useState(null);
  const onAct = async (path, body, kind, method = "POST") => {
    setMsg(null); setRouteError(null);
    try {
      await send(path, method, body || {});
      setEnv(await send(`plans/${planId}`));
    } catch (e) {
      if (kind === "route" && e.status === 409) setRouteError(adapterRefusal(e.body));
      else setMsg(e.message);
    }
  };
  return (
    <div className="space-y-4">
      {msg ? <div className="rounded-[6px] border border-[#ece8dd] bg-white p-3 text-sm text-[#9B2335]">{msg}</div> : null}
      <PlanView envelope={env} onAct={onAct} routeError={routeError} />
    </div>
  );
}
