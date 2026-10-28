import { redirect } from "next/navigation";
import { getHostSession } from "@/lib/authServer";
import AppShell from "@/components/AppShell";
import { fetchAPI } from "@/lib/api";
import { labelFor } from "@/lib/menuVisibility";
import ProvidersAdmin from "@/components/securityData/ProvidersAdmin";

// MARKETDATA1 — Super Admin → Security data: providers (status, licence facts, budgets,
// call log) and the platform rank per data type.
export default async function SecurityDataProvidersPage() {
  const session = await getHostSession();
  if (!session) redirect("/auth/login?returnTo=/admin/security-data");
  let envelope = null;
  let rank = null;
  let error = null;
  try {
    [envelope, rank] = await Promise.all([fetchAPI("/api/v1/admin/security-data/providers"),
      fetchAPI("/api/v1/admin/security-data/rank")]);
  } catch (e) {
    error = e.status === 403 ? "forbidden" : e.message;
  }
  return (
    <AppShell user={session.user}>
      <h1 className="text-3xl font-semibold text-navy">{labelFor("/admin/security-data")}</h1>
      <p className="mt-1 text-sm text-text-muted">
        Where every price, corporate action, holdings file, index level and option quote comes from, and what each source may be used for.
      </p>
      <div className="mt-6">
        {error === "forbidden" ? (
          <div className="rounded-[6px] border border-[#ece8dd] bg-white p-10 text-center text-sm text-text-muted">
            Security data providers are managed by Super Admins.
          </div>
        ) : error ? (
          <div className="rounded-[6px] border border-[#ece8dd] bg-white p-10 text-center text-sm text-text-muted">
            Could not load providers: {error}
          </div>
        ) : (
          <ProvidersAdmin initialEnvelope={envelope} initialRank={rank} />
        )}
      </div>
    </AppShell>
  );
}
