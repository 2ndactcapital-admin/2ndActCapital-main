"use client";

function fmt(v, opts = {}) {
  if (v == null) return "—";
  const n = Number(v);
  if (!Number.isFinite(n)) return "—";
  return n.toLocaleString("en-US", {
    style: "currency",
    currency: "USD",
    maximumFractionDigits: 0,
    ...opts,
  });
}

function fmtPct(v) {
  if (v == null) return "—";
  return `${Number(v).toFixed(1)}%`;
}

export default function CapTable({
  spv_name,
  total_committed,
  target_raise,
  subscriptions = [],
}) {
  if (!subscriptions.length) {
    return (
      <div className="mt-2 rounded border border-[#ece8dd] bg-white px-3 py-3 text-sm text-[#64748B]">
        No subscriptions yet for {spv_name || "this SPV"}.
      </div>
    );
  }

  const pct =
    target_raise && total_committed != null
      ? Math.min(100, Math.round((total_committed / target_raise) * 100))
      : null;

  return (
    <div className="mt-2 rounded border border-[#ece8dd] bg-white px-3 py-3">
      <p className="mb-1 text-xs font-semibold uppercase tracking-wide text-[#C5A880]">
        {spv_name || "Cap Table"}
      </p>
      <div className="mb-2 flex items-center justify-between text-xs text-[#64748B]">
        <span>{fmt(total_committed)} committed</span>
        {target_raise && <span>{pct}% of {fmt(target_raise)}</span>}
      </div>
      {pct !== null && (
        <div className="mb-3 h-1.5 w-full rounded-full bg-[#F5F1EB]">
          <div
            className="h-1.5 rounded-full"
            style={{ width: `${pct}%`, backgroundColor: "#C5A880" }}
          />
        </div>
      )}
      <table className="w-full text-xs">
        <thead>
          <tr className="border-b border-[#E2E8F0] text-[#64748B]">
            <th className="pb-1 text-left font-medium">Subscriber</th>
            <th className="pb-1 text-right font-medium">Committed</th>
            <th className="pb-1 text-right font-medium">%</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-[#E2E8F0]">
          {subscriptions.map((s, i) => (
            <tr key={i}>
              <td className="py-1.5 text-[#0F172A]">{s.entity_name}</td>
              <td className="py-1.5 text-right tabular-nums">{fmt(s.commitment_amount)}</td>
              <td className="py-1.5 text-right tabular-nums text-[#64748B]">
                {fmtPct(s.ownership_pct)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
