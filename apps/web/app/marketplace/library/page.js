import { redirect } from "next/navigation";
import { getHostSession } from "@/lib/authServer";
import AppShell from "@/components/AppShell";
import { fetchAPI } from "@/lib/api";
import TemplateLibrary from "@/components/dealroom/TemplateLibrary";

// DEALROOM2 — the firm's agreement template library. The server decides who
// may see it (manage_org_settings / Super Admin → 403 otherwise) and what each
// row allows; the first envelope is fetched here, with no client default.
export default async function TemplateLibraryPage() {
  const session = await getHostSession();
  if (!session) redirect("/auth/login?returnTo=/marketplace/library");

  let envelope = null;
  let error = null;
  try {
    envelope = await fetchAPI("/api/v1/deal-room/library");
  } catch (e) {
    error = e.status === 403 ? "forbidden" : e.message;
  }
  return (
    <AppShell user={session.user}>
      <h1 className="text-3xl font-semibold text-navy">Agreement templates</h1>
      <p className="mt-1 text-sm text-text-muted">
        Your firm&rsquo;s own agreements, versioned, beside the platform templates.
      </p>
      <div className="mt-6">
        {error === "forbidden" ? (
          <div className="rounded-[6px] border border-[#ece8dd] bg-white p-10 text-center text-sm text-text-muted">
            The template library is open to your firm&rsquo;s administrators.
          </div>
        ) : error ? (
          <div className="rounded-[6px] border border-[#ece8dd] bg-white p-10 text-center text-sm text-text-muted">
            Could not load the template library: {error}
          </div>
        ) : (
          <TemplateLibrary initialEnvelope={envelope} />
        )}
      </div>
    </AppShell>
  );
}
