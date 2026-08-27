import Link from "next/link";
import { redirect } from "next/navigation";
import { getHostSession } from "@/lib/authServer";
import AppShell from "@/components/AppShell";
import { fetchAPI } from "@/lib/api";
import CustodyImportWizard from "@/components/admin/CustodyImportWizard";

// Custody import — the account layer's only ingestion path (Sprint fee31).
// Upload a custodial CSV, map its columns, review the diff, commit.
//
// THE TWO FETCHES ARE INDEPENDENT ON PURPOSE, for the same reason the Triggers
// screen's are: the profile list and the batch list are gated on different
// permissions (read vs. write is reported inside the profiles envelope), and a
// single try/catch would let one failure blank a screen the caller is entitled
// to see.
export default async function CustodyImportPage() {
  const session = await getHostSession();
  if (!session) {
    redirect("/auth/login?returnTo=/admin/custody-import");
  }

  let envelope = null;
  let error = null;
  try {
    envelope = await fetchAPI("/api/v1/custody/profiles");
  } catch (e) {
    error = e.status === 403 ? "forbidden" : e.message;
  }

  let batches = [];
  try {
    const payload = await fetchAPI("/api/v1/custody/batches");
    batches = payload?.rows || [];
  } catch {
    batches = [];
  }

  return (
    <AppShell user={session.user}>
      <div className="flex items-baseline justify-between">
        <div>
          <h1 className="text-3xl font-semibold text-navy">Custody import</h1>
          <p className="mt-1 text-sm text-text-muted">
            Load billable accounts, daily balances and cash flows from a
            custodial export — nothing is written until you commit
          </p>
        </div>
        <Link href="/admin" className="text-sm text-gold hover:underline">
          ← Admin
        </Link>
      </div>

      {error === "forbidden" ? (
        <div className="mt-6 rounded border border-border bg-bg-card p-10 text-center text-sm text-text-muted">
          You do not have permission to import custodial data.
        </div>
      ) : error ? (
        <div className="mt-6 rounded border border-border bg-bg-card p-10 text-center text-sm text-gold">
          Could not load custodian profiles: {error}
        </div>
      ) : (
        <CustodyImportWizard
          initialProfiles={envelope?.profiles || []}
          canWrite={Boolean(envelope?.permissions?.can_write)}
          initialBatches={batches}
        />
      )}
    </AppShell>
  );
}
