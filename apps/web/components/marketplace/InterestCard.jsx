"use client";

import { useActionState, useEffect, useState } from "react";
import { IconCheck } from "@tabler/icons-react";
import VoteButtons from "@/components/marketplace/VoteButtons";
import ScoreBar from "@/components/marketplace/ScoreBar";
import {
  indicateInterestAction,
  overrideComplianceAction,
} from "@/lib/marketplaceActions";

const INPUT =
  "mt-1 w-full rounded-md border border-border bg-bg-card px-3 py-2 text-sm text-text-primary outline-none focus:ring-2 focus:ring-navy";
const LABEL = "block text-xs font-medium uppercase tracking-wide text-text-muted";

export default function InterestCard({
  dealId,
  composite,
  upvotes,
  downvotes,
  userVote,
  alreadyInterested = false,
  entities = [],
  staff = false,
}) {
  const [interested, setInterested] = useState(alreadyInterested);
  const [blocked, setBlocked] = useState(false);
  const [showModal, setShowModal] = useState(false);
  const [showOverride, setShowOverride] = useState(false);

  const [state, formAction, pending] = useActionState(
    indicateInterestAction.bind(null, dealId),
    {},
  );
  const [ovState, ovAction, ovPending] = useActionState(
    overrideComplianceAction.bind(null, dealId),
    {},
  );

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
    if (ovState?.ok) setShowOverride(false);
  }, [ovState]);

  return (
    <div className="rounded-lg border border-border border-t-4 border-t-gold bg-bg-card p-5">
      {composite != null && (
        <div className="mb-4">
          <ScoreBar score={composite} label="Composite Score" />
        </div>
      )}

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
      ) : blocked ? (
        <div>
          <button
            type="button"
            disabled
            className="w-full cursor-not-allowed rounded-md bg-border px-4 py-2.5 text-sm font-medium text-text-muted"
          >
            Indicate Interest
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
          onClick={() => setShowModal(true)}
          className="w-full rounded-md bg-navy px-4 py-2.5 text-sm font-medium text-bg-app transition-opacity hover:opacity-90"
        >
          Indicate Interest
        </button>
      )}

      {staff && (
        <button
          type="button"
          onClick={() => setShowOverride(true)}
          className="mt-3 w-full text-center text-xs font-medium text-text-muted hover:text-navy hover:underline"
        >
          Override compliance
        </button>
      )}

      {/* Interest modal */}
      {showModal && (
        <Modal title="Indicate Interest" onClose={() => setShowModal(false)}>
          <form action={formAction} className="space-y-3">
            <div>
              <label className={LABEL}>Entity</label>
              <select name="entity_id" className={INPUT} defaultValue="">
                <option value="">Select an entity…</option>
                {entities.map((e) => (
                  <option key={e.id} value={e.id}>
                    {e.display_name}
                  </option>
                ))}
              </select>
            </div>
            <div>
              <label className={LABEL}>Amount of interest (optional)</label>
              <input name="amount_interest" placeholder="$" className={INPUT} />
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
                disabled={pending}
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

      {/* Override modal */}
      {showOverride && (
        <Modal title="Override compliance" onClose={() => setShowOverride(false)}>
          <form action={ovAction} className="space-y-3">
            <p className="text-xs text-text-muted">
              Grant this member the ability to indicate interest despite an
              incomplete compliance record. This action is audit-logged.
            </p>
            <div>
              <label className={LABEL}>User ID</label>
              <input name="user_id" placeholder="UUID" className={INPUT} />
            </div>
            <div>
              <label className={LABEL}>Notes</label>
              <textarea name="notes" rows={2} className={INPUT} />
            </div>
            {ovState?.error && <p className="text-sm text-[#9B2335]">{ovState.error}</p>}
            <div className="flex gap-2 pt-1">
              <button
                type="submit"
                disabled={ovPending}
                className="flex-1 rounded-md bg-navy px-4 py-2 text-sm font-medium text-bg-app hover:opacity-90 disabled:opacity-60"
              >
                {ovPending ? "Saving…" : "Grant override"}
              </button>
              <button
                type="button"
                onClick={() => setShowOverride(false)}
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
        className="w-full max-w-md rounded-lg border border-border bg-bg-card p-6 shadow-lg"
        onClick={(e) => e.stopPropagation()}
      >
        <h3 className="text-base font-semibold text-navy">{title}</h3>
        <div className="mt-4">{children}</div>
      </div>
    </div>
  );
}
