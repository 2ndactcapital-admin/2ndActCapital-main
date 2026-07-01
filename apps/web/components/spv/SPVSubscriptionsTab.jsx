"use client";

import { useState } from "react";
import { formatCurrency, formatDate, formatPercent } from "@/lib/format";
import EntityPicker from "@/components/EntityPicker";

const STATUS_CONFIG = {
  soft: { label: "Soft", bg: "#F5F1EB", text: "#64748B" },
  pending: { label: "Pending", bg: "#EEF4FF", text: "#1B2B4B" },
  signed: { label: "Signed", bg: "#E8F5E9", text: "#2D6A4F" },
  funded: { label: "Funded", bg: "#E8F5E9", text: "#2D6A4F" },
  cancelled: { label: "Cancelled", bg: "#FEF3F2", text: "#9B2335" },
};

function StatusPill({ status }) {
  const cfg = STATUS_CONFIG[status] || { label: status, bg: "#F5F1EB", text: "#64748B" };
  return (
    <span
      className="rounded-full px-2 py-0.5 text-[10px] font-medium"
      style={{ backgroundColor: cfg.bg, color: cfg.text }}
    >
      {cfg.label}
    </span>
  );
}

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

export default function SPVSubscriptionsTab({ spvId, capTable: initialCapTable, staff = false }) {
  const [capTable, setCapTable] = useState(initialCapTable);
  const [addOpen, setAddOpen] = useState(false);
  const [selectedEntity, setSelectedEntity] = useState(null);
  const [amount, setAmount] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState(null);

  async function refreshCapTable() {
    try {
      const res = await fetch(`/api/spvs/${spvId}/captable`, { cache: "no-store" });
      if (res.ok) {
        const data = await res.json();
        setCapTable(data);
      }
    } catch {}
  }

  async function handleAdd(e) {
    e.preventDefault();
    if (!selectedEntity?.id || !amount) return;
    setSubmitting(true);
    setError(null);
    try {
      const res = await fetch(`/api/spvs/${spvId}/subscriptions`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          entity_id: selectedEntity.id,
          commitment_amount: Number(amount),
        }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw extractErrorMessage(data.error ?? data.detail ?? data);
      setAddOpen(false);
      setSelectedEntity(null);
      setAmount("");
      await refreshCapTable();
    } catch (err) {
      setError(typeof err === "string" ? err : extractErrorMessage(err));
    } finally {
      setSubmitting(false);
    }
  }

  if (!capTable) {
    return <p className="py-6 text-center text-sm text-[#64748B]">No cap table data available.</p>;
  }

  const { total_committed, target_raise, subscriptions = [] } = capTable;
  const pct = target_raise
    ? Math.min(100, Math.round((total_committed / target_raise) * 100))
    : null;

  return (
    <div>
      {/* Summary + Add Subscriber */}
      <div className="mb-4 flex items-start justify-between gap-4">
        <div>
          <p className="text-sm font-medium text-[#0F172A]">
            {formatCurrency(total_committed)} committed
          </p>
          {target_raise && (
            <p className="text-xs text-[#64748B]">
              {pct}% of {formatCurrency(target_raise)} target
            </p>
          )}
          {pct !== null && (
            <div className="mt-2 h-1.5 w-40 rounded-full bg-[#F5F1EB]">
              <div
                className="h-1.5 rounded-full"
                style={{ width: `${pct}%`, backgroundColor: "#C5A880" }}
              />
            </div>
          )}
        </div>
        {staff && (
          <button
            type="button"
            onClick={() => setAddOpen(true)}
            className="shrink-0 rounded-md px-4 py-2 text-sm font-medium text-white"
            style={{ backgroundColor: "#1B2B4B" }}
          >
            Add Subscriber
          </button>
        )}
      </div>

      {/* Subscriptions table */}
      {subscriptions.length === 0 ? (
        <p className="py-6 text-center text-sm text-[#64748B]">No subscriptions yet.</p>
      ) : (
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-[#E2E8F0]">
              <th className="py-2 text-left text-xs font-semibold uppercase tracking-wide text-[#64748B]">Subscriber</th>
              <th className="py-2 text-right text-xs font-semibold uppercase tracking-wide text-[#64748B]">Committed</th>
              <th className="py-2 text-right text-xs font-semibold uppercase tracking-wide text-[#64748B]">Funded</th>
              <th className="py-2 text-right text-xs font-semibold uppercase tracking-wide text-[#64748B]">%</th>
              <th className="py-2 text-right text-xs font-semibold uppercase tracking-wide text-[#64748B]">Status</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-[#E2E8F0]">
            {subscriptions.map((s, i) => (
              <tr key={i}>
                <td className="py-2.5 text-[#0F172A]">{s.entity_name}</td>
                <td className="py-2.5 text-right tabular-nums">{formatCurrency(s.commitment_amount)}</td>
                <td className="py-2.5 text-right tabular-nums text-[#64748B]">
                  {s.funded_amount != null ? formatCurrency(s.funded_amount) : "—"}
                </td>
                <td className="py-2.5 text-right tabular-nums text-[#64748B]">
                  {s.ownership_pct != null ? formatPercent(s.ownership_pct) : "—"}
                </td>
                <td className="py-2.5 text-right">
                  <StatusPill status={s.status} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {/* Add Subscriber modal */}
      {addOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40">
          <div className="w-full max-w-sm rounded-xl bg-white p-6 shadow-lg">
            <h2
              className="mb-4 text-base font-light"
              style={{ fontFamily: "Spectral, Georgia, serif", color: "#1B2B4B" }}
            >
              Add Subscriber
            </h2>
            <form onSubmit={handleAdd} className="space-y-3">
              <div>
                <label className="mb-1 block text-xs font-medium text-[#334155]">
                  Investor Entity *
                </label>
                <EntityPicker
                  value={selectedEntity}
                  onChange={setSelectedEntity}
                  placeholder="Search entities…"
                  className="w-full rounded border border-[#E2E8F0] bg-white px-3 py-2 text-sm focus:outline-none focus:ring-1 focus:ring-[#C5A880]"
                />
              </div>
              <div>
                <label className="mb-1 block text-xs font-medium text-[#334155]">
                  Commitment Amount (USD) *
                </label>
                <input
                  type="number"
                  min="1"
                  step="1"
                  value={amount}
                  onChange={(e) => setAmount(e.target.value)}
                  required
                  placeholder="e.g. 250000"
                  className="w-full rounded border border-[#E2E8F0] px-3 py-2 text-sm tabular-nums focus:outline-none focus:ring-1 focus:ring-[#C5A880]"
                />
              </div>
              {error && <p className="text-xs text-[#9B2335]">{error}</p>}
              <div className="flex justify-end gap-3 pt-1">
                <button
                  type="button"
                  onClick={() => { setAddOpen(false); setSelectedEntity(null); setError(null); }}
                  className="rounded-md px-4 py-2 text-sm text-[#64748B] hover:text-[#0F172A]"
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  disabled={submitting || !selectedEntity?.id || !amount}
                  className="rounded-md px-4 py-2 text-sm font-medium text-white disabled:opacity-50"
                  style={{ backgroundColor: "#1B2B4B" }}
                >
                  {submitting ? "Adding…" : "Add Subscriber"}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
}
