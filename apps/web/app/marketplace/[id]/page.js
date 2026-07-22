import { redirect, notFound } from "next/navigation";
import { auth0 } from "@/lib/auth0";
import AppShell from "@/components/AppShell";
import StatusBadge from "@/components/marketplace/StatusBadge";
import InterestCard from "@/components/marketplace/InterestCard";
import ScoringSection from "@/components/marketplace/ScoringSection";
import DocumentsList from "@/components/marketplace/DocumentsList";
import ComplianceRequests from "@/components/marketplace/ComplianceRequests";
import DealDetailTabBar from "@/components/marketplace/DealDetailTabBar";
import DealStagePipeline from "@/components/marketplace/DealStagePipeline";
import MemberInvestmentTracker from "@/components/marketplace/MemberInvestmentTracker";
import AISummaryCard from "@/components/marketplace/AISummaryCard";
import {
  getDeal,
  getConfig,
  listInvestorEntities,
  getComplianceRequests,
  getAISummary,
  getMemberInvestments,
  getDealClasses,
} from "@/lib/api";
import { isStaff } from "@/lib/roles";
import {
  formatCurrency,
  formatPercent,
  formatMonths,
  formatDate,
} from "@/lib/format";

function Metric({ label, value }) {
  return (
    <div>
      <dt className="text-xs font-medium uppercase tracking-wide text-text-muted">
        {label}
      </dt>
      <dd className="mt-0.5 text-sm font-medium text-text-primary tabular-nums">
        {value}
      </dd>
    </div>
  );
}

export default async function DealDetailPage({ params, searchParams }) {
  const { id } = await params;
  const sp = (await searchParams) || {};
  const tab = typeof sp.tab === "string" ? sp.tab : "overview";

  const session = await auth0.getSession();
  if (!session) {
    redirect(`/auth/login?returnTo=/marketplace/${id}`);
  }
  const staff = isStaff(session.user);

  let detail;
  try {
    detail = await getDeal(id);
  } catch (error) {
    if (error.status === 404) notFound();
    throw error;
  }

  const deal = detail.deal;

  // Load all supporting data in parallel (best-effort).
  const [
    dimensionsRes,
    entitiesRes,
    complianceRes,
    aiSummaryRes,
    memberInvestmentsRes,
    dealStagesRes,
    investmentStagesRes,
    documentStatusesRes,
    dealClassesRes,
  ] = await Promise.allSettled([
    staff ? getConfig("deal_scoring") : Promise.resolve([]),
    listInvestorEntities(),
    staff ? getComplianceRequests(id) : Promise.resolve([]),
    getAISummary(id),
    staff ? getMemberInvestments(id) : Promise.resolve([]),
    getConfig("deal_stages"),
    staff ? getConfig("investment_stages") : Promise.resolve([]),
    staff ? getConfig("document_statuses") : Promise.resolve([]),
    getDealClasses(id),
  ]);

  const dimensions =
    dimensionsRes.status === "fulfilled" ? dimensionsRes.value || [] : [];
  // Server already filters to investor-capable entity types (org-scoped).
  const entities =
    entitiesRes.status === "fulfilled" ? entitiesRes.value || [] : [];
  const complianceRequests =
    complianceRes.status === "fulfilled" ? complianceRes.value || [] : [];
  const aiSummary =
    aiSummaryRes.status === "fulfilled" ? aiSummaryRes.value : null;
  const memberInvestments =
    memberInvestmentsRes.status === "fulfilled"
      ? memberInvestmentsRes.value || []
      : [];
  const dealStages =
    dealStagesRes.status === "fulfilled" ? dealStagesRes.value || [] : [];
  const investmentStages =
    investmentStagesRes.status === "fulfilled"
      ? investmentStagesRes.value || []
      : [];
  const documentStatuses =
    documentStatusesRes.status === "fulfilled"
      ? documentStatusesRes.value || []
      : [];

  // Classes (SPVs) of this investment — the API already filters to
  // member-visible ones for non-staff callers.
  const dealClasses =
    dealClassesRes.status === "fulfilled"
      ? dealClassesRes.value?.classes || []
      : [];

  const sponsor = detail.sponsor_name || deal.sponsor_name_override;

  return (
    <AppShell user={session.user}>
      <nav className="text-sm text-text-muted">
        <a href="/marketplace" className="hover:text-navy">
          Marketplace
        </a>
        <span className="mx-2">›</span>
        <span className="text-text-secondary">{deal.name}</span>
      </nav>

      <div className="-mx-8 mt-3 border-b border-border bg-bg-app px-8 pb-6">
        <h1 className="text-3xl font-semibold text-navy">{deal.name}</h1>
        <div className="mt-2 flex flex-wrap items-center gap-2">
          {deal.asset_class_label && (
            <span className="rounded-md bg-gold-light px-2 py-1 text-xs font-medium text-navy">
              {deal.asset_class_label}
            </span>
          )}
          {deal.asset_super_class_label && (
            <span className="text-xs text-text-muted">
              {deal.asset_super_class_label}
            </span>
          )}
          <StatusBadge status={deal.deal_status} />
        </div>
        {sponsor && (
          <p className="mt-2 text-sm text-text-secondary">{sponsor}</p>
        )}
        {deal.published_at && (
          <p className="mt-1 text-xs text-text-muted">
            Published {formatDate(deal.published_at)}
          </p>
        )}
      </div>

      <div className="mt-8 grid gap-8 lg:grid-cols-[2fr_1fr]">
        {/* LEFT — tabbed */}
        <div>
          <DealDetailTabBar staff={staff} />

          {/* Overview */}
          {tab === "overview" && (
            <div className="mt-6 space-y-8">
              <section>
                <h2 className="text-base font-semibold text-navy">Overview</h2>
                {deal.description && (
                  <p className="mt-3 whitespace-pre-line text-sm text-text-secondary">
                    {deal.description}
                  </p>
                )}
                {deal.location && (
                  <p className="mt-3 text-sm text-text-muted">
                    📍 {deal.location}
                  </p>
                )}
                {(deal.highlights || []).length > 0 && (
                  <ul className="mt-4 space-y-1.5">
                    {deal.highlights.map((h, i) => (
                      <li
                        key={i}
                        className="flex gap-2 text-sm text-text-secondary"
                      >
                        <span className="text-gold">●</span>
                        <span>{h}</span>
                      </li>
                    ))}
                  </ul>
                )}
                {(deal.tags || []).length > 0 && (
                  <div className="mt-4 flex flex-wrap gap-2">
                    {deal.tags.map((t) => (
                      <span
                        key={t}
                        className="rounded-full border border-border bg-bg-card px-2.5 py-0.5 text-xs text-text-secondary"
                      >
                        {t}
                      </span>
                    ))}
                  </div>
                )}
              </section>

              <section>
                <h2 className="text-base font-semibold text-navy">
                  Investment Details
                </h2>
                <dl className="mt-4 grid grid-cols-2 gap-4 rounded-lg border border-border bg-bg-card p-5 sm:grid-cols-3">
                  <Metric
                    label="Target Raise"
                    value={formatCurrency(deal.target_raise)}
                  />
                  <Metric
                    label="Minimum Investment"
                    value={formatCurrency(deal.minimum_investment)}
                  />
                  <Metric
                    label="Expected Return"
                    value={formatPercent(deal.expected_return_pct)}
                  />
                  <Metric
                    label="Term"
                    value={
                      deal.term_months ? `${deal.term_months} months` : "—"
                    }
                  />
                  <Metric label="Deal Date" value={formatDate(deal.deal_date)} />
                  <Metric
                    label="Close Date"
                    value={formatDate(deal.close_date)}
                  />
                </dl>
              </section>
            </div>
          )}

          {/* Documents */}
          {tab === "documents" && (
            <div className="mt-6">
              <DocumentsList
                dealId={deal.id}
                initial={detail.documents || []}
                canUpload={staff}
                canReview={staff}
                documentStatuses={documentStatuses}
              />
            </div>
          )}

          {/* Scoring (staff) */}
          {staff && tab === "scoring" && (
            <div className="mt-6">
              <ScoringSection
                dealId={deal.id}
                dimensions={dimensions}
                scores={detail.scores || []}
                composite={deal.composite_score}
              />
            </div>
          )}

          {/* Pipeline (staff) */}
          {staff && tab === "pipeline" && (
            <div className="mt-6 space-y-8">
              <DealStagePipeline
                dealId={deal.id}
                deal={deal}
                stages={dealStages}
              />
              <MemberInvestmentTracker
                dealId={deal.id}
                initial={memberInvestments}
                investmentStages={investmentStages}
              />
              {complianceRequests.length > 0 && (
                <ComplianceRequests
                  dealId={deal.id}
                  initial={complianceRequests}
                />
              )}
            </div>
          )}
        </div>

        {/* RIGHT (sticky) */}
        <div className="space-y-6 lg:sticky lg:top-6 lg:self-start">
          <AISummaryCard
            dealId={deal.id}
            initialSummary={aiSummary}
            staff={staff}
          />

          <InterestCard
            dealId={deal.id}
            composite={deal.composite_score}
            scores={detail.scores || []}
            upvotes={deal.upvotes}
            downvotes={deal.downvotes}
            userVote={deal.user_vote}
            alreadyInterested={deal.has_indicated_interest}
            entities={entities}
            minimumInvestment={deal.minimum_investment}
          />

          <div className="rounded-lg border border-border bg-bg-card p-5">
            <h3 className="text-xs font-medium uppercase tracking-wide text-text-muted">
              Deal Info
            </h3>
            <dl className="mt-3 space-y-2 text-sm">
              <div className="flex justify-between gap-3">
                <dt className="text-text-muted">Submitted by</dt>
                <dd className="text-text-primary">
                  {detail.submitted_by_name || "—"}
                </dd>
              </div>
              {staff && (
                <div className="flex justify-between gap-3">
                  <dt className="text-text-muted">Members interested</dt>
                  <dd className="tabular-nums text-text-primary">
                    {detail.interest_count ?? 0}
                  </dd>
                </div>
              )}
            </dl>
          </div>

          {/* Co-invest via SPV — shown when deal is active */}
          {deal.deal_status === "active" && (
            <div className="rounded-lg border border-[#ece8dd] bg-white p-5">
              <h3 className="text-xs font-semibold uppercase tracking-wide text-[var(--2a-gold)]">
                Co-invest via SPV
              </h3>
              <p className="mt-2 text-sm text-[var(--2a-text-secondary)]">
                Pool capital with other members through a special purpose vehicle.
              </p>

              {/* One investment may be offered in several classes, each with its
                  own carry, management fee, and close date. */}
              {dealClasses.length > 1 ? (
                <ul className="mt-3 space-y-2">
                  {dealClasses.map((c) => (
                    <li key={c.spv_id}>
                      <a
                        href={`/spvs/${c.spv_id}`}
                        className="block rounded-md border border-[#ece8dd] px-3 py-2.5 transition hover:border-[var(--2a-gold)]"
                      >
                        <span className="block text-sm font-medium text-[var(--2a-navy)]">
                          {c.class_label ? `Class ${c.class_label}` : c.spv_name}
                        </span>
                        {c.class_label && (
                          <span className="block text-xs text-[var(--2a-text-muted)]">
                            {c.spv_name}
                          </span>
                        )}
                        <span className="mt-1 block text-xs tabular-nums text-[var(--2a-text-muted)]">
                          {c.carry_pct != null
                            ? `${formatPercent(c.carry_pct)} carry`
                            : "Carry —"}
                          {" · "}
                          {c.mgmt_fee_pct != null
                            ? `${formatPercent(c.mgmt_fee_pct)} mgmt fee`
                            : "Mgmt fee —"}
                          {" · "}
                          {c.close_date
                            ? `closes ${formatDate(c.close_date)}`
                            : "close date TBD"}
                        </span>
                      </a>
                    </li>
                  ))}
                </ul>
              ) : (
                <a
                  href={dealClasses.length === 1 ? `/spvs/${dealClasses[0].spv_id}` : "/spvs"}
                  className="mt-3 block rounded-md px-4 py-2 text-center text-sm font-medium text-white transition"
                  style={{ backgroundColor: "var(--2a-navy)" }}
                >
                  {dealClasses.length === 1 ? "View SPV" : "View open SPVs"}
                </a>
              )}
            </div>
          )}
        </div>
      </div>
    </AppShell>
  );
}
