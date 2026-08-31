"use client";

/**
 * ProfitabilityDashboard — fee39.
 *
 * Deliberately minimal. The sprint's deliverable is that the NUMBERS are
 * right; a polished screen is a later pass. What this proves is that the eight
 * cuts and the worst-margin ranking reach a real UI intact, in the fixed line
 * order, with the server's own labels.
 *
 * NOTHING HERE IS HARDCODED THAT THE SERVER PUBLISHES
 * ─────────────────────────────────────────────────────────────────────────
 * The seven P&L rows, their labels, which of them are costs, the list of cuts
 * and the two ranking keys all come from `vocabularies` on the response.
 * Rule 1, and specifically: the line ORDER is a decision about how the firm
 * argues about client profitability. A copy of that order living in this file
 * would be a second copy, free to drift from the one the service applies.
 *
 * This screen is READ-ONLY and there is no write endpoint behind it, so it
 * renders no write control at all. `permissions.can_write` is still read and
 * still honoured with no truthy fallback — if a write surface is ever added
 * here, the gate is already in the right shape rather than needing to be
 * remembered.
 *
 * WHY THE WARNINGS AND CAVEATS ARE RENDERED, NOT SWALLOWED
 * ─────────────────────────────────────────────────────────────────────────
 * `pnl.warnings` carries the possible-duplicate-cost notice (fee37 F4) and
 * `pnl.caveats` the unverified-rate one (fee37 F6). Both are computed from the
 * rows actually summed, so they appear only when they apply. A dashboard that
 * dropped them would present an order-of-magnitude figure as a bill.
 */

import { useCallback, useEffect, useMemo, useState } from "react";

const money = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  maximumFractionDigits: 0,
});

// Amounts arrive as exact decimal STRINGS. Number() is used for DISPLAY only —
// never fed back into a calculation, and never sent anywhere.
function fmt(value, { negate = false } = {}) {
  if (value === null || value === undefined) return "—";
  const n = Number(value) * (negate ? -1 : 1);
  if (Number.isNaN(n)) return String(value);
  return n < 0 ? `(${money.format(Math.abs(n))})` : money.format(n);
}

function fmtPct(value) {
  if (value === null || value === undefined) return "—";
  const n = Number(value);
  if (Number.isNaN(n)) return String(value);
  return `${(n * 100).toFixed(1)}%`;
}

const CUT_LABELS = {
  FIRM: "Whole firm",
  HOUSEHOLD: "One household",
  HOUSEHOLDS: "Several households",
  ACCOUNT: "One account",
  ACCOUNTS: "Several accounts",
  BILLING_GROUP: "Billing group",
  ADVISOR: "Advisor",
  PRODUCT_TYPE: "Product",
};

// Which query parameter each cut needs. A cut whose value is blank is not
// requested at all, rather than sent empty and refused with a 422 the operator
// has to read.
const CUT_PARAM = {
  ACCOUNT: "account_id",
  ACCOUNTS: "account_ids",
  HOUSEHOLD: "household_id",
  HOUSEHOLDS: "household_ids",
  BILLING_GROUP: "billing_group_id",
  ADVISOR: "advisor_id",
  PRODUCT_TYPE: "product_type",
};

export default function ProfitabilityDashboard() {
  const [kind, setKind] = useState("FIRM");
  const [value, setValue] = useState("");
  const [periodStart, setPeriodStart] = useState("");
  const [periodEnd, setPeriodEnd] = useState("");
  const [rankBy, setRankBy] = useState("net_profit");

  const [pnl, setPnl] = useState(null);
  const [vocab, setVocab] = useState(null);
  const [permissions, setPermissions] = useState(null);
  const [ranked, setRanked] = useState([]);
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(true);

  const query = useMemo(() => {
    const p = new URLSearchParams({ kind });
    const param = CUT_PARAM[kind];
    if (param && value.trim()) {
      // The list cuts take repeated parameters, which is what FastAPI's
      // list[str] Query expects — not a comma-joined single value.
      for (const v of value.split(",").map((s) => s.trim()).filter(Boolean)) {
        p.append(param, v);
      }
    }
    if (periodStart) p.set("period_start", periodStart);
    if (periodEnd) p.set("period_end", periodEnd);
    return p;
  }, [kind, value, periodStart, periodEnd]);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const rank = new URLSearchParams({ rank_by: rankBy });
      if (periodStart) rank.set("period_start", periodStart);
      if (periodEnd) rank.set("period_end", periodEnd);

      const [pnlRes, rankRes] = await Promise.all([
        fetch(`/api/profitability/pnl?${query}`),
        fetch(`/api/profitability/households-by-margin?${rank}`),
      ]);
      if (!pnlRes.ok) throw new Error(`P&L request failed (${pnlRes.status})`);
      if (!rankRes.ok) throw new Error(`Ranking request failed (${rankRes.status})`);

      const pnlBody = await pnlRes.json();
      const rankBody = await rankRes.json();
      setPnl(pnlBody.pnl);
      setVocab(pnlBody.vocabularies);
      setPermissions(pnlBody.permissions);
      setRanked(rankBody.rows || []);
    } catch (err) {
      setError(err.message);
      setPnl(null);
    } finally {
      setLoading(false);
    }
  }, [query, rankBy, periodStart, periodEnd]);

  useEffect(() => {
    load();
  }, [load]);

  // No truthy fallback. A missing envelope means no write surface, not a
  // silently restored one.
  const canWrite = permissions?.can_write === true;
  const cutKinds = vocab?.cut_kinds ?? [];
  const rankKeys = vocab?.rank_keys ?? [];
  const needsValue = Boolean(CUT_PARAM[kind]);

  return (
    <div className="space-y-6">
      {/* ── controls ───────────────────────────────────────────────────── */}
      <div className="rounded-md border border-[#ece8dd] bg-white p-4">
        <div className="flex flex-wrap items-end gap-4">
          <label className="flex flex-col gap-1">
            <span className="text-[11px] font-bold uppercase tracking-[0.18em] text-[var(--2a-text-tertiary)]">
              Cut
            </span>
            <select
              value={kind}
              onChange={(e) => {
                setKind(e.target.value);
                setValue("");
              }}
              className="rounded border border-[#E2E8F0] px-3 py-2 text-sm"
            >
              {cutKinds.map((k) => (
                <option key={k} value={k}>
                  {CUT_LABELS[k] ?? k}
                </option>
              ))}
            </select>
          </label>

          {needsValue && (
            <label className="flex flex-col gap-1">
              <span className="text-[11px] font-bold uppercase tracking-[0.18em] text-[var(--2a-text-tertiary)]">
                {kind.endsWith("S") ? "Ids (comma separated)" : "Id"}
              </span>
              <input
                value={value}
                onChange={(e) => setValue(e.target.value)}
                placeholder={kind === "PRODUCT_TYPE" ? "ASSET_MANAGEMENT" : "uuid"}
                className="w-[26rem] rounded border border-[#E2E8F0] px-3 py-2 font-mono text-xs"
              />
            </label>
          )}

          <label className="flex flex-col gap-1">
            <span className="text-[11px] font-bold uppercase tracking-[0.18em] text-[var(--2a-text-tertiary)]">
              From
            </span>
            <input
              type="date"
              value={periodStart}
              onChange={(e) => setPeriodStart(e.target.value)}
              className="rounded border border-[#E2E8F0] px-3 py-2 text-sm"
            />
          </label>
          <label className="flex flex-col gap-1">
            <span className="text-[11px] font-bold uppercase tracking-[0.18em] text-[var(--2a-text-tertiary)]">
              To
            </span>
            <input
              type="date"
              value={periodEnd}
              onChange={(e) => setPeriodEnd(e.target.value)}
              className="rounded border border-[#E2E8F0] px-3 py-2 text-sm"
            />
          </label>
        </div>
      </div>

      {error && (
        <div className="rounded-md border border-[#9B2335] bg-white p-4 text-sm text-[#9B2335]">
          {error}
        </div>
      )}

      {/* ── the P&L, in the server's own line order ─────────────────────── */}
      <div className="rounded-md border border-[#ece8dd] bg-white p-5">
        <div className="mb-4 flex items-baseline justify-between">
          <h2
            className="text-lg font-semibold text-[var(--2a-navy)]"
            style={{ fontFamily: "Spectral, Georgia, serif" }}
          >
            {pnl?.cut?.describe ?? "—"}
          </h2>
          <span className="text-sm text-[var(--2a-text-secondary)]">
            Margin {fmtPct(pnl?.margin_pct)}
          </span>
        </div>

        {loading && !pnl ? (
          <p className="text-sm text-[var(--2a-text-secondary)]">Loading…</p>
        ) : (
          <table className="w-full text-sm">
            <tbody>
              {(pnl?.lines ?? []).map((line) => {
                const isMargin = line.key.startsWith("contribution_margin");
                const isNet = line.key === "net_profit";
                return (
                  <tr
                    key={line.key}
                    className={
                      isMargin || isNet
                        ? "border-t border-[#ece8dd] font-semibold text-[var(--2a-navy)]"
                        : "text-[var(--2a-text-secondary)]"
                    }
                  >
                    <td className="py-2 pr-4">{line.label}</td>
                    <td className="py-2 text-right font-mono tabular-nums">
                      {/* Cost lines arrive as positive magnitudes; showing them
                          negated is what makes the column add up on the page. */}
                      {fmt(line.amount, { negate: line.is_cost })}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}

        <p className="mt-3 text-xs text-[var(--2a-text-tertiary)]">
          {pnl?.revenue_rows ?? 0} revenue rows · {pnl?.cost_rows ?? 0} cost rows
        </p>

        {(pnl?.warnings ?? []).map((w) => (
          <p key={w} className="mt-3 text-xs text-[#9B2335]">
            {w}
          </p>
        ))}
        {(pnl?.caveats ?? []).map((c) => (
          <p key={c} className="mt-3 text-xs text-[var(--2a-text-tertiary)]">
            {c}
          </p>
        ))}
      </div>

      {/* ── households, worst first ─────────────────────────────────────── */}
      <div className="rounded-md border border-[#ece8dd] bg-white p-5">
        <div className="mb-4 flex items-baseline justify-between">
          <h2
            className="text-lg font-semibold text-[var(--2a-navy)]"
            style={{ fontFamily: "Spectral, Georgia, serif" }}
          >
            Households, worst margin first
          </h2>
          <label className="flex items-center gap-2 text-xs text-[var(--2a-text-secondary)]">
            Rank by
            <select
              value={rankBy}
              onChange={(e) => setRankBy(e.target.value)}
              className="rounded border border-[#E2E8F0] px-2 py-1 text-xs"
            >
              {rankKeys.map((k) => (
                <option key={k} value={k}>
                  {k === "net_profit" ? "Net profit ($)" : "Margin (%)"}
                </option>
              ))}
            </select>
          </label>
        </div>

        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-[#ece8dd] text-left text-[11px] uppercase tracking-[0.16em] text-[var(--2a-text-tertiary)]">
              <th className="py-2 pr-4 font-bold">Household</th>
              <th className="py-2 pr-4 text-right font-bold">Revenue</th>
              <th className="py-2 pr-4 text-right font-bold">Margin (direct)</th>
              <th className="py-2 pr-4 text-right font-bold">Net profit</th>
              <th className="py-2 text-right font-bold">Margin</th>
            </tr>
          </thead>
          <tbody>
            {ranked.length === 0 && (
              <tr>
                <td colSpan={5} className="py-4 text-[var(--2a-text-secondary)]">
                  No households with revenue or costs in this period.
                </td>
              </tr>
            )}
            {ranked.map((row) => (
              <tr
                key={row.household_id ?? "unhoused"}
                className="border-b border-[#F5F1EB]"
              >
                <td className="py-2 pr-4">
                  {row.household_name ?? (
                    <span className="text-[var(--2a-text-tertiary)]">
                      Unhouseholded
                    </span>
                  )}
                </td>
                <td className="py-2 pr-4 text-right font-mono tabular-nums">
                  {fmt(row.gross_revenue)}
                </td>
                <td className="py-2 pr-4 text-right font-mono tabular-nums">
                  {fmt(row.contribution_margin_direct)}
                </td>
                <td
                  className={`py-2 pr-4 text-right font-mono tabular-nums ${
                    Number(row.net_profit) < 0 ? "text-[#9B2335]" : ""
                  }`}
                >
                  {fmt(row.net_profit)}
                </td>
                <td className="py-2 text-right font-mono tabular-nums">
                  {fmtPct(row.margin_pct)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {canWrite && (
        // Unreachable today — this surface publishes can_write: false always,
        // because it has no write endpoint. Kept so that adding one starts from
        // a gate rather than from nothing.
        <div className="text-xs text-[var(--2a-text-tertiary)]">
          Write controls would render here.
        </div>
      )}
    </div>
  );
}
