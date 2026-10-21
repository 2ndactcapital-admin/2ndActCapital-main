"use client";

import { useEffect, useActionState } from "react";
import { useRouter } from "next/navigation";
import {
  updateDealStageAction,
  archiveDealAction,
  unarchiveDealAction,
  submitDealAction,
} from "@/lib/marketplaceActions";
import ConfirmSubmit from "@/components/ui/ConfirmSubmit";
import { pipelineView } from "@/lib/dealLifecycle.mjs";

// BUGFIX1 — driven by GET /deals/{id}/lifecycle: the ONE lifecycle
// (deal_status, labels from the deal_stages config). It used to look
// deal.deal_stage ('sourced') up among config keys, never found it, and so
// never offered "Advance"; Archive fired on one click with no way back.
export default function DealStagePipeline({ dealId, deal, lifecycle }) {
  const router = useRouter();
  const view = pipelineView(lifecycle, deal);

  const [advanceState, advanceAction, advancePending] = useActionState(
    updateDealStageAction.bind(null, dealId), {});
  const [archiveState, archiveAction, archivePending] = useActionState(
    archiveDealAction.bind(null, dealId), {});
  const [unarchiveState, unarchiveAction, unarchivePending] = useActionState(
    unarchiveDealAction.bind(null, dealId), {});
  const [submitState, submitAction, submitPending] = useActionState(
    submitDealAction.bind(null, dealId), {});

  useEffect(() => {
    if (advanceState?.ok || archiveState?.ok || unarchiveState?.ok || submitState?.ok) {
      router.refresh();
    }
  }, [advanceState, archiveState, unarchiveState, submitState]);

  const error = advanceState?.error || archiveState?.error || unarchiveState?.error
    || submitState?.error;

  return (
    <section>
      <h2 className="text-base font-semibold text-navy">Deal Pipeline</h2>

      {view.stages.length === 0 ? (
        <p className="mt-3 text-sm text-text-muted">No stages configured.</p>
      ) : (
        <div className="mt-4 overflow-x-auto">
          <div className="flex items-center min-w-max">
            {view.stages.map((stage, idx) => (
              <div key={stage.status} className="flex items-center">
                <div
                  data-testid="pipeline-step"
                  data-current={stage.isCurrent ? "true" : "false"}
                  className={`rounded-md px-3 py-2 text-xs font-medium ${
                    stage.isCurrent
                      ? "bg-navy text-bg-app"
                      : stage.isPast
                        ? "bg-gold-light text-navy"
                        : "bg-border text-text-muted"
                  }`}
                >
                  {stage.label}
                </div>
                {idx < view.stages.length - 1 && (
                  <div className={`h-px w-5 ${stage.isPast || stage.isCurrent ? "bg-navy" : "bg-border"}`} />
                )}
              </div>
            ))}
          </div>
        </div>
      )}

      {view.canSubmit && view.missing.length > 0 && (
        <p className="mt-4 text-sm text-text-secondary" data-testid="submit-missing">
          Before this deal can be submitted for review, complete:{" "}
          {view.missing.map((m) => m.label).join(", ")}.
        </p>
      )}

      <div className="mt-5 flex flex-wrap gap-3">
        {view.canSubmit && (
          <form action={submitAction}>
            <ConfirmSubmit
              label="Submit for review"
              question="Submit this deal for review?"
              confirmLabel="Yes, submit"
              pending={submitPending}
              pendingLabel="Submitting…"
              className="rounded-md bg-navy px-4 py-2 text-sm font-medium text-bg-app hover:opacity-90 disabled:opacity-60"
            />
          </form>
        )}
        {view.next && (
          <form action={advanceAction}>
            <input type="hidden" name="stage" value={view.next.key || view.next.status} />
            <button
              type="submit"
              disabled={advancePending}
              className="rounded-md bg-navy px-4 py-2 text-sm font-medium text-bg-app hover:opacity-90 disabled:opacity-60"
            >
              {advancePending ? "Advancing…" : `Advance to ${view.next.label}`}
            </button>
          </form>
        )}
        {view.canArchive && (
          <form action={archiveAction}>
            <ConfirmSubmit
              label="Archive deal"
              question="Archive this deal? It leaves the marketplace; it can be un-archived."
              confirmLabel="Yes, archive"
              pending={archivePending}
              pendingLabel="Archiving…"
              className="rounded-md border border-border px-4 py-2 text-sm font-medium text-text-secondary hover:bg-border disabled:opacity-60"
            />
          </form>
        )}
        {view.unarchive && (
          <form action={unarchiveAction}>
            <button
              type="submit"
              disabled={unarchivePending}
              data-testid="unarchive"
              className="rounded-md border border-navy px-4 py-2 text-sm font-medium text-navy hover:bg-border disabled:opacity-60"
            >
              {unarchivePending ? "Restoring…" : `Un-archive (back to ${view.unarchive.label})`}
            </button>
          </form>
        )}
      </div>

      {error && <p className="mt-2 text-sm text-[#9B2335]">{error}</p>}
    </section>
  );
}
