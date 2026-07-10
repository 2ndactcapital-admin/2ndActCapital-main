import { redirect, notFound } from "next/navigation";
import { auth0 } from "@/lib/auth0";
import AppShell from "@/components/AppShell";
import { getSPV, getSPVCapTable, listSPVDocuments, getSPVHistory } from "@/lib/api";
import { isStaff } from "@/lib/roles";
import { formatCurrency, formatDate, formatPercent } from "@/lib/format";
import SPVStatusControl from "@/components/spv/SPVStatusControl";
import SPVDocumentsTab from "@/components/spv/SPVDocumentsTab";
import SPVSubscriptionsTab from "@/components/spv/SPVSubscriptionsTab";
import SPVTransactionsTab from "@/components/spv/SPVTransactionsTab";

const STATUS_CONFIG = {
  forming: { label: "Forming", bg: "bg-[#F5F1EB]", text: "text-[#64748B]" },
  open: { label: "Open", bg: "bg-[#E8F5E9]", text: "text-[#2D6A4F]" },
  closing: { label: "Closing", bg: "bg-[#EEF4FF]", text: "text-[#1B2B4B]" },
  closed: { label: "Closed", bg: "bg-[#F5F1EB]", text: "text-[#64748B]" },
  cancelled: { label: "Cancelled", bg: "bg-[#FEF3F2]", text: "text-[#9B2335]" },
};

function StatusPill({ status }) {
  const cfg = STATUS_CONFIG[status] || {
    label: status,
    bg: "bg-[#F5F1EB]",
    text: "text-[#64748B]",
  };
  return (
    <span className={`rounded-full px-2.5 py-0.5 text-xs font-medium ${cfg.bg} ${cfg.text}`}>
      {cfg.label}
    </span>
  );
}

function Metric({ label, value }) {
  return (
    <div>
      <dt className="text-xs font-semibold uppercase tracking-wide text-[#64748B]">{label}</dt>
      <dd className="mt-0.5 text-sm font-medium text-[#0F172A] tabular-nums">{value}</dd>
    </div>
  );
}

function HistorySection({ history }) {
  if (!history?.length) {
    return <p className="text-sm text-[#64748B] py-6 text-center">No status history.</p>;
  }
  return (
    <ol className="relative border-l border-[#E2E8F0] pl-4 space-y-4">
      {history.map((h) => (
        <li key={h.id} className="ml-2">
          <div className="absolute -left-1.5 mt-1 h-3 w-3 rounded-full border border-white bg-[#C5A880]" />
          <p className="text-xs text-[#64748B]">{formatDate(h.created_at)}</p>
          <p className="text-sm text-[#0F172A]">
            {h.from_status ? `${h.from_status} → ${h.to_status}` : h.to_status}
          </p>
          {h.note && <p className="text-xs text-[#64748B] mt-0.5">{h.note}</p>}
        </li>
      ))}
    </ol>
  );
}

export default async function SPVDetailPage({ params, searchParams }) {
  const { id } = await params;
  const sp = (await searchParams) || {};
  const tab = typeof sp.tab === "string" ? sp.tab : "overview";

  const session = await auth0.getSession();
  if (!session) redirect(`/auth/login?returnTo=/spvs/${id}`);

  const staff = isStaff(session.user);

  let spv;
  try {
    spv = await getSPV(id);
  } catch (error) {
    if (error.status === 404) notFound();
    throw error;
  }

  const [capTableRes, documentsRes, historyRes] = await Promise.allSettled([
    staff ? getSPVCapTable(id) : Promise.resolve(null),
    listSPVDocuments(id),
    staff ? getSPVHistory(id) : Promise.resolve([]),
  ]);

  const capTable = capTableRes.status === "fulfilled" ? capTableRes.value : null;
  const documents = documentsRes.status === "fulfilled" ? documentsRes.value || [] : [];
  const history = historyRes.status === "fulfilled" ? historyRes.value || [] : [];

  const tabs = [
    { key: "overview", label: "Overview" },
    ...(staff ? [{ key: "captable", label: "Cap Table" }] : []),
    { key: "documents", label: "Documents" },
    ...(staff ? [{ key: "transactions", label: "Transactions" }] : []),
    ...(staff ? [{ key: "history", label: "History" }] : []),
    ...(staff ? [{ key: "ledger", label: "Ledger", href: `/spvs/${id}/ledger` }] : []),
  ];

  return (
    <AppShell user={session.user}>
      <div className="mx-auto max-w-5xl">
        {/* Header */}
        <div className="mb-6">
          <a href="/spvs" className="text-xs text-[#64748B] hover:text-[#C5A880]">
            ← SPV Manager
          </a>
          <div className="mt-3 flex items-start justify-between gap-4">
            <div>
              <h1
                className="text-2xl font-light"
                style={{ fontFamily: "Spectral, Georgia, serif", color: "#1B2B4B" }}
              >
                {spv.name}
              </h1>
              {spv.close_date && (
                <p className="mt-0.5 text-sm text-[#64748B]">
                  Closes {formatDate(spv.close_date)}
                </p>
              )}
            </div>
            <div className="flex items-center gap-3 flex-shrink-0">
              <StatusPill status={spv.status} />
              {staff && <SPVStatusControl spv={spv} />}
            </div>
          </div>
        </div>

        {/* Key metrics */}
        <div className="mb-6 grid grid-cols-2 gap-4 rounded-lg border border-[#ece8dd] bg-white p-5 sm:grid-cols-4">
          <Metric label="Target Raise" value={formatCurrency(spv.target_raise)} />
          <Metric label="Min. Commitment" value={formatCurrency(spv.min_commitment)} />
          <Metric label="Carry" value={spv.carry_pct != null ? formatPercent(spv.carry_pct) : "—"} />
          <Metric label="Mgmt Fee" value={spv.mgmt_fee_pct != null ? formatPercent(spv.mgmt_fee_pct) : "—"} />
        </div>

        {/* Tabs */}
        <div className="mb-4 flex gap-4 border-b border-[#E2E8F0]">
          {tabs.map((t) => (
            <a
              key={t.key}
              href={t.href || `/spvs/${id}?tab=${t.key}`}
              className={`pb-2 text-sm font-medium transition-colors ${
                tab === t.key
                  ? "border-b-2 border-[#C5A880] text-[#1B2B4B]"
                  : "text-[#64748B] hover:text-[#0F172A]"
              }`}
            >
              {t.label}
            </a>
          ))}
        </div>

        {/* Tab content */}
        <div className="rounded-lg border border-[#ece8dd] bg-white p-5">
          {tab === "overview" && (
            <dl className="grid grid-cols-1 gap-4 sm:grid-cols-2">
              {/* Status shown as a pill in the detail grid */}
              <div>
                <dt className="text-xs font-semibold uppercase tracking-wide text-[#64748B]">Status</dt>
                <dd className="mt-1">
                  <StatusPill status={spv.status} />
                </dd>
              </div>
              <Metric label="Hard Cap" value={formatCurrency(spv.hard_cap)} />
              <Metric label="Min. Raise" value={formatCurrency(spv.minimum_raise)} />
              <Metric label="Created" value={formatDate(spv.created_at)} />
            </dl>
          )}
          {tab === "captable" && staff && (
            <SPVSubscriptionsTab
              spvId={id}
              capTable={capTable}
              staff={staff}
            />
          )}
          {tab === "documents" && (
            <SPVDocumentsTab
              spvId={id}
              initialDocuments={documents}
              staff={staff}
            />
          )}
          {tab === "transactions" && staff && (
            <SPVTransactionsTab
              spvId={id}
              staff={staff}
              spvName={spv.name}
              totalCommitted={
                Array.isArray(capTable?.subscriptions)
                  ? capTable.subscriptions.reduce(
                      (sum, s) => sum + (Number(s.commitment_amount) || 0),
                      0,
                    )
                  : null
              }
            />
          )}
          {tab === "history" && staff && <HistorySection history={history} />}
        </div>
      </div>
    </AppShell>
  );
}
