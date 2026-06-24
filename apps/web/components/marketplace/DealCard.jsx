import { IconStarFilled } from "@tabler/icons-react";
import StatusBadge from "@/components/marketplace/StatusBadge";
import ScoreBar from "@/components/marketplace/ScoreBar";
import VoteButtons from "@/components/marketplace/VoteButtons";
import { formatCurrency, formatPercent, formatMonths } from "@/lib/format";

function Metric({ label, value }) {
  return (
    <div>
      <dt className="text-[11px] font-medium uppercase tracking-wide text-text-muted">
        {label}
      </dt>
      <dd className="text-sm font-medium text-text-primary tabular-nums">
        {value}
      </dd>
    </div>
  );
}

export default function DealCard({ deal }) {
  const sponsor = deal.sponsor_name_override || deal.sponsor_name;
  return (
    <div className="flex flex-col rounded-lg border border-border bg-bg-card p-5 transition-shadow hover:shadow-sm">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            {deal.is_featured && (
              <span className="inline-flex items-center gap-1 rounded-full bg-gold px-2 py-0.5 text-[11px] font-semibold text-navy">
                <IconStarFilled size={12} /> Featured
              </span>
            )}
            <StatusBadge status={deal.deal_status} />
          </div>
          <h3 className="mt-2 truncate text-lg font-medium text-navy">
            {deal.name}
          </h3>
        </div>
        {deal.asset_class && (
          <span className="shrink-0 rounded-md bg-gold-light px-2 py-1 text-xs font-medium text-navy">
            {deal.asset_class}
          </span>
        )}
      </div>

      {sponsor && (
        <p className="mt-1 text-sm text-text-secondary">{sponsor}</p>
      )}

      <dl className="mt-4 grid grid-cols-4 gap-3">
        <Metric label="Target" value={formatCurrency(deal.target_raise, { compact: true })} />
        <Metric label="Min" value={formatCurrency(deal.minimum_investment, { compact: true })} />
        <Metric label="Return" value={formatPercent(deal.expected_return_pct)} />
        <Metric label="Term" value={formatMonths(deal.term_months)} />
      </dl>

      {deal.composite_score != null && (
        <div className="mt-4">
          <ScoreBar score={deal.composite_score} />
        </div>
      )}

      <div className="mt-4 flex items-center justify-between border-t border-border pt-3">
        <VoteButtons
          dealId={deal.id}
          initialUpvotes={deal.upvotes}
          initialDownvotes={deal.downvotes}
          initialUserVote={deal.user_vote}
        />
        <a
          href={`/marketplace/${deal.id}`}
          className="text-sm font-medium text-navy hover:underline"
        >
          View Details →
        </a>
      </div>
    </div>
  );
}
