import { redirect } from "next/navigation";
import { getHostSession } from "@/lib/authServer";
import AppShell from "@/components/AppShell";
import EntityGraphNavigator from "@/components/graph/EntityGraphNavigator";

// Member-facing ownership tree. The focal entity is resolved server-side from
// the member's own identity (their delegate-grant principals); data flows
// through the member visibility engine (resolve_entity_set / ownership +
// beneficiary look-through) + restricted-access filter. A member only ever
// sees their own tree — the route takes no focal from the client.
export default async function MemberOwnershipTreePage() {
  const session = await getHostSession();
  if (!session) {
    redirect(`/auth/login?returnTo=/portfolio/ownership-tree`);
  }

  return (
    <AppShell user={session.user}>
      <nav className="text-sm text-text-muted">
        <a href="/portfolio" className="hover:text-navy">
          Portfolio
        </a>
        <span className="mx-2">›</span>
        <span className="text-text-secondary">Ownership Tree</span>
      </nav>

      <div className="mt-3">
        <h1 className="text-3xl font-semibold text-navy">Your Ownership Tree</h1>
        <p className="mt-2 text-sm text-text-secondary">
          Your entities and their ownership and beneficiary relationships, as you
          are entitled to see them.
        </p>
      </div>

      <div className="mt-8">
        <EntityGraphNavigator
          apiBase="/api/me/ownership-graph"
          title="Ownership & Beneficiary Tree"
          emptyMessage="No ownership tree is available for your account yet."
        />
      </div>
    </AppShell>
  );
}
