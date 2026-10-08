import { CARD, CARD_STYLE, EYEBROW } from "@/components/market/marketStyles.mjs";

/**
 * "Trends and outliers" (mkt04b Task 4g). Renders lib/market/chartModel.mjs
 * model.table as given: series ordered by distance from the median at the
 * latest date, with Since anchor, 12 mo and the pack status.
 */
export default function TrendsTable({ rows }) {
  if (!rows.length) return null;
  return (
    <section className={`${CARD} min-w-0 overflow-x-auto p-4`} style={CARD_STYLE} data-market="trends-table">
      <h2 className="mb-2 text-base font-semibold text-[var(--2a-navy)]" style={{ fontFamily: "Spectral, Georgia, serif" }}>
        Trends and outliers
      </h2>
      <table className="min-w-full text-sm">
        <thead>
          <tr className="border-b border-[var(--2a-border)] text-left">
            {["Series", "Group", "Since anchor", "12 mo", "Status"].map((h, i) => (
              <th key={h} className={`${EYEBROW} py-1.5 pr-4 font-bold ${i >= 2 && i <= 3 ? "text-right" : ""}`}>
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.key} className="border-b border-[var(--2a-border)] last:border-b-0" data-trend-row={r.key} data-status={r.status ?? ""}>
              <td className="py-1.5 pr-4">
                <span className="inline-flex items-center gap-2 text-[var(--2a-text)]">
                  <span aria-hidden="true" className="inline-block h-2.5 w-2.5 shrink-0 rounded-sm" style={{ background: r.color }} />
                  {r.label}
                </span>
              </td>
              <td className="py-1.5 pr-4 text-[var(--2a-text-secondary)]">{r.group}</td>
              <td className="py-1.5 pr-4 text-right tabular-nums text-[var(--2a-text)]">{r.since}</td>
              <td className="py-1.5 pr-4 text-right tabular-nums text-[var(--2a-text)]">{r.twelve}</td>
              <td className="py-1.5 text-[var(--2a-text-secondary)]">{r.statusText}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}
