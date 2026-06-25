"use client";

import { useActionState, useEffect, useMemo, useState } from "react";
import { IconCheck } from "@tabler/icons-react";
import VoteButtons from "@/components/marketplace/VoteButtons";
import { formatCurrency } from "@/lib/format";
import {
  indicateInterestAction,
  requestComplianceReviewAction,
} from "@/lib/marketplaceActions";

const INPUT =
  "mt-1 w-full rounded-md border border-border bg-bg-card px-3 py-2 text-sm text-text-primary outline-none focus:ring-2 focus:ring-navy";
const LABEL = "block text-xs font-medium uppercase tracking-wide text-text-muted";

// Parse a user-typed amount ("$500,000" / "500000") into a finite number or null.
function parseAmount(value) {
  const cleaned = (value ?? "").toString().replace(/[$,\s]/g, "");
  if (cleaned === "") return null;
  const n = Number(cleaned);
  return Number.isFinite(n) ? n : null;
}

function ScoreDisplay({ composite, scores = [] }) {
  if (composite == null) {
    return (
      <div className="mb-4 rounded-lg bg-bg-app p-4 text-center">
        <p className="text-sm text-text-muted">No scores yet</p>
      </div>
    );
  }

  const n = Number(composite);
  const color =
    n >= 75 ? "#166534" : n >= 50 ? "#2C4A3E" : "#9B2335";
  const pct = Math.min(100, Math.max(0, n));

  return (
    <div className="mb-4">
      <div className="flex items-end justify-between">
        <div>
          <p
            className="font-semibold tabular-nums leading-none"
            style={{ fontSize: 48, color }}
          >
            {n.toFixed(1)}
          </p>
          <p className="mt-0.5 text-xs font-medium uppercase tracking-wide text-text-muted">
            Composite Score
          </p>
        </div>
      </div>
      <div className="mt-3 h-2 w-full overflow-hidden rounded-full bg-border">
        <div
          className="h-full rounded-full transition-all"
          style={{ width: `${pct}%`, backgroundColor: "#C5A880" }}
        />
      </div>
      {scores.length > 0 && (
        <div className="mt-3 space-y-1.5">
          {scores.map((s) => (
            <div key={s.dimension} className="flex items-center gap-2">
              <span className="w-28 truncate text-[11px] text-text-muted">
                {s.dimension}
              </span>
              <div className="flex-1 h-1.5 overflow-hidden rounded-full bg-border">
                <div
                  className="h-full rounded-full bg-gold"
                  style={{ width: `${Math.min(100, s.score ?? 0)}%` }}
                />
              </div>
              <span className="w-8 text-right text-[11px] tabular-nums text-text-secondary">
                {s.score != null ? Number(s.score).toFixed(0) : "—"}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// Entity <select> shared by both modals. Renders a guidance message when the
// member has no investor-capable entities to choose from.
function EntitySelect({ entities, required = false }) {
  if (entities.length === 0) {
    return (
      <div className="rounded-md border border-border bg-bg-app p-3 text-xs text-text-muted">
        No entities found — contact your advisor to set up your profile.
      </div>
    );
  }
  return (
    <select
      name="entity_id"
      className={INPUT}
      defaultValue=""
      required={required}
    >
      <option value="">Select an entity…</option>
      {entities.map((e) => (
        <option key={e.id} value={e.id}>
          {e.display_name}
        </option>
      ))}
    </select>
  );
}

export default function InterestCard({
  dealId,
  composite,
  scores = [],
  upvotes,
  downvotes,
  userVote,
  alreadyInterested = false,
  entities = [],
  minimumInvestment = null,
}) {
  const [interested, setInterested] = useState(alreadyInterested);
  const [blocked, setBlocked] = useState(false);
  const [showModal, setShowModal] = useState(false);
  const [showReviewRequest, setShowReviewRequest] = useState(false);
  const [reviewRequested, setReviewRequested] = useState(false);

  // Controlled amount field (required, currency-masked, min-validated).
  const [amount, setAmount] = useState("");
  const [amountTouched, setAmountTouched] = useState(false);

  const [state, formAction, pending] = useActionState(
    indicateInterestAction.bind(null, dealId),
    {},
  );
  const [rvState, rvAction, rvPending] = useActionState(
    requestComplianceReviewAction.bind(null, dealId),
    {},
  );

  const min = Number(minimumInvestment) || 0;
  const amountNumber = parseAmount(amount);
  const amountError = useMemo(() => {
    if (amountNumber == null) return "Amount is required";
    if (min > 0 && amountNumber < min)
      return `Amount must be at least ${formatCurrency(min)}`;
    return null;
  }, [amountNumber, min]);

  const hasEntities = entities.length > 0;

  useEffect(() => {
    if (state?.ok) {
      setInterested(true);
      setShowModal(false);
      setBlocked(false);
    } else if (state?.compliance) {
      setBlocked(true);
      setShowModal(false);
    }
  }, [state]);

  useEffect(() => {
    if (rvState?.ok) {
      setShowReviewRequest(false);
      setReviewRequested(true);
    }
  }, [rvState]);

  function handleAmountBlur() {
    setAmountTouched(true);
    const n = parseAmount(amount);
    if (n != null) setAmount(formatCurrency(n));
  }

  function openModal() {
    setAmount("");
    setAmountTouched(false);
    setShowModal(true);
  }

  return (
    <div className="rounded-lg border border-border border-t-4 border-t-gold bg-bg-card p-5">
      <ScoreDisplay composite={composite} scores={scores} />

      <div className="flex items-center justify-between">
        <span className="text-xs font-medium uppercase tracking-wide text-text-muted">
          Community
        </span>
        <VoteButtons
          dealId={dealId}
          initialUpvotes={upvotes}
          initialDownvotes={downvotes}
          initialUserVote={userVote}
          size="lg"
        />
      </div>

      <div className="my-4 border-t border-border" />

      {interested ? (
        <div className="flex w-full items-center justify-center gap-2 rounded-md bg-[#DCFCE7] px-4 py-2.5 text-sm font-medium text-[#166534]">
          <IconCheck size={18} /> Interest Indicated
        </div>
      ) : reviewRequested ? (
        <div className="rounded-md bg-bg-app p-3 text-center">
          <p className="text-sm font-medium text-text-primary">Review Requested</p>
          <p className="mt-1 text-xs text-text-muted">
            Our team will review your compliance status.
          </p>
        </div>
      ) : blocked ? (
        <div>
          <button
            type="button"
            onClick={() => setShowReviewRequest(true)}
            className="w-full rounded-md bg-navy px-4 py-2.5 text-sm font-medium text-bg-app transition-opacity hover:opacity-90"
          >
            Request Compliance Review
          </button>
          <p className="mt-2 text-xs text-text-muted">
            KYC approval and accreditation required.{" "}
            <a href="/investment-profile" className="font-medium text-navy hover:underline">
              Learn more
            </a>
          </p>
        </div>
      ) : (
        <button
          type="button"
          onClick={openModal}
          className="w-full rounded-md bg-navy px-4 py-2.5 text-sm font-medium text-bg-app transition-opacity hover:opacity-90"
        >
          Indicate Interest
        </button>
      )}

      {/* Interest modal */}
      {showModal && (
        <Modal title="Indicate Interest" onClose={() => setShowModal(false)}>
          <form action={formAction} className="space-y-3">
            <div>
              <label className={LABEL}>Entity</label>
              <div className="mt-1">
                <EntitySelect entities={entities} required />
              </div>
            </div>
            <div>
              <label className={LABEL}>Amount of interest</label>
              <input
                name="amount_interest"
                inputMode="numeric"
                placeholder="$"
                className={INPUT}
                value={amount}
                onChange={(e) => setAmount(e.target.value)}
                onBlur={handleAmountBlur}
                required
                aria-invalid={amountTouched && amountError ? "true" : undefined}
              />
              {min > 0 && (
                <p className="mt-1 text-xs text-text-muted">
                  Minimum investment: {formatCurrency(min)}
                </p>
              )}
              {amountTouched && amountError && (
                <p className="mt-1 text-xs text-[#9B2335]">{amountError}</p>
              )}
            </div>
            <div>
              <label className={LABEL}>Notes (optional)</label>
              <textarea name="notes" rows={2} className={INPUT} />
            </div>
            {state?.error && !state?.compliance && (
              <p className="text-sm text-[#9B2335]">{state.error}</p>
            )}
            <div className="flex gap-2 pt-1">
              <button
                type="submit"
                disabled={pending || !hasEntities || !!amountError}
                onClick={() => setAmountTouched(true)}
                className="flex-1 rounded-md bg-navy px-4 py-2 text-sm font-medium text-bg-app hover:opacity-90 disabled:opacity-60"
              >
                {pending ? "Submitting…" : "Submit"}
              </button>
              <button
                type="button"
                onClick={() => setShowModal(false)}
                className="rounded-md border border-border px-4 py-2 text-sm font-medium text-text-secondary hover:bg-border"
              >
                Cancel
              </button>
            </div>
          </form>
        </Modal>
      )}

      {/* Compliance review request modal */}
      {showReviewRequest && (
        <Modal title="Request Compliance Review" onClose={() => setShowReviewRequest(false)}>
          <form action={rvAction} className="space-y-3">
            <p className="text-xs text-text-muted">
              Submit a request for our team to review your compliance status for
              this deal. We may reach out for additional information.
            </p>
            <div>
              <label className={LABEL}>Entity (optional)</label>
              <div className="mt-1">
                <EntitySelect entities={entities} />
              </div>
            </div>
            <div>
              <label className={LABEL}>Notes (optional)</label>
              <textarea
                name="request_notes"
                rows={3}
                placeholder="Explain your situation or ask a question…"
                className={INPUT}
              />
            </div>
            {rvState?.error && <p className="text-sm text-[#9B2335]">{rvState.error}</p>}
            <div className="flex gap-2 pt-1">
              <button
                type="submit"
                disabled={rvPending}
                className="flex-1 rounded-md bg-navy px-4 py-2 text-sm font-medium text-bg-app hover:opacity-90 disabled:opacity-60"
              >
                {rvPending ? "Submitting…" : "Submit Request"}
              </button>
              <button
                type="button"
                onClick={() => setShowReviewRequest(false)}
                className="rounded-md border border-border px-4 py-2 text-sm font-medium text-text-secondary hover:bg-border"
              >
                Cancel
              </button>
            </div>
          </form>
        </Modal>
      )}
    </div>
  );
}

function Modal({ title, onClose, children }) {
  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-navy/30 p-4"
      onClick={onClose}
    >
      <div
        className="w-full max-w-md rounded-lg border border-[#ece8dd] bg-bg-card p-6"
        style={{ boxShadow: "0 1px 3px rgba(0,0,0,0.06)" }}
        onClick={(e) => e.stopPropagation()}
      >
        <h3 className="text-base font-semibold text-navy">{title}</h3>
        <div className="mt-4">{children}</div>
      </div>
    </div>
  );
}
