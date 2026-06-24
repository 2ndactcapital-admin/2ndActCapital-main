"use client";

import { useState, useEffect, useActionState } from "react";
import { updateMemberInvestmentStageAction } from "@/lib/marketplaceActions";

function StageUpdateForm({ dealId, userId, currentStage, investmentStages }) {
  const [open, setOpen] = useState(false);
  const [state, formAction, pending] = useActionState(
    updateMemberInvestmentStageAction.bind(null, dealId, userId),
    {},
  );

  useEffect(() => {
    if (state?.ok) setOpen(false);
  }, [state]);

  if (!open) {
    return (
      <button
        type="button"
        onClick={() => setOpen(true)}
        className="text-xs font-medium text-navy hover:underline"
      >
        Update
      </button>
    );
  }

  return (
    <form action={formAction} className="flex items-center gap-2">
      <select
        name="stage"
        defaultValue={currentStage || ""}
        className="rounded-md border border-border bg-bg-card px-2 py-1 text-xs text-text-primary"
      >
        {investmentStages.map((s) => (
          <option key={s.config_key} value={s.config_key}>
            {s.config_value || s.config_key}
          </option>
        ))}
      </select>
      <button
        type="submit"
        disabled={pending}
        className="rounded-md bg-navy px-2 py-1 text-xs font-medium text-bg-app hover:opacity-90 disabled:opacity-60"
      >
        {pending ? "…" : "Save"}
      </button>
      <button
        type="button"
        onClick={() => setOpen(false)}
        className="text-xs text-text-muted hover:text-navy"
      >
        ✕
      </button>
      {state?.error && (
        <span className="text-xs text-[#9B2335]">{state.error}</span>
      )}
    </form>
  );
}

export default function MemberInvestmentTracker({
  dealId,
  initial = [],
  investmentStages = [],
}) {
  const [investments, setInvestments] = useState(initial);

  return (
    <section>
      <h2 className="text-base font-semibold text-navy">Member Investments</h2>
      {investments.length === 0 ? (
        <p className="mt-3 text-sm text-text-muted">
          No members have indicated interest yet.
        </p>
      ) : (
        <div className="mt-3 overflow-x-auto rounded-lg border border-border">
          <table className="w-full text-sm">
            <thead className="border-b border-border bg-bg-app">
              <tr>
                <th className="px-3 py-2.5 text-left text-xs font-semibold uppercase tracking-wide text-text-muted">
                  Member
                </th>
                <th className="px-3 py-2.5 text-left text-xs font-semibold uppercase tracking-wide text-text-muted">
                  Stage
                </th>
                <th className="px-3 py-2.5 text-left text-xs font-semibold uppercase tracking-wide text-text-muted">
                  Amount
                </th>
                <th className="px-3 py-2.5 text-left text-xs font-semibold uppercase tracking-wide text-text-muted">
                  Actions
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {investments.map((inv) => {
                const stageLabel =
                  investmentStages.find((s) => s.config_key === inv.stage)
                    ?.config_value || inv.stage || "—";
                return (
                  <tr key={inv.id}>
                    <td className="px-3 py-3 font-mono text-xs text-text-muted">
                      {String(inv.user_id).slice(0, 8)}…
                    </td>
                    <td className="px-3 py-3">
                      <span className="rounded-md bg-gold-light px-2 py-0.5 text-xs font-medium text-navy">
                        {stageLabel}
                      </span>
                    </td>
                    <td className="px-3 py-3 tabular-nums text-text-secondary">
                      {inv.invested_amount != null
                        ? `$${Number(inv.invested_amount).toLocaleString()}`
                        : "—"}
                    </td>
                    <td className="px-3 py-3">
                      <StageUpdateForm
                        dealId={dealId}
                        userId={inv.user_id}
                        currentStage={inv.stage}
                        investmentStages={investmentStages}
                      />
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
