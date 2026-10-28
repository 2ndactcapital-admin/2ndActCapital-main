import { redirect } from "next/navigation";
import { getHostSession } from "@/lib/authServer";
import AppShell from "@/components/AppShell";
import { fetchAPI } from "@/lib/api";
import { labelFor } from "@/lib/menuVisibility";
import QualityView from "@/components/securityData/QualityView";

// MARKETDATA1 — Data quality: stale, gap, outlier, disagreement and budget events, per item and day.
export default async function SecurityDataQualityPage() {
  const session = await getHostSession();
  if (!session) redirect("/auth/login?returnTo=/admin/security-data/quality");
  let envelope = null;
  let error = null;
  try {
    envelope = await fetchAPI("/api/v1/security-data/quality");
  } catch (e) {
    error = e.status === 403 ? "forbidden" : e.message;
  }
  return (
    <AppShell user={session.user}>
      <h1 className="text-3xl font-semibold text-navy">{labelFor("/admin/security-data/quality")}</h1>
      <div className="mt-6">
        {error === "forbidden" ? (
          <div className="rounded-[6px] border border-[#ece8dd] bg-white p-10 text-center text-sm text-text-muted">
            Data quality is open to organization admins and replication staff.
          </div>
        ) : error ? (
          <div className="rounded-[6px] border border-[#ece8dd] bg-white p-10 text-center text-sm text-text-muted">
            Could not load data quality: {error}
          </div>
        ) : (
          <QualityView envelope={envelope} />
        )}
      </div>
    </AppShell>
  );
}
