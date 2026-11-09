"use client";

/**
 * TreasuryDaily — what a family sees day to day on Household → Treasury (CASH05), at the top of the EXISTING Treasury
 * tab (no second tab): an entity filter, then Overview, Holdings, Activity (+ the Earnings dialog and the detail drawer)
 * and Documents, then (CASH07) Payments. Each screen is ONE read through its Next.js proxy (/api/cash/households/{id}/overview | holdings |
 * activity | activity/{entry} | earnings | documents) — never FastAPI from the browser. Until the overview read
 * arrives (and on the server render) cash01's coverage card renders from the Treasury envelope (``fallback``).
 * ``initial`` ({overview, holdings, activity, documents, earnings, drawer}) renders without fetching (render tests).
 * Read-only: nothing here writes. Light / cream.
 */

import { useCallback, useEffect, useState } from "react";
import { entityOptions, readQuery } from "@/lib/cashTreasury.mjs";
import TreasuryOverview from "@/components/treasury/TreasuryOverview";
import TreasuryHoldings from "@/components/treasury/TreasuryHoldings";
import TreasuryActivity, { EarningsDialog } from "@/components/treasury/TreasuryActivity";
import TreasuryDocuments from "@/components/treasury/TreasuryDocuments";
import TreasuryPayments from "@/components/treasury/TreasuryPayments";

const input = "rounded border border-[#E2E8F0] px-2 py-1 text-sm";

async function read(path) {
  const res = await fetch(path);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || data.error || `Request failed (${res.status})`);
  return data;
}

export default function TreasuryDaily({ env, fallback = null, initial = null }) {
  const hid = env?.household?.id;
  const [entity, setEntity] = useState("");
  const [range, setRange] = useState(null);
  const [filters, setFilters] = useState({ type: "", status: "" });
  const [period, setPeriod] = useState("mtd");
  const [data, setData] = useState(initial || {});
  const [err, setErr] = useState(null);
  const base = hid ? `/api/cash/households/${encodeURIComponent(hid)}` : null;

  const load = useCallback((key, path) => {
    read(path).then((d) => setData((cur) => ({ ...cur, [key]: d }))).catch((e) => setErr(e.message));
  }, []);

  // each effect only SUBSCRIBES: state is set in the read's callback, and a stale read (the filter moved on) is dropped
  const fetchInto = (pairs) => {
    let alive = true;
    Promise.all(pairs.map(([k, p]) => read(p).then((d) => [k, d])))
      .then((rs) => { if (alive) setData((cur) => ({ ...cur, ...Object.fromEntries(rs) })); })
      .catch((e) => { if (alive) setErr(e.message); });
    return () => { alive = false; };
  };

  useEffect(() => {
    if (!base || initial) return undefined;
    const e = entity || null;
    return fetchInto([["overview", `${base}/overview${readQuery({ entity_id: e, range })}`],
      ["holdings", `${base}/holdings${readQuery({ entity_id: e })}`],
      ["documents", `${base}/documents${readQuery({ entity_id: e })}`]]);
  }, [base, entity, range, initial]);

  useEffect(() => {
    if (!base || initial) return undefined;
    return fetchInto([["activity", `${base}/activity${readQuery({ entity_id: entity || null, type: filters.type || null,
      status: filters.status || null })}`]]);
  }, [base, entity, filters, initial]);

  if (!hid) return fallback;
  const opts = entityOptions(data.overview || data.holdings || data.activity);
  const openEntry = (id) => load("drawer", `${base}/activity/${encodeURIComponent(id)}${readQuery({ entity_id: entity || null })}`);
  const openEarnings = (p = period) => { setPeriod(p); load("earnings", `${base}/earnings${readQuery({ entity_id: entity || null, period: p })}`); };
  return (
    <div className="space-y-4" aria-label="Treasury — day to day">
      {opts.length > 1 ? (
        <label className="flex items-center gap-2 text-sm text-[#334155]">Show
          <select className={input} value={entity} onChange={(e) => { setEntity(e.target.value); setData({}); }} aria-label="Entity">
            <option value="">The whole household</option>
            {opts.map((o) => <option key={o.key} value={o.key}>{o.label}</option>)}</select></label>) : null}
      {data.overview ? <TreasuryOverview data={data.overview} range={range} onRange={setRange} /> : fallback}
      {data.holdings ? <TreasuryHoldings data={data.holdings} /> : null}
      {data.activity ? <TreasuryActivity data={data.activity} filters={filters} onFilters={setFilters} onOpen={openEntry}
        drawer={data.drawer} onCloseDrawer={() => setData((c) => ({ ...c, drawer: null }))} onEarnings={() => openEarnings()} /> : null}
      {data.earnings ? <EarningsDialog data={data.earnings} period={period} onPeriod={openEarnings}
        onClose={() => setData((c) => ({ ...c, earnings: null }))} /> : null}
      {data.documents ? <TreasuryDocuments data={data.documents} /> : null}
      {/* CASH07 — payment instructions between the family's own accounts (same tab; its own reads) */}
      <TreasuryPayments householdId={hid} initial={initial ? (initial.payments || null) : null} />
      {err ? <p className="text-sm text-[#9B2335]" role="alert">{err}</p> : null}
    </div>
  );
}
