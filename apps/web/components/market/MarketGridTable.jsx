import { CARD, CARD_STYLE, QUIET } from "@/components/market/marketStyles.mjs";

/**
 * The grid table (mkt04a Task 5c-5d). Renders a view model from
 * lib/market/gridView.mjs buildGridView and nothing else: the date column,
 * one column per series (server colour + name, notes for floating, warnings
 * and unavailable series), cells exactly as the model gives them.
 *
 * Built here rather than on components/ui/DataGrid.jsx: DataGrid sorts,
 * filters, paginates and drag-reorders client-side, and has no sticky header
 * or rich header cell. This grid must keep the server's row order and show
 * every row, so a plain table in the same visual style is the smaller change.
 *
 * The container scrolls both ways, so a wide grid never scrolls the page.
 */
export default function MarketGridTable({ view }) {
  if (view.rows.length === 0) {
    return (
      <p className={`${QUIET} py-10 text-center`} data-market="grid-no-rows">
        No rows for this window.
      </p>
    );
  }
  return (
    <div
      className={`${CARD} max-h-[70vh] max-w-full overflow-auto`}
      style={CARD_STYLE}
      data-market="grid-table"
    >
      <table className="min-w-full border-separate border-spacing-0 text-sm">
        <thead>
          <tr>
            <th className="sticky left-0 top-0 z-20 border-b border-[var(--2a-border)] bg-[var(--2a-bg)] px-3 py-2 text-left font-semibold text-[var(--2a-text-secondary)]">
              Date
            </th>
            {view.columns.map((c) => (
              <th
                key={c.key}
                className="sticky top-0 z-10 border-b border-[var(--2a-border)] bg-[var(--2a-bg)] px-3 py-2 text-right align-bottom font-normal"
                data-series={c.key}
                data-floating={c.floating ? "true" : "false"}
              >
                <span
                  className={`inline-flex items-center gap-1.5 whitespace-nowrap font-semibold text-[var(--2a-navy)] ${
                    c.floating ? "border-b border-dashed border-[var(--2a-text-muted)]" : ""
                  }`}
                >
                  <span aria-hidden="true" className="inline-block h-2.5 w-2.5 rounded-sm" style={{ background: c.color }} />
                  {c.label}
                </span>
                {c.notes.map((n) => (
                  <span key={n} className="block whitespace-nowrap text-[11px] text-[var(--2a-text-muted)]" data-market="column-note">
                    {n}
                  </span>
                ))}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {view.rows.map((r) => (
            <tr key={r.date}>
              <td className="sticky left-0 whitespace-nowrap border-b border-[var(--2a-border)] bg-white px-3 py-1.5 tabular-nums text-[var(--2a-text-secondary)]">
                {r.date}
              </td>
              {r.cells.map((cell, i) =>
                cell.kind === "skip" ? null : cell.kind === "unavailable" ? (
                  <td
                    key={view.columns[i].key}
                    rowSpan={cell.rowSpan}
                    className="border-b border-[var(--2a-border)] px-3 py-1.5 text-center align-top text-xs italic text-[var(--2a-text-muted)]"
                    data-market="column-unavailable"
                  >
                    {cell.text}
                  </td>
                ) : (
                  <td
                    key={view.columns[i].key}
                    className="whitespace-nowrap border-b border-[var(--2a-border)] px-3 py-1.5 text-right tabular-nums text-[var(--2a-text)]"
                  >
                    {cell.text}
                  </td>
                ),
              )}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
