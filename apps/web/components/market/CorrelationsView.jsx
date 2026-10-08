import { CARD, CARD_STYLE, CONTROL, ERROR_BOX, ERROR_STYLE, EYEBROW, QUIET } from "@/components/market/marketStyles.mjs";
import { SIGN_NEGATIVE } from "@/lib/market/correlationModel.mjs";

const NAVY = "#1B2B4B";
const ERROR_RED = "#9B2335";
const TRACK = "#E2E8F0";

/**
 * "What moves with it" (mkt04c Task 5), drawn from state it is given. No
 * hooks: tests call it directly and fire its handlers.
 *
 * state: {kind: "empty"} fewer than two series selected — nothing is sent;
 *        {kind: "loading"}; {kind: "error", message};
 *        {kind: "ready", rows (correlationRows), lagConvention}.
 * r is shown exactly as the server returned it; the bar's width and colour
 * come from lib/market/correlationModel.mjs (rBarWidth / rSign).
 *
 * Props: series ([{key, label}]), focusKey, onFocus(key), lag, lagOptions
 * ([{value, label}]), lagEnabled, onLag(value), state.
 */
export default function CorrelationsView({ series, focusKey, onFocus, lag, lagOptions, lagEnabled, onLag, state }) {
  let body;
  if (state.kind === "empty") {
    body = (
      <p className={QUIET} data-correlations="empty">
        Select at least two series to see what moves with them.
      </p>
    );
  } else if (state.kind === "error") {
    body = (
      <div className={ERROR_BOX} style={ERROR_STYLE} role="alert" data-correlations="error">
        {state.message}
      </div>
    );
  } else if (state.kind === "ready") {
    body = (
      <>
        {state.lagConvention && <p className={QUIET}>{state.lagConvention}</p>}
        <ul className="divide-y divide-[var(--2a-border)]" data-correlations="results">
          {state.rows.map((row) => (
            <li key={row.key} className="grid grid-cols-[minmax(0,14rem)_minmax(0,1fr)_auto] items-center gap-3 py-1.5 text-sm" data-correlation-row={row.key}>
              <span className="truncate text-[var(--2a-text)]">{row.name}</span>
              {row.r === null ? (
                <span className="text-xs text-[var(--2a-text-secondary)]" data-correlation-reason={row.key}>
                  {row.reason}
                </span>
              ) : (
                <span className="block h-2 rounded-sm" style={{ background: TRACK }} aria-hidden="true">
                  <span
                    className="block h-2 rounded-sm"
                    style={{ width: row.width, background: row.sign === SIGN_NEGATIVE ? ERROR_RED : NAVY }}
                    data-correlation-bar={row.sign}
                  />
                </span>
              )}
              <span className="flex items-baseline gap-3 text-xs tabular-nums text-[var(--2a-text-secondary)]">
                {row.r !== null && (
                  <span className="text-sm text-[var(--2a-text)]" data-correlation-r={row.key}>
                    {row.r}
                  </span>
                )}
                <span>n {row.n}</span>
                {row.frequency && <span>{row.frequency}</span>}
              </span>
            </li>
          ))}
        </ul>
      </>
    );
  } else {
    body = (
      <p className={QUIET} role="status" data-correlations="loading">
        Comparing changes…
      </p>
    );
  }

  const canChoose = state.kind !== "empty";
  return (
    <section className={`${CARD} min-w-0 space-y-3 p-4`} style={CARD_STYLE} data-market="correlations">
      <h2 className="text-base font-semibold text-[var(--2a-navy)]" style={{ fontFamily: "Spectral, Georgia, serif" }}>
        What moves with it
      </h2>
      {canChoose && (
        <div className="flex flex-wrap items-end gap-4">
          <label className="flex min-w-0 flex-col gap-1">
            <span className={EYEBROW}>Focus</span>
            <select className={CONTROL} value={focusKey ?? ""} onChange={(e) => onFocus(e.target.value)} data-correlations="focus">
              {series.map((s) => (
                <option key={s.key} value={s.key}>
                  {s.label}
                </option>
              ))}
            </select>
          </label>
          <label className="flex flex-col gap-1">
            <span className={EYEBROW}>Lag (months)</span>
            <select
              className={CONTROL}
              value={String(lag)}
              disabled={!lagEnabled}
              onChange={(e) => onLag(lagOptions[e.currentTarget.selectedIndex]?.value ?? 0)}
              data-correlations="lag"
            >
              {lagOptions.map((o) => (
                <option key={o.value} value={String(o.value)}>
                  {o.label}
                </option>
              ))}
            </select>
          </label>
        </div>
      )}
      {body}
      <p className={QUIET} data-correlations="footnote">
        Compares period-to-period changes, not levels, at the coarser frequency of each pair.
      </p>
    </section>
  );
}
