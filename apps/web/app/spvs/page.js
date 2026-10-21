import { redirect } from "next/navigation";
import { getHostSession } from "@/lib/authServer";
import AppShell from "@/components/AppShell";
import { listSPVs } from "@/lib/api";
import { isStaff } from "@/lib/roles";
import { formatCurrency, formatDate } from "@/lib/format";
import NewSPVForm from "@/components/spv/NewSPVForm";
import CommitmentProgress from "@/components/spv/CommitmentProgress";

const STATUS_CONFIG = {
  forming: { label: "Forming", bg: "bg-[var(--2a-bg-sidebar)]", text: "text-[var(--2a-text-muted)]" },
  open: { label: "Open", bg: "bg-[#E8F5E9]", text: "text-[#2D6A4F]" },
  closing: { label: "Closing", bg: "bg-[#EEF4FF]", text: "text-[var(--2a-navy)]" },
  closed: { label: "Closed", bg: "bg-[var(--2a-bg-sidebar)]", text: "text-[var(--2a-text-muted)]" },
  cancelled: { label: "Cancelled", bg: "bg-[#FEF3F2]", text: "text-[#9B2335]" },
};

function StatusPill({ status }) {
  const cfg = STATUS_CONFIG[status] || {
    label: status,
    bg: "bg-[var(--2a-bg-sidebar)]",
    text: "text-[var(--2a-text-muted)]",
  };
  return (
    <span className={`rounded-full px-2.5 py-0.5 text-xs font-medium ${cfg.bg} ${cfg.text}`}>
      {cfg.label}
    </span>
  );
}

export default async function SPVListPage() {
  const session = await getHostSession();
  if (!session) redirect("/auth/login?returnTo=/spvs");

  const staff = isStaff(session.user);

  let spvs = [];
  try {
    spvs = await listSPVs();
  } catch {
    // render empty state
  }

  return (
    <AppShell user={session.user}>
      <div className="mx-auto max-w-5xl">
        <div className="mb-6 flex items-center justify-between">
          <div>
            <h1
              className="text-2xl font-light"
              style={{ fontFamily: "Spectral, Georgia, serif", color: "var(--2a-navy)" }}
            >
              SPV Manager
            </h1>
            <p className="mt-0.5 text-sm text-[var(--2a-text-muted)]">
              Special purpose vehicles and co-investment structures
            </p>
          </div>
          {staff && <NewSPVForm />}
        </div>

        {spvs.length === 0 ? (
          <div className="rounded-lg border border-[#ece8dd] bg-white p-12 text-center">
            <p className="text-sm text-[var(--2a-text-muted)]">No SPVs available.</p>
          </div>
        ) : (
          <div className="space-y-3">
            {spvs.map((spv) => (
              <a
                key={spv.id}
                href={`/spvs/${spv.id}`}
                className="block rounded-lg border border-[#ece8dd] bg-white px-5 py-4 transition hover:shadow-sm"
              >
                <div className="flex items-start justify-between gap-4">
                  <div className="min-w-0 flex-1">
                    <p className="font-medium text-[var(--2a-text)] truncate">{spv.name}</p>
                    {spv.close_date && (
                      <p className="mt-0.5 text-xs text-[var(--2a-text-muted)]">
                        Closes {formatDate(spv.close_date)}
                      </p>
                    )}
                    {spv.min_commitment && (
                      <p className="mt-0.5 text-xs text-[var(--2a-text-muted)]">
                        Min. {formatCurrency(spv.min_commitment)}
                      </p>
                    )}
                  </div>
                  <StatusPill status={spv.status} />
                </div>
                {/* BUGFIX1 (SPV-01) — same summary as the cap table. */}
                <CommitmentProgress commitment={spv.commitment} compact />
              </a>
            ))}
          </div>
        )}
      </div>
    </AppShell>
  );
}
