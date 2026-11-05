"use client";

// PORTENTRY2 (MUSE-OLIVO-P.1) — find a security in the master by ticker, name, CUSIP, ISIN or FIGI (GET
// /api/securities/search → the API's require_internal read). Read-only: choosing one fills the row's ticker / CUSIP.
// A caller the API refuses (a member) simply sees no results — the held-away entry still accepts a typed ticker.
import { useEffect, useRef, useState } from "react";

const input = "rounded-[6px] border border-[#E2E8F0] bg-white px-3 py-2 text-sm text-[#0F172A]";

export default function SecuritySearch({ onPick }) {
  const [q, setQ] = useState("");
  const [rows, setRows] = useState([]);
  const [msg, setMsg] = useState(null);
  const timer = useRef(null);
  useEffect(() => {
    clearTimeout(timer.current);
    if (!q.trim()) {
      setRows([]);
      return undefined;
    }
    timer.current = setTimeout(async () => {
      try {
        const res = await fetch(`/api/securities/search?q=${encodeURIComponent(q.trim())}`, { cache: "no-store" });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) {
          setRows([]);
          setMsg(typeof data.detail === "string" ? data.detail : null);
          return;
        }
        setMsg(null);
        setRows(Array.isArray(data.rows) ? data.rows : []);
      } catch {
        setRows([]);
      }
    }, 250);
    return () => clearTimeout(timer.current);
  }, [q]);
  return (
    <div className="relative w-full">
      <input className={`${input} w-full`} value={q} onChange={(e) => setQ(e.target.value)}
        placeholder="Find a security — ticker, name, CUSIP, ISIN or FIGI" aria-label="Find a security" />
      {rows.length > 0 && (
        <ul className="absolute z-40 mt-1 max-h-60 w-full overflow-auto rounded-[6px] border border-[#ece8dd] bg-white">
          {rows.map((r) => (
            <li key={r.id}>
              <button type="button" className="w-full px-3 py-1.5 text-left text-sm hover:bg-[#FAF9F6]"
                onClick={() => { onPick(r); setQ(""); setRows([]); }}>
                {r.identifiers?.ticker ? <strong>{r.identifiers.ticker}</strong> : null} {r.name}
                <span className="text-xs text-[#64748B]">
                  {r.identifiers?.cusip ? ` · CUSIP ${r.identifiers.cusip}` : ""}
                  {r.identifiers?.isin ? ` · ${r.identifiers.isin}` : ""}
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}
      {msg && <p className="mt-1 text-xs text-[#64748B]">{msg}</p>}
    </div>
  );
}
