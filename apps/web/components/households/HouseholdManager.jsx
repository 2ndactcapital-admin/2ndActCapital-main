"use client";

// PORTENTRY2 (Joe 2026-11-05) — an existing household's Members panel (people AND legal entities), an account owner
// that is any member or a legal entity a member owns, the account's registration type / tax status / billable /
// discretionary / service model on Add and on each card, DVP fields visibly required with the reason, and live
// member / account counts. Every write control still renders only for permissions.can_write === true.
// PERMS1 (REP-01, Joe 2026-11-04) — Portfolio → Households: "New household"
// (name + member entities linked from the CRM) and, on a household, "Add
// account" (custodian, account number, owner, authority / custody). Rendered
// ONLY when the API envelope says permissions.can_write === true (no fallback:
// a lost envelope renders nothing). The full account number is typed once and
// never shown back — the API returns the mask only. A non-default authority /
// custody becomes a REP7B change awaiting a second person.

import { useState } from "react";
import { NetWorthLines } from "@/components/crm/LiabilitiesTab";
import { formatMoney } from "@/lib/crmDetail.mjs";
import { accountLine, billableDefault, dvpMissing, dvpReason, ownerOptions, withCounts } from "@/lib/portentry2.mjs";

const card = "rounded-[6px] border border-[#ece8dd] bg-white p-6";
const input = "w-full rounded-[6px] border border-[#E2E8F0] bg-white px-3 py-2 text-sm text-[#0F172A]";
const button = "rounded-[6px] bg-[#1B2B4B] px-4 py-2 text-sm text-white disabled:opacity-50";
const quiet = "text-sm text-[#64748B]";

async function call(url, options = {}) {
  const res = await fetch(url, {
    ...options,
    headers: { "Content-Type": "application/json" },
    body: options.body ? JSON.stringify(options.body) : undefined,
  });
  let data = null;
  try {
    data = await res.json();
  } catch {
    data = null;
  }
  if (!res.ok) {
    const detail = data && (typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail || data));
    throw new Error(detail || `Request failed (${res.status})`);
  }
  return data;
}

function EntityPicker({ chosen, setChosen }) {
  const [q, setQ] = useState("");
  const [hits, setHits] = useState([]);
  async function search(value) {
    setQ(value);
    if (value.trim().length < 2) {
      setHits([]);
      return;
    }
    try {
      const data = await call(`/api/entities/search?q=${encodeURIComponent(value.trim())}`);
      const items = Array.isArray(data?.items) ? data.items : [];
      setHits(items.filter((e) => !["account", "spv"].includes(e.entity_type)));
    } catch {
      setHits([]);
    }
  }
  return (
    <div>
      <input className={input} value={q} placeholder="Search the CRM for a person, trust or company"
        onChange={(e) => search(e.target.value)} />
      {hits.length > 0 && (
        <ul className="mt-1 max-h-48 overflow-auto rounded-[6px] border border-[#ece8dd] bg-white">
          {hits.map((e) => (
            <li key={e.id}>
              <button type="button" className="w-full px-3 py-1 text-left text-sm hover:bg-[#FAF9F6]"
                onClick={() => {
                  if (!chosen.some((c) => c.id === e.id)) setChosen([...chosen, e]);
                  setHits([]);
                  setQ("");
                }}>
                {e.display_name} <span className={quiet}>· {e.entity_type}</span>
              </button>
            </li>
          ))}
        </ul>
      )}
      {chosen.length > 0 && (
        <ul className="mt-2 flex flex-wrap gap-2">
          {chosen.map((c) => (
            <li key={c.id} className="rounded-[6px] border border-[#ece8dd] bg-[#F5F1EB] px-2 py-1 text-sm">
              {c.display_name}{" "}
              <button type="button" className="text-[#64748B]" aria-label={`Remove ${c.display_name}`}
                onClick={() => setChosen(chosen.filter((x) => x.id !== c.id))}>×</button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function NewHousehold({ onCreated }) {
  const [name, setName] = useState("");
  const [members, setMembers] = useState([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  async function submit(e) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const row = await call("/api/admin/households", {
        method: "POST", body: { name, member_entity_ids: members.map((m) => m.id) },
      });
      setName("");
      setMembers([]);
      onCreated(row);
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <form onSubmit={submit} className={card}>
      <h2 className="font-[Spectral,Georgia,serif] text-xl text-[#1B2B4B]">New household</h2>
      <label className="mt-4 block text-sm text-[#334155]">Name
        <input className={input} value={name} onChange={(e) => setName(e.target.value)} required />
      </label>
      <div className="mt-4 text-sm text-[#334155]">Members (from the CRM)</div>
      <EntityPicker chosen={members} setChosen={setMembers} />
      {error && <p className="mt-3 text-sm text-[#9B2335]">{error}</p>}
      <button className={`${button} mt-4`} disabled={busy || !name.trim()}>Create household</button>
    </form>
  );
}

function MembersPanel({ household, canWrite, onChanged }) {
  // PORTENTRY2 (MUSE-OLIVO-H-02 / G-02) — the members of an EXISTING household: people AND legal entities (trusts,
  // LLCs, LPs, foundations …) from the CRM; add / remove are audited by the API. Rendered only for can_write.
  const [adding, setAdding] = useState([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  async function add() {
    setBusy(true);
    setError(null);
    try {
      for (const m of adding) {
        await call(`/api/admin/households/${encodeURIComponent(household.id)}/members`, {
          method: "POST", body: { entity_id: m.id },
        });
      }
      setAdding([]);
      await onChanged();
    } catch (err) {
      setError(err.message);
      await onChanged();
    } finally {
      setBusy(false);
    }
  }
  async function remove(m) {
    if (!window.confirm(`Remove ${m.display_name} from ${household.name}?`)) return;
    setBusy(true);
    setError(null);
    try {
      await call(`/api/admin/households/${encodeURIComponent(household.id)}/members/${encodeURIComponent(m.id)}`,
        { method: "DELETE" });
      await onChanged();
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="mt-3">
      <h3 className="text-xs font-medium uppercase tracking-wide text-[#64748B]">Members</h3>
      {household.members.length === 0 ? <p className={quiet}>None yet.</p> : (
        <ul className="mt-1 flex flex-wrap gap-2">
          {household.members.map((m) => (
            <li key={m.id} className="rounded-[6px] border border-[#ece8dd] bg-[#F5F1EB] px-2 py-1 text-sm text-[#334155]">
              <a href={`/crm/${m.id}`} className="hover:underline">{m.display_name}</a>
              <span className={quiet}> · {m.entity_type}</span>
              {canWrite && (
                <button type="button" className="ml-1 text-[#64748B]" aria-label={`Remove ${m.display_name}`}
                  disabled={busy} onClick={() => remove(m)}>×</button>
              )}
            </li>
          ))}
        </ul>
      )}
      {canWrite && (
        <div className="mt-2">
          <EntityPicker chosen={adding} setChosen={setAdding} />
          {adding.length > 0 && (
            <button type="button" className={`${button} mt-2`} disabled={busy} onClick={add}>
              Add {adding.length === 1 ? "member" : `${adding.length} members`}
            </button>
          )}
        </div>
      )}
      {error && <p className="mt-2 text-sm text-[#9B2335]">{error}</p>}
    </div>
  );
}

function AccountCard({ household, account, vocabularies, canWrite, onSaved }) {
  // PORTENTRY2 (H-01) — the account's type, tax status and flags on its card, with an edit for each.
  const regs = Array.isArray(vocabularies?.registration_types) ? vocabularies.registration_types : [];
  const taxes = Array.isArray(vocabularies?.tax_statuses) ? vocabularies.tax_statuses : [];
  const [editing, setEditing] = useState(false);
  const [form, setForm] = useState({
    registration_type: account.registration_type, tax_status: account.tax_status,
    is_billable: account.is_billable, is_discretionary: account.is_discretionary,
    service_model: account.service_model || "",
  });
  const [error, setError] = useState(null);
  async function save(e) {
    e.preventDefault();
    setError(null);
    try {
      await call(`/api/admin/households/${encodeURIComponent(household.id)}/accounts/${encodeURIComponent(account.id)}`,
        { method: "PATCH", body: { ...form, service_model: form.service_model || null } });
      setEditing(false);
      await onSaved();
    } catch (err) {
      setError(err.message);
    }
  }
  return (
    <li className="border-b border-[#E2E8F0] py-1 last:border-0">
      <span>{accountLine(account)}</span>
      {canWrite && !editing && (
        <button type="button" className="ml-2 text-xs text-[#1B2B4B] hover:underline" onClick={() => setEditing(true)}>
          Edit
        </button>
      )}
      {canWrite && editing && (
        <form onSubmit={save} className="mt-2 grid grid-cols-2 gap-2">
          <select className={input} value={form.registration_type} aria-label="Registration type"
            onChange={(e) => setForm({ ...form, registration_type: e.target.value })}>
            {!regs.some((r) => r.key === form.registration_type) && <option value={form.registration_type}>Not set</option>}
            {regs.map((r) => <option key={r.key} value={r.key}>{r.label}</option>)}
          </select>
          <select className={input} value={form.tax_status} aria-label="Tax status"
            onChange={(e) => setForm({ ...form, tax_status: e.target.value })}>
            {!taxes.some((t) => t.key === form.tax_status) && <option value={form.tax_status}>Not set</option>}
            {taxes.map((t) => <option key={t.key} value={t.key}>{t.label}</option>)}
          </select>
          <label className="text-sm text-[#334155]"><input type="checkbox" checked={form.is_billable}
            onChange={(e) => setForm({ ...form, is_billable: e.target.checked })} /> Billable</label>
          <label className="text-sm text-[#334155]"><input type="checkbox" checked={form.is_discretionary}
            onChange={(e) => setForm({ ...form, is_discretionary: e.target.checked })} /> Discretionary</label>
          <input className={input} placeholder="Service model (optional)" value={form.service_model}
            onChange={(e) => setForm({ ...form, service_model: e.target.value })} />
          <div className="flex gap-2">
            <button className={button}>Save</button>
            <button type="button" className="text-sm text-[#64748B]" onClick={() => setEditing(false)}>Cancel</button>
          </div>
          {error && <p className="col-span-2 text-sm text-[#9B2335]">{error}</p>}
        </form>
      )}
    </li>
  );
}

function AddAccount({ household, vocabularies, onAdded }) {
  const authorities = Array.isArray(vocabularies?.authorities) ? vocabularies.authorities : [];
  const custodies = Array.isArray(vocabularies?.custodies) ? vocabularies.custodies : [];
  const regs = Array.isArray(vocabularies?.registration_types) ? vocabularies.registration_types : [];
  const taxes = Array.isArray(vocabularies?.tax_statuses) ? vocabularies.tax_statuses : [];
  const owners = ownerOptions(household);
  const [form, setForm] = useState({
    custodian_code: "", account_number: "", primary_entity_id: owners[0]?.value || "",
    authority: authorities[0]?.key || "", custody: custodies[0]?.key || "", reason: "",
    receiving_name: "", receiving_participant: "", registration_type: "", tax_status: "",
    is_billable: billableDefault(household), is_discretionary: true, service_model: "",
  });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [done, setDone] = useState(null);
  const set = (k) => (e) => setForm({ ...form, [k]: e.target.value });
  const missing = dvpMissing(form.custody, form.receiving_name, form.receiving_participant);
  const owner = owners.find((o) => o.value === form.primary_entity_id);
  async function submit(e) {
    e.preventDefault();
    setError(null);
    setDone(null);
    if (missing.length) {
      setError(dvpReason(missing));
      return;
    }
    setBusy(true);
    const body = {
      custodian_code: form.custodian_code, account_number: form.account_number,
      primary_entity_id: form.primary_entity_id, authority: form.authority, custody: form.custody,
      reason: form.reason || null, registration_type: form.registration_type || null,
      tax_status: form.tax_status || null, is_billable: form.is_billable, is_discretionary: form.is_discretionary,
      service_model: form.service_model || null, add_owner_as_member: Boolean(owner?.addAsMember),
    };
    if (form.custody === "other_custodian") {
      body.receiving = { custodian_name: form.receiving_name, participant_number: form.receiving_participant };
    }
    try {
      const row = await call(`/api/admin/households/${encodeURIComponent(household.id)}/accounts`, {
        method: "POST", body,
      });
      setForm({ ...form, account_number: "" });
      setDone(row);
      await onAdded();
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }
  if (!owners.length) {
    return <p className={quiet}>Link a member to this household before adding an account.</p>;
  }
  return (
    <form onSubmit={submit} className="mt-4 grid grid-cols-2 gap-3">
      <h3 className="col-span-2 text-xs font-medium uppercase tracking-wide text-[#64748B]">Add account</h3>
      <label className="text-sm text-[#334155]">Custodian
        <input className={input} value={form.custodian_code} onChange={set("custodian_code")} required />
      </label>
      <label className="text-sm text-[#334155]">Account number (stored masked)
        <input className={input} value={form.account_number} onChange={set("account_number")} autoComplete="off"
          required />
      </label>
      <label className="text-sm text-[#334155]">Owner
        <select className={input} value={form.primary_entity_id} onChange={set("primary_entity_id")}>
          <optgroup label="Members">
            {owners.filter((o) => o.group === "members").map((o) => (
              <option key={o.value} value={o.value}>{o.label} · {o.entity_type}</option>
            ))}
          </optgroup>
          {owners.some((o) => o.group === "candidates") && (
            <optgroup label="Legal entities owned by a member">
              {owners.filter((o) => o.group === "candidates").map((o) => (
                <option key={o.value} value={o.value}>{o.label}</option>
              ))}
            </optgroup>
          )}
        </select>
      </label>
      <label className="text-sm text-[#334155]">Registration type
        <select className={input} value={form.registration_type} onChange={set("registration_type")} required>
          <option value="">Choose…</option>
          {regs.map((r) => <option key={r.key} value={r.key}>{r.label}</option>)}
        </select>
      </label>
      <label className="text-sm text-[#334155]">Tax status
        <select className={input} value={form.tax_status} onChange={set("tax_status")} required>
          <option value="">Choose…</option>
          {taxes.map((t) => <option key={t.key} value={t.key}>{t.label}</option>)}
        </select>
      </label>
      <label className="text-sm text-[#334155]">Service model (optional)
        <input className={input} value={form.service_model} onChange={set("service_model")} />
      </label>
      <label className="text-sm text-[#334155]">
        <input type="checkbox" checked={form.is_billable}
          onChange={(e) => setForm({ ...form, is_billable: e.target.checked })} /> Billable
        {household.is_demo && <span className={quiet}> (off by default for a demo household)</span>}
      </label>
      <label className="text-sm text-[#334155]">
        <input type="checkbox" checked={form.is_discretionary}
          onChange={(e) => setForm({ ...form, is_discretionary: e.target.checked })} /> Discretionary
      </label>
      <label className="text-sm text-[#334155]">Authority
        <select className={input} value={form.authority} onChange={set("authority")}>
          {authorities.map((a) => <option key={a.key} value={a.key}>{a.label}</option>)}
        </select>
      </label>
      <label className="text-sm text-[#334155]">Custody
        <select className={input} value={form.custody} onChange={set("custody")}>
          {custodies.map((c) => <option key={c.key} value={c.key}>{c.label}</option>)}
        </select>
      </label>
      <label className="text-sm text-[#334155]">Reason (for a non-default authority)
        <input className={input} value={form.reason} onChange={set("reason")} />
      </label>
      {form.custody === "other_custodian" && (
        <>
          {/* MUSE-OLIVO-H-03 / H-04 — visibly required, with the reason when missing */}
          <label className="text-sm text-[#334155]">Receiving custodian <span className="text-[#9B2335]">*</span>
            <input className={input} value={form.receiving_name} onChange={set("receiving_name")} required
              aria-required="true" />
          </label>
          <label className="text-sm text-[#334155]">DTC / participant number <span className="text-[#9B2335]">*</span>
            <input className={input} value={form.receiving_participant} onChange={set("receiving_participant")}
              required aria-required="true" />
          </label>
          {missing.length > 0 && <p className="col-span-2 text-xs text-[#64748B]">{dvpReason(missing)}</p>}
        </>
      )}
      <div className="col-span-2">
        {error && <p className="text-sm text-[#9B2335]">{error}</p>}
        {done && (
          <p className="text-sm text-[#2D6A4F]">
            Added {done.account_number_masked} at {done.custodian_code}
            {done.owner_linked_now ? " — its owner is now a member of the household" : ""}
            {done.pending_change ? " — the authority / custody change awaits a second person's approval." : "."}
          </p>
        )}
        <button className={`${button} mt-2`} disabled={busy}>Add account</button>
      </div>
    </form>
  );
}

// BUGFIX3 (MUSE-OLIVO-H) — Recalculate on the household: renders only when the envelope's own
// permissions.can.recalculate is true (the exact rule of the route; no fallback). The API runs the members'
// family-scoped rollup, then the card re-reads its detail so net worth shows the new figure.
function RecalculateHousehold({ household, onDone }) {
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState(null);
  if (household?.permissions?.can?.recalculate !== true) return null;
  async function run() {
    setBusy(true);
    setNote(null);
    try {
      const r = await call(`/api/admin/households/${encodeURIComponent(household.id)}/recalculate`, { method: "POST" });
      setNote(`Recalculated (${r.status}).`);
      await onDone();
    } catch (err) {
      setNote(err.message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <span className="ml-3 inline-flex items-center gap-2">
      <button type="button" className="text-sm text-[#1B2B4B] hover:underline disabled:opacity-50" disabled={busy}
        onClick={run}>{busy ? "Recalculating…" : "Recalculate"}</button>
      {note && <span className={quiet}>{note}</span>}
    </span>
  );
}

// BUGFIX3 (Task 4) — approved one-time import rows: LIVE positions, labelled provisional (label from the API).
function ProvisionalHoldings({ household }) {
  const rows = Array.isArray(household?.provisional_holdings) ? household.provisional_holdings : [];
  if (rows.length === 0) return null;
  return (
    <>
      <h3 className="mt-4 text-xs font-medium uppercase tracking-wide text-[#64748B]">Imported holdings</h3>
      <ul className="mt-1 space-y-1 text-sm text-[#334155]">
        {rows.map((r) => (
          <li key={r.position_id}>
            {r.asset_name} · {r.owner_name}{r.account_number_masked ? ` · ${r.account_number_masked}` : ""}
            {" · "}{formatMoney(r.market_value)}
            <span className="ml-2 text-xs text-[#C5A880]">{r.label || household.provisional_label}</span>
          </li>
        ))}
      </ul>
    </>
  );
}

function HouseholdCard({ row, onDetail }) {
  const [detail, setDetail] = useState(null);
  const [open, setOpen] = useState(false);
  const [error, setError] = useState(null);
  async function load() {
    try {
      const d = await call(`/api/admin/households/${encodeURIComponent(row.id)}`);
      setDetail(d);
      onDetail(d); // PORTENTRY2 (H-05) — the list's member / account counts follow without a reload
    } catch (err) {
      setError(err.message);
    }
  }
  async function toggle() {
    const next = !open;
    setOpen(next);
    if (next && !detail) await load();
  }
  const canWrite = detail?.permissions?.can_write === true;
  return (
    <li className={card}>
      <button type="button" className="text-left" onClick={toggle}>
        <span className="font-[Spectral,Georgia,serif] text-lg text-[#1B2B4B]">{row.name}</span>
        <span className={quiet}> · {row.members} member(s) · {row.accounts} account(s)</span>
      </button>
      {open && (
        <div className="mt-3">
          {error && <p className="text-sm text-[#9B2335]">{error}</p>}
          {detail && (
            <>
              <MembersPanel household={detail} canWrite={canWrite} onChanged={load} />
              <h3 className="mt-4 text-xs font-medium uppercase tracking-wide text-[#64748B]">Accounts</h3>
              <ul className="mt-1 text-sm text-[#334155]">
                {detail.accounts.map((a) => (
                  <AccountCard key={a.id} household={detail} account={a} vocabularies={detail.vocabularies}
                    canWrite={canWrite} onSaved={load} />
                ))}
              </ul>
              {/* CRMDETAIL1 — net worth (gross, liabilities, net) and the household's Liabilities section */}
              <div className="mt-4"><NetWorthLines netWorth={detail.net_worth} vocabularies={detail.vocabularies} />
                <RecalculateHousehold household={detail} onDone={load} /></div>
              <ProvisionalHoldings household={detail} />
              <h3 className="mt-4 text-xs font-medium uppercase tracking-wide text-[#64748B]">Liabilities</h3>
              {(detail.liabilities || []).length === 0 ? (
                <p className={quiet}>None recorded. Liabilities are entered on each member's CRM record.</p>
              ) : (
                <ul className="mt-1 space-y-1 text-sm text-[#334155]">
                  {detail.liabilities.map((l) => (
                    <li key={l.id}>
                      <a href={`/crm/${l.owner_entity_id}?tab=liabilities`} className="hover:underline">{l.name}</a>
                      {" · "}{l.kind_label}{l.owner_name ? ` · ${l.owner_name}` : ""}{" · "}
                      {formatMoney(l.current_balance, l.currency_code)}
                    </li>
                  ))}
                </ul>
              )}
              {canWrite && <AddAccount key={detail.member_count + ":" + detail.account_count} household={detail}
                vocabularies={detail.vocabularies} onAdded={load} />}
            </>
          )}
        </div>
      )}
    </li>
  );
}

export default function HouseholdManager({ envelope }) {
  const [rows, setRows] = useState(Array.isArray(envelope?.rows) ? envelope.rows : []);
  if (envelope?.permissions?.can_write !== true) return null;
  return (
    <section className="mt-8 space-y-4">
      <NewHousehold onCreated={(r) => setRows([...rows, { id: r.id, name: r.name, members: r.members.length, accounts: 0 }])} />
      <ul className="space-y-3">
        {rows.map((r) => <HouseholdCard key={r.id} row={r} onDetail={(d) => setRows((prev) => withCounts(prev, d))} />)}
      </ul>
    </section>
  );
}
