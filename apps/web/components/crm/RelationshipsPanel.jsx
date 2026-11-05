"use client";

// CRMDETAIL1 — the record's Relationships panel: governed types (from the API envelope), grouped, both
// directions with the reverse label; add (type, other record, note, effective date); end (valid_to — kept in
// history, never deleted). Write controls render only when permissions.can_write === true (no fallback).
import { useEffect, useState } from "react";
import EntityPicker from "@/components/EntityPicker";
import { canWrite, groupRows, parseOption, relationshipOptions, typeLabelFrom } from "@/lib/crmDetail.mjs";

const card = "rounded-[6px] border border-[#ece8dd] bg-white p-6";
const input = "rounded-[6px] border border-[#E2E8F0] bg-white px-3 py-2 text-sm text-[#0F172A]";
const button = "rounded-[6px] bg-[#1B2B4B] px-4 py-2 text-sm text-white disabled:opacity-50";
const quiet = "text-sm text-[#64748B]";

async function call(url, options = {}) {
  const res = await fetch(url, { cache: "no-store", headers: { "Content-Type": "application/json" }, ...options });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error(typeof data.detail === "string" ? data.detail : data.error || `Request failed (${res.status})`);
  }
  return data;
}

function Row({ r, vocab, writable, onEnded }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  async function end() {
    if (!window.confirm(`End "${r.label} · ${r.other_name}"? It stays in the history.`)) return;
    setBusy(true);
    setError(null);
    try {
      await call(`/api/crm-relationships/${encodeURIComponent(r.id)}/end`, { method: "POST", body: "{}" });
      onEnded();
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <li className="flex flex-wrap items-baseline justify-between gap-2 border-b border-[#E2E8F0] py-2 last:border-0">
      <span className="text-sm text-[#0F172A]">
        <span className="text-[#334155]">{r.label}</span>{" · "}
        <a href={`/crm/${r.other_entity_id}?tab=relationships`} className="font-medium text-[#1B2B4B] hover:underline">
          {r.other_name}
        </a>
        <span className={quiet}> ({typeLabelFrom(vocab, r.other_type)})</span>
        {r.effective_date && <span className={quiet}> · since {r.effective_date}</span>}
        {r.ended && <span className={quiet}> · ended {String(r.valid_to).slice(0, 10)}</span>}
        {r.notes && <span className="block text-xs text-[#64748B]">{r.notes}</span>}
      </span>
      {writable && !r.ended && (
        <button type="button" disabled={busy} onClick={end} className="text-xs text-[#9B2335] hover:underline">
          End
        </button>
      )}
      {error && <span className="w-full text-xs text-[#9B2335]">{error}</span>}
    </li>
  );
}

function AddRelationship({ entityId, entityType, vocab, onAdded }) {
  const options = relationshipOptions(vocab, entityType);
  const [choice, setChoice] = useState("");
  const [other, setOther] = useState(null);
  const [notes, setNotes] = useState("");
  const [effective, setEffective] = useState("");
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  const picked = options.find((o) => o.value === choice);

  async function submit(e) {
    e.preventDefault();
    const opt = parseOption(choice);
    if (!opt || !other?.id) {
      setError("Choose the relationship and the other record.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await call(`/api/entities/${encodeURIComponent(entityId)}/crm-relationships`, {
        method: "POST",
        body: JSON.stringify({ relationship_type: opt.key, direction: opt.direction, other_entity_id: other.id,
          notes: notes || null, effective_date: effective || null }),
      });
      setChoice("");
      setOther(null);
      setNotes("");
      setEffective("");
      onAdded();
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  const groups = Array.isArray(vocab?.relationship_groups) ? vocab.relationship_groups : [];
  return (
    <form onSubmit={submit} className="mt-4 grid gap-3 sm:grid-cols-2">
      <select value={choice} onChange={(e) => setChoice(e.target.value)} className={input} aria-label="Relationship">
        <option value="">This record is…</option>
        {groups.map((g) => {
          const opts = options.filter((o) => o.group === g.key);
          if (!opts.length) return null;
          return (
            <optgroup key={g.key} label={g.label}>
              {opts.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
            </optgroup>
          );
        })}
      </select>
      <EntityPicker value={other} onChange={setOther} excludeId={entityId}
        entityTypes={Array.isArray(picked?.otherTypes) ? picked.otherTypes : []}
        placeholder="The other record…" />
      <input value={notes} onChange={(e) => setNotes(e.target.value)} placeholder="Note" className={input} />
      <label className={quiet}>
        Effective date{" "}
        <input type="date" value={effective} onChange={(e) => setEffective(e.target.value)} className={input} />
      </label>
      {error && <p className="text-sm text-[#9B2335] sm:col-span-2">{error}</p>}
      <div className="sm:col-span-2">
        <button type="submit" disabled={busy} className={button}>{busy ? "Saving…" : "Add relationship"}</button>
      </div>
    </form>
  );
}

export default function RelationshipsPanel({ entityId, entityType }) {
  const [env, setEnv] = useState(null);
  const [error, setError] = useState(null);
  const [showHistory, setShowHistory] = useState(false);

  async function load() {
    try {
      setEnv(await call(`/api/entities/${encodeURIComponent(entityId)}/crm-relationships`));
      setError(null);
    } catch (err) {
      setError(err.message);
    }
  }
  useEffect(() => {
    load();
  }, [entityId]); // eslint-disable-line react-hooks/exhaustive-deps

  const vocab = env?.vocabularies;
  const writable = canWrite(env);
  const current = groupRows(env?.rows?.current, vocab);
  const history = env?.rows?.history || [];
  return (
    <section className={card}>
      <h2 className="font-[Spectral,Georgia,serif] text-xl text-[#1B2B4B]">Relationships</h2>
      {error && <p className="mt-2 text-sm text-[#9B2335]">{error}</p>}
      {env && current.length === 0 && <p className={`mt-3 ${quiet}`}>No relationships recorded.</p>}
      {current.map((g) => (
        <div key={g.key} className="mt-4">
          <h3 className="text-xs font-medium uppercase tracking-wide text-[#64748B]">{g.label}</h3>
          <ul>{g.rows.map((r) => <Row key={r.id} r={r} vocab={vocab} writable={writable} onEnded={load} />)}</ul>
        </div>
      ))}
      {history.length > 0 && (
        <div className="mt-4">
          <button type="button" className="text-sm text-[#1B2B4B] hover:underline"
            onClick={() => setShowHistory(!showHistory)}>
            {showHistory ? "Hide" : "Show"} ended relationships ({history.length})
          </button>
          {showHistory && <ul>{history.map((r) => <Row key={r.id} r={r} vocab={vocab} writable={false} />)}</ul>}
        </div>
      )}
      {writable && <AddRelationship entityId={entityId} entityType={entityType} vocab={vocab} onAdded={load} />}
    </section>
  );
}
