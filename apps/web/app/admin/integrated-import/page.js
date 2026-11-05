import { redirect } from "next/navigation";
import { getHostSession } from "@/lib/authServer";
import AppShell from "@/components/AppShell";
import { fetchAPI } from "@/lib/api";
import { labelFor } from "@/lib/menuVisibility";
import IntegratedImport from "@/components/portentry/IntegratedImport";

// PORTENTRY1 Task 6 — Super Admin → Integrated import: upload a position file for integrated custodian accounts
// awaiting their feed, check the preview, and a SECOND Super Admin approves it. Rows land as a provisional
// 'stated' source that the custodian feed supersedes.
export default async function IntegratedImportPage() {
  const session = await getHostSession();
  if (!session) redirect("/auth/login?returnTo=/admin/integrated-import");
  let envelope = null;
  let error = null;
  try {
    envelope = await fetchAPI("/api/v1/admin/integrated-imports");
  } catch (e) {
    error = e.status === 403 ? "forbidden" : e.message;
  }
  return (
    <AppShell user={session.user}>
      <h1 className="text-3xl font-semibold text-navy">{labelFor("/admin/integrated-import")}</h1>
      <p className="mt-1 text-sm text-text-muted">
        A one-time position file for integrated accounts whose custodian feed has not started. A second person approves
        it; the feed supersedes it when it arrives.
      </p>
      <div className="mt-6">
        {error === "forbidden" ? (
          <div className="rounded-[6px] border border-[#ece8dd] bg-white p-10 text-center text-sm text-text-muted">
            Integrated imports are managed by Super Admins.
          </div>
        ) : error ? (
          <div className="rounded-[6px] border border-[#ece8dd] bg-white p-10 text-center text-sm text-text-muted">
            Could not load imports: {error}
          </div>
        ) : (
          <IntegratedImport initialEnvelope={envelope} />
        )}
      </div>
    </AppShell>
  );
}
