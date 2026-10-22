import { redirect } from "next/navigation";
import { getHostSession } from "@/lib/authServer";
import AppShell from "@/components/AppShell";
import { fetchAPI } from "@/lib/api";
import { labelFor } from "@/lib/menuVisibility";
import AccessGrid from "@/components/dealroom/AccessGrid";

// DEALROOM1 — the access grid. The server decides the scope (403 for an
// investor who publishes nothing); the first page is fetched here so the first
// paint already carries the real envelope. No client-side default for it.
export default async function DealAccessPage() {
  const session = await getHostSession();
  if (!session) redirect("/auth/login?returnTo=/marketplace/access");

  let envelope = null;
  let error = null;
  try {
    envelope = await fetchAPI("/api/v1/deal-room/access-grid");
  } catch (e) {
    error = e.status === 403 ? "forbidden" : e.message;
  }
  return (
    <AppShell user={session.user}>
      <h1 className="text-3xl font-semibold text-navy">{labelFor("/marketplace/access")}</h1>
      <p className="mt-1 text-sm text-text-muted">
        Who has asked to see each deal, what they signed, and the access they hold.
      </p>
      <div className="mt-6">
        {error === "forbidden" ? (
          <div className="rounded-[6px] border border-[#ece8dd] bg-white p-10 text-center text-sm text-text-muted">
            Deal access is open to a deal&rsquo;s publisher, its delegates and staff.
          </div>
        ) : error ? (
          <div className="rounded-[6px] border border-[#ece8dd] bg-white p-10 text-center text-sm text-text-muted">
            Could not load the access grid: {error}
          </div>
        ) : (
          <AccessGrid initialEnvelope={envelope} />
        )}
      </div>
    </AppShell>
  );
}
