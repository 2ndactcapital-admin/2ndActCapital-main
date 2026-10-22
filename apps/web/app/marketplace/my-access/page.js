import { redirect } from "next/navigation";
import { getHostSession } from "@/lib/authServer";
import AppShell from "@/components/AppShell";
import { fetchAPI } from "@/lib/api";
import { labelFor } from "@/lib/menuVisibility";
import MyDealAccess from "@/components/dealroom/MyDealAccess";

// DEALROOM1 — the investor's own deal-room access (GET /deal-room/my-access,
// self-scoped server-side). Publishers / delegates also get a link to the grid.
export default async function MyDealAccessPage() {
  const session = await getHostSession();
  if (!session) redirect("/auth/login?returnTo=/marketplace/my-access");

  let data = null;
  let me = null;
  let error = null;
  try {
    [data, me] = await Promise.all([fetchAPI("/api/v1/deal-room/my-access"), fetchAPI("/api/v1/deal-room/me")]);
  } catch (e) {
    error = e.message;
  }
  return (
    <AppShell user={session.user}>
      <h1 className="text-3xl font-semibold text-navy">{labelFor("/marketplace/my-access")}</h1>
      <p className="mt-1 text-sm text-text-muted">The deals you have asked to see, what you signed and when.</p>
      {me?.has_access_grid === true && (
        <a href="/marketplace/access" className="mt-3 inline-block text-sm text-navy hover:underline">
          Manage access to the deals you publish →
        </a>
      )}
      <div className="mt-6">
        {error ? (
          <div className="rounded-[6px] border border-[#ece8dd] bg-white p-10 text-center text-sm text-text-muted">
            Could not load your deal access: {error}
          </div>
        ) : (
          <MyDealAccess rows={data?.rows || []} />
        )}
      </div>
    </AppShell>
  );
}
