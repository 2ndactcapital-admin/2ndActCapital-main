import { redirect } from "next/navigation";
import { getHostSession } from "@/lib/authServer";
import AppShell from "@/components/AppShell";
import { fetchAPI } from "@/lib/api";
import { labelFor } from "@/lib/menuVisibility";
import ModelList from "@/components/models/ModelList";

// REP3 — Portfolio → Models: the org's ONE model registry (uploads, library
// copies and subscriptions, replicated funds, composites). The first page is
// fetched here so the first paint carries the server's real envelope.
export default async function ModelsPage() {
  const session = await getHostSession();
  if (!session) redirect("/auth/login?returnTo=/portfolio/models");

  let envelope = null;
  let error = null;
  try {
    envelope = await fetchAPI("/api/v1/models");
  } catch (e) {
    error = e.status === 403 ? "forbidden" : e.message;
  }
  return (
    <AppShell user={session.user}>
      <h1 className="text-3xl font-semibold text-navy">{labelFor("/portfolio/models")}</h1>
      <p className="mt-1 text-sm text-text-muted">
        Every model in one registry, whatever its source — each version approved by a second person before it is live.
      </p>
      <div className="mt-6">
        {error === "forbidden" ? (
          <div className="rounded-[6px] border border-[#ece8dd] bg-white p-10 text-center text-sm text-text-muted">
            Models are open to staff with model access.
          </div>
        ) : error ? (
          <div className="rounded-[6px] border border-[#ece8dd] bg-white p-10 text-center text-sm text-text-muted">
            Could not load models: {error}
          </div>
        ) : (
          <ModelList initialEnvelope={envelope} />
        )}
      </div>
    </AppShell>
  );
}
