import Link from "next/link";
import { redirect } from "next/navigation";
import { getHostSession } from "@/lib/authServer";
import AppShell from "@/components/AppShell";
import { fetchAPI } from "@/lib/api";
import BillingGroupsManager from "@/components/admin/BillingGroupsManager";

// Billing groups — the breakpoint aggregation unit (sprint fee33).
//
// A billing group is deliberately NOT a household. A household is a CRM
// relationship; a billing group is the arithmetic container that decides which
// accounts' values sum together. They diverge in the cases that cost money — a
// trust reported with the family but billed standalone, two households sharing
// one negotiated breakpoint — so the household link here is an optional label
// and membership is always explicit.
//
// The permission envelope is seeded from this server fetch so the first paint
// is already correct: a view-only caller never sees a write control appear and
// then vanish. `permissions` is passed through untouched — the client applies
// no default of its own, and a missing envelope fails closed.
export default async function BillingGroupsPage() {
  const session = await getHostSession();
  if (!session) {
    redirect("/auth/login?returnTo=/admin/billing-groups");
  }

  let envelope = null;
  let error = null;
  try {
    envelope = await fetchAPI("/api/v1/billing-groups");
  } catch (e) {
    error = e.status === 403 ? "forbidden" : e.message;
  }

  return (
    <AppShell user={session.user}>
      <div className="flex items-baseline justify-between">
        <div>
          <h1 className="text-3xl font-semibold text-navy">Billing groups</h1>
          <p className="mt-1 text-sm text-text-muted">
            Which accounts&rsquo; values sum together — for breakpoints,
            statements and payers
          </p>
        </div>
        <Link href="/admin" className="text-sm text-gold hover:underline">
          ← Admin
        </Link>
      </div>

      {error === "forbidden" ? (
        <div className="mt-6 rounded border border-border bg-bg-card p-10 text-center text-sm text-text-muted">
          You do not have permission to view billing groups.
        </div>
      ) : error ? (
        <div className="mt-6 rounded border border-border bg-bg-card p-10 text-center text-sm text-gold">
          Could not load billing groups: {error}
        </div>
      ) : (
        <BillingGroupsManager
          initialRows={envelope?.rows || []}
          initialPermissions={envelope?.permissions || null}
          initialVocabularies={envelope?.vocabularies || null}
          households={envelope?.households || []}
        />
      )}
    </AppShell>
  );
}
