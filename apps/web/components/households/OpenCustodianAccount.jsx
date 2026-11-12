"use client";

import { useState } from "react";
import {
  SIMULATED_LABEL, actionState, buildBody, candidatesFor, canOpen, fieldLabel, groupProblems, initialState,
  needsTypedDob, needsTypedTaxId, openedText, roleLabel, taxIdText, typeOptions,
} from "@/lib/custodianAccountOpening.mjs";

// CPSIM06 — "Open account at custodian" on a household (the SIMULATED custodian only). The account types, roles and
// every vocabulary are the server's; the controls render only when the server says can_open === true (fails closed).
// A tax id is shown as its last four only; a date of birth is never shown (the server reads both at send time, or the
// opener types them for this one request — sent once, never stored).
const card = "rounded-[6px] border border-[#ece8dd] bg-white p-4";
const button = "rounded border border-[#C5A880] px-3 py-1 text-sm text-[#1B2B4B] hover:bg-[#F5F1EB] disabled:opacity-50";
const input = "mt-1 w-full rounded border border-[#E2E8F0] bg-white px-2 py-1 text-sm";
const quiet = "text-xs text-[#64748B]";

export function SimulatedBanner() {
  return (
    <p className="rounded border border-[#C5A880] bg-[#FAF9F6] px-3 py-1 text-xs text-[#1B2B4B]" data-testid="simulated-banner">
      <span className="font-semibold">{SIMULATED_LABEL}</span> — a simulated custodian. In good order the account is opened at
      once; otherwise every problem is listed. Nothing is sent to a real custodian.
    </p>
  );
}

function Problems({ items, testid }) {
  if (!items || !items.length) return null;
  return (
    <ul className="mt-1 space-y-0.5 text-xs text-[#9B2335]" data-testid={testid}>
      {items.map((p, i) => (
        <li key={i} data-problem-field={p.field}><span className="font-medium">{fieldLabel(p.field)}</span>: {p.message}</li>
      ))}
    </ul>
  );
}

function addressText(a) {
  if (!a) return "—";
  return [a.street1, a.street2, a.city, a.state, a.postal_code, a.country].filter(Boolean).join(", ");
}

function OwnerSlot({ env, owner, index, problems, onChange }) {
  const members = candidatesFor(env, owner.role);
  const m = (env.members || []).find((x) => x.entity_id === owner.entity_id) || null;
  const ov = owner.overrides || {};
  const set = (k, v) => onChange({ ...owner, overrides: { ...ov, [k]: v } });
  const cit = env.vocabularies?.citizenship || [];
  const emp = env.vocabularies?.employment_statuses || [];
  return (
    <div className="border-t border-[#ece8dd] pt-2" data-owner={index} data-role={owner.role}>
      <label className="block text-sm text-[#334155]">{roleLabel(env, owner.role)}
        <select className={input} value={owner.entity_id} onChange={(e) => onChange({ ...owner, entity_id: e.target.value })}>
          <option value="">Choose a household member…</option>
          {members.map((x) => <option key={x.entity_id} value={x.entity_id}>{x.display_name}</option>)}
        </select>
      </label>
      {m && (
        <div className="mt-1 grid grid-cols-2 gap-2 text-xs text-[#334155]" data-testid={`owner-prefill-${index}`}>
          <div>Legal name: {m.legal_name || "—"}</div>
          <div>Email: {m.email || "—"}</div>
          <div>Phone: {m.phone || "—"}</div>
          <div>Residence: {addressText(m.residential_address)}</div>
          <div>Mailing: {addressText(m.mailing_address)}</div>
          <div data-testid={`owner-taxid-${index}`}>Tax ID: {taxIdText(m)}</div>
          <div>Date of birth: {m.date_of_birth_on_record ? "on record" : "not on record"}{m.is_minor === true ? " · a minor" : ""}</div>
          {needsTypedTaxId(m) && (
            <label className="col-span-1">Tax ID (sent once, not stored)
              <input className={input} type="password" autoComplete="off" value={ov.tax_id_value || ""}
                onChange={(e) => set("tax_id_value", e.target.value)} />
            </label>
          )}
          {needsTypedDob(m) && m.kind === "person" && (
            <label>Date of birth (sent once, not stored)
              <input className={input} type="date" value={ov.date_of_birth || ""} onChange={(e) => set("date_of_birth", e.target.value)} />
            </label>
          )}
          {m.kind === "person" && (
            <>
              <label>Citizenship
                <select className={input} value={ov.citizenship || m.citizenship || ""} onChange={(e) => set("citizenship", e.target.value)}>
                  <option value="">—</option>
                  {cit.map((c) => <option key={c.key} value={c.key}>{c.label}</option>)}
                </select>
              </label>
              {(ov.citizenship || m.citizenship) === "visa" && (
                <>
                  <label>Visa type
                    <select className={input} value={ov.visa_type || ""} onChange={(e) => set("visa_type", e.target.value)}>
                      <option value="">—</option>
                      {(env.vocabularies?.visa_types || []).map((v) => <option key={v} value={v}>{v}</option>)}
                    </select>
                  </label>
                  <label>Visa expires
                    <input className={input} type="date" value={ov.visa_expiration || ""} onChange={(e) => set("visa_expiration", e.target.value)} />
                  </label>
                </>
              )}
              {owner.role !== "minor" && (
                <label>Employment
                  <select className={input} value={ov.employment_status || (m.employment ? m.employment.status : "")}
                    onChange={(e) => set("employment_status", e.target.value)}>
                    <option value="">—</option>
                    {emp.map((c) => <option key={c.key} value={c.key}>{c.label}</option>)}
                  </select>
                </label>
              )}
            </>
          )}
        </div>
      )}
      <Problems items={problems} testid={`owner-problems-${index}`} />
    </div>
  );
}

export function OpenCustodianAccountForm({ envelope, initialState: init = null, initialResult = null, onOpened }) {
  const env = envelope;
  const [state, setState] = useState(init || initialState(env, ""));
  const [result, setResult] = useState(initialResult);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  if (!env) return null;
  if (!canOpen(env)) {
    return (
      <div className={card} data-testid="custodian-open-unavailable">
        <SimulatedBanner />
        <p className={`${quiet} mt-2`}>{env?.availability?.reason || "Account opening is not available here."}</p>
      </div>
    );
  }
  const options = typeOptions(env);
  const groups = groupProblems(result?.problems);
  async function send(which) {
    setBusy(true);
    setError(null);
    try {
      const res = await fetch(`/api/admin/households/${encodeURIComponent(env.household.id)}/custodian-account/${which}`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(buildBody(state)),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data?.error || `Request failed (${res.status})`);
      setResult(data);
      if (data.draft_reference) setState((s) => ({ ...s, draft_reference: data.draft_reference }));
      if (data.opened && onOpened) onOpened(data);
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }
  const bens = state.beneficiaries || [];
  const tiers = env.vocabularies?.tiers || [];
  return (
    <div className={card} data-testid="custodian-open-form">
      <SimulatedBanner />
      <label className="mt-3 block text-sm text-[#334155]">Account type
        <select className={input} value={state.account_type} data-testid="account-type"
          onChange={(e) => setState(initialState(env, e.target.value))}>
          <option value="">Choose…</option>
          {options.map((o) => (
            <option key={o.key} value={o.key} disabled={o.disabled} data-supported={o.disabled ? "no" : "yes"}
              title={o.reason || undefined}>
              {o.label}{o.disabled ? ` — ${o.reason}` : (o.registration ? ` (recorded as ${o.registration})` : "")}
            </option>
          ))}
        </select>
      </label>
      <Problems items={groups.account_type} testid="type-problems" />
      <Problems items={groups.owners} testid="owners-problems" />
      <div className="mt-2 space-y-2">
        {(state.owners || []).map((o, i) => (
          <OwnerSlot key={`${o.role}-${i}`} env={env} owner={o} index={i} problems={groups[`owners.${i}`]}
            onChange={(next) => setState((s) => ({ ...s, owners: s.owners.map((x, j) => (j === i ? next : x)) }))} />
        ))}
      </div>
      {state.account_type && (
        <div className="mt-3" data-testid="beneficiaries">
          <h4 className="text-xs font-medium uppercase tracking-wide text-[#64748B]">Beneficiaries</h4>
          {bens.map((b, i) => (
            <div key={i} className="mt-1 grid grid-cols-4 gap-2" data-beneficiary={i}>
              <select className={input} value={b.tier || "primary"} onChange={(e) => setState((s) => ({ ...s,
                beneficiaries: s.beneficiaries.map((x, j) => (j === i ? { ...x, tier: e.target.value } : x)) }))}>
                {tiers.map((t) => <option key={t.key} value={t.key}>{t.label}</option>)}
              </select>
              <input className={input} placeholder="Name" value={b.name || ""} onChange={(e) => setState((s) => ({ ...s,
                beneficiaries: s.beneficiaries.map((x, j) => (j === i ? { ...x, name: e.target.value } : x)) }))} />
              <select className={input} value={b.kind || ""} onChange={(e) => setState((s) => ({ ...s,
                beneficiaries: s.beneficiaries.map((x, j) => (j === i ? { ...x, kind: e.target.value } : x)) }))}>
                <option value="">Person or entity…</option><option value="person">Person</option><option value="entity">Entity</option>
              </select>
              <input className={input} placeholder="%" inputMode="decimal" value={b.percentage || ""} onChange={(e) => setState((s) => ({ ...s,
                beneficiaries: s.beneficiaries.map((x, j) => (j === i ? { ...x, percentage: e.target.value } : x)) }))} />
              <div className="col-span-4"><Problems items={groups[`beneficiaries.${i}`]} testid={`beneficiary-problems-${i}`} /></div>
            </div>
          ))}
          <Problems items={groups.beneficiaries} testid="beneficiaries-problems" />
          <button type="button" className={`${button} mt-1`} onClick={() => setState((s) => ({ ...s,
            beneficiaries: [...(s.beneficiaries || []), { tier: "primary", name: "", kind: "", percentage: "" }] }))}>
            Add beneficiary</button>
          <h4 className="mt-3 text-xs font-medium uppercase tracking-wide text-[#64748B]">Trusted contact (optional)</h4>
          <div className="grid grid-cols-3 gap-2">
            {["name", "email", "phone"].map((k) => (
              <input key={k} className={input} placeholder={k} value={state.trusted_contact?.[k] || ""}
                onChange={(e) => setState((s) => ({ ...s, trusted_contact: { ...(s.trusted_contact || {}), [k]: e.target.value } }))} />
            ))}
          </div>
          <Problems items={groups.trusted_contact} testid="trusted-problems" />
        </div>
      )}
      <Problems items={groups.general} testid="general-problems" />
      {error && <p className="mt-2 text-sm text-[#9B2335]">{error}</p>}
      {result && result.ok === true && !result.opened && (
        <p className="mt-2 text-sm text-[#2D6A4F]" data-testid="check-ok">In good order — the simulated custodian would open it.</p>
      )}
      {result?.opened && <p className="mt-2 text-sm text-[#2D6A4F]" data-testid="opened">{openedText(result)}</p>}
      <div className="mt-3 flex gap-2">
        <button type="button" className={button} disabled={busy || !state.account_type} onClick={() => send("check")}>Check</button>
        <button type="button" className={button} disabled={busy || !state.account_type} onClick={() => send("open")}>Open</button>
      </div>
    </div>
  );
}

// The household card's action: present only when the server's detail says can_open === true; otherwise the reason (if
// the caller could write at all) — never a button.
export default function OpenCustodianAccountAction({ household, onOpened }) {
  const [env, setEnv] = useState(null);
  const [error, setError] = useState(null);
  const st = actionState(household);
  if (!st.show) {
    return st.reason ? <p className={`${quiet} mt-2`} data-testid="custodian-open-reason">Open account at custodian: {st.reason}</p> : null;
  }
  async function load() {
    setError(null);
    try {
      const res = await fetch(`/api/admin/households/${encodeURIComponent(household.id)}/custodian-account`);
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data?.error || `Request failed (${res.status})`);
      setEnv(data);
    } catch (err) {
      setError(err.message);
    }
  }
  return (
    <div className="mt-4" data-testid="custodian-open-action">
      {!env && <button type="button" className={button} onClick={load}>Open account at custodian</button>}
      {error && <p className="text-sm text-[#9B2335]">{error}</p>}
      {env && <OpenCustodianAccountForm envelope={env} onOpened={onOpened} />}
    </div>
  );
}
