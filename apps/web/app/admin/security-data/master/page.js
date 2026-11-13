import { redirect } from "next/navigation";
import { getHostSession } from "@/lib/authServer";
import AppShell from "@/components/AppShell";
import { fetchAPI } from "@/lib/api";
import { labelFor } from "@/lib/menuVisibility";
import SecurityMasterAdmin, { TrackedPanel } from "@/components/securityData/SecurityMaster";
import { cleanFilter } from "@/lib/secmaster2.mjs";
import { cleanTrackedFilter } from "@/lib/track1.mjs";

// SECMASTER1 — Super Admin → Security master: counts by type / source, the platform jobs (last run,
// Run now, close a held run) and the review queue. Every control comes from the server's envelope.
// SECMASTER2 — the load summary (each count links here with its filter in the query string); a query
// that carries a list filter shows that list instead. Only filter names the SERVER lists
// (summary.list_filters) are forwarded, and a review kind only if the server's vocabulary names it.
export default async function SecurityMasterPage({ searchParams }) {
  const session = await getHostSession();
  if (!session) redirect("/auth/login?returnTo=/admin/security-data/master");
  const search = (await searchParams) || {};
  const one = (v) => (Array.isArray(v) ? v[0] : v);
  let status = null;
  let jobs = null;
  let review = null;
  let summary = null;
  let list = null;
  let known = null;
  let error = null;
  // TRACK1 — ?view=tracked: the Tracked view. Its filters are only the names the SERVER lists
  // (tracked envelope vocabularies.filters); with none, it shows the tracked securities.
  if (one(search.view) === "tracked") {
    let tracked = null;
    let tList = null;
    let tFilter = {};
    try {
      tracked = await fetchAPI("/api/v1/admin/security-data/master/tracked");
      const raw = Object.fromEntries(Object.entries(search).map(([k, v]) => [k, one(v)]));
      tFilter = cleanTrackedFilter(raw, tracked);
      if (!Object.keys(tFilter).length) tFilter = cleanTrackedFilter({ tracked: "true" }, tracked);
      const offset = Number(one(search.offset)) > 0 ? Math.floor(Number(one(search.offset))) : 0;
      tList = Object.keys(tFilter).length ? await fetchAPI(`/api/v1/admin/security-data/master/securities?${new URLSearchParams({ ...tFilter, limit: "200", offset: String(offset) })}`) : null;
    } catch (e) {
      error = e.status === 403 ? "forbidden" : e.message;
    }
    return (
      <AppShell user={session.user}>
        <h1 className="text-3xl font-semibold text-navy">{labelFor("/admin/security-data/master")}</h1>
        <div className="mt-6">
          {error === "forbidden" ? (
            <div className="rounded-[6px] border border-[#ece8dd] bg-white p-10 text-center text-sm text-text-muted">
              Tracked securities are managed by Super Admins.
            </div>
          ) : error ? (
            <div className="rounded-[6px] border border-[#ece8dd] bg-white p-10 text-center text-sm text-text-muted">
              Could not load the tracked securities: {error}
            </div>
          ) : (
            <TrackedPanel initialTracked={tracked} initialList={tList} filter={tFilter} />
          )}
        </div>
      </AppShell>
    );
  }
  try {
    [status, jobs, summary] = await Promise.all([fetchAPI("/api/v1/admin/security-data/master/status"),
      fetchAPI("/api/v1/admin/security-data/master/jobs"), fetchAPI("/api/v1/admin/security-data/master/summary")]);
    const kinds = (summary?.vocabularies?.review_kinds || []).map((k) => k.key);
    const kind = one(search.review_kind);
    const reviewQs = `?${new URLSearchParams({ ...(kind && kinds.includes(kind) ? { kind } : {}), limit: "500" })}`;
    const filter = cleanFilter(Object.fromEntries(Object.entries(search).map(([k, v]) => [k, one(v)])),
      summary?.list_filters);
    const offset = Number(one(search.offset)) > 0 ? Math.floor(Number(one(search.offset))) : 0;
    [review, list, known] = await Promise.all([
      fetchAPI(`/api/v1/admin/security-data/master/review${reviewQs}`),
      // REVIEWQ1 — the Known discrepancies section (decided Use existing / Keep both)
      fetchAPI("/api/v1/admin/security-data/master/review?status=known"),
      Object.keys(filter).length
        ? fetchAPI(`/api/v1/admin/security-data/master/securities?${new URLSearchParams({ ...filter, offset: String(offset) })}`)
        : Promise.resolve(null),
    ]);
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
          <SecurityMasterAdmin initialStatus={status} initialJobs={jobs} initialReview={review}
            initialSummary={summary} initialList={list} initialKnown={known} />
        )}
      </div>
    </AppShell>
  );
}
