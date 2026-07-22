"use client";

import { useState, useEffect } from "react";
import { useRouter } from "next/navigation";

function extractErrorMessage(err) {
  // err may be a plain Error, an API error object, or a Pydantic validation array
  if (!err) return "An unexpected error occurred";
  if (typeof err === "string") return err;
  // Array of Pydantic validation errors: [{loc, msg, type}, ...]
  if (Array.isArray(err)) {
    const first = err[0];
    if (first && typeof first === "object") {
      const loc = Array.isArray(first.loc) ? first.loc.join(" → ") : "";
      const msg = first.msg || String(first);
      return loc ? `${loc}: ${msg}` : msg;
    }
    return String(err);
  }
  // FastAPI detail string or array nested on an Error object
  if (err.detail) return extractErrorMessage(err.detail);
  if (err.message && typeof err.message === "string") return err.message;
  return String(err);
}

export default function NewSPVForm({ dealId: lockedDealId, dealName: lockedDealName } = {}) {
  const [open, setOpen] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState(null);
  const [deals, setDeals] = useState([]);
  const [dealsLoading, setDealsLoading] = useState(false);
  // Class state for the selected deal: a deal's second and later SPVs are
  // Classes and must be labelled. The API enforces this too.
  const [dealId, setDealId] = useState(lockedDealId || "");
  const [classState, setClassState] = useState(null);
  const [classLabel, setClassLabel] = useState("");
  const router = useRouter();

  useEffect(() => {
    if (!open || lockedDealId) return;
    setDealsLoading(true);
    fetch("/api/deals?limit=200")
      .then((r) => r.json())
      .then((data) => setDeals(Array.isArray(data) ? data : []))
      .catch(() => setDeals([]))
      .finally(() => setDealsLoading(false));
  }, [open, lockedDealId]);

  useEffect(() => {
    if (!open || !dealId) {
      setClassState(null);
      setClassLabel("");
      return;
    }
    let cancelled = false;
    fetch(`/api/deals/${dealId}/classes`, { cache: "no-store" })
      .then((r) => (r.ok ? r.json() : null))
      .then((data) => {
        if (cancelled || !data) return;
        setClassState(data);
        // Pre-fill the suggested next letter; the user can override it.
        setClassLabel(data.class_label_required ? data.suggested_class_label : "");
      })
      .catch(() => {
        if (!cancelled) setClassState(null);
      });
    return () => {
      cancelled = true;
    };
  }, [open, dealId]);

  const classRequired = Boolean(classState?.class_label_required);

  async function handleSubmit(e) {
    e.preventDefault();
    setSubmitting(true);
    setError(null);
    const fd = new FormData(e.currentTarget);
    const body = {
      name: fd.get("name"),
      deal_id: lockedDealId || fd.get("deal_id") || null,
      target_raise: fd.get("target_raise") ? Number(fd.get("target_raise")) : null,
      min_commitment: fd.get("min_commitment") ? Number(fd.get("min_commitment")) : null,
      carry_pct: fd.get("carry_pct") ? Number(fd.get("carry_pct")) : null,
      mgmt_fee_pct: fd.get("mgmt_fee_pct") ? Number(fd.get("mgmt_fee_pct")) : null,
      close_date: fd.get("close_date") || null,
      class_label: (fd.get("class_label") || "").trim() || null,
    };
    try {
      const res = await fetch("/api/spvs", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        throw extractErrorMessage(data.error ?? data.detail ?? data);
      }
      setOpen(false);
      router.push(`/spvs/${data.id}`);
      router.refresh();
    } catch (err) {
      setError(typeof err === "string" ? err : extractErrorMessage(err));
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
        style={{ backgroundColor: "var(--2a-navy)" }}
      >
        New SPV
      </button>

      {open && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40">
          <div className="w-full max-w-md rounded-xl bg-white p-6 shadow-lg">
            <h2
              className="mb-4 text-lg font-light"
              style={{ fontFamily: "Spectral, Georgia, serif", color: "var(--2a-navy)" }}
            >
              New SPV
            </h2>
            <form onSubmit={handleSubmit} className="space-y-3">
              <div>
                <label className="block text-xs font-medium text-[var(--2a-text-secondary)] mb-1">
                  Name *
                </label>
                <input
                  name="name"
                  required
                  placeholder="e.g. Acme Growth SPV I"
                  className="w-full rounded border border-[var(--2a-border)] px-3 py-2 text-sm focus:outline-none focus:ring-1 focus:ring-[var(--2a-gold)]"
                />
              </div>
              <div>
                <label className="block text-xs font-medium text-[var(--2a-text-secondary)] mb-1">
                  Deal *
                </label>
                {lockedDealId ? (
                  <div className="w-full rounded border border-[var(--2a-border)] bg-[var(--2a-bg)] px-3 py-2 text-sm text-[var(--2a-text-secondary)]">
                    {lockedDealName || lockedDealId}
                  </div>
                ) : dealsLoading ? (
                  <div className="w-full rounded border border-[var(--2a-border)] px-3 py-2 text-sm text-[var(--2a-text-muted)]">
                    Loading deals…
                  </div>
                ) : (
                  <select
                    name="deal_id"
                    required
                    value={dealId}
                    onChange={(e) => setDealId(e.target.value)}
                    className="w-full rounded border border-[var(--2a-border)] px-3 py-2 text-sm focus:outline-none focus:ring-1 focus:ring-[var(--2a-gold)] bg-white"
                  >
                    <option value="" disabled>Select a deal</option>
                    {deals.map((d) => (
                      <option key={d.id} value={d.id}>
                        {d.name}
                      </option>
                    ))}
                  </select>
                )}
              </div>

              {/* Class — required once the investment already has an SPV */}
              {classState && (
                <div>
                  <label className="block text-xs font-medium text-[var(--2a-text-secondary)] mb-1">
                    Class {classRequired && "*"}
                  </label>
                  <input
                    name="class_label"
                    required={classRequired}
                    value={classLabel}
                    onChange={(e) => setClassLabel(e.target.value)}
                    placeholder={classRequired ? classState.suggested_class_label : "Optional"}
                    className="w-full rounded border border-[var(--2a-border)] px-3 py-2 text-sm focus:outline-none focus:ring-1 focus:ring-[var(--2a-gold)]"
                  />
                  <p className="mt-1 text-xs text-[var(--2a-text-muted)]">
                    {classRequired
                      ? `This investment already has ${classState.spv_count} SPV${
                          classState.spv_count === 1 ? "" : "s"
                        }${
                          classState.existing_labels?.length
                            ? ` (Class ${classState.existing_labels.join(", ")})`
                            : ""
                        }. Classes carry their own fee, carry, and close terms.`
                      : "Leave blank for a single-class investment."}
                  </p>
                </div>
              )}
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="block text-xs font-medium text-[var(--2a-text-secondary)] mb-1">Target Raise ($)</label>
                  <input
                    name="target_raise"
                    type="number"
                    min="0"
                    placeholder="5000000"
                    className="w-full rounded border border-[var(--2a-border)] px-3 py-2 text-sm focus:outline-none focus:ring-1 focus:ring-[var(--2a-gold)]"
                  />
                </div>
                <div>
                  <label className="block text-xs font-medium text-[var(--2a-text-secondary)] mb-1">Min. Commitment ($)</label>
                  <input
                    name="min_commitment"
                    type="number"
                    min="0"
                    placeholder="100000"
                    className="w-full rounded border border-[var(--2a-border)] px-3 py-2 text-sm focus:outline-none focus:ring-1 focus:ring-[var(--2a-gold)]"
                  />
                </div>
                <div>
                  <label className="block text-xs font-medium text-[var(--2a-text-secondary)] mb-1">Carry (%)</label>
                  <input
                    name="carry_pct"
                    type="number"
                    min="0"
                    max="100"
                    step="0.1"
                    placeholder="20"
                    className="w-full rounded border border-[var(--2a-border)] px-3 py-2 text-sm focus:outline-none focus:ring-1 focus:ring-[var(--2a-gold)]"
                  />
                </div>
                <div>
                  <label className="block text-xs font-medium text-[var(--2a-text-secondary)] mb-1">Mgmt Fee (%)</label>
                  <input
                    name="mgmt_fee_pct"
                    type="number"
                    min="0"
                    max="100"
                    step="0.1"
                    placeholder="2"
                    className="w-full rounded border border-[var(--2a-border)] px-3 py-2 text-sm focus:outline-none focus:ring-1 focus:ring-[var(--2a-gold)]"
                  />
                </div>
              </div>
              <div>
                <label className="block text-xs font-medium text-[var(--2a-text-secondary)] mb-1">Close Date</label>
                <input
                  name="close_date"
                  type="date"
                  className="w-full rounded border border-[var(--2a-border)] px-3 py-2 text-sm focus:outline-none focus:ring-1 focus:ring-[var(--2a-gold)]"
                />
              </div>
              {error && <p className="text-xs text-[#9B2335]">{error}</p>}
              <div className="flex justify-end gap-3 pt-2">
                <button
                  type="button"
                  onClick={() => setOpen(false)}
                  className="rounded-md px-4 py-2 text-sm text-[var(--2a-text-muted)] hover:text-[var(--2a-text)]"
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  disabled={submitting}
                  className="rounded-md px-4 py-2 text-sm font-medium text-white disabled:opacity-50"
                  style={{ backgroundColor: "var(--2a-navy)" }}
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
