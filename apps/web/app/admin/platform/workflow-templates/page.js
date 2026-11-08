import { redirect } from "next/navigation";
import { getHostSession } from "@/lib/authServer";
import AppShell from "@/components/AppShell";
import { PlatformTemplatesHome } from "@/components/admin/WorkflowTemplatesHome";
import { fetchAPI } from "@/lib/api";
import { labelFor } from "@/lib/menuVisibility";

// WFTEMPLATES1 — Platform → Workflow templates (Super Admin): the platform catalog's drafts and versions, publish by a
// DIFFERENT Super Admin than the drafter (maker-checker), retire (existing copies keep working), and usage as counts
// only — never which organisation made a copy.
export const dynamic = "force-dynamic";

export default async function PlatformWorkflowTemplatesPage() {
  const session = await getHostSession();
  if (!session) redirect("/auth/login?returnTo=/admin/platform/workflow-templates");
  let initial = null;
  let error = null;
  try {
    initial = await fetchAPI("/api/v1/admin/platform/workflow-templates");
  } catch (e) {
    error = e.status === 403 ? "Platform workflow templates are restricted to Super Admins." : e.message;
  }
  return (
    <AppShell user={session.user}>
      <h1 className="font-serif text-3xl font-semibold text-navy">{labelFor("/admin/platform/workflow-templates")}</h1>
      <p className="mt-1 text-sm text-text-muted">One Super Admin drafts a version; a different Super Admin publishes it.
        Published versions are what client firms see in their template library.</p>
      <div className="mt-6">
        {error ? <div className="rounded-[6px] border border-[#ece8dd] bg-white p-10 text-center text-sm text-text-muted">{error}</div>
          : <PlatformTemplatesHome initial={initial} />}
      </div>
    </AppShell>
  );
}
