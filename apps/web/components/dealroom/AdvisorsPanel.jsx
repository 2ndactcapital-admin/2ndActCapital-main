"use client";

/**
 * Advisors (DEALROOM3).
 *
 * InvestorAdvisors — an investor with granted access names their own
 * advisors (name, firm, role, email). The advisor signs an acknowledgement
 * before seeing anything, then sees exactly the investor's open folders; they
 * cannot sign, ask or invite. Roles come from the envelope's vocabulary.
 *
 * AdvisorAcknowledgement — what an invited advisor sees: the acknowledgement
 * text (from the server, with its hash), a typed name and a consent box.
 */

import { useState } from "react";
import { advisorLines } from "@/lib/dealRoom.mjs";

async function call(path, { method = "GET", body } = {}) {
  const res = await fetch(`/api/deal-room/${path}`, {
    method, headers: body !== undefined ? { "Content-Type": "application/json" } : undefined,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `Request failed (${res.status})`);
  return data;
}

export function InvestorAdvisors({ envelope, onChange }) {
  const roles = envelope?.vocabularies?.advisor_roles || [];
  const [form, setForm] = useState({ name: "", firm: "", role: roles[0]?.key || "", email: "" });
  const [error, setError] = useState(null);
  const dealId = envelope?.deal?.id;
  const canInvite = envelope?.my_access?.status === "granted";
  const advisors = envelope?.advisors || [];
  return (
    <section className="rounded-[6px] border border-[#ece8dd] bg-white p-5" data-testid="investor-advisors">
      <h3 className="text-base font-semibold text-navy">Your advisors</h3>
      <p className="mt-1 text-xs text-text-muted">
        An advisor you name sees exactly the folders open to you, once they sign an acknowledgement. You remain
        responsible for them. Their access ends when yours does.
      </p>
      <ul className="mt-2 space-y-1 text-sm text-text-secondary">
        {advisorLines(advisors).map((line, i) => (
          <li key={advisors[i].advisor_user_id} className="flex justify-between gap-2">
            <span>{line}</span>
            {advisors[i].status !== "revoked" && (
              <button type="button" className="text-xs text-[#9B2335] hover:underline" onClick={async () => {
                const reason = window.prompt("Why remove this advisor?");
                if (!reason) return;
                try {
                  await call(`deals/${dealId}/advisors/${advisors[i].advisor_user_id}/remove`, { method: "POST", body: { reason } });
                  await onChange?.();
                } catch (e) { setError(e.message); }
              }}>Remove</button>
            )}
          </li>
        ))}
      </ul>
      {canInvite && (
        <div className="mt-3 grid gap-2 text-sm sm:grid-cols-2" data-testid="advisor-invite">
          <input aria-label="Advisor name" placeholder="Name" className="rounded border border-border px-2 py-1.5"
            value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
          <input aria-label="Advisor firm" placeholder="Firm" className="rounded border border-border px-2 py-1.5"
            value={form.firm} onChange={(e) => setForm({ ...form, firm: e.target.value })} />
          <select aria-label="Advisor role" className="rounded border border-border bg-white px-2 py-1.5" value={form.role}
            onChange={(e) => setForm({ ...form, role: e.target.value })}>
            {roles.map((r) => <option key={r.key} value={r.key}>{r.label}</option>)}
          </select>
          <input aria-label="Advisor email" placeholder="Email" className="rounded border border-border px-2 py-1.5"
            value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} />
          <button type="button" disabled={form.name.trim().length < 2 || !form.email.includes("@") || !form.role}
            className="rounded bg-navy px-4 py-2 font-medium text-white disabled:opacity-50 sm:col-span-2"
            onClick={async () => {
              try {
                setError(null);
                await call(`deals/${dealId}/advisors`, { method: "POST", body: { ...form, firm: form.firm || null } });
                setForm({ ...form, name: "", firm: "", email: "" });
                await onChange?.();
              } catch (e) { setError(e.message); }
            }}>Invite advisor</button>
        </div>
      )}
      {error && <p className="mt-2 text-sm text-[#9B2335]" role="alert">{error}</p>}
    </section>
  );
}

export function AdvisorAcknowledgement({ envelope, onChange }) {
  const [doc, setDoc] = useState(null);
  const [name, setName] = useState("");
  const [consent, setConsent] = useState(false);
  const [error, setError] = useState(null);
  const dealId = envelope?.deal?.id;
  const adv = envelope?.advisor || {};
  return (
    <section className="rounded-[6px] border border-[#ece8dd] bg-white p-5" data-testid="advisor-ack">
      <h3 className="text-base font-semibold text-navy">Advisor to {adv.investor_name}</h3>
      {adv.needs_ack ? (
        <div className="mt-2 space-y-3 text-sm">
          <p className="text-text-secondary">Sign the acknowledgement to open the folders {adv.investor_name} can see.
            As an advisor you cannot sign agreements, ask questions or invite others.</p>
          {!doc ? (
            <button type="button" className="rounded border border-navy px-4 py-2 text-navy" onClick={async () => {
              try { setDoc(await call(`deals/${dealId}/advisor-acknowledgement`)); } catch (e) { setError(e.message); }
            }}>Read the acknowledgement</button>
          ) : (
            <>
              <pre className="whitespace-pre-wrap rounded border border-border p-3 font-sans text-text-secondary">{doc.text}</pre>
              <label className="block text-text-secondary">Type your full name
                <input className="mt-1 block w-full rounded border border-border px-2 py-1.5" value={name}
                  onChange={(e) => setName(e.target.value)} />
              </label>
              <label className="flex items-center gap-2 text-text-secondary">
                <input type="checkbox" checked={consent} onChange={(e) => setConsent(e.target.checked)} />
                I agree to be bound as set out above
              </label>
              <button type="button" disabled={!consent || name.trim().length < 2}
                className="rounded bg-navy px-4 py-2 font-medium text-white disabled:opacity-50" onClick={async () => {
                  try {
                    await call(`deals/${dealId}/advisor-acknowledgement`, { method: "POST",
                      body: { typed_name: name, consent, text_sha256: doc.text_sha256 } });
                    await onChange?.();
                  } catch (e) { setError(e.message); }
                }}>Acknowledge</button>
            </>
          )}
        </div>
      ) : (
        <p className="mt-2 text-sm text-text-secondary">
          You see the folders open to {adv.investor_name}
          {adv.investor_status_label ? ` (their access: ${adv.investor_status_label})` : ""}.
        </p>
      )}
      {error && <p className="mt-2 text-sm text-[#9B2335]" role="alert">{error}</p>}
    </section>
  );
}
