import Link from "next/link";
import { redirect } from "next/navigation";
import { getHostSession } from "@/lib/authServer";
import AppShell from "@/components/AppShell";
import { TemplateHome } from "@/components/admin/WorkflowTemplatesHome";
import { fetchAPI } from "@/lib/api";

// WFTEMPLATES1 — one template: its description, the diagram (read-only viewer), the steps with each verb's tier
// ("needs approval" for Tier 1), the parameters, what the firm needs (missing items named), "Use this template"
// (author_workflows, from the server's envelope) and this firm's copies with "a newer version exists".
export const dynamic = "force-dynamic";

export default async function WorkflowTemplatePage({ params, searchParams }) {
  const { id } = await params;
  const sp = (await searchParams) || {};
  const session = await getHostSession();
  if (!session) redirect(`/auth/login?returnTo=/admin/workflows/templates/${id}`);
  let initial = null;
  let error = null;
  try {
    initial = await fetchAPI(`/api/v1/admin/workflow-templates/${encodeURIComponent(id)}`,
      { searchParams: { version_id: sp.version_id } });
  } catch (e) {
    error = e.status === 404 ? "Template not found." : e.status === 403
      ? "The template library is open to staff who work with workflows." : e.message;
  }
  return (
    <AppShell user={session.user}>
      <Link href="/admin/workflows/templates" className="text-xs font-medium uppercase tracking-wide text-text-muted hover:underline">
        ← Templates
      </Link>
      <h1 className="mt-1 font-serif text-3xl font-semibold text-navy">{initial?.version?.title || "Template"}</h1>
      <div className="mt-4">
        {error ? <div className="rounded-[6px] border border-[#ece8dd] bg-white p-10 text-center text-sm text-text-muted">{error}</div>
          : <TemplateHome initial={initial} />}
      </div>
    </AppShell>
  );
}
