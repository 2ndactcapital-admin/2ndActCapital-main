import { redirect } from "next/navigation";
import { getHostSession } from "@/lib/authServer";
import AppShell from "@/components/AppShell";
import { fetchAPI } from "@/lib/api";
import { labelFor } from "@/lib/menuVisibility";
import SecurityMasterAdmin from "@/components/securityData/SecurityMaster";

// SECMASTER1 — Super Admin → Security master: counts by type / source, the platform jobs (last run,
// Run now, close a held run) and the review queue. Every control comes from the server's envelope.
export default async function SecurityMasterPage() {
  const session = await getHostSession();
  if (!session) redirect("/auth/login?returnTo=/admin/security-data/master");
  let status = null;
  let jobs = null;
  let review = null;
  let error = null;
  try {
    [status, jobs, review] = await Promise.all([fetchAPI("/api/v1/admin/security-data/master/status"),
      fetchAPI("/api/v1/admin/security-data/master/jobs"), fetchAPI("/api/v1/admin/security-data/master/review")]);
  } catch (e) {
    error = e.status === 403 ? "forbidden" : e.message;
  }
  return (
    <AppShell user={session.user}>
      <h1 className="text-3xl font-semibold text-navy">{labelFor("/admin/security-data/master")}</h1>
      <p className="mt-1 text-sm text-text-muted">
        The platform security master: what is in it, where each fact came from, the jobs that keep it current, and what needs a decision.
      </p>
      <div className="mt-6">
        {error === "forbidden" ? (
          <div className="rounded-[6px] border border-[#ece8dd] bg-white p-10 text-center text-sm text-text-muted">
            The security master is managed by Super Admins.
          </div>
        ) : error ? (
          <div className="rounded-[6px] border border-[#ece8dd] bg-white p-10 text-center text-sm text-text-muted">
            Could not load the security master: {error}
          </div>
        ) : (
          <SecurityMasterAdmin initialStatus={status} initialJobs={jobs} initialReview={review} />
        )}
      </div>
    </AppShell>
  );
}
