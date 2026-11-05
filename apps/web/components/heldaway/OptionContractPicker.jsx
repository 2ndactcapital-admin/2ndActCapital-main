"use client";

// BUGFIX3 (MUSE-OLIVO-M) — listed_option entry: a picker of the EXISTING rep8 master contracts on the typed
// underlying (an option resolves only through one). Picking fills option type / strike / expiry / multiplier; when
// none exists the API's own reason is shown ("No option contract on X exists yet …"), never a silent empty list.
import { useState } from "react";
import { optionContractLabel } from "@/lib/bugfix3.mjs";

export default function OptionContractPicker({ underlying, onPick }) {
  const [env, setEnv] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  async function find() {
    setBusy(true);
    setError(null);
    try {
      const res = await fetch(`/api/portentry/portfolio/option-contracts?underlying_ticker=${encodeURIComponent(underlying || "")}`,
        { cache: "no-store" });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(typeof body.detail === "string" ? body.detail : `Request failed (${res.status})`);
      setEnv(body);
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }
  const rows = Array.isArray(env?.rows) ? env.rows : [];
  return (
    <span className="inline-flex flex-wrap items-center gap-2">
      <button type="button" className="rounded-[6px] border border-[#1B2B4B] px-3 py-1 text-sm text-[#1B2B4B] disabled:opacity-50"
        disabled={busy || !(underlying || "").trim()} onClick={find}>{busy ? "Finding…" : "Find contracts"}</button>
      {env && rows.length > 0 && (
        <select className="rounded-[6px] border border-[#E2E8F0] bg-white px-2 py-1 text-sm" defaultValue=""
          aria-label="Option contract" onChange={(e) => {
            const c = rows.find((r) => r.global_security_id === e.target.value);
            if (c) onPick(c);
          }}>
          <option value="">Choose a contract…</option>
          {rows.map((r) => <option key={r.global_security_id} value={r.global_security_id}>{optionContractLabel(r)}</option>)}
        </select>
      )}
      {env && rows.length === 0 && <span className="text-sm text-[#64748B]">{env.empty_reason}</span>}
      {error && <span className="text-sm text-[#9B2335]">{error}</span>}
    </span>
  );
}
