"use client";

import { useEffect, useActionState } from "react";
import { useRouter } from "next/navigation";
import { updateDealStageAction, archiveDealAction } from "@/lib/marketplaceActions";

export default function DealStagePipeline({ dealId, deal, stages = [] }) {
  const router = useRouter();
  const currentStageKey = deal.deal_stage;
  const currentIdx = stages.findIndex((s) => s.config_key === currentStageKey);
  const nextStage =
    currentIdx >= 0 && currentIdx < stages.length - 1
      ? stages[currentIdx + 1]
      : null;

  const [advanceState, advanceAction, advancePending] = useActionState(
    updateDealStageAction.bind(null, dealId),
    {},
  );

  const [archiveState, archiveAction, archivePending] = useActionState(
    archiveDealAction.bind(null, dealId),
    {},
  );

  useEffect(() => {
    if (advanceState?.ok || archiveState?.ok) {
      router.refresh();
    }
  }, [advanceState, archiveState]);

  return (
    <section>
      <h2 className="text-base font-semibold text-navy">Deal Pipeline</h2>

      {stages.length === 0 ? (
        <p className="mt-3 text-sm text-text-muted">No stages configured.</p>
      ) : (
        <div className="mt-4 overflow-x-auto">
          <div className="flex items-center min-w-max">
            {stages.map((stage, idx) => {
              const isCurrent = stage.config_key === currentStageKey;
              const isPast = currentIdx >= 0 && idx < currentIdx;
              return (
                <div key={stage.config_key} className="flex items-center">
                  <div
                    className={`rounded-md px-3 py-2 text-xs font-medium ${
                      isCurrent
                        ? "bg-navy text-bg-app"
                        : isPast
                          ? "bg-gold-light text-navy"
                          : "bg-border text-text-muted"
                    }`}
                  >
                    {stage.config_value || stage.config_key}
                  </div>
                  {idx < stages.length - 1 && (
                    <div
                      className={`h-px w-5 ${
                        isPast || isCurrent ? "bg-navy" : "bg-border"
                      }`}
                    />
                  )}
                </div>
              );
            })}
          </div>
        </div>
      )}

      <div className="mt-5 flex flex-wrap gap-3">
        {nextStage && (
          <form action={advanceAction}>
            <input type="hidden" name="stage" value={nextStage.config_key} />
            <button
              type="submit"
              disabled={advancePending}
              className="rounded-md bg-navy px-4 py-2 text-sm font-medium text-bg-app hover:opacity-90 disabled:opacity-60"
            >
              {advancePending
                ? "Advancing…"
                : `Advance to ${nextStage.config_value || nextStage.config_key}`}
            </button>
          </form>
        )}
        {deal.deal_status !== "archived" && (
          <form action={archiveAction}>
            <button
              type="submit"
              disabled={archivePending}
              className="rounded-md border border-border px-4 py-2 text-sm font-medium text-text-secondary hover:bg-border disabled:opacity-60"
            >
              {archivePending ? "Archiving…" : "Archive Deal"}
            </button>
          </form>
        )}
      </div>

      {(advanceState?.error || archiveState?.error) && (
        <p className="mt-2 text-sm text-[#9B2335]">
          {advanceState?.error || archiveState?.error}
        </p>
      )}
    </section>
  );
}
