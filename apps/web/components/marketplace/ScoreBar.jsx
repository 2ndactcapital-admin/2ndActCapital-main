// Gold composite-score progress bar (0–100). Renders nothing if no score.
export default function ScoreBar({ score, label = "Score" }) {
  if (score == null) return null;
  const pct = Math.max(0, Math.min(100, Number(score)));
  return (
    <div>
      <div className="flex items-center justify-between">
        <span className="text-xs font-medium uppercase tracking-wide text-text-muted">
          {label}
        </span>
        <span className="text-sm font-semibold text-navy tabular-nums">
          {pct.toFixed(0)}
        </span>
      </div>
      <div className="mt-1 h-2 w-full overflow-hidden rounded-full bg-border">
        <div
          className="h-full rounded-full bg-gold transition-[width]"
          style={{ width: `${pct}%` }}
        />
      </div>
    </div>
  );
}
