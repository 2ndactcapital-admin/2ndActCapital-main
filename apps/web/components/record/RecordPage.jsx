"use client";

// NAV3 — ONE Entities record page. Every tab is lazy (mounted the first time it is opened) and reuses the EXISTING
// component / API for its data — nothing here computes a number. Which tabs show: lib/recordTabs.mjs (type + role),
// plus a run-time refusal from a tab's own API (hidden, never an empty tab). One as-of date (URL `as_of`, default
// today) is passed to every tab that supports it; the others say "Shows today".
import { useCallback, useEffect, useMemo, useState } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { canEdit } from "@/lib/portentry2.mjs";
import { activeTab, followsAsOf, isToday, showsTodayNote, tabsFor } from "@/lib/recordTabs.mjs";
import { canAccess } from "@/lib/menuVisibility.mjs";
import { RecordHeader, RecordTabList, ShowsToday, HoldingsSplitView, UdfTabView } from "@/components/record/RecordViews";
import { useTabFetch, TabLoading, TabError, Card } from "@/components/record/useTabFetch";
import EntityDetailsForm from "@/components/crm/EntityDetailsForm";
import AttributesSection from "@/components/crm/AttributesSection";
import OwnershipTree from "@/components/crm/OwnershipTree";
import AddressesTab from "@/components/crm/tabs/AddressesTab";
import EmploymentTab from "@/components/crm/tabs/EmploymentTab";
import TaxIdsTab from "@/components/crm/tabs/TaxIdsTab";
import SocialTab from "@/components/crm/tabs/SocialTab";
import ComplianceTab from "@/components/crm/tabs/ComplianceTab";
import NotesTab from "@/components/crm/tabs/NotesTab";
import DocumentsTab from "@/components/crm/tabs/DocumentsTab";
import OwnershipTab from "@/components/crm/tabs/OwnershipTab";
import DocumentsPanel from "@/components/DocumentsPanel";
import RelationshipsPanel from "@/components/crm/RelationshipsPanel";
import DetailFieldsPanel from "@/components/crm/DetailFieldsPanel";
import LiabilitiesTab from "@/components/crm/LiabilitiesTab";
import HoldingsTab from "@/components/crm/HoldingsTab";
import AllocationSunburst from "@/components/allocation/AllocationSunburst";
import XrayView from "@/components/xray/XrayView";
import TreasuryPanel from "@/components/treasury/TreasuryPanel";
import AccountsList from "@/components/proposals/AccountsList";
import TeamPanel from "@/components/visibility/TeamPanel";

const enc = encodeURIComponent;

export default function RecordPage({ full, graph, me, household, vocab, asOf, today, team, people, teams }) {
  const entity = full.entity;
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const [refused, setRefused] = useState([]);
  const refuse = useCallback((key) => setRefused((r) => (r.includes(key) ? r : [...r, key])), []);

  const visible = useMemo(() => tabsFor({ me, entityType: entity.entity_type, hasHousehold: Boolean(household), refused }),
    [me, entity.entity_type, household, refused]);
  const [active, setActiveState] = useState(() => activeTab(searchParams.get("tab"), visible));
  const current = visible.some((t) => t.key === active) ? active : (visible[0] && visible[0].key);
  const [mounted, setMounted] = useState(() => new Set([current]));

  const setParam = useCallback((key, value) => {
    const p = new URLSearchParams(searchParams.toString());
    if (value) p.set(key, value); else p.delete(key);
    const qs = p.toString();
    router.replace(`${pathname}${qs ? `?${qs}` : ""}`, { scroll: false });
  }, [router, pathname, searchParams]);

  function select(key, focus) {
    setActiveState(key);
    setMounted((m) => (m.has(key) ? m : new Set([...m, key])));
    setParam("tab", key === "overview" ? null : key);
    if (focus && typeof document !== "undefined") {
      const el = document.getElementById(`record-tab-${key}`);
      if (el) el.focus();
    }
  }
  const onAsOf = (v) => setParam("as_of", v && v !== today ? v : null);

  // PORTENTRY2 — the record's write envelope; until it arrives (or if lost) every write control stays hidden.
  const [perms, setPerms] = useState(null);
  useEffect(() => {
    let live = true;
    fetch(`/api/entities/${enc(entity.id)}/permissions`, { cache: "no-store" })
      .then((r) => (r.ok ? r.json() : null)).then((d) => { if (live) setPerms(d); })
      .catch(() => { if (live) setPerms(null); });
    return () => { live = false; };
  }, [entity.id]);

  const todayTabs = visible.filter((t) => !followsAsOf(t.key)).map((t) => t.label);
  const ctx = { entity, full, graph, me, household, asOf, today, perms, refuse };

  return (
    <div>
      <RecordHeader entity={entity} vocab={vocab} household={household} owners={full.owners} asOf={asOf} today={today}
        onAsOf={onAsOf} todayTabs={todayTabs} />
      {team ? <div className="mt-4"><TeamPanel kind="entity" id={entity.id} initialEnvelope={team} people={people || []} teams={teams || []} /></div> : null}
      <div className="mt-6">
        <RecordTabList tabs={visible} active={current} onSelect={select} />
        {visible.map((t) => (
          <div key={t.key} role="tabpanel" id={`record-panel-${t.key}`} aria-labelledby={`record-tab-${t.key}`}
            hidden={t.key !== current} tabIndex={0} className="mt-6 focus:outline-none">
            {mounted.has(t.key) || t.key === current ? (
              <>
                {showsTodayNote(t.key, asOf, today) ? <ShowsToday /> : null}
                <TabBody tabKey={t.key} ctx={ctx} />
              </>
            ) : null}
          </div>
        ))}
      </div>
    </div>
  );
}

function TabBody({ tabKey, ctx }) {
  switch (tabKey) {
    case "overview": return <OverviewTab {...ctx} />;
    case "holdings": return <HoldingsPanel {...ctx} key={ctx.asOf} />;
    case "transactions": return <TransactionsPanel {...ctx} key={ctx.asOf} />;
    case "allocation": return <AllocationPanel {...ctx} key={ctx.asOf} />;
    case "cash": return <CashPanel {...ctx} />;
    case "models": return <ModelsPanel {...ctx} />;
    case "ownership": return <OwnershipPanel {...ctx} key={ctx.asOf} />;
    case "details": return <DetailsPanel {...ctx} />;
    case "documents": return <DocumentsPanelTab {...ctx} />;
    case "activity": return <ActivityPanel {...ctx} />;
    default: return null;
  }
}

// ── Overview: the record's summary (the existing pieces of the old Overview) ─────────────────────────────────────
function OverviewTab({ entity, full, graph }) {
  const hasHoldings = (full.holdings || []).length > 0;
  return (
    <div className="grid gap-8 lg:grid-cols-2">
      <Card title="Record">
        <EntityDetailsForm entity={entity} canEdit={false} />
        <a href={`/investment-profile?entity=${enc(entity.id)}&tab=brief`} className="mt-4 inline-block text-sm font-medium text-navy hover:underline">
          View Client Brief →
        </a>
      </Card>
      <Card title="Ownership">
        <div className="space-y-6">
          <OwnershipTree graph={graph} rootId={entity.id} direction="up" title="Owned by" />
          {hasHoldings && <OwnershipTree graph={graph} rootId={entity.id} direction="down" title="Owns" />}
        </div>
      </Card>
    </div>
  );
}

// ── Holdings: held directly / through ownership (GET /entities/{id}/holdings-split) + the existing entry tabs ────
function HoldingsPanel({ entity, asOf, today, refuse }) {
  const url = `/api/entities/${enc(entity.id)}/holdings-split${isToday(asOf, today) ? "" : `?as_of=${enc(asOf)}`}`;
  const { loading, data, error } = useTabFetch(url, () => refuse("holdings"));
  const [part, setPart] = useState("direct");
  if (loading) return <TabLoading />;
  if (error === "refused") return null;
  return (
    <div className="space-y-8">
      {error ? <TabError error={error} /> : <HoldingsSplitView envelope={data} part={part} onPart={setPart} />}
      <div>
        {!isToday(asOf, today) ? <ShowsToday /> : null}
        <HoldingsTab entityId={entity.id} />
      </div>
      <div><LiabilitiesTab entityId={entity.id} /></div>
    </div>
  );
}

// ── Transactions: the Transactions grid's API, owner = this entity, trade date ≤ as-of ────────────────────────────
function TransactionsPanel({ entity, asOf, refuse }) {
  const p = new URLSearchParams({ owner_entity_id: entity.id, trade_to: asOf, limit: "200" });
  const { loading, data, error } = useTabFetch(`/api/portfolio/transactions?${p}`, () => refuse("transactions"));
  if (loading) return <TabLoading />;
  if (error === "refused") return null;
  if (error) return <TabError error={error} />;
  const rows = data.transactions || [];
  const types = new Map((data.transaction_types || []).map((t) => [t.code, t.label]));
  return (
    <Card title={`Transactions to ${asOf}`} note={`${rows.length} of ${data.total ?? rows.length}`}>
      {!rows.length ? <p className="text-sm text-text-muted">No transactions.</p> : (
        <table className="w-full text-sm">
          <thead><tr className="text-left text-text-muted"><th className="py-1">Trade date</th><th>Type</th><th>Security</th><th className="text-right">Amount</th></tr></thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.id} className="border-t border-border" data-transaction={r.id}>
                <td className="py-1">{r.trade_date}</td>
                <td>{types.get(r.transaction_type_code) || r.transaction_type_code}</td>
                <td>{r.asset_name || ""}</td>
                <td className="text-right">{r.net_amount ?? ""}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <a href={`/portfolio/transactions?entity_id=${enc(entity.id)}`} className="mt-4 inline-block text-sm text-navy hover:underline">
        Open in Transactions →
      </a>
    </Card>
  );
}

// ── Allocation & X-ray: the allocation lens (as-of) + the primary household's X-ray (today) ───────────────────────
function AllocationPanel({ entity, asOf, today, me, household, refuse }) {
  const p = new URLSearchParams({ selector_type: "entity", entity_id: entity.id });
  if (!isToday(asOf, today)) p.set("as_of", asOf);
  const lens = useTabFetch(`/api/allocation-lens?${p}`, () => refuse("allocation"));
  const xrayOk = Boolean(household) && canAccess(me, { perm: "view_replication", strict: true });
  const xray = useTabFetch(xrayOk ? `/api/xray/households/${enc(household.id)}` : null, null);
  if (lens.loading) return <TabLoading />;
  if (lens.error === "refused") return null;
  return (
    <div className="space-y-8">
      <Card title="Allocation" note={lens.data ? `as of ${lens.data.as_of}` : null}>
        {lens.error ? <TabError error={lens.error} /> : (
          <div className="flex justify-center"><AllocationSunburst data={lens.data} size={480} /></div>
        )}
      </Card>
      {xrayOk ? (
        <Card title={`X-ray — ${household.name}`} note="Shows today">
          {xray.loading ? <TabLoading /> : xray.error ? <TabError error={xray.error === "refused" ? "not available to you" : xray.error} />
            : <XrayView initialEnvelope={xray.data} />}
        </Card>
      ) : null}
    </div>
  );
}

// ── Cash & Treasury: the primary household's treasury (GET /cash/households/{id}/treasury) ────────────────────────
function CashPanel({ household, refuse }) {
  const { loading, data, error } = useTabFetch(`/api/cash/households/${enc(household.id)}/treasury`, () => refuse("cash"));
  if (loading) return <TabLoading />;
  if (error === "refused") return null;
  if (error) return <TabError error={error} />;
  return (
    <div>
      <p className="mb-4 text-sm text-text-muted">Household: {household.name}</p>
      <TreasuryPanel initialEnvelope={data} />
    </div>
  );
}

// ── Models & rebalancing: the accounts list, narrowed to the accounts this entity is the primary owner of ─────────
function ModelsPanel({ entity, refuse }) {
  const { loading, data, error } = useTabFetch(`/api/proposals/accounts?owner_entity_id=${enc(entity.id)}`, () => refuse("models"));
  if (loading) return <TabLoading />;
  if (error === "refused") return null;
  if (error) return <TabError error={error} />;
  if (!(data.rows || []).length) return <Card><p className="text-sm text-text-muted">No open accounts owned by this record.</p></Card>;
  return <AccountsList initialEnvelope={data} />;
}

function OwnershipPanel({ entity, graph, asOf, today }) {
  return (
    <div className="space-y-8">
      <OwnershipTab entityId={entity.id} graph={graph} asOf={isToday(asOf, today) ? "" : asOf} />
      <div>
        {!isToday(asOf, today) ? <ShowsToday /> : null}
        <RelationshipsPanel entityId={entity.id} entityType={entity.entity_type} />
      </div>
      <a href={`/crm/${enc(entity.id)}/hierarchy`} className="text-sm text-gold hover:underline">View ownership structure →</a>
    </div>
  );
}

// ── Details: the typed CRM fields + the UDF custom tabs ───────────────────────────────────────────────────────────
function DetailsPanel({ entity, full, perms, me }) {
  const isIndividual = entity.entity_type === "individual";
  const udfOk = canAccess(me, { perm: "view_portfolio", strict: true });
  return (
    <div className="space-y-8">
      <Card title="Record details"><EntityDetailsForm entity={entity} canEdit={canEdit(perms, "record")} /></Card>
      {!isIndividual ? <div className="max-w-3xl"><DetailFieldsPanel entityId={entity.id} /></div> : null}
      <div className="max-w-3xl"><AttributesSection entityId={entity.id} attributes={full.attributes || []} /></div>
      <Card title="Addresses">
        <AddressesTab entityId={entity.id} initial={full.addresses || []} canAdd={canEdit(perms, "addresses")} canEdit={canEdit(perms, "addresses")} />
      </Card>
      {isIndividual ? <Card title="Employment"><EmploymentTab entityId={entity.id} initial={full.employment || []} canAdd={canEdit(perms, "employment")} /></Card> : null}
      <Card title="Tax IDs"><TaxIdsTab entityId={entity.id} initial={full.tax_ids || []} canAdd={canEdit(perms, "tax_ids")} /></Card>
      <Card title="Social profiles"><SocialTab entityId={entity.id} initial={full.social_profiles || []} canAdd={canEdit(perms, "social")} /></Card>
      <Card title="Compliance"><ComplianceTab entityId={entity.id} initial={full.compliance_record} /></Card>
      {udfOk ? <UdfTabs entityId={entity.id} /> : null}
    </div>
  );
}

function coerce(dataType, raw) {
  if (raw === "") return null;
  if (["number", "integer", "decimal", "currency", "percent"].includes(dataType)) {
    const n = Number(raw);
    return Number.isFinite(n) ? n : raw;
  }
  if (dataType === "boolean" || dataType === "checkbox") return raw === "true" || raw === true;
  return raw;
}

function UdfTabs({ entityId }) {
  const tabs = useTabFetch("/api/udf/tabs?applies_to=entity", null);
  const [values, setValues] = useState(null);
  const loadValues = useCallback(() => {
    fetch(`/api/udf/values/entity/${enc(entityId)}`, { cache: "no-store" })
      .then((r) => (r.ok ? r.json() : null)).then(setValues).catch(() => setValues(null));
  }, [entityId]);
  useEffect(() => { loadValues(); }, [loadValues]);
  if (tabs.loading || tabs.error || !(tabs.data && tabs.data.rows && tabs.data.rows.length)) return null;
  return (
    <div className="space-y-8" data-udf-tabs="">
      {tabs.data.rows.map((t) => <UdfOneTab key={t.id} tab={t} entityId={entityId} values={values} reload={loadValues} />)}
    </div>
  );
}

function UdfOneTab({ tab, entityId, values, reload }) {
  const layout = useTabFetch(`/api/udf/layouts/${enc(tab.id)}`, null);
  const [drafts, setDrafts] = useState({});
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState("");
  if (layout.loading || layout.error) return null;   // a hidden tab is a 403 → nothing
  const items = new Map((layout.data.sections || []).flatMap((s) => s.items || []).map((i) => [i.definition_id, i]));
  async function save(defId) {
    setSaving(true); setMessage("");
    try {
      const r = await fetch(`/api/udf/values/entity/${enc(entityId)}`, {
        method: "PUT", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ definition_id: defId, value: coerce(items.get(defId)?.data_type, drafts[defId] ?? "") }),
      });
      const d = await r.json().catch(() => ({}));
      if (!r.ok) setMessage(d.error || `Not saved (${r.status})`);
      else { setMessage("Saved."); setDrafts((x) => { const y = { ...x }; delete y[defId]; return y; }); reload(); }
    } finally { setSaving(false); }
  }
  return <UdfTabView tab={tab} layout={layout.data} values={values} drafts={drafts}
    onDraft={(k, v) => setDrafts((x) => ({ ...x, [k]: v }))} onSave={save} saving={saving} message={message} />;
}

function DocumentsPanelTab({ entity }) {
  return (
    <div className="space-y-8">
      <DocumentsTab entityId={entity.id} />
      <DocumentsPanel recordType="entity" recordId={entity.id} title="Linked Documents" />
    </div>
  );
}

function ActivityPanel({ entity, full, perms }) {
  return (
    <div className="space-y-8">
      <NotesTab entityId={entity.id} initial={full.notes || []} canAdd={canEdit(perms, "notes")} />
    </div>
  );
}

