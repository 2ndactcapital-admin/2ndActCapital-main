"use client";

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

function AddAccount({ household, vocabularies, onAdded }) {
  const authorities = Array.isArray(vocabularies?.authorities) ? vocabularies.authorities : [];
  const custodies = Array.isArray(vocabularies?.custodies) ? vocabularies.custodies : [];
  const [form, setForm] = useState({
    custodian_code: "", account_number: "", primary_entity_id: household.members[0]?.id || "",
    authority: authorities[0]?.key || "", custody: custodies[0]?.key || "", reason: "",
    receiving_name: "", receiving_participant: "",
  });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [done, setDone] = useState(null);
  const set = (k) => (e) => setForm({ ...form, [k]: e.target.value });
  async function submit(e) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    setDone(null);
    const body = {
      custodian_code: form.custodian_code, account_number: form.account_number,
      primary_entity_id: form.primary_entity_id, authority: form.authority, custody: form.custody,
      reason: form.reason || null,
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
      onAdded();
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }
  if (!household.members.length) {
    return <p className={quiet}>Link a member to this household before adding an account.</p>;
  }
  return (
    <form onSubmit={submit} className="mt-4 grid grid-cols-2 gap-3">
      <label className="text-sm text-[#334155]">Custodian
        <input className={input} value={form.custodian_code} onChange={set("custodian_code")} required />
      </label>
      <label className="text-sm text-[#334155]">Account number (stored masked)
        <input className={input} value={form.account_number} onChange={set("account_number")} autoComplete="off"
          required />
      </label>
      <label className="text-sm text-[#334155]">Owner
        <select className={input} value={form.primary_entity_id} onChange={set("primary_entity_id")}>
          {household.members.map((m) => <option key={m.id} value={m.id}>{m.display_name}</option>)}
        </select>
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
          <label className="text-sm text-[#334155]">Receiving custodian
            <input className={input} value={form.receiving_name} onChange={set("receiving_name")} />
          </label>
          <label className="text-sm text-[#334155]">DTC / participant number
            <input className={input} value={form.receiving_participant} onChange={set("receiving_participant")} />
          </label>
        </>
      )}
      <div className="col-span-2">
        {error && <p className="text-sm text-[#9B2335]">{error}</p>}
        {done && (
          <p className="text-sm text-[#2D6A4F]">
            Added {done.account_number_masked} at {done.custodian_code}
            {done.pending_change ? " — the authority / custody change awaits a second person's approval." : "."}
          </p>
        )}
        <button className={`${button} mt-2`} disabled={busy}>Add account</button>
      </div>
    </form>
  );
}

function HouseholdCard({ row }) {
  const [detail, setDetail] = useState(null);
  const [open, setOpen] = useState(false);
  const [error, setError] = useState(null);
  async function load() {
    try {
      setDetail(await call(`/api/admin/households/${encodeURIComponent(row.id)}`));
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
              <p className={quiet}>Members: {detail.members.map((m) => m.display_name).join(", ") || "none yet"}</p>
              <ul className="mt-2 space-y-1 text-sm text-[#334155]">
                {detail.accounts.map((a) => (
                  <li key={a.id}>{a.account_number_masked} · {a.custodian_code}{a.is_held_away ? " · held away" : ""}</li>
                ))}
              </ul>
              {/* CRMDETAIL1 — net worth (gross, liabilities, net) and the household's Liabilities section */}
              <div className="mt-4"><NetWorthLines netWorth={detail.net_worth} vocabularies={detail.vocabularies} /></div>
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
              {canWrite && <AddAccount household={detail} vocabularies={detail.vocabularies} onAdded={load} />}
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
        {rows.map((r) => <HouseholdCard key={r.id} row={r} />)}
      </ul>
    </section>
  );
}
