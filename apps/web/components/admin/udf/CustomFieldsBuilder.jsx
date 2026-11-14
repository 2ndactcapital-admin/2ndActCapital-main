"use client";

// UDFUI1 — Firm settings → Custom fields & tabs. Six sections over the existing UDF engine (lib/udfBuilder.mjs maps
// every control to its route). The page passes the server's envelopes; nothing here defaults a permission — a missing
// envelope renders the read-only notice and no write control.
import { useState } from "react";
import FieldsPanel from "./FieldsPanel";
import TabsPanel from "./TabsPanel";
import PermissionsPanel from "./PermissionsPanel";
import { AuditPanel, RecordsView, TagsPanel } from "./TagsAuditRecords";
import { udfCall } from "./udfParts";

export const SECTIONS = [
  { key: "fields", label: "Fields" },
  { key: "tabs", label: "Tabs & layouts" },
  { key: "permissions", label: "Permissions & preview" },
  { key: "tags", label: "Tags" },
  { key: "audit", label: "Change history" },
  { key: "records", label: "Records" },
];

export default function CustomFieldsBuilder({ initialDefinitions, initialTabs, profiles, permissionSets, users, initialSection = "fields" }) {
  const [defs, setDefs] = useState(initialDefinitions);
  const [tabs, setTabs] = useState(initialTabs);
  const [section, setSection] = useState(initialSection);
  const [error, setError] = useState(null);

  async function reload() {
    try {
      const [d, t] = await Promise.all([udfCall("listDefinitions"), udfCall("listTabs")]);
      setDefs(d); setTabs(t); setError(null);
    } catch (e) {
      setError(e.message);
    }
  }

  const onKey = (e, i) => {
    let next = null;
    if (e.key === "ArrowRight") next = (i + 1) % SECTIONS.length;
    else if (e.key === "ArrowLeft") next = (i - 1 + SECTIONS.length) % SECTIONS.length;
    else if (e.key === "Home") next = 0;
    else if (e.key === "End") next = SECTIONS.length - 1;
    if (next !== null) { e.preventDefault(); setSection(SECTIONS[next].key); document.getElementById(`udf-tab-${SECTIONS[next].key}`)?.focus(); }
  };

  return (
    <div className="mt-6">
      <div role="tablist" aria-label="Custom fields & tabs" className="flex flex-wrap gap-1 border-b border-border">
        {SECTIONS.map((s, i) => (
          <button key={s.key} id={`udf-tab-${s.key}`} type="button" role="tab" aria-selected={section === s.key}
            aria-controls={`udf-panel-${s.key}`} tabIndex={section === s.key ? 0 : -1} onKeyDown={(e) => onKey(e, i)}
            onClick={() => setSection(s.key)}
            className={`px-3 py-2 text-sm ${section === s.key ? "border-b-2 border-[#C5A880] font-semibold text-navy" : "text-text-muted hover:text-navy"}`}>
            {s.label}
          </button>
        ))}
      </div>
      {error ? <p role="alert" className="mt-2 text-sm text-[#9B2335]">{error}</p> : null}
      <div role="tabpanel" id={`udf-panel-${section}`} aria-labelledby={`udf-tab-${section}`}>
        {section === "fields" ? <FieldsPanel envelope={defs} onChanged={reload} /> : null}
        {section === "tabs" ? <TabsPanel envelope={tabs} definitions={defs?.rows || []} onChanged={reload} /> : null}
        {section === "permissions" ? (
          <PermissionsPanel defsEnvelope={defs} tabsEnvelope={tabs} profiles={profiles} permissionSets={permissionSets} users={users} />
        ) : null}
        {section === "tags" ? <TagsPanel envelope={defs} /> : null}
        {section === "audit" ? <AuditPanel envelope={defs} /> : null}
        {section === "records" ? <RecordsView envelope={defs} /> : null}
      </div>
    </div>
  );
}
