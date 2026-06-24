"use client";

import { useActionState, useEffect, useState } from "react";
import { updateComplianceAction } from "@/lib/crmActions";

const INPUT = "mt-1 w-full rounded-md border border-border bg-bg-card px-3 py-2 text-sm text-text-primary outline-none focus:ring-2 focus:ring-navy";
const LABEL = "block text-xs font-medium uppercase tracking-wide text-text-muted";

const KYC = ["not_started", "in_progress", "approved", "flagged", "expired"];
const OFAC = ["not_screened", "passed", "false_positive", "review_required"];
const AML = ["low", "medium", "high"];
const ACCRED = ["not_verified", "self_certified", "third_party_verified", "expired"];

function humanize(v) {
  return v ? v.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase()) : "—";
}

// Tailwind-safe badge colors keyed by semantic level.
const LEVEL = {
  good: "bg-[#DCFCE7] text-[#166534]",
  warn: "bg-gold-light text-navy",
  bad: "bg-[#FBE3E6] text-[#9B2335]",
  neutral: "bg-border text-text-secondary",
};

function kycLevel(s) {
  if (s === "approved") return "good";
  if (s === "in_progress") return "warn";
  if (s === "flagged" || s === "expired") return "bad";
  return "neutral";
}
function ofacLevel(s) {
  if (s === "passed") return "good";
  if (s === "review_required") return "bad";
  if (s === "false_positive") return "warn";
  return "neutral";
}
function amlLevel(s) {
  return s === "low" ? "good" : s === "medium" ? "warn" : s === "high" ? "bad" : "neutral";
}
function accredLevel(s) {
  if (s === "third_party_verified") return "good";
  if (s === "self_certified") return "warn";
  if (s === "expired") return "bad";
  return "neutral";
}

function Badge({ value, level }) {
  return (
    <span className={`inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium ${LEVEL[level]}`}>
      {humanize(value)}
    </span>
  );
}

function Field({ label, children }) {
  return (
    <div>
      <dt className="text-xs font-medium uppercase tracking-wide text-text-muted">{label}</dt>
      <dd className="mt-1 text-sm text-text-primary">{children}</dd>
    </div>
  );
}

export default function ComplianceTab({ entityId, initial }) {
  const [record, setRecord] = useState(initial || null);
  const [editing, setEditing] = useState(false);
  const [state, formAction, pending] = useActionState(
    updateComplianceAction.bind(null, entityId),
    {},
  );

  useEffect(() => {
    if (state?.ok && state.item) {
      setRecord(state.item);
      setEditing(false);
    }
  }, [state]);

  const r = record || {};

  return (
    <div>
      <div className="flex items-center justify-between">
        <h2 className="text-base font-semibold text-navy">Compliance</h2>
        {!editing && (
          <button type="button" onClick={() => setEditing(true)} className="text-sm font-medium text-navy hover:underline">
            Edit
          </button>
        )}
      </div>
      <p className="mt-1 text-xs text-text-muted">Compliance data — advisor access only</p>

      {!editing ? (
        <dl className="mt-4 grid gap-4 sm:grid-cols-2">
          <Field label="KYC Status"><Badge value={r.kyc_status} level={kycLevel(r.kyc_status)} /></Field>
          <Field label="KYC Verified Date">{r.kyc_verified_date || "—"}</Field>
          <Field label="OFAC Screen"><Badge value={r.ofac_screen_status} level={ofacLevel(r.ofac_screen_status)} /></Field>
          <Field label="OFAC Screen Date">{r.ofac_screen_date ? new Date(r.ofac_screen_date).toLocaleDateString() : "—"}</Field>
          <Field label="AML Risk Rating"><Badge value={r.aml_risk_rating} level={amlLevel(r.aml_risk_rating)} /></Field>
          <Field label="Accreditation"><Badge value={r.accreditation_status} level={accredLevel(r.accreditation_status)} /></Field>
          <Field label="Accreditation Basis">{r.accreditation_basis || "—"}</Field>
          <Field label="Next Re-verification Due">{r.next_reverification_due || "—"}</Field>
          <Field label="PEP">{r.pep_status ? "Yes" : "No"}</Field>
          <Field label="PEP Details">{r.pep_details || "—"}</Field>
          <div className="sm:col-span-2"><Field label="Notes">{r.notes || "—"}</Field></div>
        </dl>
      ) : (
        <form action={formAction} className="mt-4 grid gap-3 sm:grid-cols-2">
          <div>
            <label className={LABEL}>KYC Status</label>
            <select name="kyc_status" defaultValue={r.kyc_status || "not_started"} className={INPUT}>
              {KYC.map((v) => <option key={v} value={v}>{humanize(v)}</option>)}
            </select>
          </div>
          <div>
            <label className={LABEL}>OFAC Screen Status</label>
            <select name="ofac_screen_status" defaultValue={r.ofac_screen_status || "not_screened"} className={INPUT}>
              {OFAC.map((v) => <option key={v} value={v}>{humanize(v)}</option>)}
            </select>
          </div>
          <div>
            <label className={LABEL}>AML Risk Rating</label>
            <select name="aml_risk_rating" defaultValue={r.aml_risk_rating || "low"} className={INPUT}>
              {AML.map((v) => <option key={v} value={v}>{humanize(v)}</option>)}
            </select>
          </div>
          <div>
            <label className={LABEL}>Accreditation Status</label>
            <select name="accreditation_status" defaultValue={r.accreditation_status || "not_verified"} className={INPUT}>
              {ACCRED.map((v) => <option key={v} value={v}>{humanize(v)}</option>)}
            </select>
          </div>
          <div className="sm:col-span-2">
            <label className={LABEL}>Accreditation Basis</label>
            <input name="accreditation_basis" defaultValue={r.accreditation_basis || ""} className={INPUT} />
          </div>
          <label className="flex items-center gap-2 text-sm text-text-secondary">
            <input type="checkbox" name="pep_status" defaultChecked={!!r.pep_status} /> PEP (Politically Exposed Person)
          </label>
          <div />
          <div className="sm:col-span-2">
            <label className={LABEL}>PEP Details</label>
            <input name="pep_details" defaultValue={r.pep_details || ""} className={INPUT} />
          </div>
          <div className="sm:col-span-2">
            <label className={LABEL}>Notes</label>
            <textarea name="notes" rows={3} defaultValue={r.notes || ""} className={INPUT} />
          </div>
          {state?.error && <p className="text-sm text-[#9B2335] sm:col-span-2">{state.error}</p>}
          <div className="flex gap-2 sm:col-span-2">
            <button type="submit" disabled={pending} className="rounded-md bg-navy px-4 py-2 text-sm font-medium text-bg-app hover:opacity-90 disabled:opacity-60">
              {pending ? "Saving…" : "Save compliance"}
            </button>
            <button type="button" onClick={() => setEditing(false)} className="rounded-md border border-border px-4 py-2 text-sm font-medium text-text-secondary hover:bg-border">
              Cancel
            </button>
          </div>
        </form>
      )}
    </div>
  );
}
