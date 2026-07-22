"use client";

import { useActionState, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { saveScoreAction } from "@/lib/marketplaceActions";
import ScoreBar from "@/components/marketplace/ScoreBar";
import { usePermissions } from "@/lib/usePermissions";

const INPUT =
  "rounded-md border border-border bg-bg-card px-3 py-2 text-sm text-text-primary outline-none focus:ring-2 focus:ring-navy";

function weightPct(weight) {
  if (weight == null) return null;
  const w = Number(weight);
  if (!Number.isFinite(w)) return null;
  // Weights may be stored as fractions (0.1667) or percents (16.67).
  return (w <= 1 ? w * 100 : w).toFixed(1);
}

function DimensionRow({ dealId, dimension, label, weight, existing, onSaved }) {
  const [state, formAction, pending] = useActionState(
    saveScoreAction.bind(null, dealId),
    {},
  );
  const [score, setScore] = useState(existing?.score ?? 50);
  const pct = weightPct(existing?.weight ?? weight);

  useEffect(() => {
    if (state?.ok && onSaved) onSaved();
  }, [state]);

  return (
    <form
      action={formAction}
      className="rounded-lg border border-border bg-bg-card p-4"
    >
      <input type="hidden" name="dimension" value={dimension} />
      <input type="hidden" name="weight" value={existing?.weight ?? weight ?? 0} />
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <span className="text-sm font-medium text-text-primary">{label}</span>
          {existing?.scored_by_ai && (
            <span className="rounded-full bg-gold-light px-2 py-0.5 text-[10px] font-semibold text-navy">
              AI
            </span>
          )}
        </div>
        {pct != null && (
          <span className="text-xs text-text-muted">Weight {pct}%</span>
        )}
      </div>

      <div className="mt-3 flex items-center gap-3">
        <input
          type="range"
          name="score"
          min={0}
          max={100}
          value={score}
          onChange={(e) => setScore(Number(e.target.value))}
          className="flex-1 accent-[var(--2a-gold)]"
        />
        <input
          type="number"
          min={0}
          max={100}
          value={score}
          onChange={(e) => setScore(Number(e.target.value))}
          className={`w-20 ${INPUT}`}
          aria-label={`${label} score`}
        />
      </div>

      <textarea
        name="notes"
        rows={2}
        defaultValue={existing?.notes || ""}
        placeholder="Notes…"
        className={`mt-3 w-full ${INPUT}`}
      />

      {state?.error && <p className="mt-2 text-sm text-[#9B2335]">{state.error}</p>}

      <div className="mt-3 flex items-center justify-between">
        {state?.ok && <span className="text-xs text-[#2D6A4F]">Saved ✓</span>}
        <button
          type="submit"
          disabled={pending}
          className="ml-auto rounded-md bg-navy px-3 py-1.5 text-sm font-medium text-bg-app hover:opacity-90 disabled:opacity-60"
        >
          {pending ? "Saving…" : "Save"}
        </button>
      </div>
    </form>
  );
}

export default function ScoringSection({ dealId, dimensions = [], scores = [], composite }) {
  const router = useRouter();
  const { can } = usePermissions();
  const byDimension = Object.fromEntries(scores.map((s) => [s.dimension, s]));

  if (!can("score_deal")) {
    return (
      <section>
        <h2 className="text-base font-semibold text-navy">Scoring</h2>
        <p className="mt-2 text-sm text-text-muted">
          You do not have permission to score deals.
        </p>
      </section>
    );
  }

  return (
    <section>
      <h2 className="text-base font-semibold text-navy">Scoring</h2>
      {dimensions.length === 0 ? (
        <p className="mt-2 text-sm text-text-muted">
          No scoring dimensions configured.
        </p>
      ) : (
        <>
          <div className="mt-4 grid gap-3">
            {dimensions.map((d) => (
              <DimensionRow
                key={d.config_key}
                dealId={dealId}
                dimension={d.config_key}
                label={d.config_value}
                weight={byDimension[d.config_key]?.weight ?? 0.1667}
                existing={byDimension[d.config_key]}
                onSaved={() => router.refresh()}
              />
            ))}
          </div>
          <div className="mt-5 rounded-lg border border-border bg-bg-card p-4">
            <p className="text-3xl font-semibold text-navy tabular-nums">
              {composite != null ? Number(composite).toFixed(1) : "—"}
            </p>
            <p className="text-xs font-medium uppercase tracking-wide text-text-muted">
              Composite Score
            </p>
            <div className="mt-3">
              <ScoreBar score={composite} label="" />
            </div>
          </div>
        </>
      )}
    </section>
  );
}
