import { redirect } from "next/navigation";
import { getHostSession } from "@/lib/authServer";
import AppShell from "@/components/AppShell";
import EdgarPipelineMonitor from "@/components/admin/EdgarPipelineMonitor";
import { getEdgarFilings } from "@/lib/api";

// EDGAR pipeline monitoring (edgarpipelinea). Server component: fetch the first
// page of the filing manifest — sorted newest first, server-side — then hand it
// to the client monitor, which pages, sorts and filters through server actions.
//
// Super Admin is enforced SERVER-SIDE by FastAPI. The nav entry is gated too,
// but a hidden link is not a permission: anyone else who types the URL gets a
// 403 from the API and the "not permitted" panel below.
export default async function EdgarPipelinePage() {
  const session = await getHostSession();
  if (!session) {
    redirect("/auth/login?returnTo=/admin/edgar-pipeline");
  }

  let initialFilings = null;
  let error = null;
  try {
    initialFilings = await getEdgarFilings({
      sort: "filing_date",
      direction: "desc",
      page: 1,
      page_size: 50,
    });
  } catch (e) {
    error = e.status === 403 ? "forbidden" : e.message;
  }

  return (
    <AppShell user={session.user}>
      <div>
        <h1 className="text-3xl font-semibold text-navy">EDGAR Pipeline</h1>
        <p className="mt-1 text-sm text-text-muted">
          Structured-note filings from discovery to fetch: the filing manifest, progress by
          quarter, and the issuers that decide what is selected.
        </p>
      </div>

      {error === "forbidden" ? (
        <div className="mt-6 rounded-lg border border-border bg-bg-card p-10 text-center text-sm text-text-muted">
          Super Admin access required.
        </div>
      ) : error ? (
        <div className="mt-6 rounded-lg border border-border bg-bg-card p-10 text-center text-sm text-text-muted">
          Could not load the filing manifest: {error}
        </div>
      ) : (
        <EdgarPipelineMonitor initialFilings={initialFilings} />
      )}
    </AppShell>
  );
}
