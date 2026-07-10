"use client";

import { useEffect, useState, useCallback } from "react";

// ─── Formatters ──────────────────────────────────────────────────────────────

function fmtDate(val) {
  if (!val) return "—";
  try {
    return new Date(val + "T00:00:00").toLocaleDateString("en-US", {
      month: "short", day: "numeric", year: "numeric",
    });
  } catch {
    return val;
  }
}

function fmtMoney(val) {
  if (val == null) return "—";
  const n = typeof val === "string" ? parseFloat(val) : val;
  if (isNaN(n)) return "—";
  return new Intl.NumberFormat("en-US", {
    style: "currency", currency: "USD", minimumFractionDigits: 2,
  }).format(n);
}

// ─── Status pill ─────────────────────────────────────────────────────────────

function StatusPill({ posted }) {
  if (posted) {
    return (
      <span className="inline-block rounded-full px-2 py-0.5 text-[10px] font-semibold"
        style={{ background: "#1B2B4B", color: "#FFF" }}>
        Posted
      </span>
    );
  }
  return (
    <span className="inline-block rounded-full px-2 py-0.5 text-[10px] font-semibold"
      style={{ background: "#F1F5F9", color: "#64748B" }}>
      Draft
    </span>
  );
}

// ─── Add Event modal ─────────────────────────────────────────────────────────

function AddEventModal({ vehicleId, templates, onClose, onCreated }) {
  const [templateId, setTemplateId] = useState("");
  const [amount, setAmount] = useState("");
  const [entryDate, setEntryDate] = useState(new Date().toISOString().slice(0, 10));
  const [basis, setBasis] = useState("GAAP");
  const [memberSeriesId, setMemberSeriesId] = useState("");
  const [investmentId, setInvestmentId] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState(null);

  const selectedTmpl = templates.find((t) => t.id === templateId);
  const needsMemberSeries = selectedTmpl?.lines?.some(
    (l) => l.dimension_source === "member_series",
  );
  const needsInvestment = selectedTmpl?.lines?.some(
    (l) => l.dimension_source === "investment",
  );

  async function handleSubmit(e) {
    e.preventDefault();
    setError(null);
    setSaving(true);
    try {
      const dims = {};
      if (needsMemberSeries && memberSeriesId) dims.member_series_id = memberSeriesId;
      if (needsInvestment && investmentId) dims.investment_id = investmentId;

      const res = await fetch("/api/ledger/entries", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          vehicle_id: vehicleId,
          transaction_type_code: selectedTmpl.transaction_type_code,
          entry_date: entryDate,
          amount: parseFloat(amount),
          dims,
          basis,
        }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || data.detail || "Failed to create entry");
      onCreated(data);
      onClose();
    } catch (err) {
      setError(err.message);
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30">
      <div className="w-full max-w-md rounded-lg border border-[#ece8dd] bg-white p-6 shadow-lg">
        <h2 className="mb-4 text-base font-semibold text-[#1B2B4B]"
          style={{ fontFamily: "Spectral, Georgia, serif" }}>
          Add Journal Event
        </h2>
        {error && (
          <div className="mb-3 rounded bg-[#FEF3F2] p-2 text-xs text-[#9B2335]">{error}</div>
        )}
        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <label className="block text-xs font-semibold uppercase tracking-wide text-[#64748B] mb-1">
              Transaction Type
            </label>
            <select
              className="w-full rounded border border-[#E2E8F0] px-3 py-1.5 text-sm text-[#0F172A] focus:outline-none focus:ring-1 focus:ring-[#C5A880]"
              value={templateId}
              onChange={(e) => setTemplateId(e.target.value)}
              required
            >
              <option value="">Select…</option>
              {templates.map((t) => (
                <option key={t.id} value={t.id}>
                  {t.name}
                </option>
              ))}
            </select>
          </div>

          <div>
            <label className="block text-xs font-semibold uppercase tracking-wide text-[#64748B] mb-1">
              Amount
            </label>
            <input
              type="number"
              step="0.01"
              min="0.01"
              className="w-full rounded border border-[#E2E8F0] px-3 py-1.5 text-sm text-[#0F172A] focus:outline-none focus:ring-1 focus:ring-[#C5A880]"
              value={amount}
              onChange={(e) => setAmount(e.target.value)}
              required
            />
          </div>

          <div>
            <label className="block text-xs font-semibold uppercase tracking-wide text-[#64748B] mb-1">
              Entry Date
            </label>
            <input
              type="date"
              className="w-full rounded border border-[#E2E8F0] px-3 py-1.5 text-sm text-[#0F172A] focus:outline-none focus:ring-1 focus:ring-[#C5A880]"
              value={entryDate}
              onChange={(e) => setEntryDate(e.target.value)}
              required
            />
          </div>

          <div>
            <label className="block text-xs font-semibold uppercase tracking-wide text-[#64748B] mb-1">
              Basis
            </label>
            <select
              className="w-full rounded border border-[#E2E8F0] px-3 py-1.5 text-sm text-[#0F172A] focus:outline-none focus:ring-1 focus:ring-[#C5A880]"
              value={basis}
              onChange={(e) => setBasis(e.target.value)}
            >
              <option value="GAAP">GAAP</option>
              <option value="TAX">TAX</option>
            </select>
          </div>

          {needsMemberSeries && (
            <div>
              <label className="block text-xs font-semibold uppercase tracking-wide text-[#64748B] mb-1">
                Class ID (member series)
              </label>
              <input
                type="text"
                className="w-full rounded border border-[#E2E8F0] px-3 py-1.5 text-sm font-mono text-[#0F172A] focus:outline-none focus:ring-1 focus:ring-[#C5A880]"
                value={memberSeriesId}
                onChange={(e) => setMemberSeriesId(e.target.value)}
                placeholder="UUID"
              />
            </div>
          )}

          {needsInvestment && (
            <div>
              <label className="block text-xs font-semibold uppercase tracking-wide text-[#64748B] mb-1">
                Investment ID
              </label>
              <input
                type="text"
                className="w-full rounded border border-[#E2E8F0] px-3 py-1.5 text-sm font-mono text-[#0F172A] focus:outline-none focus:ring-1 focus:ring-[#C5A880]"
                value={investmentId}
                onChange={(e) => setInvestmentId(e.target.value)}
                placeholder="UUID"
              />
            </div>
          )}

          {selectedTmpl && (
            <div className="rounded border border-[#ece8dd] bg-[#FAF9F6] p-3">
              <p className="text-[10px] font-semibold uppercase tracking-wide text-[#64748B] mb-1.5">
                Journal Preview
              </p>
              <table className="w-full text-xs">
                <thead>
                  <tr className="text-[#64748B]">
                    <th className="text-left font-medium pb-1">Account</th>
                    <th className="text-right font-medium">Debit</th>
                    <th className="text-right font-medium">Credit</th>
                  </tr>
                </thead>
                <tbody>
                  {selectedTmpl.lines.map((ln, i) => (
                    <tr key={i} className="border-t border-[#E2E8F0]">
                      <td className="py-0.5 text-[#334155]">
                        {ln.account_code} {ln.account_name}
                      </td>
                      <td className="text-right tabular-nums text-[#0F172A]">
                        {ln.dr_cr === "D" ? fmtMoney(parseFloat(amount) || 0) : ""}
                      </td>
                      <td className="text-right tabular-nums text-[#0F172A]">
                        {ln.dr_cr === "C" ? fmtMoney(parseFloat(amount) || 0) : ""}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          <div className="flex justify-end gap-2 pt-1">
            <button
              type="button"
              onClick={onClose}
              className="rounded border border-[#E2E8F0] px-4 py-1.5 text-sm text-[#64748B] hover:bg-[#F5F1EB]"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={saving}
              className="rounded bg-[#1B2B4B] px-4 py-1.5 text-sm font-medium text-white hover:bg-[#2a3f6f] disabled:opacity-50"
            >
              {saving ? "Saving…" : "Create Draft"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

// ─── Reverse modal ────────────────────────────────────────────────────────────

function ReverseModal({ entry, onClose, onReversed }) {
  const [reason, setReason] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState(null);

  async function handleSubmit(e) {
    e.preventDefault();
    setError(null);
    setSaving(true);
    try {
      const res = await fetch(`/api/ledger/entries/${entry.id}/reverse`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ reason }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || data.detail || "Reversal failed");
      onReversed(data);
      onClose();
    } catch (err) {
      setError(err.message);
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30">
      <div className="w-full max-w-sm rounded-lg border border-[#ece8dd] bg-white p-6 shadow-lg">
        <h2 className="mb-3 text-base font-semibold text-[#1B2B4B]"
          style={{ fontFamily: "Spectral, Georgia, serif" }}>
          Reverse Entry
        </h2>
        {error && (
          <div className="mb-3 rounded bg-[#FEF3F2] p-2 text-xs text-[#9B2335]">{error}</div>
        )}
        <form onSubmit={handleSubmit} className="space-y-3">
          <div>
            <label className="block text-xs font-semibold uppercase tracking-wide text-[#64748B] mb-1">
              Reason
            </label>
            <textarea
              rows={3}
              className="w-full rounded border border-[#E2E8F0] px-3 py-1.5 text-sm text-[#0F172A] focus:outline-none focus:ring-1 focus:ring-[#C5A880]"
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              required
            />
          </div>
          <div className="flex justify-end gap-2">
            <button
              type="button"
              onClick={onClose}
              className="rounded border border-[#E2E8F0] px-3 py-1.5 text-sm text-[#64748B] hover:bg-[#F5F1EB]"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={saving}
              className="rounded bg-[#9B2335] px-3 py-1.5 text-sm font-medium text-white hover:bg-[#7d1c2b] disabled:opacity-50"
            >
              {saving ? "Reversing…" : "Reverse"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

// ─── Journal panel (right pane) ───────────────────────────────────────────────

function JournalPanel({ entry, onPost, onReverse }) {
  if (!entry) {
    return (
      <div className="flex h-full items-center justify-center text-sm text-[#64748B]">
        Select an event to view its journal lines.
      </div>
    );
  }

  const lines = entry.lines || [];

  return (
    <div className="h-full overflow-auto p-5">
      <div className="mb-4 flex items-start justify-between gap-3">
        <div>
          <p className="text-[10px] font-semibold uppercase tracking-wide text-[#64748B]">
            {(entry.transaction_type_code || "").replace(/_/g, " ")}
          </p>
          <p className="text-sm font-medium text-[#0F172A]">{fmtDate(entry.entry_date)}</p>
          <p className="text-sm text-[#64748B]">{fmtMoney(entry.amount)}</p>
          {entry.template_name && (
            <p className="mt-0.5 text-xs text-[#64748B]">via {entry.template_name}</p>
          )}
        </div>
        <div className="flex flex-col items-end gap-2">
          <StatusPill posted={!!entry.posted_at} />
          {!entry.posted_at && (
            <button
              onClick={() => onPost(entry)}
              className="rounded bg-[#1B2B4B] px-3 py-1 text-xs font-medium text-white hover:bg-[#2a3f6f]"
            >
              Post
            </button>
          )}
          {entry.posted_at && !entry.reversed_by_entry_id && (
            <button
              onClick={() => onReverse(entry)}
              className="rounded border border-[#E2E8F0] px-3 py-1 text-xs text-[#9B2335] hover:bg-[#FEF3F2]"
            >
              Reverse
            </button>
          )}
        </div>
      </div>

      {lines.length > 0 ? (
        <table className="w-full text-xs">
          <thead>
            <tr className="border-b border-[#E2E8F0]">
              <th className="pb-1.5 text-left font-semibold text-[#64748B]">Account</th>
              <th className="pb-1.5 text-right font-semibold text-[#64748B]">Debit</th>
              <th className="pb-1.5 text-right font-semibold text-[#64748B]">Credit</th>
            </tr>
          </thead>
          <tbody>
            {lines.map((ln) => (
              <tr key={ln.id} className="border-t border-[#E2E8F0]">
                <td className="py-1 text-[#334155]">
                  {ln.account_code ? `${ln.account_code} ` : ""}
                  {ln.account_name || ln.account_id}
                </td>
                <td className="py-1 text-right tabular-nums text-[#0F172A]">
                  {ln.debit != null ? fmtMoney(ln.debit) : ""}
                </td>
                <td className="py-1 text-right tabular-nums text-[#0F172A]">
                  {ln.credit != null ? fmtMoney(ln.credit) : ""}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : (
        <p className="text-xs text-[#64748B]">No lines attached to this entry.</p>
      )}

      <dl className="mt-4 space-y-1 text-[10px]">
        {entry.basis && (
          <div className="flex gap-2">
            <dt className="font-semibold uppercase tracking-wide text-[#64748B]">Basis</dt>
            <dd className="text-[#334155]">{entry.basis}</dd>
          </div>
        )}
        {entry.reverses_entry_id && (
          <div className="flex gap-2">
            <dt className="font-semibold uppercase tracking-wide text-[#64748B]">Reverses</dt>
            <dd className="font-mono text-[#C5A880]">{entry.reverses_entry_id}</dd>
          </div>
        )}
        {entry.reversed_by_entry_id && (
          <div className="flex gap-2">
            <dt className="font-semibold uppercase tracking-wide text-[#64748B]">Reversed by</dt>
            <dd className="font-mono text-[#9B2335]">{entry.reversed_by_entry_id}</dd>
          </div>
        )}
      </dl>
    </div>
  );
}

// ─── Trial balance sub-tab ────────────────────────────────────────────────────

function TrialBalanceTab({ vehicleId, basis }) {
  const [rows, setRows] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  useEffect(() => {
    setLoading(true);
    setError(null);
    fetch(`/api/ledger/trial-balance?vehicle_id=${vehicleId}&basis=${basis}`)
      .then((r) => r.json())
      .then((d) => {
        if (d.error) throw new Error(d.error);
        setRows(Array.isArray(d) ? d : []);
      })
      .catch((err) => setError(err.message))
      .finally(() => setLoading(false));
  }, [vehicleId, basis]);

  if (loading) return <p className="p-4 text-sm text-[#64748B]">Loading…</p>;
  if (error) return <p className="p-4 text-sm text-[#9B2335]">{error}</p>;
  if (!rows?.length)
    return <p className="p-4 text-sm text-[#64748B]">No data — post at least one entry.</p>;

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-xs">
        <thead>
          <tr className="border-b border-[#E2E8F0]">
            <th className="py-2 text-left font-semibold text-[#64748B]">Code</th>
            <th className="py-2 text-left font-semibold text-[#64748B]">Account</th>
            <th className="py-2 text-right font-semibold text-[#64748B]">Total Debit</th>
            <th className="py-2 text-right font-semibold text-[#64748B]">Total Credit</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i} className="border-t border-[#E2E8F0]">
              <td className="py-1.5 font-mono text-[#64748B]">{r.account_code}</td>
              <td className="py-1.5 text-[#334155]">{r.account_name}</td>
              <td className="py-1.5 text-right tabular-nums text-[#0F172A]">
                {r.total_debit != null ? fmtMoney(r.total_debit) : ""}
              </td>
              <td className="py-1.5 text-right tabular-nums text-[#0F172A]">
                {r.total_credit != null ? fmtMoney(r.total_credit) : ""}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// ─── Capital accounts sub-tab ─────────────────────────────────────────────────

function CapitalAccountsTab({ vehicleId, basis }) {
  const [rows, setRows] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  useEffect(() => {
    setLoading(true);
    setError(null);
    fetch(`/api/ledger/capital-accounts?vehicle_id=${vehicleId}&basis=${basis}`)
      .then((r) => r.json())
      .then((d) => {
        if (d.error) throw new Error(d.error);
        setRows(Array.isArray(d) ? d : []);
      })
      .catch((err) => setError(err.message))
      .finally(() => setLoading(false));
  }, [vehicleId, basis]);

  if (loading) return <p className="p-4 text-sm text-[#64748B]">Loading…</p>;
  if (error) return <p className="p-4 text-sm text-[#9B2335]">{error}</p>;
  if (!rows?.length)
    return (
      <p className="p-4 text-sm text-[#64748B]">
        No capital accounts — post a contribution or distribution first.
      </p>
    );

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-xs">
        <thead>
          <tr className="border-b border-[#E2E8F0]">
            <th className="py-2 text-left font-semibold text-[#64748B]">Class</th>
            <th className="py-2 text-left font-semibold text-[#64748B]">Account</th>
            <th className="py-2 text-right font-semibold text-[#64748B]">Balance</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i} className="border-t border-[#E2E8F0]">
              <td className="py-1.5 font-mono text-[10px] text-[#64748B]">
                {r.dim_member_series_id || "—"}
              </td>
              <td className="py-1.5 text-[#334155]">{r.account_name || r.account_code}</td>
              <td className="py-1.5 text-right tabular-nums text-[#0F172A]">
                {fmtMoney(r.balance)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// ─── Main client component ────────────────────────────────────────────────────

export default function SPVLedgerClient({ vehicleId }) {
  const [entries, setEntries] = useState([]);
  const [templates, setTemplates] = useState([]);
  const [selectedEntry, setSelectedEntry] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [showAddModal, setShowAddModal] = useState(false);
  const [reverseEntry, setReverseEntry] = useState(null);
  const [reporting, setReporting] = useState(null); // null | "trial-balance" | "capital-accounts"
  const [basis, setBasis] = useState("GAAP");

  const loadEntries = useCallback(async () => {
    if (!vehicleId) return;
    try {
      const res = await fetch(`/api/ledger/entries?vehicle_id=${vehicleId}`);
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || "Failed to load entries");
      setEntries(Array.isArray(data) ? data : []);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }, [vehicleId]);

  useEffect(() => {
    if (!vehicleId) return;
    fetch(`/api/ledger/templates?vehicle_id=${vehicleId}`)
      .then((r) => r.json())
      .then((d) => setTemplates(Array.isArray(d) ? d : []))
      .catch(() => {});
    loadEntries();
  }, [vehicleId, loadEntries]);

  async function handlePost(entry) {
    try {
      const res = await fetch(`/api/ledger/entries/${entry.id}/post`, { method: "POST" });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || data.detail || "Post failed");
      setEntries((prev) => prev.map((e) => (e.id === data.id ? data : e)));
      if (selectedEntry?.id === data.id) setSelectedEntry(data);
    } catch (err) {
      alert(err.message);
    }
  }

  function handleEntryCreated(entry) {
    setEntries((prev) => [entry, ...prev]);
    setSelectedEntry(entry);
  }

  function handleReversed(newEntry) {
    setEntries((prev) => [newEntry, ...prev]);
  }

  if (loading) {
    return (
      <div className="flex h-48 items-center justify-center text-sm text-[#64748B]">
        Loading ledger…
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-6xl">
      {/* Header */}
      <div className="mb-4 flex items-center justify-between">
        <div>
          <a
            href={`/spvs/${vehicleId}`}
            className="text-xs text-[#64748B] hover:text-[#C5A880]"
          >
            ← SPV Detail
          </a>
          <h1
            className="mt-1 text-xl font-light text-[#1B2B4B]"
            style={{ fontFamily: "Spectral, Georgia, serif" }}
          >
            General Ledger
          </h1>
        </div>
        <div className="flex items-center gap-3">
          <select
            value={basis}
            onChange={(e) => setBasis(e.target.value)}
            className="rounded border border-[#E2E8F0] px-2 py-1 text-xs text-[#334155] focus:outline-none focus:ring-1 focus:ring-[#C5A880]"
          >
            <option value="GAAP">GAAP</option>
            <option value="TAX">TAX</option>
          </select>
          <button
            onClick={() => setShowAddModal(true)}
            className="rounded bg-[#1B2B4B] px-4 py-1.5 text-sm font-medium text-white hover:bg-[#2a3f6f]"
          >
            Add Event
          </button>
        </div>
      </div>

      {error && (
        <div className="mb-4 rounded bg-[#FEF3F2] p-3 text-sm text-[#9B2335]">{error}</div>
      )}

      {/* Sub-tabs */}
      <div className="mb-4 flex gap-4 border-b border-[#E2E8F0]">
        {[
          { key: null, label: "Events" },
          { key: "trial-balance", label: "Trial Balance" },
          { key: "capital-accounts", label: "Capital Accounts" },
        ].map((t) => (
          <button
            key={String(t.key)}
            onClick={() => setReporting(t.key)}
            className={`pb-2 text-sm font-medium transition-colors ${
              reporting === t.key
                ? "border-b-2 border-[#C5A880] text-[#1B2B4B]"
                : "text-[#64748B] hover:text-[#0F172A]"
            }`}
          >
            {t.label}
          </button>
        ))}
      </div>

      {/* Reporting views */}
      {reporting === "trial-balance" && (
        <div className="rounded-lg border border-[#ece8dd] bg-white p-5">
          <TrialBalanceTab vehicleId={vehicleId} basis={basis} />
        </div>
      )}
      {reporting === "capital-accounts" && (
        <div className="rounded-lg border border-[#ece8dd] bg-white p-5">
          <CapitalAccountsTab vehicleId={vehicleId} basis={basis} />
        </div>
      )}

      {/* Two-pane events view */}
      {reporting === null && (
        <div className="grid grid-cols-5 gap-4" style={{ height: "calc(100vh - 240px)" }}>
          {/* Left — Events list */}
          <div className="col-span-2 overflow-auto rounded-lg border border-[#ece8dd] bg-white">
            {entries.length === 0 ? (
              <div className="flex h-32 items-center justify-center text-sm text-[#64748B]">
                No entries. Click Add Event to begin.
              </div>
            ) : (
              <table className="w-full text-xs">
                <thead className="sticky top-0 border-b border-[#E2E8F0] bg-white">
                  <tr>
                    <th className="px-4 py-2 text-left font-semibold text-[#64748B]">Date</th>
                    <th className="px-2 py-2 text-left font-semibold text-[#64748B]">Type</th>
                    <th className="px-4 py-2 text-right font-semibold text-[#64748B]">Amount</th>
                    <th className="px-2 py-2" />
                  </tr>
                </thead>
                <tbody>
                  {entries.map((e) => (
                    <tr
                      key={e.id}
                      onClick={() => setSelectedEntry(e)}
                      className={`cursor-pointer border-t border-[#E2E8F0] hover:bg-[#FAF9F6] ${
                        selectedEntry?.id === e.id ? "bg-[#FAF9F6]" : ""
                      }`}
                    >
                      <td className="px-4 py-2 text-[#64748B]">{fmtDate(e.entry_date)}</td>
                      <td className="px-2 py-2 text-[#334155]">
                        {(e.transaction_type_code || "").replace(/_/g, " ")}
                      </td>
                      <td className="px-4 py-2 text-right tabular-nums text-[#0F172A]">
                        {fmtMoney(e.amount)}
                      </td>
                      <td className="px-2 py-2">
                        <StatusPill posted={!!e.posted_at} />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>

          {/* Right — Journal preview */}
          <div className="col-span-3 overflow-auto rounded-lg border border-[#ece8dd] bg-white">
            <JournalPanel
              entry={selectedEntry}
              onPost={handlePost}
              onReverse={(e) => setReverseEntry(e)}
            />
          </div>
        </div>
      )}

      {showAddModal && (
        <AddEventModal
          vehicleId={vehicleId}
          templates={templates}
          onClose={() => setShowAddModal(false)}
          onCreated={handleEntryCreated}
        />
      )}
      {reverseEntry && (
        <ReverseModal
          entry={reverseEntry}
          onClose={() => setReverseEntry(null)}
          onReversed={handleReversed}
        />
      )}
    </div>
  );
}
