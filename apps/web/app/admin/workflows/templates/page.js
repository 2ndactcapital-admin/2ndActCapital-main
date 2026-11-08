import Link from "next/link";
import { redirect } from "next/navigation";
import { getHostSession } from "@/lib/authServer";
import AppShell from "@/components/AppShell";
import { TemplatesHome } from "@/components/admin/WorkflowTemplatesHome";
import { fetchAPI, getWorkflows } from "@/lib/api";
import { labelFor } from "@/lib/menuVisibility";

// WFTEMPLATES1 — Workflows → Templates: ready-made workflows (platform templates Hollisworks publishes, and this
// firm's own) to browse, preview and copy into the firm as a draft. Readable by any of the three workflow keys; using
// or saving needs author_workflows — decided by the server's envelope, fetched here so the first paint carries it.
export const dynamic = "force-dynamic";

export default async function WorkflowTemplatesPage() {
  const session = await getHostSession();
  if (!session) redirect("/auth/login?returnTo=/admin/workflows/templates");
  let initial = null;
  let error = null;
  try {
    initial = await fetchAPI("/api/v1/admin/workflow-templates");
  } catch (e) {
    error = e.status === 403 ? "The template library is open to staff who work with workflows." : e.message;
  }
  // The org's own workflows, for "save as a template" — only when the envelope says the caller may save.
  let workflows = [];
  if (initial?.permissions?.can_write === true) {
    try {
      workflows = await getWorkflows();
    } catch {
      workflows = [];
    }
  }
  return (
    <AppShell user={session.user}>
      <div className="flex items-baseline justify-between">
        <div>
          <h1 className="font-serif text-3xl font-semibold text-navy">{labelFor("/admin/workflows/templates")}</h1>
          <p className="mt-1 text-sm text-text-muted">Ready-made workflows. Using one creates a draft workflow your firm owns and edits;
            the template never changes it, and nothing runs until you add a trigger.</p>
        </div>
        <Link href="/admin/workflows" className="text-sm text-gold hover:underline">Your workflows</Link>
      </div>
      <div className="mt-6">
        {error ? <div className="rounded-[6px] border border-[#ece8dd] bg-white p-10 text-center text-sm text-text-muted">{error}</div>
          : <TemplatesHome initial={initial} workflows={workflows} />}
      </div>
    </AppShell>
  );
}
