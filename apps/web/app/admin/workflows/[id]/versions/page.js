import Link from "next/link";
import { redirect } from "next/navigation";
import { getHostSession } from "@/lib/authServer";
import AppShell from "@/components/AppShell";
import { getWorkflowVersions } from "@/lib/api";
import { formatDateTime, personLabel } from "@/lib/workflowFormat";

// Workflow Manager — Phase 4 Version History. Lists every version of a
// definition in order, marking the single current one. Read-only browsing (no
// diff rendering this phase). Org Admin (own org) or Super Admin.
export default async function WorkflowVersionsPage({ params }) {
  const { id } = await params;
  const session = await getHostSession();
  if (!session) {
    redirect(`/auth/login?returnTo=/admin/workflows/${id}/versions`);
  }

  let data = null;
  let error = null;
  try {
    data = await getWorkflowVersions(id);
  } catch (e) {
    error =
      e.status === 403
        ? "forbidden"
        : e.status === 404
        ? "notfound"
        : e.message;
  }

  const versions = data?.versions || [];

  return (
    <AppShell user={session.user}>
      <div className="flex items-baseline justify-between">
        <div>
          <h1 className="text-3xl font-semibold text-navy">Version History</h1>
          <p className="mt-1 text-sm text-text-muted">
            {data ? data.definition.name : "Workflow versions"}
          </p>
        </div>
        <Link href={`/admin/workflows/${id}/edit`} className="text-sm text-gold hover:underline">
          ← Editor
        </Link>
      </div>

      {error === "forbidden" ? (
        <div className="mt-6 rounded-lg border border-border bg-bg-card p-10 text-center text-sm text-text-muted">
          You do not have permission to view this workflow.
        </div>
      ) : error === "notfound" ? (
        <div className="mt-6 rounded-lg border border-border bg-bg-card p-10 text-center text-sm text-text-muted">
          Workflow not found.
        </div>
      ) : error ? (
        <div className="mt-6 rounded-lg border border-border bg-bg-card p-10 text-center text-sm text-gold">
          Could not load versions: {error}
        </div>
      ) : (
        <div className="mt-6 overflow-hidden rounded-lg border border-border bg-bg-card">
          <table className="w-full text-left text-sm">
            <thead className="border-b border-border text-xs uppercase tracking-wide text-text-muted">
              <tr>
                <th className="px-4 py-3 font-semibold">Version</th>
                <th className="px-4 py-3 font-semibold">Change summary</th>
                <th className="px-4 py-3 font-semibold">Created by</th>
                <th className="px-4 py-3 font-semibold">Created</th>
              </tr>
            </thead>
            <tbody>
              {versions.map((v) => (
                <tr key={v.id} className="border-b border-border last:border-0">
                  <td className="px-4 py-3">
                    <span className="font-medium text-navy">v{v.version_number}</span>
                    {v.is_current && (
                      <span className="ml-2 inline-flex items-center rounded border border-gold px-2 py-0.5 text-xs font-medium text-gold">
                        current
                      </span>
                    )}
                  </td>
                  <td className="px-4 py-3 text-text-secondary">
                    {v.change_summary || "—"}
                  </td>
                  <td className="px-4 py-3 text-text-secondary">
                    {personLabel(v.created_by_name, v.created_by_email)}
                  </td>
                  <td className="px-4 py-3 text-text-muted">
                    {formatDateTime(v.created_at)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </AppShell>
  );
}
