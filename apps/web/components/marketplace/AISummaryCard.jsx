"use client";

import { useActionState } from "react";
import { generateAISummaryAction } from "@/lib/marketplaceActions";

export default function AISummaryCard({
  dealId,
  initialSummary = null,
  staff = false,
}) {
  const [state, formAction, pending] = useActionState(
    generateAISummaryAction.bind(null, dealId),
    { summary: initialSummary },
  );

  const summary = state?.summary || initialSummary;

  return (
    <div className="rounded-lg border border-border bg-bg-card p-5">
      <div className="flex items-center justify-between">
        <h3 className="text-xs font-medium uppercase tracking-wide text-text-muted">
          AI Analysis
        </h3>
        {staff && (
          <form action={formAction}>
            <button
              type="submit"
              disabled={pending}
              className="text-xs font-medium text-navy hover:underline disabled:opacity-60"
            >
              {pending ? "Generating…" : summary ? "Regenerate" : "Generate"}
            </button>
          </form>
        )}
      </div>

      {state?.error && (
        <p className="mt-2 text-xs text-[#9B2335]">{state.error}</p>
      )}

      {pending && !summary && (
        <div className="mt-3 space-y-2 animate-pulse">
          <div className="h-3 rounded bg-border w-full" />
          <div className="h-3 rounded bg-border w-4/5" />
          <div className="h-3 rounded bg-border w-3/5" />
        </div>
      )}

      {!summary && !pending && (
        <p className="mt-3 text-xs text-text-muted">
          {staff
            ? "Click Generate to create an AI summary."
            : "No summary available."}
        </p>
      )}

      {summary && (
        <div className="mt-3 space-y-3 text-sm">
          {summary.summary_text && (
            <p className="text-text-secondary">{summary.summary_text}</p>
          )}
          {(summary.strengths || []).length > 0 && (
            <div>
              <p className="mb-1 text-xs font-semibold uppercase tracking-wide text-[#166534]">
                Strengths
              </p>
              <ul className="space-y-1">
                {summary.strengths.map((s, i) => (
                  <li
                    key={i}
                    className="flex gap-1.5 text-xs text-text-secondary"
                  >
                    <span className="shrink-0 text-[#166534]">+</span>
                    {s}
                  </li>
                ))}
              </ul>
            </div>
          )}
          {(summary.risks || []).length > 0 && (
            <div>
              <p className="mb-1 text-xs font-semibold uppercase tracking-wide text-[#9B2335]">
                Risks
              </p>
              <ul className="space-y-1">
                {summary.risks.map((r, i) => (
                  <li
                    key={i}
                    className="flex gap-1.5 text-xs text-text-secondary"
                  >
                    <span className="shrink-0 text-[#9B2335]">−</span>
                    {r}
                  </li>
                ))}
              </ul>
            </div>
          )}
          {summary.market_context && (
            <p className="text-xs italic text-text-muted">
              {summary.market_context}
            </p>
          )}
        </div>
      )}
    </div>
  );
}
