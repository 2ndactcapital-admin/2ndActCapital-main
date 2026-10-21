import { commitmentView } from "@/lib/spvCommitment.mjs";

// BUGFIX1 (SPV-01) — shared by the cap table, the SPV list card and the
// dashboard SPV card. Renders the server's commitment summary as-is.
export default function CommitmentProgress({ commitment, compact = false }) {
  const v = commitmentView(commitment);
  if (!v) return null;
  return (
    <div className="mt-2" data-testid="commitment-progress">
      <div className="mb-1 flex flex-wrap justify-between gap-x-3 text-xs text-[var(--2a-text-muted)]">
        <span className="font-medium text-[var(--2a-text)]">{v.committedText}</span>
        {v.pctText && (
          <span>
            {v.pctText}
            {v.overTargetText && (
              <span className="ml-1 font-semibold" style={{ color: "#9B2335" }}>
                {"· "}{v.overTargetText}
              </span>
            )}
          </span>
        )}
      </div>
      {v.barPct !== null && (
        <div className="h-1.5 w-full rounded-full bg-[var(--2a-bg-sidebar)]">
          <div
            className="h-1.5 rounded-full"
            style={{ width: `${v.barPct}%`, backgroundColor: "var(--2a-gold)" }}
          />
        </div>
      )}
      {!compact && (
        <p className="mt-1 text-xs text-[var(--2a-text-muted)]">
          {v.softText}{" · "}{v.hardText}
        </p>
      )}
    </div>
  );
}
