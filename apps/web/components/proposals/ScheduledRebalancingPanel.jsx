/**
 * ScheduledRebalancingPanel — Portfolio → Accounts (DAILYREBAL1).
 *
 * Today's scheduled-rebalancing run from the `schedule` block of GET /proposals/accounts
 * (services.rebalance_schedule.panel): when it ran, whether today's inputs were ready (prices for the last close,
 * the nightly refresh), how many accounts were checked / proposed / skipped and why, and the proposals it made
 * that wait for a person. Every word, status and reason is the server's. Read-only: no control renders here —
 * approval happens on each proposal's own page, behind its own permission envelope. No `schedule` block (or no
 * opted-in account) → nothing.
 */

const card = "rounded-[6px] border border-[#ece8dd] bg-white";

function Fact({ label, children }) {
  return (
    <div className="flex flex-col">
      <span className="text-xs text-text-muted">{label}</span>
      <span className="text-sm text-text-primary">{children}</span>
    </div>
  );
}

function when(iso) {
  if (!iso) return "—";
  return iso.replace("T", " ").slice(0, 16) + " UTC";
}

export default function ScheduledRebalancingPanel({ schedule }) {
  if (!schedule || typeof schedule !== "object") return null;
  const day = schedule.day || null;
  if (!schedule.opted_in_accounts && !day) return null;
  const results = Array.isArray(day?.results) ? day.results : [];
  const proposals = Array.isArray(schedule.proposals) ? schedule.proposals : [];
  return (
    <section className={`${card} p-5`} data-testid="scheduled-rebalancing">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="font-serif text-xl text-navy">Scheduled rebalancing</h2>
        <span className="text-xs text-text-muted">
          {schedule.opted_in_accounts} account{schedule.opted_in_accounts === 1 ? "" : "s"} on a schedule · runs
          business days at {schedule.run_time_local} New York, after prices are complete (or{" "}
          {schedule.prices_fallback_local} on partial prices)
        </span>
      </div>
      {!day ? (
        <p className="mt-3 text-sm text-text-muted" data-testid="schedule-no-run">No run recorded yet.</p>
      ) : (
        <>
          <p className="mt-2 text-sm text-text-muted">
            {schedule.is_today ? "Today" : `Last run day ${day.run_day}`} — <span
              className="text-text-primary" data-testid="schedule-status">{day.status_label}</span>
            {day.waiting_reason ? <span> · {day.waiting_reason}</span> : null}
          </p>
          <div className="mt-4 grid grid-cols-2 gap-4 sm:grid-cols-4">
            <Fact label="Started">{when(day.started_at)}</Fact>
            <Fact label="Prices for the last close">
              {day.prices_day || "—"} · {day.prices_status || "no day row"}
              {day.partial_prices ? (
                <span className="block text-xs text-[#9B2335]" data-testid="schedule-partial">
                  Ran on partial prices — oldest valuation date {day.oldest_price_date || "—"}
                </span>
              ) : null}
            </Fact>
            <Fact label="Nightly refresh">{day.nightly_ok ? "Ran" : "Not yet"}</Fact>
            <Fact label="Accounts">
              <span data-testid="schedule-counts">
                {day.checked} checked · {day.proposed} proposed · {day.skipped} skipped
                {day.failed ? ` · ${day.failed} failed` : ""}
              </span>
            </Fact>
          </div>
          {results.length ? (
            <table className="mt-4 w-full text-sm">
              <thead className="border-b border-[#ece8dd] text-left text-text-muted">
                <tr>
                  <th className="py-2 pr-4 font-medium">Account</th>
                  <th className="py-2 pr-4 font-medium">Outcome</th>
                  <th className="py-2 font-medium">Why</th>
                </tr>
              </thead>
              <tbody>
                {results.map((r) => (
                  <tr key={r.account_id} className="border-b border-[#ece8dd] last:border-0">
                    <td className="py-2 pr-4">
                      <a className="text-navy hover:underline" href={`/portfolio/accounts/${r.account_id}`}>
                        {r.account || r.account_id}
                      </a>
                      {r.household ? <span className="ml-2 text-xs text-text-muted">{r.household}</span> : null}
                    </td>
                    <td className="py-2 pr-4">{r.action_label} — {r.reason_label}</td>
                    <td className="py-2 text-text-muted">{r.detail}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : null}
          {Array.isArray(day.errors) && day.errors.length ? (
            <ul className="mt-3 list-disc pl-5 text-sm text-[#9B2335]" data-testid="schedule-errors">
              {day.errors.map((e, i) => <li key={i}>{e}</li>)}
            </ul>
          ) : null}
        </>
      )}
      {proposals.length ? (
        <div className="mt-4" data-testid="schedule-proposals">
          <h3 className="text-sm font-medium text-navy">Proposals from this run</h3>
          <ul className="mt-1 space-y-1 text-sm">
            {proposals.map((p) => (
              <li key={p.id}>
                <a className="text-navy hover:underline" href={`/portfolio/proposals/${p.id}`}>
                  {p.trigger_label} — {p.status_label}
                </a>
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </section>
  );
}
