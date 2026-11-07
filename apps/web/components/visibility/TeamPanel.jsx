"use client";

/**
 * TeamPanel — VISIBILITY1: who covers this family (assigned users and teams with a role label), its owner, and —
 * for a caller the server lets assign — add / remove. Every control renders only from the server's envelope
 * (lib/visibility1.mjs, fails closed). Calls go through the Next.js API routes (never FastAPI from the browser).
 */

import { useState } from "react";
import { assigneeName, ownerLine, roleLabels, teamControls } from "@/lib/visibility1.mjs";

const card = "rounded-[6px] border border-[#ece8dd] bg-white p-4 text-sm";
const input = "rounded border border-[#E2E8F0] bg-white px-2 py-1 text-sm";
const primary = "rounded bg-[#1B2B4B] px-3 py-1 text-white disabled:opacity-50";

export function TeamView({ envelope, people = [], teams = [], onAdd, onRemove, busy, error }) {
  const [target, setTarget] = useState("");
  const [label, setLabel] = useState("");
  if (!envelope || !envelope.family) return null;
  const c = teamControls(envelope);
  const rows = Array.isArray(envelope.assignments) ? envelope.assignments : [];
  const labels = roleLabels(envelope);
  return (
    <section className={card}>
      <div className="flex items-baseline justify-between">
        <h2 className="text-base font-semibold text-[#1B2B4B]">Team</h2>
        <span className="text-xs text-[#64748B]">{envelope.family.name}</span>
      </div>
      <p className="mt-1 text-xs text-[#64748B]">{ownerLine(envelope.owner)}</p>
      {envelope.shared_record ? (
        <p className="mt-1 text-xs text-[#64748B]">This record is shared by several families; an assignment here covers this record and what it owns only.</p>
      ) : null}
      {rows.length ? (
        <table className="mt-3 w-full">
          <thead className="text-left text-[#64748B]"><tr><th className="py-1">Covered by</th><th>Role</th><th>Recorded on</th>{c.remove ? <th /> : null}</tr></thead>
          <tbody>{rows.map((a) => (
            <tr key={a.id} className="border-t border-[#ece8dd]">
              <td className="py-1">{assigneeName(a)}</td>
              <td>{a.role_label || "—"}</td>
              <td>{a.entity_name || "—"}</td>
              {c.remove ? (
                <td className="text-right">
                  <button type="button" className="text-[#9B2335] underline disabled:opacity-50" disabled={busy}
                    onClick={() => onRemove && onRemove(a.id)}>Remove</button>
                </td>
              ) : null}
            </tr>
          ))}</tbody>
        </table>
      ) : (
        <p className="mt-3 text-[#64748B]">No one is assigned. Only org admins and the owner see this family once visibility is enforced.</p>
      )}
      {c.add ? (
        <div className="mt-3 flex flex-wrap items-center gap-2">
          <select aria-label="Person or team" className={input} value={target} onChange={(e) => setTarget(e.target.value)}>
            <option value="">Person or team…</option>
            {people.map((p) => <option key={`u:${p.user_id}`} value={`u:${p.user_id}`}>{p.full_name || p.email}</option>)}
            {teams.map((t) => <option key={`t:${t.id}`} value={`t:${t.id}`}>Team: {t.name}</option>)}
          </select>
          <select aria-label="Role" className={input} value={label} onChange={(e) => setLabel(e.target.value)}>
            <option value="">Role…</option>
            {labels.map((l) => <option key={l} value={l}>{l}</option>)}
          </select>
          <button type="button" className={primary} disabled={busy || !target}
            onClick={() => onAdd && onAdd(target, label)}>Assign</button>
        </div>
      ) : null}
      {error ? <p className="mt-2 text-xs text-[#9B2335]">{error}</p> : null}
    </section>
  );
}

export default function TeamPanel({ kind, id, initialEnvelope, people = [], teams = [] }) {
  const [env, setEnv] = useState(initialEnvelope || null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const base = `/api/staff-visibility/${kind === "household" ? "households" : "entities"}/${encodeURIComponent(id)}/team`;

  async function reload() {
    const res = await fetch(base, { cache: "no-store" });
    setEnv(res.ok ? await res.json() : null);
  }
  async function call(url, init) {
    setBusy(true);
    setError(null);
    try {
      const res = await fetch(url, init);
      if (!res.ok) {
        const d = await res.json().catch(() => ({}));
        setError(typeof d.detail === "string" ? d.detail : d.error || `Request failed (${res.status})`);
      }
      await reload();
    } finally {
      setBusy(false);
    }
  }
  function onAdd(target, label) {
    const [t, tid] = target.split(":");
    const body = { role_label: label || null, ...(t === "u" ? { assigned_to_user_id: tid } : { assigned_to_team_id: tid }) };
    return call(base, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body) });
  }
  function onRemove(assignmentId) {
    return call(`/api/staff-visibility/assignments/${encodeURIComponent(assignmentId)}`, { method: "DELETE" });
  }
  return <TeamView envelope={env} people={people} teams={teams} onAdd={onAdd} onRemove={onRemove} busy={busy} error={error} />;
}
