// Horizontal segmented bar showing deal count per pipeline stage.
// stageSummary: [{stage, count}]  stages: [{config_key, config_value}]

const STAGE_COLORS = [
  "var(--2a-gold)", // gold
  "#2C4A3E", // navy
  "#4A7C6B",
  "#6A9E8E",
  "#8ABFB2",
  "#AADAD0",
];

export default function StageBar({ stageSummary = [], stages = [] }) {
  if (!stageSummary.length) return null;

  const labelMap = Object.fromEntries(
    stages.map((s) => [s.config_key, s.config_value])
  );
  const total = stageSummary.reduce((s, r) => s + Number(r.count), 0);
  if (!total) return null;

  return (
    <div className="rounded-lg border border-border bg-bg-card p-4">
      <div className="mb-3 flex items-center justify-between">
        <span className="text-xs font-medium uppercase tracking-wide text-text-muted">
          Deal Pipeline
        </span>
        <span className="text-xs text-text-muted">{total} total</span>
      </div>
      <div className="flex h-4 w-full overflow-hidden rounded-full">
        {stageSummary.map((row, i) => {
          const pct = (Number(row.count) / total) * 100;
          return (
            <div
              key={row.stage}
              style={{
                width: `${pct}%`,
                backgroundColor: STAGE_COLORS[i % STAGE_COLORS.length],
              }}
              title={`${labelMap[row.stage] || row.stage}: ${row.count}`}
            />
          );
        })}
      </div>
      <div className="mt-3 flex flex-wrap gap-x-4 gap-y-1.5">
        {stageSummary.map((row, i) => (
          <div key={row.stage} className="flex items-center gap-1.5">
            <span
              className="inline-block h-2.5 w-2.5 rounded-sm"
              style={{ backgroundColor: STAGE_COLORS[i % STAGE_COLORS.length] }}
            />
            <span className="text-xs text-text-secondary">
              {labelMap[row.stage] || row.stage}
            </span>
            <span className="text-xs font-semibold tabular-nums text-text-primary">
              {row.count}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}
