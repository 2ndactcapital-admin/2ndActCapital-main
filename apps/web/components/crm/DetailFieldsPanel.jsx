"use client";

// CRMDETAIL1 — typed detail fields for a trust / foundation / LLC / LP / GP / family office. Fields, labels, types
// and choices come from the API (GET /entities/{id}/details); the editable list is the envelope's
// vocabularies.editable (empty for a view-only caller). State of formation is shown here and edited on the
// Details form (Country / State of formation).
import { useEffect, useState } from "react";
import EntityPicker from "@/components/EntityPicker";
import DateInput from "@/components/ui/DateInput";

// PORTENTRY2 — (D17-01) a picked record shows its NAME (kept from the pick), never its id; (D-02 / D19-02) after a
// save the rows are taken from the save's own response when it carries them, then re-read without a cache.
import { canWrite, editableList, fieldInputValue, fieldPayload } from "@/lib/crmDetail.mjs";

const card = "rounded-[6px] border border-[#ece8dd] bg-white p-6";
const input = "mt-1 w-full rounded-[6px] border border-[#E2E8F0] bg-white px-3 py-2 text-sm text-[#0F172A]";
const label = "block text-xs font-medium uppercase tracking-wide text-[#64748B]";
const button = "rounded-[6px] bg-[#1B2B4B] px-4 py-2 text-sm text-white disabled:opacity-50";

async function call(url, options = {}) {
  const res = await fetch(url, { cache: "no-store", headers: { "Content-Type": "application/json" }, ...options });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error(typeof data.detail === "string" ? data.detail : data.error || `Request failed (${res.status})`);
  }
  return data;
}

function FieldInput({ f, value, onChange, names, onPicked }) {
  if (f.type === "choice") {
    return (
      <select value={value} onChange={(e) => onChange(e.target.value)} className={input}>
        <option value="">—</option>
        {f.choices.map((c) => <option key={c.value} value={c.value}>{c.label}</option>)}
      </select>
    );
  }
  if (f.type === "boolean") {
    return (
      <select value={value} onChange={(e) => onChange(e.target.value)} className={input}>
        <option value="">—</option>
        <option value="true">Yes</option>
        <option value="false">No</option>
      </select>
    );
  }
  if (f.type === "entity") {
    return (
      <EntityPicker value={value ? { id: value, display_name: names[value] || f.display || "" } : null}
        onChange={(e) => { if (e) onPicked(e); onChange(e ? e.id : ""); }}
        entityTypes={f.entity_types || []} placeholder="Search records…" />
    );
  }
  if (f.type === "entities") {
    // PORTENTRY1 — several CRM records (an ILIT's insureds): the chosen ones, each removable, plus a picker
    const ids = String(value || "").split(",").filter(Boolean);
    return (
      <div className="space-y-1">
        {ids.map((id) => (
          <div key={id} className="flex items-center justify-between rounded border border-[#E2E8F0] px-2 py-1 text-sm">
            <span>{names[id] || (f.display ? f.display.split(", ")[ids.indexOf(id)] : null) || "Chosen record"}</span>
            <button type="button" className="text-xs text-[#9B2335]"
              onClick={() => onChange(ids.filter((x) => x !== id).join(","))}>Remove</button>
          </div>
        ))}
        <EntityPicker value={null} onChange={(e) => { if (e) { onPicked(e); onChange([...ids.filter((x) => x !== e.id), e.id].join(",")); } }}
          entityTypes={f.entity_types || []} placeholder="Add a record…" />
      </div>
    );
  }
  if (f.type === "date") return <DateInput value={value} onChange={onChange} className={input} ariaLabel={f.label} />;
  const type = "text";
  const mode = f.type === "integer" ? "numeric" : f.type === "decimal" ? "decimal" : undefined;
  return <input type={type} inputMode={mode} value={value} onChange={(e) => onChange(e.target.value)} className={input} />;
}

export default function DetailFieldsPanel({ entityId }) {
  const [env, setEnv] = useState(null);
  const [form, setForm] = useState({});
  const [editing, setEditing] = useState(false);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  const [names, setNames] = useState({});

  async function load() {
    try {
      const data = await call(`/api/entities/${encodeURIComponent(entityId)}/details`);
      setEnv(data);
      setForm(Object.fromEntries((data.rows?.fields || []).map((f) => [f.key, fieldInputValue(f)])));
      setError(null);
    } catch (err) {
      setError(err.message);
    }
  }
  useEffect(() => {
    load();
  }, [entityId]); // eslint-disable-line react-hooks/exhaustive-deps

  const fields = env?.rows?.fields || [];
  const editable = new Set(editableList(env));
  const writable = canWrite(env) && editable.size > 0;

  async function save(e) {
    e.preventDefault();
    const values = fieldPayload(fields.filter((f) => editable.has(f.key)), form);
    if (!Object.keys(values).length) {
      setEditing(false);
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const saved = await call(`/api/entities/${encodeURIComponent(entityId)}/details`, { method: "PUT",
        body: JSON.stringify({ values }) });
      const savedFields = saved?.fields || saved?.rows?.fields;
      if (Array.isArray(savedFields)) setEnv((prev) => ({ ...prev, rows: { ...(prev?.rows || {}), fields: savedFields } }));
      setEditing(false);
      await load();
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  if (env && fields.length === 0) return null;
  return (
    <section className={card}>
      <div className="flex items-center justify-between">
        <h2 className="font-[Spectral,Georgia,serif] text-xl text-[#1B2B4B]">Detail fields</h2>
        {writable && !editing && (
          <button type="button" onClick={() => setEditing(true)} className="text-sm text-[#1B2B4B] hover:underline">
            Edit
          </button>
        )}
      </div>
      {error && <p className="mt-2 text-sm text-[#9B2335]">{error}</p>}
      {!editing ? (
        <dl className="mt-4 grid gap-4 sm:grid-cols-2">
          {fields.map((f) => (
            <div key={f.key}>
              <dt className={label}>{f.label}</dt>
              <dd className="mt-0.5 text-sm text-[#0F172A]">{f.display ?? "—"}</dd>
            </div>
          ))}
        </dl>
      ) : (
        <form onSubmit={save} className="mt-4 grid gap-3 sm:grid-cols-2">
          {fields.map((f) => (
            <div key={f.key}>
              <label className={label}>{f.label}</label>
              {editable.has(f.key) ? (
                <FieldInput f={f} value={form[f.key] ?? ""} onChange={(v) => setForm((cur) => ({ ...cur, [f.key]: v }))}
                  names={names} onPicked={(e) => setNames((n) => ({ ...n, [e.id]: e.display_name }))} />
              ) : (
                <p className="mt-1 text-sm text-[#64748B]">{f.display ?? "—"} {f.help ? `— ${f.help}` : ""}</p>
              )}
              {editable.has(f.key) && f.help && <p className="mt-1 text-xs text-[#64748B]">{f.help}</p>}
            </div>
          ))}
          <div className="flex gap-2 sm:col-span-2">
            <button type="submit" disabled={busy} className={button}>{busy ? "Saving…" : "Save"}</button>
            <button type="button" onClick={() => { setEditing(false); load(); }}
              className="rounded-[6px] border border-[#E2E8F0] px-4 py-2 text-sm text-[#334155]">Cancel</button>
          </div>
        </form>
      )}
    </section>
  );
}
