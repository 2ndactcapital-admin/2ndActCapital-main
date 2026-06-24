"use client";

import { useState } from "react";
import {
  IconChevronUp,
  IconChevronDown,
  IconSelector,
  IconStarFilled,
} from "@tabler/icons-react";
import StatusBadge from "@/components/marketplace/StatusBadge";
import DealCard from "@/components/marketplace/DealCard";
import VoteButtons from "@/components/marketplace/VoteButtons";
import {
  formatCurrency,
  formatPercent,
  formatMonths,
} from "@/lib/format";

function SortIcon({ field, sortField, sortDir }) {
  if (sortField !== field) return <IconSelector size={14} className="text-text-muted" />;
  return sortDir === "asc" ? (
    <IconChevronUp size={14} className="text-navy" />
  ) : (
    <IconChevronDown size={14} className="text-navy" />
  );
}

function Th({ label, field, sortField, sortDir, onSort, className = "" }) {
  const active = sortField === field;
  return (
    <th
      className={`whitespace-nowrap px-3 py-2.5 text-left text-[11px] font-semibold uppercase tracking-wide ${
        active ? "text-navy" : "text-text-muted"
      } ${className}`}
    >
      <button
        type="button"
        onClick={() => onSort(field)}
        className="inline-flex items-center gap-1 hover:text-navy"
      >
        {label}
        <SortIcon field={field} sortField={sortField} sortDir={sortDir} />
      </button>
    </th>
  );
}

function ScoreCell({ score }) {
  if (score == null) return <span className="text-text-muted">—</span>;
  const n = Number(score);
  const color =
    n >= 75 ? "text-[#166534]" : n >= 50 ? "text-navy" : "text-[#9B2335]";
  return <span className={`font-semibold tabular-nums ${color}`}>{n.toFixed(1)}</span>;
}

export default function DealsTable({ deals = [], staff = false, stageLabels = {} }) {
  const [sortField, setSortField] = useState("created_at");
  const [sortDir, setSortDir] = useState("desc");

  function onSort(field) {
    if (sortField === field) {
      setSortDir((d) => (d === "asc" ? "desc" : "asc"));
    } else {
      setSortField(field);
      setSortDir("asc");
    }
  }

  const sorted = [...deals].sort((a, b) => {
    const mul = sortDir === "asc" ? 1 : -1;
    const av = a[sortField] ?? "";
    const bv = b[sortField] ?? "";
    if (typeof av === "number" || typeof bv === "number") {
      return ((av ?? -Infinity) - (bv ?? -Infinity)) * mul;
    }
    return String(av).localeCompare(String(bv)) * mul;
  });

  return (
    <>
      {/* Mobile: cards */}
      <div className="md:hidden space-y-4">
        {sorted.map((deal) => (
          <DealCard key={deal.id} deal={deal} />
        ))}
      </div>

      {/* Desktop: table */}
      <div className="hidden md:block overflow-x-auto rounded-lg border border-border">
        <table className="w-full min-w-[900px] border-collapse bg-bg-card text-sm">
          <thead className="border-b border-border bg-bg-app">
            <tr>
              <Th label="Name" field="name" sortField={sortField} sortDir={sortDir} onSort={onSort} className="min-w-[200px]" />
              <Th label="Asset Class" field="asset_class_label" sortField={sortField} sortDir={sortDir} onSort={onSort} />
              <Th label="Stage" field="deal_stage" sortField={sortField} sortDir={sortDir} onSort={onSort} />
              <Th label="Target" field="target_raise" sortField={sortField} sortDir={sortDir} onSort={onSort} />
              <Th label="Min" field="minimum_investment" sortField={sortField} sortDir={sortDir} onSort={onSort} />
              <Th label="Return" field="expected_return_pct" sortField={sortField} sortDir={sortDir} onSort={onSort} />
              <Th label="Term" field="term_months" sortField={sortField} sortDir={sortDir} onSort={onSort} />
              <Th label="Score" field="composite_score" sortField={sortField} sortDir={sortDir} onSort={onSort} />
              {staff && <th className="px-3 py-2.5 text-left text-[11px] font-semibold uppercase tracking-wide text-text-muted">Interest</th>}
              <th className="px-3 py-2.5 text-left text-[11px] font-semibold uppercase tracking-wide text-text-muted">Votes</th>
              <th className="px-3 py-2.5" />
            </tr>
          </thead>
          <tbody className="divide-y divide-border">
            {sorted.length === 0 ? (
              <tr>
                <td colSpan={staff ? 11 : 10} className="px-3 py-10 text-center text-sm text-text-muted">
                  No deals match your filters
                </td>
              </tr>
            ) : (
              sorted.map((deal) => (
                <tr key={deal.id} className="group hover:bg-bg-app/50 transition-colors">
                  <td className="px-3 py-3">
                    <div className="flex items-center gap-2">
                      {deal.is_featured && (
                        <IconStarFilled size={12} className="shrink-0 text-gold" />
                      )}
                      <a
                        href={`/marketplace/${deal.id}`}
                        className="font-medium text-navy hover:underline line-clamp-2"
                      >
                        {deal.name}
                      </a>
                    </div>
                    <div className="mt-1">
                      <StatusBadge status={deal.deal_status} />
                    </div>
                  </td>
                  <td className="px-3 py-3 text-text-secondary">
                    {deal.asset_class_label ? (
                      <span className="rounded-md bg-gold-light px-1.5 py-0.5 text-xs font-medium text-navy">
                        {deal.asset_class_label}
                      </span>
                    ) : (
                      <span className="text-text-muted">—</span>
                    )}
                  </td>
                  <td className="px-3 py-3 text-text-secondary">
                    {stageLabels[deal.deal_stage] || deal.deal_stage || "—"}
                  </td>
                  <td className="px-3 py-3 tabular-nums text-text-secondary">
                    {formatCurrency(deal.target_raise, { compact: true })}
                  </td>
                  <td className="px-3 py-3 tabular-nums text-text-secondary">
                    {formatCurrency(deal.minimum_investment, { compact: true })}
                  </td>
                  <td className="px-3 py-3 tabular-nums text-text-secondary">
                    {formatPercent(deal.expected_return_pct)}
                  </td>
                  <td className="px-3 py-3 tabular-nums text-text-secondary">
                    {formatMonths(deal.term_months)}
                  </td>
                  <td className="px-3 py-3">
                    <ScoreCell score={deal.composite_score} />
                  </td>
                  {staff && (
                    <td className="px-3 py-3 text-center">
                      {deal.has_indicated_interest ? (
                        <span className="text-xs font-medium text-[#166534]">✓</span>
                      ) : (
                        <span className="text-text-muted">—</span>
                      )}
                    </td>
                  )}
                  <td className="px-3 py-3">
                    <VoteButtons
                      dealId={deal.id}
                      initialUpvotes={deal.upvotes}
                      initialDownvotes={deal.downvotes}
                      initialUserVote={deal.user_vote}
                    />
                  </td>
                  <td className="px-3 py-3 text-right">
                    <a
                      href={`/marketplace/${deal.id}`}
                      className="text-xs font-medium text-navy opacity-0 group-hover:opacity-100 hover:underline transition-opacity"
                    >
                      View →
                    </a>
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
    </>
  );
}
