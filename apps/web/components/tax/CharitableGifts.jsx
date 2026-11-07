"use client";

/**
 * CharitableGifts — Tax → Charitable gifts (REP10). Donees, the gift
 * proposals, and one gift's detail: the lots (long-term gains only), the
 * in-kind vs sell-and-give-cash comparison (every figure an ESTIMATE with its
 * rates, or the server's reason it is not estimated), approve (never the
 * maker), print the transfer letter, mark sent / received (the gift date) /
 * acknowledged, and the rebuy. Delivery is manual, always. Controls from
 * lib/rep10.mjs (fail closed); labels from the envelope.
 */

import { useEffect, useState } from "react";
import { BusyButton, useBusy } from "@/components/ui/Busy";
import { estimate, giftControls, labelOf, money, vocab } from "@/lib/rep10.mjs";

const card = "rounded-[6px] border border-[#ece8dd] bg-white";
const input = "rounded border border-[#E2E8F0] px-3 py-2";
const primary = "rounded bg-navy px-3 py-1 text-white disabled:opacity-50";
const secondary = "rounded border border-navy px-3 py-1 text-navy disabled:opacity-50";

export function ComparisonView({ comparison }) {
  if (!comparison) return null;
  const ik = comparison.in_kind || {};
  const ca = comparison.cash || {};
  return (
    <table className="w-full text-sm">
      <thead><tr className="text-left text-text-muted"><th /><th>Give in kind</th><th>Sell and give cash</th></tr></thead>
      <tbody>
        <tr><td>Fair market value</td><td>{money(comparison.fmv)}</td><td>{money(comparison.fmv)}</td></tr>
        <tr><td>Capital-gains tax</td><td>{estimate(ik.cg_tax_avoided, ik.cg_note)} avoided</td><td>{estimate(ca.cg_tax_paid, ca.cg_note)} paid</td></tr>
        <tr><td>Deduction value this year</td><td>{estimate(ik.deduction?.value, ik.deduction?.note)}</td><td>{estimate(ca.deduction?.value, ca.deduction?.note)}</td></tr>
        <tr><td>Carried forward</td><td>{ik.deduction?.carryforward ? money(ik.deduction.carryforward) : "—"}</td><td>{ca.deduction?.carryforward ? money(ca.deduction.carryforward) : "—"}</td></tr>
        <tr><td className="font-medium">Tax saved by giving in kind</td><td colSpan={2} className="font-medium">{estimate(comparison.tax_saved_in_kind, comparison.tax_saved_note)}</td></tr>
      </tbody>
    </table>
  );
}

export function GiftDetailView({ envelope, onAct }) {
  const g = envelope?.gift;
  const [note, setNote] = useState("");
  const [received, setReceived] = useState("");
  const letter = useBusy(); // CATCHALL1 busy — Print transfer letter (errors shown by the page's message line)
  if (!g) return null;
  const v = vocab(envelope);
  const c = giftControls(envelope, g);
  return (
    <div className={`${card} space-y-3 p-4 text-sm`}>
      <p className="font-medium text-navy">{g.donee?.name} ({labelOf(v.donee_kinds, g.donee_kind)}) — {labelOf(v.gift_statuses, g.status)}</p>
      {g.gift_date ? <p>Gift date (received by the donee): {g.gift_date}</p> : null}
      <table className="w-full">
        <thead><tr className="text-left text-text-muted"><th>Lot acquired</th><th>Quantity</th><th>Price</th><th>FMV</th><th>Gain</th></tr></thead>
        <tbody>{(g.lots || []).map((l) => (
          <tr key={l.id}><td>{l.acquired_date}</td><td>{l.quantity}</td><td>{l.price_at_proposal} ({l.price_as_of})</td><td>{money(l.fmv_at_proposal)}</td><td>{money(l.gain_at_proposal)}</td></tr>
        ))}</tbody>
      </table>
      <ComparisonView comparison={g.comparison} />
      {(g.reminders || []).map((r) => <p key={r.code} className="text-text-muted">{r.message}</p>)}
      <div className="flex flex-wrap items-center gap-2">
        {c.submit ? <button type="button" className={primary} onClick={() => onAct?.("submit")}>Submit for approval</button> : null}
        {c.approve || c.reject ? <input className={input} placeholder="Note (required to reject)" value={note} onChange={(e) => setNote(e.target.value)} /> : null}
        {c.approve ? <button type="button" className={primary} onClick={() => onAct?.("approve", { note })}>Approve</button> : null}
        {c.reject ? <button type="button" className={secondary} onClick={() => onAct?.("reject", { note })}>Reject</button> : null}
        {c.letter ? <BusyButton busy={letter.busy} busyLabel="Generating…" className={secondary} onClick={() => letter.run(async () => { await onAct?.("letter"); })}>Print transfer letter</BusyButton> : null}
        {c.markSent ? <button type="button" className={primary} onClick={() => onAct?.("sent", { channel: "letter to the custodian" })}>Mark sent</button> : null}
        {c.markReceived ? (
          <>
            <input className={input} type="date" value={received} onChange={(e) => setReceived(e.target.value)} />
            <button type="button" className={primary} disabled={!received} onClick={() => onAct?.("received", { received_on: received })}>Mark received</button>
          </>
        ) : null}
        {c.acknowledge ? <button type="button" className={primary} onClick={() => onAct?.("acknowledged", { acknowledged_on: new Date().toISOString().slice(0, 10) })}>Note the donee's receipt</button> : null}
        {c.rebuy ? <button type="button" className={secondary} onClick={() => onAct?.("rebuy")}>Create the rebuy proposal</button> : null}
        {c.cancel ? <button type="button" className={secondary} onClick={() => onAct?.("cancel", { reason: note || "cancelled by the adviser" })}>Cancel</button> : null}
      </div>
    </div>
  );
}

export function GiftsView({ envelope, onOpen }) {
  const v = vocab(envelope);
  const rows = Array.isArray(envelope?.rows) ? envelope.rows : [];
  const donees = Array.isArray(envelope?.donees) ? envelope.donees : [];
  const notes = Array.isArray(v.open_items) ? v.open_items : [];
  return (
    <div className="space-y-4">
      <div className={`${card} p-4 text-sm`}>
        <h2 className="text-lg font-semibold text-navy">Donees</h2>
        {donees.length ? <ul className="mt-1 list-disc pl-5">{donees.map((d) => <li key={d.id}>{d.name} — {d.kind_label}, EIN {d.ein}, {d.receiving_broker} (DTC {d.dtc_participant}), account {d.account_number_masked}</li>)}</ul>
          : <p className="text-text-muted">No donee yet.</p>}
      </div>
      <div className={`${card} p-4 text-sm`}>
        <h2 className="text-lg font-semibold text-navy">Gift proposals</h2>
        {rows.length ? rows.map((g) => (
          <button key={g.id} type="button" className="block w-full py-1 text-left hover:underline" onClick={() => onOpen?.(g.id)}>
            {g.tax_year} · {labelOf(v.donee_kinds, g.donee_kind)} · {money(g.comparison?.fmv)} · {labelOf(v.gift_statuses, g.status)}
          </button>
        )) : <p className="text-text-muted">No gift proposed.</p>}
      </div>
      {notes.length ? (
        <div className={`${card} p-4 text-sm`}>
          <h2 className="text-lg font-semibold text-navy">Open for the CPA and counsel</h2>
          <ul className="mt-1 list-disc pl-5 text-text-muted">{notes.map((n) => <li key={n}>{n}</li>)}</ul>
        </div>
      ) : null}
    </div>
  );
}

export default function CharitableGifts() {
  const [env, setEnv] = useState(null);
  const [detail, setDetail] = useState(null);
  const [msg, setMsg] = useState(null);
  async function load() {
    const res = await fetch("/api/tax/gifts");
    const data = await res.json().catch(() => ({}));
    if (res.ok) setEnv(data); else setMsg(data.error);
  }
  async function open(id) {
    const res = await fetch(`/api/tax/gifts/${id}`);
    const data = await res.json().catch(() => ({}));
    if (res.ok) setDetail(data); else setMsg(data.error);
  }
  useEffect(() => { load(); }, []);
  async function onAct(kind, body) {
    setMsg(null);
    const id = detail.gift.id;
    const res = await fetch(`/api/tax/gifts/${id}/${kind}`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) });
    if (kind === "letter" && res.ok) {
      const url = URL.createObjectURL(await res.blob());
      window.open(url, "_blank");
      return;
    }
    const data = await res.json().catch(() => ({}));
    if (!res.ok) { setMsg(data.error); return; }
    setDetail(data);
    await load();
  }
  return (
    <div className="space-y-4">
      {msg ? <p className="text-sm text-[#9B2335]">{msg}</p> : null}
      <GiftsView envelope={env} onOpen={open} />
      <GiftDetailView envelope={detail} onAct={onAct} />
    </div>
  );
}
