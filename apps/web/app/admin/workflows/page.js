import Link from "next/link";
import { redirect } from "next/navigation";
import { getHostSession } from "@/lib/authServer";
import AppShell from "@/components/AppShell";
import WorkflowLibraryManager from "@/components/admin/WorkflowLibraryManager";
import { getWorkflows } from "@/lib/api";

// Workflow Manager — Phase 3 library screen. Lists the org's workflow
// definitions with their current version + a step/tier summary, offers a
// natural-language "New Workflow" form (Phase 2 generation), and links each row
// to the diagram editor. Org Admin (own org) or Super Admin, enforced
// server-side by the FastAPI gate; a 403 surfaces as the "forbidden" panel.
export default async function AdminWorkflowsPage() {
  const session = await getHostSession();
  if (!session) {
    redirect("/auth/login?returnTo=/admin/workflows");
  }

  let workflows = [];
  let error = null;
  try {
    workflows = await getWorkflows();
  } catch (e) {
    error = e.status === 403 ? "forbidden" : e.message;
  }

  return (
    <AppShell user={session.user}>
      <div className="flex items-baseline justify-between">
        <div>
          <h1 className="text-3xl font-semibold text-navy">Workflows</h1>
          <p className="mt-1 text-sm text-text-muted">
            Governed, executable process definitions
          </p>
        </div>
        <nav className="flex gap-4 text-sm">
          <Link href="/admin/workflows/runs" className="text-gold hover:underline">
            Run Console
          </Link>
          <Link href="/admin/workflows/triggers" className="text-gold hover:underline">
            Scheduler & Routines
          </Link>
        </nav>
      </div>

      {error === "forbidden" ? (
        <div className="mt-6 rounded-lg border border-border bg-bg-card p-10 text-center text-sm text-text-muted">
          You do not have permission to manage workflows.
        </div>
      ) : error ? (
        <div className="mt-6 rounded-lg border border-border bg-bg-card p-10 text-center text-sm text-[#9B2335]">
          Could not load workflows: {error}
        </div>
      ) : (
        <WorkflowLibraryManager initialWorkflows={workflows || []} />
      )}
    </AppShell>
  );
}
