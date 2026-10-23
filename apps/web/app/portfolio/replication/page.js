import { redirect } from "next/navigation";
import { getHostSession } from "@/lib/authServer";
import AppShell from "@/components/AppShell";
import { fetchAPI } from "@/lib/api";
import { labelFor } from "@/lib/menuVisibility";
import RegistryList from "@/components/replication/RegistryList";

// REP1 — Portfolio → Replication: the org's registry of funds to replicate.
// The first page is fetched here so the first paint already carries the
// server's real envelope (permissions + vocabularies); no client default.
export default async function ReplicationPage() {
  const session = await getHostSession();
  if (!session) redirect("/auth/login?returnTo=/portfolio/replication");

  let envelope = null;
  let error = null;
  try {
    envelope = await fetchAPI("/api/v1/replication/registry");
  } catch (e) {
    error = e.status === 403 ? "forbidden" : e.message;
  }
  return (
    <AppShell user={session.user}>
      <h1 className="text-3xl font-semibold text-navy">{labelFor("/portfolio/replication")}</h1>
      <p className="mt-1 text-sm text-text-muted">
        Funds whose published holdings a member can own directly, and the terms each replication follows.
      </p>
      <div className="mt-6">
        {error === "forbidden" ? (
          <div className="rounded-[6px] border border-[#ece8dd] bg-white p-10 text-center text-sm text-text-muted">
            Replication is open to staff with replication access.
          </div>
        ) : error ? (
          <div className="rounded-[6px] border border-[#ece8dd] bg-white p-10 text-center text-sm text-text-muted">
            Could not load the registry: {error}
          </div>
        ) : (
          <RegistryList initialEnvelope={envelope} />
        )}
      </div>
    </AppShell>
  );
}
