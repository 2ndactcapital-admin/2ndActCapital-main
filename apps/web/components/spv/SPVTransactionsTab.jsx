"use client";

import { useEffect, useState, useCallback } from "react";

// ─── Formatters ────────────────────────────────────────────────────────────
function formatMoney(val) {
  if (val == null || val === "") return "—";
  const n = typeof val === "string" ? parseFloat(val) : val;
  if (isNaN(n)) return "—";
  return new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" }).format(n);
}

function formatDate(val) {
  if (!val) return "—";
  try {
    return new Date(val + "T00:00:00").toLocaleDateString("en-US", {
      month: "short",
      day: "numeric",
      year: "numeric",
    });
  } catch {
    return val;
  }
}

// ─── Type badge config ──────────────────────────────────────────────────────
const TYPE_CONFIG = {
  capital_call: { label: "Capital Call", bg: "#1B2B4B", text: "#FFFFFF" },
  call_investment: { label: "Capital Call", bg: "#1B2B4B", text: "#FFFFFF" },
  distribution: { label: "Distribution", bg: "#E8F5E9", text: "#2D6A4F" },
  dist_standard: { label: "Distribution", bg: "#E8F5E9", text: "#2D6A4F" },
  dist_recallable: { label: "Dist. (Recallable)", bg: "#FDF8EE", text: "#C5A880" },
  fee: { label: "Fee", bg: "#F1F5F9", text: "#64748B" },
  management_fee: { label: "Mgmt Fee", bg: "#F1F5F9", text: "#64748B" },
  return_of_capital: { label: "Return of Capital", bg: "#EFF6FF", text: "#1D4ED8" },
};

function TypeBadge({ type }) {
  const cfg = TYPE_CONFIG[type] || { label: (type || "").replace(/_/g, " "), bg: "#F1F5F9", text: "#64748B" };
  return (
    <span
      className="inline-block rounded px-2 py-0.5 text-[10px] font-semibold tracking-wide"
      style={{ backgroundColor: cfg.bg, color: cfg.text }}
    >
      {cfg.label}
    </span>
  );
}

// ─── Status pill config ─────────────────────────────────────────────────────
const STATUS_CONFIG = {
  draft: { label: "Draft", bg: "#F1F5F9", text: "#64748B" },
  allocated: { label: "Allocated", bg: "#FDF8EE", text: "#C5A880" },
  posted: { label: "Posted", bg: "#1B2B4B", text: "#FFFFFF" },
  void: { label: "Void", bg: "#FEF3F2", text: "#9B2335" },
};

function StatusPill({ status }) {
  const cfg = STATUS_CONFIG[status] || { label: status, bg: "#F1F5F9", text: "#64748B" };
  return (
    <span
      className="inline-block rounded-full px-2.5 py-0.5 text-[10px] font-medium"
      style={{ backgroundColor: cfg.bg, color: cfg.text }}
    >
      {cfg.label}
    </span>
  );
}

// ─── Error extractor ────────────────────────────────────────────────────────
function extractErrorMessage(err) {
  if (!err) return "Request failed";
  if (typeof err === "string") return err;
  if (Array.isArray(err)) {
    const first = err[0];
    if (first?.msg) {
      const loc = Array.isArray(first.loc) ? first.loc.join(" → ") : "";
      return loc ? `${loc}: ${first.msg}` : first.msg;
    }
  }
  if (err.detail) return extractErrorMessage(err.detail);
  if (err.message) return err.message;
  return String(err);
}

// ─── Allocation sub-table ────────────────────────────────────────────────────
function AllocationRow({ spvId, txnId, txnAmount }) {
  const [allocations, setAllocations] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  useEffect(() => {
    setLoading(true);
    fetch(`/api/spvs/${spvId}/transactions/${txnId}/allocations`, { cache: "no-store" })
      .then((r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.json();
      })
      .then((data) => setAllocations(Array.isArray(data) ? data : []))
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, [spvId, txnId]);

  if (loading) {
    return (
      <tr>
        <td colSpan={7} className="pb-2 pt-0">
          <div className="mx-6 rounded bg-[#FAF9F6] px-4 py-3 text-xs text-[#64748B]">
            Loading allocations…
          </div>
        </td>
      </tr>
    );
  }

  if (error) {
    return (
      <tr>
        <td colSpan={7} className="pb-2 pt-0">
          <div className="mx-6 rounded bg-[#FEF3F2] px-4 py-3 text-xs text-[#9B2335]">
            Failed to load allocations: {error}
          </div>
        </td>
      </tr>
    );
  }

  if (!allocations || allocations.length === 0) {
    return (
      <tr>
        <td colSpan={7} className="pb-2 pt-0">
          <div className="mx-6 rounded bg-[#FAF9F6] px-4 py-3 text-xs text-[#64748B]">
            No allocations recorded.
          </div>
        </td>
      </tr>
    );
  }

  const sum = allocations.reduce((acc, a) => acc + (parseFloat(a.allocated_amount) || 0), 0);
  const target = parseFloat(txnAmount) || 0;
  const balanced = target > 0 && Math.abs(sum - target) < 0.005;

  return (
    <tr>
      <td colSpan={7} className="pb-3 pt-0">
        <div className="mx-6 rounded border border-[#ece8dd] bg-[#FAF9F6]">
          <table className="w-full text-xs">
            <thead>
              <tr className="border-b border-[#ece8dd]">
                <th className="py-2 pl-3 text-left font-semibold uppercase tracking-wide text-[#64748B]">
                  Entity
                </th>
                <th className="py-2 text-right font-semibold uppercase tracking-wide text-[#64748B]">
                  Ownership %
                </th>
                <th className="py-2 pr-3 text-right font-semibold uppercase tracking-wide text-[#64748B]">
                  Allocated Amount
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-[#ece8dd]">
              {allocations.map((a, i) => (
                <tr key={i}>
                  <td className="py-1.5 pl-3 text-[#0F172A]">{a.entity_name || a.entity_id}</td>
                  <td className="py-1.5 text-right tabular-nums text-[#334155]">
                    {a.ownership_pct != null ? `${parseFloat(a.ownership_pct).toFixed(2)}%` : "—"}
                  </td>
                  <td className="py-1.5 pr-3 text-right tabular-nums text-[#0F172A]">
                    {formatMoney(a.allocated_amount)}
                  </td>
                </tr>
              ))}
            </tbody>
            <tfoot>
              <tr className="border-t border-[#ece8dd]">
                <td
                  colSpan={2}
                  className="py-2 pl-3 text-xs font-semibold text-[#334155]"
                >
                  Total
                  {balanced && (
                    <span className="ml-1.5 text-[#2D6A4F]">&#10003;</span>
                  )}
                </td>
                <td
                  className="py-2 pr-3 text-right tabular-nums font-semibold text-[#0F172A]"
                >
                  {formatMoney(sum)}
                </td>
              </tr>
            </tfoot>
          </table>
        </div>
      </td>
    </tr>
  );
}

// ─── Add Transaction modal ───────────────────────────────────────────────────

function AddTransactionModal({ spvId, onClose, onCreated }) {
  const [txnTypes, setTxnTypes] = useState([]);
  const [currencies, setCurrencies] = useState([]);
  const [form, setForm] = useState({
    transaction_type_id: "",
    txn_date: "",
    amount: "",
    currency_code: "USD",
    reference: "",
    description: "",
  });
  const [selectedType, setSelectedType] = useState(null);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState(null);

  useEffect(() => {
    fetch("/api/transaction-types", { cache: "no-store" })
      .then((r) => r.ok ? r.json() : [])
      .then((data) => setTxnTypes(Array.isArray(data) ? data : []));
    fetch("/api/reference/currency", { cache: "no-store" })
      .then((r) => r.ok ? r.json() : { items: [] })
      .then((data) => setCurrencies(data.items || []));
  }, []);

  function handleChange(e) {
    const { name, value } = e.target;
    if (name === "transaction_type_id") {
      const t = txnTypes.find((x) => x.id === value);
      setSelectedType(t || null);
    }
    setForm((prev) => ({ ...prev, [name]: value }));
  }

  const amountLabel = selectedType
    ? { currency: "Amount", units: "Units", percent: "Percent (%)" }[selectedType.amount_basis] || "Amount"
    : "Amount";

  async function handleSubmit(e) {
    e.preventDefault();
    if (!form.transaction_type_id || !form.txn_date || !form.amount) return;
    setSubmitting(true);
    setError(null);
    try {
      const res = await fetch(`/api/spvs/${spvId}/transactions`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          transaction_type_id: form.transaction_type_id,
          txn_date: form.txn_date,
          amount: parseFloat(form.amount),
          currency_code: form.currency_code || "USD",
          reference: form.reference || null,
          description: form.description || null,
        }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw extractErrorMessage(data.error ?? data.detail ?? data);
      onCreated();
      onClose();
    } catch (err) {
      setError(typeof err === "string" ? err : extractErrorMessage(err));
    } finally {
      setSubmitting(false);
    }
  }

  const fieldClass =
    "w-full rounded border border-[#E2E8F0] bg-white px-3 py-2 text-sm text-[#0F172A] focus:outline-none focus:ring-1 focus:ring-[#C5A880]";
  const labelClass = "mb-1 block text-xs font-medium text-[#334155]";

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40">
      <div className="w-full max-w-lg rounded-xl bg-white p-6 shadow-lg">
        <h2
          className="mb-5 text-lg font-light text-[#1B2B4B]"
          style={{ fontFamily: "Spectral, Georgia, serif" }}
        >
          Add Transaction
        </h2>
        <form onSubmit={handleSubmit} className="space-y-3">
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className={labelClass}>Type *</label>
              <select
                name="transaction_type_id"
                value={form.transaction_type_id}
                onChange={handleChange}
                required
                className={fieldClass}
              >
                <option value="" disabled>Select type</option>
                {txnTypes.map((t) => (
                  <option key={t.id} value={t.id}>{t.label}</option>
                ))}
              </select>
            </div>
            <div>
              <label className={labelClass}>Date *</label>
              <input
                type="date"
                name="txn_date"
                value={form.txn_date}
                onChange={handleChange}
                required
                className={fieldClass}
              />
            </div>
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className={labelClass}>{amountLabel} *</label>
              <input
                type="number"
                name="amount"
                value={form.amount}
                onChange={handleChange}
                min="0.01"
                step="0.01"
                required
                placeholder="0.00"
                className={fieldClass + " tabular-nums"}
              />
            </div>
            <div>
              <label className={labelClass}>Currency</label>
              <select
                name="currency_code"
                value={form.currency_code}
                onChange={handleChange}
                className={fieldClass}
              >
                {currencies.length === 0 && (
                  <option value="USD">USD</option>
                )}
                {currencies.map((c) => (
                  <option key={c.code} value={c.code}>
                    {c.extra?.symbol ? `${c.extra.symbol} ${c.code}` : c.code}
                  </option>
                ))}
              </select>
            </div>
          </div>
          {selectedType && (
            <p className="text-[11px] text-[#64748B]">
              {selectedType.direction === "debit" ? "Debit" : "Credit"} ·{" "}
              {selectedType.category}
              {selectedType.is_recallable && " · Recallable"}
            </p>
          )}
          <div>
            <label className={labelClass}>Reference</label>
            <input
              type="text"
              name="reference"
              value={form.reference}
              onChange={handleChange}
              placeholder="e.g. CC-2026-001"
              className={fieldClass}
            />
          </div>
          <div>
            <label className={labelClass}>Description</label>
            <input
              type="text"
              name="description"
              value={form.description}
              onChange={handleChange}
              placeholder="Optional description"
              className={fieldClass}
            />
          </div>
          {error && (
            <p className="rounded bg-[#FEF3F2] px-3 py-2 text-xs text-[#9B2335]">{error}</p>
          )}
          <div className="flex justify-end gap-3 pt-2">
            <button
              type="button"
              onClick={onClose}
              className="rounded-md px-4 py-2 text-sm text-[#64748B] hover:text-[#0F172A]"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={submitting || !form.transaction_type_id || !form.txn_date || !form.amount}
              className="rounded-md px-4 py-2 text-sm font-medium text-white disabled:opacity-50"
              style={{ backgroundColor: "#1B2B4B" }}
            >
              {submitting ? "Adding…" : "Add Transaction"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

// ─── Main component ──────────────────────────────────────────────────────────
export default function SPVTransactionsTab({ spvId, staff = false, spvName, totalCommitted }) {
  const [transactions, setTransactions] = useState([]);
  const [ledger, setLedger] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [expandedIds, setExpandedIds] = useState(new Set());
  const [addOpen, setAddOpen] = useState(false);
  const [actionError, setActionError] = useState(null);

  const fetchTransactions = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await fetch(`/api/spvs/${spvId}/transactions`, { cache: "no-store" });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      setTransactions(Array.isArray(data) ? data : []);
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, [spvId]);

  const fetchLedger = useCallback(async () => {
    if (!staff) return;
    try {
      const res = await fetch(`/api/spvs/${spvId}/ledger`, { cache: "no-store" });
      if (!res.ok) return;
      const data = await res.json();
      setLedger(data);
    } catch {
      // non-critical; ledger summary is staff-only
    }
  }, [spvId, staff]);

  useEffect(() => {
    fetchTransactions();
    fetchLedger();
  }, [fetchTransactions, fetchLedger]);

  function toggleExpand(txnId) {
    setExpandedIds((prev) => {
      const next = new Set(prev);
      if (next.has(txnId)) {
        next.delete(txnId);
      } else {
        next.add(txnId);
      }
      return next;
    });
  }

  async function handleAction(action, txn) {
    setActionError(null);

    if (action === "void") {
      // Optimistic: remove immediately
      setTransactions((prev) => prev.filter((t) => t.id !== txn.id));
      try {
        const res = await fetch(`/api/spvs/${spvId}/transactions/${txn.id}/void`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) {
          // Restore on error
          setTransactions((prev) => {
            const copy = [...prev];
            // Re-insert in original order by id sort or append
            return [...copy, { ...txn, status: "void" }];
          });
          setActionError(extractErrorMessage(data.error ?? data.detail ?? data));
        } else {
          await fetchTransactions();
          await fetchLedger();
        }
      } catch (e) {
        setTransactions((prev) => [...prev, { ...txn }]);
        setActionError(e.message);
      }
      return;
    }

    const endpointMap = {
      allocate: `allocate`,
      post: `post`,
    };
    const endpoint = endpointMap[action];
    if (!endpoint) return;

    try {
      const res = await fetch(`/api/spvs/${spvId}/transactions/${txn.id}/${endpoint}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw extractErrorMessage(data.error ?? data.detail ?? data);
      await fetchTransactions();
      await fetchLedger();
    } catch (err) {
      setActionError(typeof err === "string" ? err : extractErrorMessage(err));
    }
  }

  // ── Action buttons per status ──
  function ActionButtons({ txn }) {
    if (!staff) return null;
    const btnBase =
      "rounded px-2.5 py-1 text-[11px] font-medium transition-colors";
    const navyBtn = `${btnBase} text-white`;
    const ghostBtn = `${btnBase} border border-[#E2E8F0] text-[#334155] hover:border-[#C5A880] hover:text-[#1B2B4B]`;
    const redBtn = `${btnBase} text-[#9B2335] border border-[#FECDD3] hover:bg-[#FEF3F2]`;

    if (txn.status === "draft") {
      return (
        <span className="flex items-center gap-1.5">
          <button
            className={navyBtn}
            style={{ backgroundColor: "#1B2B4B" }}
            onClick={() => handleAction("allocate", txn)}
          >
            Allocate
          </button>
          <button className={ghostBtn} onClick={() => {}}>
            Edit
          </button>
          <button className={redBtn} onClick={() => handleAction("void", txn)}>
            Void
          </button>
        </span>
      );
    }
    if (txn.status === "allocated") {
      return (
        <span className="flex items-center gap-1.5">
          <button
            className={navyBtn}
            style={{ backgroundColor: "#1B2B4B" }}
            onClick={() => handleAction("post", txn)}
          >
            Post
          </button>
          <button className={redBtn} onClick={() => handleAction("void", txn)}>
            Void
          </button>
        </span>
      );
    }
    if (txn.status === "posted") {
      return (
        <button
          className={ghostBtn}
          onClick={() => toggleExpand(txn.id)}
        >
          {expandedIds.has(txn.id) ? "Hide" : "View Allocations"}
        </button>
      );
    }
    return <span className="text-[#94A3B8]">—</span>;
  }

  // ── Summary header ──
  function LedgerSummary() {
    if (!staff || !ledger) return null;
    const s = ledger.summary || ledger;
    const items = [
      ...(totalCommitted != null ? [{ label: "Committed", value: totalCommitted }] : []),
      { label: "Total Called", value: s.total_called },
      { label: "Total Distributed", value: s.total_distributed },
      ...(s.total_recallable > 0 ? [{ label: "Recallable", value: s.total_recallable }] : []),
      { label: "Total Fees", value: s.total_fees },
      { label: "Net", value: s.net },
    ];
    return (
      <div
        className="mb-5 flex flex-wrap items-center gap-6 rounded-lg border border-[#ece8dd] bg-white px-5 py-3"
        style={{ boxShadow: "0 1px 3px rgba(0,0,0,0.06)" }}
      >
        <div className="mr-auto">
          <p
            className="text-[10px] font-semibold uppercase tracking-widest"
            style={{ color: "#C5A880" }}
          >
            Summary
          </p>
          <p className="mt-0.5 text-sm font-medium text-[#1B2B4B]">
            {spvName || "—"}
          </p>
        </div>
        {items.map((item) => (
          <div key={item.label} className="text-right">
            <p
              className="text-[10px] font-semibold uppercase tracking-widest"
              style={{ color: "#C5A880" }}
            >
              {item.label}
            </p>
            <p className="mt-0.5 tabular-nums text-sm font-medium text-[#0F172A]">
              {formatMoney(item.value)}
            </p>
          </div>
        ))}
      </div>
    );
  }

  // ── Render ──
  return (
    <div>
      {/* Top bar */}
      <div className="mb-4 flex items-center justify-between">
        <h3
          className="text-base font-light text-[#1B2B4B]"
          style={{ fontFamily: "Spectral, Georgia, serif" }}
        >
          Transactions
        </h3>
        {staff && (
          <button
            type="button"
            onClick={() => setAddOpen(true)}
            className="rounded-md px-4 py-2 text-sm font-medium text-white"
            style={{ backgroundColor: "#1B2B4B" }}
          >
            Add Transaction
          </button>
        )}
      </div>

      {/* Ledger summary */}
      <LedgerSummary />

      {/* Action error banner */}
      {actionError && (
        <div className="mb-3 rounded bg-[#FEF3F2] px-4 py-2.5 text-xs text-[#9B2335]">
          {actionError}
          <button
            className="ml-3 underline"
            onClick={() => setActionError(null)}
          >
            Dismiss
          </button>
        </div>
      )}

      {/* Loading / error / empty */}
      {loading && (
        <p className="py-8 text-center text-sm text-[#64748B]">Loading transactions…</p>
      )}
      {!loading && error && (
        <p className="py-8 text-center text-sm text-[#9B2335]">Failed to load: {error}</p>
      )}
      {!loading && !error && transactions.length === 0 && (
        <p className="py-8 text-center text-sm text-[#64748B]">No transactions recorded.</p>
      )}

      {/* Table */}
      {!loading && !error && transactions.length > 0 && (
        <div className="overflow-x-auto rounded-lg border border-[#ece8dd] bg-white" style={{ boxShadow: "0 1px 3px rgba(0,0,0,0.06)" }}>
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-[#ece8dd] bg-[#FAF9F6]">
                <th className="py-2.5 pl-4 pr-3 text-left text-[10px] font-semibold uppercase tracking-widest text-[#64748B]">
                  Date
                </th>
                <th className="px-3 py-2.5 text-left text-[10px] font-semibold uppercase tracking-widest text-[#64748B]">
                  Type
                </th>
                <th className="px-3 py-2.5 text-left text-[10px] font-semibold uppercase tracking-widest text-[#64748B]">
                  Reference
                </th>
                <th className="px-3 py-2.5 text-left text-[10px] font-semibold uppercase tracking-widest text-[#64748B]">
                  Description
                </th>
                <th className="px-3 py-2.5 text-right text-[10px] font-semibold uppercase tracking-widest text-[#64748B]">
                  Amount
                </th>
                <th className="px-3 py-2.5 text-left text-[10px] font-semibold uppercase tracking-widest text-[#64748B]">
                  Status
                </th>
                {staff && (
                  <th className="py-2.5 pl-3 pr-4 text-right text-[10px] font-semibold uppercase tracking-widest text-[#64748B]">
                    Actions
                  </th>
                )}
              </tr>
            </thead>
            <tbody>
              {transactions.map((txn) => (
                <>
                  <tr
                    key={txn.id}
                    className="border-b border-[#ece8dd] last:border-b-0 hover:bg-[#FAF9F6]"
                    style={{ cursor: "default" }}
                  >
                    <td
                      className="py-2.5 pl-4 pr-3 text-[#334155]"
                      style={{ whiteSpace: "nowrap" }}
                    >
                      {formatDate(txn.txn_date)}
                    </td>
                    <td className="px-3 py-2.5">
                      <TypeBadge type={txn.txn_type} />
                    </td>
                    <td className="px-3 py-2.5 text-[#334155]">
                      {txn.reference || <span className="text-[#94A3B8]">—</span>}
                    </td>
                    <td className="px-3 py-2.5 text-[#334155]">
                      {txn.description ? (
                        <span className="max-w-[220px] truncate block">{txn.description}</span>
                      ) : (
                        <span className="text-[#94A3B8]">—</span>
                      )}
                    </td>
                    <td className="px-3 py-2.5 text-right tabular-nums font-medium text-[#0F172A]">
                      {formatMoney(txn.amount)}
                    </td>
                    <td className="px-3 py-2.5">
                      <StatusPill status={txn.status} />
                    </td>
                    {staff && (
                      <td className="py-2.5 pl-3 pr-4 text-right">
                        <ActionButtons txn={txn} />
                      </td>
                    )}
                  </tr>
                  {expandedIds.has(txn.id) && (
                    <AllocationRow
                      key={`alloc-${txn.id}`}
                      spvId={spvId}
                      txnId={txn.id}
                      txnAmount={txn.amount}
                    />
                  )}
                </>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Add Transaction modal */}
      {addOpen && (
        <AddTransactionModal
          spvId={spvId}
          onClose={() => setAddOpen(false)}
          onCreated={async () => {
            await fetchTransactions();
            await fetchLedger();
          }}
        />
      )}
    </div>
  );
}
