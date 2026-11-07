"use client";

// BUGFIX3 (MUSE-OLIVO-M) — listed_option entry: a picker of the EXISTING rep8 master contracts on the typed
// underlying (an option resolves only through one). Picking fills option type / strike / expiry / multiplier; when
// none exists the API's own reason is shown ("No option contract on X exists yet …"), never a silent empty list.
// CATCHALL1 (MUSE I-12) — (a) the search runs when the underlying CHANGES (typed, pasted or autofilled — debounced),
// not only on the button; (b) when none exists and the envelope grants it (rep8's manage_replication), "Define this
// contract" opens an inline form saved through rep8's own route (find-or-create on its terms: the same terms again
// return the existing contract), then the contract is selected in the line.
import { useEffect, useRef, useState } from "react";
import { optionContractLabel } from "@/lib/bugfix3.mjs";
import { canDefine, defineBody, defineForm, defineProblems, pickedFromDefine, withContract } from "@/lib/optionDefine.mjs";
import { BusyButton, BusyError, useBusy } from "@/components/ui/Busy";

const box = "rounded-[6px] border border-[#E2E8F0] bg-white px-2 py-1 text-sm";

export function DefineContractForm({ env, form, setForm, onSave, onCancel, busy, error }) {
  const v = env?.vocabularies || {};
  const problems = defineProblems(form);
  const set = (k) => (e) => setForm({ ...form, [k]: e.target.type === "checkbox" ? e.target.checked : e.target.value });
  return (
    <span className="flex w-full flex-wrap items-center gap-2 rounded-[6px] border border-[#ece8dd] bg-[#FAF9F6] p-2" data-define="1">
      <span className="text-sm text-[#334155]">Define a contract on {env?.underlying?.ticker}:</span>
      <select className={box} aria-label="Call or put" value={form.option_type} onChange={set("option_type")}>
        {(v.option_types || []).map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
      </select>
      <input className={`${box} w-24`} aria-label="Strike" placeholder="Strike" value={form.strike} onChange={set("strike")} onInput={set("strike")} />
      <input className={box} aria-label="Expiry" type="date" value={form.expiry} onChange={set("expiry")} />
      <input className={`${box} w-20`} aria-label="Multiplier" value={form.multiplier} onChange={set("multiplier")} />
      <select className={box} aria-label="Exercise style" value={form.exercise_style} onChange={set("exercise_style")}>
        <option value="">Style…</option>
        {(v.exercise_styles || []).map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
      </select>
      {env?.underlying?.kind === "index" && (
        <label className="text-sm text-[#334155]"><input type="checkbox" checked={Boolean(form.broad_based_index)} onChange={set("broad_based_index")} /> Broad-based index</label>
      )}
      <BusyButton className="rounded-[6px] bg-[#1B2B4B] px-3 py-1 text-sm text-white disabled:opacity-50" busy={busy}
        disabled={problems.length > 0} onClick={onSave}>Save contract</BusyButton>
      <button type="button" className="text-sm text-[#64748B]" disabled={busy} onClick={onCancel}>Cancel</button>
      {problems.length > 0 && <span className="text-xs text-[#64748B]">{problems[0]}</span>}
      <BusyError error={error} />
    </span>
  );
}

export default function OptionContractPicker({ underlying, onPick }) {
  const [env, setEnv] = useState(null);
  const [picked, setPicked] = useState("");
  const [defining, setDefining] = useState(null);   // the define form's values, or null
  const [note, setNote] = useState(null);
  const finder = useBusy();
  const saver = useBusy();
  const seq = useRef(0);

  async function find(ticker = underlying) {
    const t = (ticker || "").trim();
    if (!t) return;
    const mine = ++seq.current;   // an older response never overwrites a newer search
    await finder.run(async () => {
      const res = await fetch(`/api/portentry/portfolio/option-contracts?underlying_ticker=${encodeURIComponent(t)}`,
        { cache: "no-store" });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(typeof body.error === "string" ? body.error : `Request failed (${res.status})`);
      if (mine === seq.current) { setEnv(body); setDefining(null); setNote(null); }
    });
  }
  // any change of the underlying — typing, paste, autofill — searches after a pause
  useEffect(() => {
    if (!(underlying || "").trim()) return undefined;
    const id = setTimeout(() => find(underlying), 400);
    return () => clearTimeout(id);
  }, [underlying]); // eslint-disable-line react-hooks/exhaustive-deps

  async function save() {
    await saver.run(async () => {
      const res = await fetch("/api/replication/options/contracts", {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(defineBody(env, defining)),
      });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(typeof body.error === "string" ? body.error : `Request failed (${res.status})`);
      const { row, existed } = pickedFromDefine(env, body);
      if (!row) throw new Error("The contract was not returned");
      setEnv({ ...env, rows: withContract(env.rows, row), empty_reason: null });
      setPicked(row.global_security_id);
      setDefining(null);
      setNote(existed ? "That contract already existed — selected it." : "Contract defined and selected.");
      onPick(row);
    });
  }

  const rows = Array.isArray(env?.rows) ? env.rows : [];
  return (
    <span className="inline-flex flex-wrap items-center gap-2">
      <BusyButton className="rounded-[6px] border border-[#1B2B4B] px-3 py-1 text-sm text-[#1B2B4B] disabled:opacity-50"
        busy={finder.busy} busyLabel="Finding…" disabled={!(underlying || "").trim()} onClick={() => find()}>Find contracts</BusyButton>
      {env && rows.length > 0 && (
        <select className={box} value={picked} aria-label="Option contract" onChange={(e) => {
          setPicked(e.target.value);
          const c = rows.find((r) => r.global_security_id === e.target.value);
          if (c) onPick(c);
        }}>
          <option value="">Choose a contract…</option>
          {rows.map((r) => <option key={r.global_security_id} value={r.global_security_id}>{optionContractLabel(r)}</option>)}
        </select>
      )}
      {env && rows.length === 0 && <span className="text-sm text-[#64748B]">{env.empty_reason}</span>}
      {env && rows.length === 0 && !defining && canDefine(env) && (
        <button type="button" className="text-sm text-[#1B2B4B] underline" onClick={() => setDefining(defineForm(env))}>Define this contract</button>
      )}
      {defining && <DefineContractForm env={env} form={defining} setForm={setDefining} onSave={save}
        onCancel={() => setDefining(null)} busy={saver.busy} error={saver.error} />}
      {note && <span className="text-sm text-[#2D6A4F]">{note}</span>}
      <BusyError error={finder.error} />
    </span>
  );
}
