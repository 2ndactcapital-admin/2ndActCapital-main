"use client";

/**
 * TaxWorkspace — Portfolio → Tax (REP6): Harvest dashboard | Substitutions |
 * Profiles | Declared outside trades. The first view's envelope comes from the
 * server; the others are fetched through the Next.js API routes when opened.
 * Every control lives in the sub-views, decided from their own envelopes.
 */

import { useState } from "react";
import HarvestDashboard from "@/components/tax/HarvestDashboard";
import Substitutions from "@/components/tax/Substitutions";
import Profiles from "@/components/tax/Profiles";
import DeclaredTrades from "@/components/tax/DeclaredTrades";
import { get } from "@/components/tax/common";

const VIEWS = [
  { key: "harvest", label: "Harvest", path: "tax/overview" },
  { key: "substitutions", label: "Substitutions", path: "tax/substitutions" },
  { key: "profiles", label: "Profiles", path: "tax/profiles" },
  { key: "declared", label: "Declared outside trades", path: "tax/declared-trades" },
];

export default function TaxWorkspace({ initialEnvelope, initialView = "harvest" }) {
  const [view, setView] = useState(VIEWS.some((x) => x.key === initialView) ? initialView : "harvest");
  const [envs, setEnvs] = useState({ harvest: initialEnvelope });
  const [subjects, setSubjects] = useState(null);
  const [err, setErr] = useState(null);

  async function load(key) {
    setErr(null);
    try {
      const v = VIEWS.find((x) => x.key === key);
      const env = await get(v.path);
      setEnvs((e) => ({ ...e, [key]: env }));
      if (subjects === null && key !== "harvest") {
        const s = await get("tax/subjects");
        setSubjects(Array.isArray(s.rows) ? s.rows : []);
      }
    } catch (e) { setErr(e.message); }
  }
  function open(key) {
    setView(key);
    if (!envs[key]) load(key);
  }
  const env = envs[view];
  return (
    <div>
      <div className="flex gap-4 border-b border-[#ece8dd] text-sm">
        {VIEWS.map((x) => (
          <button key={x.key} type="button" onClick={() => open(x.key)}
            className={`pb-2 ${view === x.key ? "border-b-2 border-[#C5A880] font-medium text-navy" : "text-text-muted"}`}>{x.label}</button>
        ))}
      </div>
      <div className="mt-5">
        {err ? <p className="text-sm text-[#9B2335]">{err}</p> : null}
        {!env ? <p className="text-sm text-text-muted">Loading…</p> : null}
        {env && view === "harvest" ? <HarvestDashboard envelope={env} onReload={() => load("harvest")} /> : null}
        {env && view === "substitutions" ? <Substitutions envelope={env} onReload={() => load("substitutions")} /> : null}
        {env && view === "profiles" ? <Profiles envelope={env} subjects={subjects || []} onReload={() => load("profiles")} /> : null}
        {env && view === "declared" ? <DeclaredTrades envelope={env} subjects={subjects || []} onReload={() => load("declared")} /> : null}
      </div>
    </div>
  );
}
