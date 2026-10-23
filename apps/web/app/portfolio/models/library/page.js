import { redirect } from "next/navigation";
import { getHostSession } from "@/lib/authServer";
import AppShell from "@/components/AppShell";
import { fetchAPI } from "@/lib/api";
import LibraryView from "@/components/models/LibraryView";

// REP3 — the model library: the platform shelf and this org's shelf.
export default async function ModelLibraryPage() {
  const session = await getHostSession();
  if (!session) redirect("/auth/login?returnTo=/portfolio/models/library");

  let envelope = null;
  let error = null;
  try {
    envelope = await fetchAPI("/api/v1/models/library");
  } catch (e) {
    error = e.status === 403 ? "The model library is open to staff with model access." : e.message;
  }
  return (
    <AppShell user={session.user}>
      <p className="text-sm"><a className="text-navy underline" href="/portfolio/models">Models</a></p>
      <h1 className="mt-1 text-3xl font-semibold text-navy">Model library</h1>
      <p className="mt-1 text-sm text-text-muted">Copy a model to keep it as your own, or subscribe to receive each update as a draft to review.</p>
      <div className="mt-6">
        {error ? (
          <div className="rounded-[6px] border border-[#ece8dd] bg-white p-10 text-center text-sm text-text-muted">{error}</div>
        ) : (
          <LibraryView initialEnvelope={envelope} />
        )}
      </div>
    </AppShell>
  );
}
