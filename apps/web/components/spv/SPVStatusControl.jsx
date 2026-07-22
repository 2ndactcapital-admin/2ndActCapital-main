"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";

const TRANSITIONS = {
  forming: ["open", "cancelled"],
  open: ["closing", "cancelled"],
  closing: ["closed", "cancelled"],
  closed: ["cancelled"],
  cancelled: [],
};

export default function SPVStatusControl({ spv }) {
  const [open, setOpen] = useState(false);
  const [target, setTarget] = useState("");
  const [note, setNote] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState(null);
  const router = useRouter();

  const options = TRANSITIONS[spv.status] || [];
  if (!options.length) return null;

  async function handleTransition(e) {
    e.preventDefault();
    if (!target) return;
    setSubmitting(true);
    setError(null);
    try {
      const res = await fetch(`/api/spvs/${spv.id}/status`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ status: target, note }),
      });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        throw new Error(data.error || "Transition failed");
      }
      setOpen(false);
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
        onClick={() => { setOpen(true); setTarget(options[0] || ""); }}
        className="rounded-md border border-[var(--2a-border)] px-3 py-1.5 text-xs font-medium text-[var(--2a-text-secondary)] hover:border-[var(--2a-gold)] hover:text-[var(--2a-navy)] transition"
      >
        Change status
      </button>

      {open && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40">
          <div className="w-full max-w-sm rounded-xl bg-white p-6 shadow-lg">
            <h2
              className="mb-4 text-base font-light"
              style={{ fontFamily: "Spectral, Georgia, serif", color: "var(--2a-navy)" }}
            >
              Transition SPV Status
            </h2>
            <form onSubmit={handleTransition} className="space-y-3">
              <div>
                <label className="block text-xs font-medium text-[var(--2a-text-secondary)] mb-1">New Status</label>
                <select
                  value={target}
                  onChange={(e) => setTarget(e.target.value)}
                  className="w-full rounded border border-[var(--2a-border)] px-3 py-2 text-sm focus:outline-none focus:ring-1 focus:ring-[var(--2a-gold)]"
                >
                  {options.map((s) => (
                    <option key={s} value={s}>{s}</option>
                  ))}
                </select>
              </div>
              <div>
                <label className="block text-xs font-medium text-[var(--2a-text-secondary)] mb-1">Note (optional)</label>
                <input
                  value={note}
                  onChange={(e) => setNote(e.target.value)}
                  placeholder="Reason for transition"
                  className="w-full rounded border border-[var(--2a-border)] px-3 py-2 text-sm focus:outline-none focus:ring-1 focus:ring-[var(--2a-gold)]"
                />
              </div>
              {error && <p className="text-xs text-[#9B2335]">{error}</p>}
              <div className="flex justify-end gap-3 pt-1">
                <button
                  type="button"
                  onClick={() => setOpen(false)}
                  className="rounded-md px-4 py-2 text-sm text-[var(--2a-text-muted)] hover:text-[var(--2a-text)]"
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  disabled={submitting || !target}
                  className="rounded-md px-4 py-2 text-sm font-medium text-white disabled:opacity-50"
                  style={{ backgroundColor: "var(--2a-navy)" }}
                >
                  {submitting ? "Saving…" : "Confirm"}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </>
  );
}
