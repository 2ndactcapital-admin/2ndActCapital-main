import { redirect } from "next/navigation";
import Link from "next/link";
import { auth0 } from "@/lib/auth0";
import AppShell from "@/components/AppShell";
import WorkflowDiagramEditor from "@/components/admin/WorkflowDiagramEditor";
import { getWorkflow } from "@/lib/api";

// Workflow Manager — Phase 3 diagram editor. Loads a definition's CURRENT
// version BPMN and renders it in bpmn-js with a governance properties panel.
// Saving produces a NEW version (never mutates the loaded one). Org Admin (own
// org) or Super Admin, enforced server-side; a 403 surfaces as "forbidden".
export default async function WorkflowEditorPage({ params }) {
  const { id } = await params;
  const session = await auth0.getSession();
  if (!session) {
    redirect(`/auth/login?returnTo=/admin/workflows/${id}/edit`);
  }

  let workflow = null;
  let error = null;
  try {
    workflow = await getWorkflow(id);
  } catch (e) {
    if (e.status === 403) error = "forbidden";
    else if (e.status === 404) error = "not_found";
    else error = e.message;
  }

  return (
    <AppShell user={session.user}>
      <div className="flex items-center justify-between">
        <div>
          <Link
            href="/admin/workflows"
            className="text-xs font-medium uppercase tracking-wide text-text-muted hover:underline"
          >
            ← Workflows
          </Link>
          <h1 className="mt-1 text-3xl font-semibold text-navy">
            {workflow ? workflow.name : "Workflow editor"}
          </h1>
          {workflow && (
            <p className="mt-1 text-sm text-text-muted">
              Editing from version {workflow.current_version.version_number} ·
              saving creates a new version
            </p>
          )}
        </div>
      </div>

      {error === "forbidden" ? (
        <div className="mt-6 rounded-lg border border-border bg-bg-card p-10 text-center text-sm text-text-muted">
          You do not have permission to edit workflows.
        </div>
      ) : error === "not_found" ? (
        <div className="mt-6 rounded-lg border border-border bg-bg-card p-10 text-center text-sm text-text-muted">
          Workflow not found.
        </div>
      ) : error ? (
        <div className="mt-6 rounded-lg border border-border bg-bg-card p-10 text-center text-sm text-[#9B2335]">
          Could not load workflow: {error}
        </div>
      ) : (
        <WorkflowDiagramEditor workflow={workflow} />
      )}
    </AppShell>
  );
}
