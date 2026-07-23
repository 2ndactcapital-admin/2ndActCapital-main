"use client";

import { useMemo } from "react";
import { IconStarFilled } from "@tabler/icons-react";
import DataGrid from "@/components/ui/DataGrid";
import StatusBadge from "@/components/marketplace/StatusBadge";
import DealCard from "@/components/marketplace/DealCard";
import VoteButtons from "@/components/marketplace/VoteButtons";
import {
  formatCurrency,
  formatPercent,
  formatMonths,
} from "@/lib/format";

// Migrated onto the shared DataGrid (Grid UX mini-sprint B). The desktop
// table is now a DataGrid; the mobile card layout is untouched. Every column,
// its formatting, the featured star + name link + status badge, the score
// coloring, the staff-only Interest column, the VoteButtons widget, and the
// row "View" link are all preserved — the grid adds quick-search /
// column-picker / column-reorder on top.
//
// Note: we deliberately do NOT wire onRowClick navigation, because a row
// contains the interactive VoteButtons widget — a whole-row click handler
// would hijack vote clicks. Navigation stays on the name link and the
// trailing "View" link, exactly as before.

function ScoreCell({ score }) {
  if (score == null) return <span className="text-text-muted">—</span>;
  const n = Number(score);
  const color =
    n >= 75 ? "text-[#166534]" : n >= 50 ? "text-navy" : "text-[#9B2335]";
  return <span className={`font-semibold tabular-nums ${color}`}>{n.toFixed(1)}</span>;
}

function buildColumnDefs(staff, stageLabels) {
  const defs = [
    {
      field: "name",
      headerName: "Name",
      minWidth: 200,
      cell: (value, deal) => (
        <div>
          <div className="flex items-center gap-2">
            {deal.is_featured && (
              <IconStarFilled size={12} className="shrink-0 text-gold" />
            )}
            <a
              href={`/marketplace/${deal.id}`}
              className="font-medium text-navy hover:underline line-clamp-2"
            >
              {value}
            </a>
          </div>
          <div className="mt-1">
            <StatusBadge status={deal.deal_status} />
          </div>
        </div>
      ),
    },
    {
      field: "asset_class_label",
      headerName: "Asset Class",
      cell: (value) =>
        value ? (
          <span className="rounded-md bg-gold-light px-1.5 py-0.5 text-xs font-medium text-navy">
            {value}
          </span>
        ) : (
          <span className="text-text-muted">—</span>
        ),
    },
    {
      field: "deal_stage",
      headerName: "Stage",
      cell: (value) => stageLabels[value] || value || "—",
    },
    {
      field: "target_raise",
      headerName: "Target",
      cell: (value) => (
        <span className="tabular-nums">
          {formatCurrency(value, { compact: true })}
        </span>
      ),
    },
    {
      field: "minimum_investment",
      headerName: "Min",
      cell: (value) => (
        <span className="tabular-nums">
          {formatCurrency(value, { compact: true })}
        </span>
      ),
    },
    {
      field: "expected_return_pct",
      headerName: "Return",
      cell: (value) => (
        <span className="tabular-nums">{formatPercent(value)}</span>
      ),
    },
    {
      field: "term_months",
      headerName: "Term",
      cell: (value) => (
        <span className="tabular-nums">{formatMonths(value)}</span>
      ),
    },
    {
      field: "composite_score",
      headerName: "Score",
      cell: (value) => <ScoreCell score={value} />,
    },
  ];

  if (staff) {
    defs.push({
      field: "has_indicated_interest",
      headerName: "Interest",
      align: "center",
      enableSorting: false,
      cell: (value) =>
        value ? (
          <span className="text-xs font-medium text-[#166534]">✓</span>
        ) : (
          <span className="text-text-muted">—</span>
        ),
    });
  }

  defs.push({
    field: "votes",
    headerName: "Votes",
    enableSorting: false,
    cell: (_value, deal) => (
      <VoteButtons
        dealId={deal.id}
        initialUpvotes={deal.upvotes}
        initialDownvotes={deal.downvotes}
        initialUserVote={deal.user_vote}
      />
    ),
  });

  defs.push({
    field: "view",
    headerName: "",
    align: "right",
    enableSorting: false,
    cell: (_value, deal) => (
      <a
        href={`/marketplace/${deal.id}`}
        className="text-xs font-medium text-navy hover:underline"
      >
        View →
      </a>
    ),
  });

  return defs;
}

export default function DealsTable({ deals = [], staff = false, stageLabels = {} }) {
  const columnDefs = useMemo(
    () => buildColumnDefs(staff, stageLabels),
    [staff, stageLabels],
  );

  // Preserve the previous default view: newest first.
  const rowData = useMemo(
    () =>
      [...deals].sort((a, b) =>
        String(b.created_at ?? "").localeCompare(String(a.created_at ?? "")),
      ),
    [deals],
  );

  return (
    <>
      {/* Mobile: cards */}
      <div className="md:hidden space-y-4">
        {rowData.map((deal) => (
          <DealCard key={deal.id} deal={deal} />
        ))}
      </div>

      {/* Desktop: DataGrid */}
      <div className="hidden md:block rounded-lg border border-border bg-bg-card p-4">
        <DataGrid
          gridId="marketplace-deals"
          columnDefs={columnDefs}
          rowData={rowData}
          getRowId={(row) => row.id}
          quickFilterPlaceholder="Search deals…"
          emptyMessage="No deals match your filters"
        />
      </div>
    </>
  );
}
