"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";

export default function NewSPVForm() {
  const [open, setOpen] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState(null);
  const router = useRouter();

  async function handleSubmit(e) {
    e.preventDefault();
    setSubmitting(true);
    setError(null);
    const fd = new FormData(e.currentTarget);
    const body = {
      name: fd.get("name"),
      target_raise: fd.get("target_raise") ? Number(fd.get("target_raise")) : null,
      min_commitment: fd.get("min_commitment") ? Number(fd.get("min_commitment")) : null,
      carry_pct: fd.get("carry_pct") ? Number(fd.get("carry_pct")) : null,
      mgmt_fee_pct: fd.get("mgmt_fee_pct") ? Number(fd.get("mgmt_fee_pct")) : null,
      close_date: fd.get("close_date") || null,
    };
    try {
      const res = await fetch("/api/spvs", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        throw new Error(data.error || "Failed to create SPV");
      }
      const spv = await res.json();
      setOpen(false);
      router.push(`/spvs/${spv.id}`);
      router.refresh();
    } catch (err) {
      setError(err.message);
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(true)}
        className="rounded-md px-4 py-2 text-sm font-medium text-white"
        style={{ backgroundColor: "#1B2B4B" }}
      >
        New SPV
      </button>

      {open && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40">
          <div className="w-full max-w-md rounded-xl bg-white p-6 shadow-lg">
            <h2
              className="mb-4 text-lg font-light"
              style={{ fontFamily: "Spectral, Georgia, serif", color: "#1B2B4B" }}
            >
              New SPV
            </h2>
            <form onSubmit={handleSubmit} className="space-y-3">
              <div>
                <label className="block text-xs font-medium text-[#334155] mb-1">
                  Name *
                </label>
                <input
                  name="name"
                  required
                  placeholder="e.g. Acme Growth SPV I"
                  className="w-full rounded border border-[#E2E8F0] px-3 py-2 text-sm focus:outline-none focus:ring-1 focus:ring-[#C5A880]"
                />
              </div>
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="block text-xs font-medium text-[#334155] mb-1">Target Raise ($)</label>
                  <input
                    name="target_raise"
                    type="number"
                    min="0"
                    placeholder="5000000"
                    className="w-full rounded border border-[#E2E8F0] px-3 py-2 text-sm focus:outline-none focus:ring-1 focus:ring-[#C5A880]"
                  />
                </div>
                <div>
                  <label className="block text-xs font-medium text-[#334155] mb-1">Min. Commitment ($)</label>
                  <input
                    name="min_commitment"
                    type="number"
                    min="0"
                    placeholder="100000"
                    className="w-full rounded border border-[#E2E8F0] px-3 py-2 text-sm focus:outline-none focus:ring-1 focus:ring-[#C5A880]"
                  />
                </div>
                <div>
                  <label className="block text-xs font-medium text-[#334155] mb-1">Carry (%)</label>
                  <input
                    name="carry_pct"
                    type="number"
                    min="0"
                    max="100"
                    step="0.1"
                    placeholder="20"
                    className="w-full rounded border border-[#E2E8F0] px-3 py-2 text-sm focus:outline-none focus:ring-1 focus:ring-[#C5A880]"
                  />
                </div>
                <div>
                  <label className="block text-xs font-medium text-[#334155] mb-1">Mgmt Fee (%)</label>
                  <input
                    name="mgmt_fee_pct"
                    type="number"
                    min="0"
                    max="100"
                    step="0.1"
                    placeholder="2"
                    className="w-full rounded border border-[#E2E8F0] px-3 py-2 text-sm focus:outline-none focus:ring-1 focus:ring-[#C5A880]"
                  />
                </div>
              </div>
              <div>
                <label className="block text-xs font-medium text-[#334155] mb-1">Close Date</label>
                <input
                  name="close_date"
                  type="date"
                  className="w-full rounded border border-[#E2E8F0] px-3 py-2 text-sm focus:outline-none focus:ring-1 focus:ring-[#C5A880]"
                />
              </div>
              {error && <p className="text-xs text-[#9B2335]">{error}</p>}
              <div className="flex justify-end gap-3 pt-2">
                <button
                  type="button"
                  onClick={() => setOpen(false)}
                  className="rounded-md px-4 py-2 text-sm text-[#64748B] hover:text-[#0F172A]"
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  disabled={submitting}
                  className="rounded-md px-4 py-2 text-sm font-medium text-white disabled:opacity-50"
                  style={{ backgroundColor: "#1B2B4B" }}
                >
                  {submitting ? "Creating…" : "Create SPV"}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </>
  );
}
