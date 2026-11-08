"use client";

/**
 * SegregationOfDuties — S30. Admin → Segregation of duties: the duty list, the item types with the policy in force
 * (self-approval, floor enforced), the rules (floor / default / policy / org, with history), the conflict report
 * (people whose roles combine a maker and a checker duty), the self-approval log and refused checks (last 90 days),
 * each with a CSV export. An org admin drafts an org rule or policy; a DIFFERENT person decides it; a Super Admin
 * drafts a platform rule, a different Super Admin decides it. Controls from lib/sod.mjs (fail closed); labels from the
 * server's own rows. Light / cream only.
 */

import { useState } from "react";
import { canDecide, controls, csvHref, dutyLabel, floorNotEnforced, itemLabel, rulesByItem, vocab } from "@/lib/sod.mjs";

const card = "rounded-[6px] border border-[#ece8dd] bg-white p-5";
const input = "rounded border border-[#E2E8F0] px-3 py-2";
const primary = "rounded bg-navy px-3 py-1 text-white disabled:opacity-50";
const secondary = "rounded border border-[#E2E8F0] px-3 py-1 text-navy disabled:opacity-50";
const th = "px-2 py-1 text-left font-medium text-text-muted";
const td = "px-2 py-1 align-top";
const TABS = [
  ["rules", "Duties and rules"], ["conflicts", "Conflicting duties"], ["self_approvals", "Self-approvals"],
  ["refusals", "Refused checks"],
];

async function send(path, method = "GET", body) {
  const res = await fetch(`/api/sod${path ? `/${path}` : ""}`, {
    method, headers: body ? { "Content-Type": "application/json" } : undefined, body: body ? JSON.stringify(body) : undefined,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || "Request failed");
  return data;
}

function Csv({ env, name }) {
  const href = csvHref(env, name);
  return href ? <a className="text-sm text-gold hover:underline" href={href}>Export CSV</a> : null;
}

export function RulesView({ env, viewerId, onDecide, busy }) {
  const grouped = rulesByItem(env);
  const notEnforced = floorNotEnforced(env);
  return (
    <div className="space-y-6">
      {notEnforced.length > 0 && (
        <div className={card}>
          <p className="text-sm text-text-secondary">
            The floor is not yet enforced for: {notEnforced.map((t) => t.label).join(", ")}. Until now no rule stopped
            this; an administrator can turn it on below (turning it off again needs a Super Admin).
          </p>
        </div>
      )}
      <div className={card}>
        <div className="flex items-baseline justify-between">
          <h2 className="font-serif text-xl text-navy">Item types</h2>
          <Csv env={env} name="item_types" />
        </div>
        <table className="mt-3 w-full text-sm">
          <thead><tr><th className={th}>Item</th><th className={th}>Made by</th><th className={th}>Checked by</th>
            <th className={th}>Floor</th><th className={th}>Self-approval</th><th className={th}>Rules</th></tr></thead>
          <tbody>
            {(env.item_types || []).map((t) => (
              <tr key={t.item_type} className="border-t border-[#ece8dd]">
                <td className={td}>{t.label}</td>
                <td className={td}>{dutyLabel(env, t.maker_duty)}</td>
                <td className={td}>{dutyLabel(env, t.checker_duty)}</td>
                <td className={td}>{t.floor_class ? `Floor (${t.floor_class})` : "—"}
                  {t.policy && t.policy.enforce_floor === false ? " · not enforced" : ""}</td>
                <td className={td}>{t.policy ? (t.policy.self_approval === "when_no_other_checker"
                  ? "When no other checker (disclosed)" : "Never") : "—"}
                  {t.policy && t.policy.source === "org" ? " · this firm" : ""}</td>
                <td className={td}>
                  {(grouped[t.item_type] || []).map((r) => (
                    <div key={r.id} className={r.history ? "text-text-muted line-through" : ""}>
                      <span className="text-text-secondary">{r.rule_kind}</span> {r.rule_key} v{r.version_no} · {r.status}
                      {r.status === "draft" && canDecide(env, r, viewerId) && (
                        <span className="ml-2">
                          <button type="button" className={primary} disabled={busy} onClick={() => onDecide(r, "approve")}>Approve</button>{" "}
                          <button type="button" className={secondary} disabled={busy} onClick={() => onDecide(r, "reject")}>Reject</button>
                        </span>
                      )}
                    </div>
                  ))}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className={card}>
        <div className="flex items-baseline justify-between">
          <h2 className="font-serif text-xl text-navy">Duties</h2>
          <Csv env={env} name="duties" />
        </div>
        <ul className="mt-3 space-y-1 text-sm">
          {(env.duties || []).map((d) => (
            <li key={d.duty_key}><span className="text-navy">{d.label}</span> — {d.description}
              {(d.permissions || []).length ? <span className="text-text-muted"> ({d.permissions.join(", ")})</span> : null}</li>
          ))}
        </ul>
      </div>
    </div>
  );
}

export function DraftForm({ env, onDraft, busy }) {
  const c = controls(env);
  const v = vocab(env);
  const [kind, setKind] = useState(c.draftOrgRule ? "org" : "policy");
  const [form, setForm] = useState({ item_type: "", scope: "same_household", period_days: 30, severity: "block",
    self_approval: "never", enforce_floor: true, description: "" });
  if (!c.draftOrgRule && !c.draftPolicy) return null;
  const set = (k) => (e) => setForm({ ...form, [k]: e.target.type === "checkbox" ? e.target.checked : e.target.value });
  const submit = () => {
    const base = { owner_scope: "org", rule_kind: kind, item_type: form.item_type, description: form.description };
    const body = kind === "policy"
      ? { ...base, self_approval: form.self_approval, enforce_floor: form.enforce_floor === true }
      : { ...base, scope: form.scope, severity: form.severity,
          ...(form.scope === "same_item" ? {} : { period_days: Number(form.period_days) }) };
    onDraft(body);
  };
  return (
    <div className={card}>
      <h2 className="font-serif text-xl text-navy">Draft a change</h2>
      <p className="mt-1 text-sm text-text-muted">A different administrator approves it before it applies. A firm can add
        stricter rules; it can never relax a floor.</p>
      <div className="mt-3 grid grid-cols-2 gap-3 text-sm">
        <select className={input} value={kind} onChange={(e) => setKind(e.target.value)}>
          {c.draftOrgRule && <option value="org">A stricter rule</option>}
          {c.draftPolicy && <option value="policy">The policy for an item type</option>}
        </select>
        <select className={input} value={form.item_type} onChange={set("item_type")}>
          <option value="">Item type…</option>
          {(env.item_types || []).map((t) => <option key={t.item_type} value={t.item_type}>{t.label}</option>)}
        </select>
        {kind === "org" ? (
          <>
            <select className={input} value={form.scope} onChange={set("scope")}>
              {(v.scopes || []).map((s) => <option key={s} value={s}>{s.replace("_", " ")}</option>)}
            </select>
            <select className={input} value={form.severity} onChange={set("severity")}>
              {(v.severities || []).map((s) => <option key={s} value={s}>{s}</option>)}
            </select>
            {form.scope !== "same_item" && (
              <input className={input} type="number" min={1} max={3660} value={form.period_days} onChange={set("period_days")} />
            )}
          </>
        ) : (
          <>
            <select className={input} value={form.self_approval} onChange={set("self_approval")}>
              {(v.self_approval || []).map((s) => <option key={s} value={s}>{s.replace(/_/g, " ")}</option>)}
            </select>
            <label className="flex items-center gap-2"><input type="checkbox" checked={form.enforce_floor === true}
              onChange={set("enforce_floor")} /> Enforce the floor</label>
          </>
        )}
        <textarea className={`${input} col-span-2`} placeholder="Why" value={form.description} onChange={set("description")} />
      </div>
      <button type="button" className={`${primary} mt-3`} disabled={busy || !form.item_type || !form.description.trim()}
        onClick={submit}>Draft for approval</button>
    </div>
  );
}

export function ConflictsView({ env }) {
  const rows = env.conflicts || [];
  return (
    <div className={card}>
      <div className="flex items-baseline justify-between">
        <h2 className="font-serif text-xl text-navy">People with conflicting duties</h2>
        <Csv env={env} name="conflicts" />
      </div>
      <p className="mt-1 text-sm text-text-muted">Each person below can both make and check items of a type. The rules
        listed still stop them on any one item they made.</p>
      {rows.length === 0 ? <p className="mt-3 text-sm text-text-muted">No one holds conflicting duties.</p> : (
        <table className="mt-3 w-full text-sm">
          <thead><tr><th className={th}>Person</th><th className={th}>Roles</th><th className={th}>Item types</th></tr></thead>
          <tbody>
            {rows.map((p) => (
              <tr key={p.user_id} className="border-t border-[#ece8dd]">
                <td className={td}>{p.name}</td>
                <td className={td}>{(p.roles || []).join(", ")}</td>
                <td className={td}>{(p.conflicts || []).map((c) => (
                  <div key={c.item_type}>{c.label} — still stopped by {(c.still_stopped_by || []).map((r) => r.rule_key).join(", ")}</div>
                ))}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

export function DecisionsView({ env, name, title, empty }) {
  const rows = env[name] || [];
  return (
    <div className={card}>
      <div className="flex items-baseline justify-between">
        <h2 className="font-serif text-xl text-navy">{title}</h2>
        <Csv env={env} name={name} />
      </div>
      {rows.length === 0 ? <p className="mt-3 text-sm text-text-muted">{empty}</p> : (
        <table className="mt-3 w-full text-sm">
          <thead><tr><th className={th}>When</th><th className={th}>Item</th><th className={th}>Who</th>
            <th className={th}>Rule</th><th className={th}>Reason</th></tr></thead>
          <tbody>
            {rows.map((d) => (
              <tr key={d.id} className="border-t border-[#ece8dd]">
                <td className={td}>{(d.created_at || "").slice(0, 16).replace("T", " ")}</td>
                <td className={td}>{d.item_label || itemLabel(env, d.item_type)}</td>
                <td className={td}>{d.checker_name || d.checker_email || "—"}</td>
                <td className={td}>{d.rule_key || "—"}</td>
                <td className={td}>{d.disclosure || d.reason}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

export default function SegregationOfDuties({ initial }) {
  const [env, setEnv] = useState(initial || {});
  const [tab, setTab] = useState("rules");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState(null);
  const reload = async () => setEnv(await send(""));
  const act = async (fn) => {
    setBusy(true);
    setMsg(null);
    try {
      await fn();
      await reload();
    } catch (e) {
      setMsg(e.message);
    } finally {
      setBusy(false);
    }
  };
  const onDecide = (rule, outcome) => act(async () => {
    const note = outcome === "reject" ? window.prompt("Why is this rejected?") : null;
    await send(`rules/${rule.id}/decide`, "POST", { outcome, ...(note ? { note } : {}) });
  });
  const onDraft = (body) => act(() => send("rules", "POST", body));
  return (
    <div className="space-y-6">
      <div className="flex gap-4 border-b border-[#ece8dd] text-sm">
        {TABS.map(([k, label]) => (
          <button key={k} type="button" onClick={() => setTab(k)}
            className={`pb-2 ${tab === k ? "border-b-2 border-gold text-navy" : "text-text-muted"}`}>{label}</button>
        ))}
      </div>
      {msg && <div className="rounded-[6px] border border-[#ece8dd] bg-white p-3 text-sm text-[#9B2335]">{msg}</div>}
      {tab === "rules" && <><RulesView env={env} viewerId={env.viewer_id} onDecide={onDecide} busy={busy} />
        <DraftForm env={env} onDraft={onDraft} busy={busy} /></>}
      {tab === "conflicts" && <ConflictsView env={env} />}
      {tab === "self_approvals" && <DecisionsView env={env} name="self_approvals" title="Self-approvals"
        empty="No self-approvals recorded." />}
      {tab === "refusals" && <DecisionsView env={env} name="refusals" title="Refused checks — last 90 days"
        empty="No refused checks in the last 90 days." />}
    </div>
  );
}
