import { redirect } from "next/navigation";
import { getHostSession } from "@/lib/authServer";
import AppShell from "@/components/AppShell";
import { fetchAPI } from "@/lib/api";
import { labelFor } from "@/lib/menuVisibility";
import OrgSources from "@/components/securityData/OrgSources";

// MARKETDATA1 — org admin → Price sources: the order sources are asked in (within the
// platform's allowed providers) and file uploads awaiting a second admin's approval.
export default async function SecurityDataSourcesPage() {
  const session = await getHostSession();
  if (!session) redirect("/auth/login?returnTo=/admin/security-data/sources");
  let rank = null;
  let uploads = null;
  let error = null;
  try {
    [rank, uploads] = await Promise.all([fetchAPI("/api/v1/security-data/rank"), fetchAPI("/api/v1/security-data/uploads")]);
  } catch (e) {
    error = e.status === 403 ? "forbidden" : e.message;
  }
  return (
    <AppShell user={session.user}>
      <h1 className="text-3xl font-semibold text-navy">{labelFor("/admin/security-data/sources")}</h1>
      <div className="mt-6">
        {error === "forbidden" ? (
          <div className="rounded-[6px] border border-[#ece8dd] bg-white p-10 text-center text-sm text-text-muted">
            Price sources are open to organization admins and replication staff.
          </div>
        ) : error ? (
          <div className="rounded-[6px] border border-[#ece8dd] bg-white p-10 text-center text-sm text-text-muted">
            Could not load price sources: {error}
          </div>
        ) : (
          <OrgSources initialRank={rank} initialUploads={uploads} />
        )}
      </div>
    </AppShell>
  );
}
