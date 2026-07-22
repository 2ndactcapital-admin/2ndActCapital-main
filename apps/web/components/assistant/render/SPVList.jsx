"use client";

function formatCurrency(v) {
  if (v == null) return "—";
  return Number(v).toLocaleString("en-US", {
    style: "currency",
    currency: "USD",
    maximumFractionDigits: 0,
  });
}

const STATUS_PILL = {
  open: "bg-[#E8F5E9] text-[#2D6A4F]",
  closing: "bg-[#EEF4FF] text-[var(--2a-navy)]",
  forming: "bg-[var(--2a-bg-sidebar)] text-[var(--2a-text-muted)]",
  closed: "bg-[var(--2a-bg-sidebar)] text-[var(--2a-text-muted)]",
  cancelled: "bg-[#FEF3F2] text-[#9B2335]",
};

export default function SPVList({ spvs = [] }) {
  if (!spvs.length) {
    return <p className="text-sm text-slate-500 mt-2">No open SPVs at the moment.</p>;
  }
  return (
    <ul className="mt-2 space-y-2">
      {spvs.map((s) => {
        const pct =
          s.target_raise && s.total_committed != null
            ? Math.min(100, Math.round((s.total_committed / s.target_raise) * 100))
            : null;
        return (
          <li
            key={s.id}
            className="rounded border border-[#ece8dd] bg-white px-3 py-2.5"
          >
            <div className="flex items-start justify-between gap-2">
              <div className="min-w-0">
                <p className="text-sm font-medium text-[var(--2a-text)] truncate">{s.name}</p>
                {s.min_commitment && (
                  <p className="text-xs text-[var(--2a-text-muted)]">
                    Min. {formatCurrency(s.min_commitment)}
                    {s.close_date ? ` · closes ${s.close_date}` : ""}
                  </p>
                )}
              </div>
              <span
                className={`shrink-0 rounded-full px-2 py-0.5 text-xs font-medium ${
                  STATUS_PILL[s.status] || "bg-[var(--2a-bg-sidebar)] text-[var(--2a-text-muted)]"
                }`}
              >
                {s.status}
              </span>
            </div>
            {pct !== null && (
              <div className="mt-1.5">
                <div className="h-1 w-full rounded-full bg-[var(--2a-bg-sidebar)]">
                  <div
                    className="h-1 rounded-full"
                    style={{ width: `${pct}%`, backgroundColor: "var(--2a-gold)" }}
                  />
                </div>
                <p className="mt-0.5 text-xs text-[var(--2a-text-muted)]">
                  {formatCurrency(s.total_committed)} of {formatCurrency(s.target_raise)} ({pct}%)
                </p>
              </div>
            )}
          </li>
        );
      })}
    </ul>
  );
}
