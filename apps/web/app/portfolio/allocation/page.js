"use client";

import { useEffect, useState, useCallback } from "react";
import AppShell from "@/components/AppShell";
import AllocationSunburst from "@/components/allocation/AllocationSunburst";
import EntityPicker from "@/components/EntityPicker";

const LABEL_INPUT =
  "mt-1 rounded-md border border-[var(--2a-border)] bg-white px-3 py-2 text-sm text-[var(--2a-text)] outline-none focus:ring-2 focus:ring-[var(--2a-navy)]";

const SCOPE_OPTIONS = [
  { value: "entity",  label: "Single entity" },
  { value: "subtree", label: "Entity (look-through)" },
];

function ScopeSelector({ scopeType, setScopeType, entity, setEntity }) {
  return (
    <div
      style={{
        display: "flex",
        flexWrap: "wrap",
        gap: 12,
        alignItems: "flex-end",
        padding: "16px 20px",
        backgroundColor: "var(--2a-bg-card)",
        border: "1px solid #ece8dd",
        borderRadius: 8,
        marginBottom: 24,
      }}
    >
      <div>
        <label
          style={{
            display: "block",
            fontSize: 11,
            fontWeight: 700,
            textTransform: "uppercase",
            letterSpacing: "0.1em",
            color: "var(--2a-text-muted)",
            marginBottom: 4,
          }}
        >
          Scope
        </label>
        <select
          value={scopeType}
          onChange={(e) => setScopeType(e.target.value)}
          className={LABEL_INPUT}
          style={{ minWidth: 200 }}
        >
          {SCOPE_OPTIONS.map((o) => (
            <option key={o.value} value={o.value}>{o.label}</option>
          ))}
        </select>
      </div>
      <div style={{ flex: "1 1 240px" }}>
        <label
          style={{
            display: "block",
            fontSize: 11,
            fontWeight: 700,
            textTransform: "uppercase",
            letterSpacing: "0.1em",
            color: "var(--2a-text-muted)",
            marginBottom: 4,
          }}
        >
          Entity
        </label>
        <EntityPicker
          value={entity}
          onChange={setEntity}
          placeholder="Search entities…"
        />
      </div>
    </div>
  );
}

function SummaryLine({ data }) {
  if (!data) return null;
  const { total_actual_dollar, as_of, entity_count } = data;
  const fmtDollar = (v) => {
    if (v >= 1e9) return `$${(v / 1e9).toFixed(2)}B`;
    if (v >= 1e6) return `$${(v / 1e6).toFixed(2)}M`;
    if (v >= 1e3) return `$${(v / 1e3).toFixed(0)}K`;
    return `$${v.toFixed(0)}`;
  };
  return (
    <p style={{ fontSize: 13, color: "var(--2a-text-muted)", marginBottom: 16, textAlign: "center" }}>
      {fmtDollar(total_actual_dollar)} across {entity_count} {entity_count === 1 ? "entity" : "entities"} · as of {as_of}
    </p>
  );
}

export default function AllocationPage() {
  const [scopeType, setScopeType] = useState("entity");
  const [entity, setEntity] = useState(null);
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [user, setUser] = useState(null);

  useEffect(() => {
    fetch("/api/users/me", { cache: "no-store" })
      .then((r) => r.ok ? r.json() : null)
      .then((d) => d && setUser(d))
      .catch(() => {});
  }, []);

  const load = useCallback(() => {
    if (!entity) { setData(null); return; }
    setLoading(true);
    setError(null);
    const p = new URLSearchParams({ selector_type: scopeType, entity_id: entity.id });
    fetch(`/api/allocation-lens?${p}`, { cache: "no-store" })
      .then((r) => r.ok ? r.json() : Promise.reject(r.status))
      .then((d) => { setData(d); setLoading(false); })
      .catch((e) => { setError(String(e)); setLoading(false); });
  }, [scopeType, entity]);

  useEffect(() => { load(); }, [load]);

  return (
    <AppShell user={user}>
      <div style={{ maxWidth: 760, margin: "0 auto", padding: "32px 16px" }}>
        <div style={{ marginBottom: 24 }}>
          <p
            style={{
              fontSize: 11,
              fontWeight: 700,
              textTransform: "uppercase",
              letterSpacing: "0.22em",
              color: "var(--2a-gold)",
              marginBottom: 6,
            }}
          >
            Portfolio
          </p>
          <h1
            style={{
              fontFamily: "Spectral, Georgia, serif",
              fontWeight: 300,
              fontSize: "clamp(1.5rem, 3vw, 2rem)",
              color: "var(--2a-navy)",
              letterSpacing: "-0.015em",
              margin: 0,
            }}
          >
            Allocation lens
          </h1>
        </div>

        <ScopeSelector
          scopeType={scopeType}
          setScopeType={setScopeType}
          entity={entity}
          setEntity={setEntity}
        />

        {!entity && (
          <div
            style={{
              textAlign: "center",
              padding: "80px 24px",
              color: "var(--2a-text-muted)",
              fontSize: 14,
            }}
          >
            Select an entity to view its allocation breakdown.
          </div>
        )}

        {entity && loading && (
          <div style={{ textAlign: "center", padding: "80px 24px", color: "var(--2a-text-muted)", fontSize: 14 }}>
            Loading…
          </div>
        )}

        {entity && error && (
          <div
            style={{
              padding: "16px 20px",
              backgroundColor: "#FEF2F2",
              border: "1px solid #FECACA",
              borderRadius: 8,
              color: "#9B2335",
              fontSize: 14,
            }}
          >
            Failed to load allocation data ({error}).
          </div>
        )}

        {data && !loading && (
          <>
            <SummaryLine data={data} />
            <div style={{ display: "flex", justifyContent: "center" }}>
              <AllocationSunburst data={data} size={540} />
            </div>
          </>
        )}
      </div>
    </AppShell>
  );
}
