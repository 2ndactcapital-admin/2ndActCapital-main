"use client";

/**
 * VISIBILITY1 — Admin → Staff visibility: the preview ("what will each person see"), the org switch with the
 * preview on its confirm step, Unassigned families with bulk assign, and People & managers. Controls render only
 * from the server's envelopes (lib/visibility1.mjs — fails closed). Writes go through Next.js API routes.
 */

import { useState } from "react";
import {
  canWrite, confirmSummary, previewRows, reasonText, seesNone, switchControl, unassignedRows,
} from "@/lib/visibility1.mjs";

const card = "rounded-[6px] border border-[#ece8dd] bg-white p-5 text-sm";
const input = "rounded border border-[#E2E8F0] bg-white px-2 py-1 text-sm";
const primary = "rounded bg-[#1B2B4B] px-3 py-1 text-white disabled:opacity-50";
const secondary = "rounded border border-[#1B2B4B] px-3 py-1 text-[#1B2B4B] disabled:opacity-50";

async function send(url, method, body) {
  const res = await fetch(url, { method, headers: { "content-type": "application/json" }, body: JSON.stringify(body) });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(typeof data.detail === "string" ? data.detail : data.error || `Request failed (${res.status})`);
  return data;
}

export function PreviewView({ preview, onSwitch, busy, error }) {
  const [open, setOpen] = useState(null);
  const [confirming, setConfirming] = useState(false);
  if (!preview) return null;
  const rows = previewRows(preview);
  const none = seesNone(preview);
  const v = preview.vocabularies || {};
  const turningOn = !preview.enforced;
  return (
    <section className={card}>
      <div className="flex flex-wrap items-baseline justify-between gap-3">
        <div>
          <h2 className="text-base font-semibold text-[#1B2B4B]">What each person will see</h2>
          <p className="mt-1 text-[#64748B]">
            Enforcement is <strong className="text-[#0F172A]">{preview.enforced ? "on" : "off"}</strong>.
            {preview.enforced ? " Staff see only their families." : " Every internal user currently sees every family."}
            {" "}{preview.families_total} families, {preview.unassigned_count} with no one assigned.
          </p>
        </div>
        {switchControl(preview) && !confirming ? (
          <button type="button" className={primary} disabled={busy} onClick={() => setConfirming(true)}>
            {preview.enforced ? "Turn enforcement off…" : "Turn enforcement on…"}
          </button>
        ) : null}
      </div>
      {none.length ? (
        <div className="mt-3 rounded-[6px] border border-[#ece8dd] bg-[#FAF9F6] p-3 text-[#9B2335]">
          Would see no family: {none.map((u) => u.name || u.email).join(", ")}
        </div>
      ) : null}
      {confirming && switchControl(preview) ? (
        <div className="mt-3 rounded-[6px] border border-[#C5A880] bg-[#FAF9F6] p-3">
          <p className="text-[#334155]">{confirmSummary(preview, turningOn)}</p>
          <div className="mt-2 flex gap-2">
            <button type="button" className={primary} disabled={busy}
              onClick={() => onSwitch && onSwitch(turningOn, none.length)}>
              {turningOn ? "Confirm — turn on" : "Confirm — turn off"}
            </button>
            <button type="button" className={secondary} disabled={busy} onClick={() => setConfirming(false)}>Cancel</button>
          </div>
        </div>
      ) : null}
      {error ? <p className="mt-2 text-xs text-[#9B2335]">{error}</p> : null}
      <table className="mt-4 w-full">
        <thead className="text-left text-[#64748B]"><tr><th className="py-1">Person</th><th>Families</th><th /></tr></thead>
        <tbody>{rows.map((r) => (
          <tr key={r.userId} className="border-t border-[#ece8dd] align-top">
            <td className="py-1">{r.name}</td>
            <td>{r.count}</td>
            <td className="text-right">
              {r.families.length ? (
                <button type="button" className="text-[#1B2B4B] underline" onClick={() => setOpen(open === r.userId ? null : r.userId)}>
                  {open === r.userId ? "Hide" : "Which and why"}
                </button>
              ) : null}
              {open === r.userId ? (
                <ul className="mt-1 text-left text-[#334155]">
                  {r.families.map((f) => (
                    <li key={f.key}>{f.name} — {(f.reasons || []).map((x) => reasonText(x, v)).join("; ")}</li>
                  ))}
                </ul>
              ) : null}
            </td>
          </tr>
        ))}</tbody>
      </table>
    </section>
  );
}

export function UnassignedView({ envelope, people = [], teams = [], onAssign, busy, error }) {
  const [picked, setPicked] = useState([]);
  const [target, setTarget] = useState("");
  const [label, setLabel] = useState("");
  if (!envelope) return null;
  const rows = unassignedRows(envelope);
  const can = canWrite(envelope) && (envelope.vocabularies?.editable || []).includes("assignments");
  const labels = Array.isArray(envelope.vocabularies?.role_labels) ? envelope.vocabularies.role_labels : [];
  const toggle = (k) => setPicked((p) => (p.includes(k) ? p.filter((x) => x !== k) : [...p, k]));
  const owners = (r) => [...(r.relationship_managers || []), ...(r.created_by || [])].map((u) => u.name || "—");
  return (
    <section className={card}>
      <h2 className="text-base font-semibold text-[#1B2B4B]">Unassigned families</h2>
      <p className="mt-1 text-[#64748B]">With enforcement on, these are seen only by org admins and their owner.</p>
      {rows.length ? (
        <table className="mt-3 w-full">
          <thead className="text-left text-[#64748B]"><tr>{can ? <th /> : null}<th className="py-1">Family</th><th>Owner</th><th>Records</th></tr></thead>
          <tbody>{rows.map((r) => (
            <tr key={r.key} className="border-t border-[#ece8dd]">
              {can ? <td><input type="checkbox" aria-label={`Select ${r.name}`} checked={picked.includes(r.key)} onChange={() => toggle(r.key)} /></td> : null}
              <td className="py-1">{r.name}</td>
              <td>{owners(r).join(", ") || "—"}</td>
              <td>{r.entity_count}</td>
            </tr>
          ))}</tbody>
        </table>
      ) : <p className="mt-3 text-[#64748B]">Every family has someone assigned.</p>}
      {can && rows.length ? (
        <div className="mt-3 flex flex-wrap items-center gap-2">
          <select aria-label="Person or team" className={input} value={target} onChange={(e) => setTarget(e.target.value)}>
            <option value="">Assign to…</option>
            {people.map((p) => <option key={`u:${p.user_id}`} value={`u:${p.user_id}`}>{p.full_name || p.email}</option>)}
            {teams.map((t) => <option key={`t:${t.id}`} value={`t:${t.id}`}>Team: {t.name}</option>)}
          </select>
          <select aria-label="Role" className={input} value={label} onChange={(e) => setLabel(e.target.value)}>
            <option value="">Role…</option>
            {labels.map((l) => <option key={l} value={l}>{l}</option>)}
          </select>
          <button type="button" className={primary} disabled={busy || !target || !picked.length}
            onClick={() => onAssign && onAssign(picked, target, label).then(() => setPicked([]))}>
            Assign {picked.length || ""} selected
          </button>
        </div>
      ) : null}
      {error ? <p className="mt-2 text-xs text-[#9B2335]">{error}</p> : null}
    </section>
  );
}

export function PeopleView({ people = [], canEdit, onSetManager, busy, error }) {
  return (
    <section className={card}>
      <h2 className="text-base font-semibold text-[#1B2B4B]">People and managers</h2>
      <p className="mt-1 text-[#64748B]">A manager sees the families assigned to everyone who reports to them.</p>
      <table className="mt-3 w-full">
        <thead className="text-left text-[#64748B]"><tr><th className="py-1">Person</th><th>Teams</th><th>Reports to</th></tr></thead>
        <tbody>{people.map((p) => (
          <tr key={p.user_id} className="border-t border-[#ece8dd]">
            <td className="py-1">{p.full_name || p.email}</td>
            <td>{(p.teams || []).join(", ") || "—"}</td>
            <td>
              {canEdit ? (
                <select aria-label={`Manager of ${p.full_name || p.email}`} className={input} disabled={busy}
                  value={p.manager_id || ""} onChange={(e) => onSetManager && onSetManager(p.user_id, e.target.value || null)}>
                  <option value="">No manager</option>
                  {people.filter((m) => m.user_id !== p.user_id).map((m) => (
                    <option key={m.user_id} value={m.user_id}>{m.full_name || m.email}</option>
                  ))}
                </select>
              ) : (p.manager_name || "—")}
            </td>
          </tr>
        ))}</tbody>
      </table>
      {error ? <p className="mt-2 text-xs text-[#9B2335]">{error}</p> : null}
    </section>
  );
}

export default function StaffVisibilityAdmin({ initialPreview, initialUnassigned, initialPeople = [], teams = [], canManagePeople }) {
  const [preview, setPreview] = useState(initialPreview || null);
  const [unassigned, setUnassigned] = useState(initialUnassigned || null);
  const [people, setPeople] = useState(initialPeople);
  const [busy, setBusy] = useState(false);
  const [errors, setErrors] = useState({});

  async function guarded(key, fn) {
    setBusy(true);
    setErrors((e) => ({ ...e, [key]: null }));
    try {
      await fn();
    } catch (err) {
      setErrors((e) => ({ ...e, [key]: err.message }));
    } finally {
      setBusy(false);
    }
  }
  const refresh = async () => {
    const [p, u, pe] = await Promise.all([
      fetch("/api/admin/staff-visibility", { cache: "no-store" }),
      fetch("/api/admin/staff-visibility/unassigned", { cache: "no-store" }),
      fetch("/api/admin/staff/people", { cache: "no-store" }),
    ]);
    if (p.ok) setPreview(await p.json());
    if (u.ok) setUnassigned(await u.json());
    if (pe.ok) setPeople(await pe.json());
  };
  const onSwitch = (on, acknowledged) => guarded("preview", async () => {
    await send("/api/admin/staff-visibility/enforcement", "PUT", { enforced: on, confirm: true, acknowledged_sees_none: acknowledged });
    await refresh();
  });
  const onAssign = (keys, target, label) => guarded("unassigned", async () => {
    const [t, id] = target.split(":");
    await send("/api/admin/staff-visibility/bulk-assign", "POST", {
      family_keys: keys, role_label: label || null, ...(t === "u" ? { assigned_to_user_id: id } : { assigned_to_team_id: id }),
    });
    await refresh();
  });
  const onSetManager = (userId, managerId) => guarded("people", async () => {
    await send(`/api/admin/staff/users/${encodeURIComponent(userId)}/manager`, "PUT", { manager_id: managerId });
    await refresh();
  });
  return (
    <div className="mt-6 space-y-6">
      {preview ? <PreviewView preview={preview} onSwitch={onSwitch} busy={busy} error={errors.preview} /> : null}
      <UnassignedView envelope={unassigned} people={people} teams={teams} onAssign={onAssign} busy={busy} error={errors.unassigned} />
      <PeopleView people={people} canEdit={Boolean(canManagePeople)} onSetManager={onSetManager} busy={busy} error={errors.people} />
    </div>
  );
}
