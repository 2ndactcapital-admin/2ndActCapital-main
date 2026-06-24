import { redirect } from "next/navigation";
import { auth0 } from "@/lib/auth0";
import AppShell from "@/components/AppShell";
import NewDealForm from "@/components/marketplace/NewDealForm";
import { getTaxonomy } from "@/lib/api";
import { isStaff } from "@/lib/roles";

export default async function NewDealPage() {
  const session = await auth0.getSession();
  if (!session) {
    redirect("/auth/login?returnTo=/marketplace/new");
  }
  // Staff only — members are redirected back to the marketplace.
  if (!isStaff(session.user)) {
    redirect("/marketplace");
  }

  let taxonomy = { super_classes: [] };
  try {
    taxonomy = await getTaxonomy();
  } catch {
    // taxonomy selector will show empty — form still works
  }

  return (
    <AppShell user={session.user}>
      <nav className="text-sm text-text-muted">
        <a href="/marketplace" className="hover:text-navy">
          Marketplace
        </a>
        <span className="mx-2">›</span>
        <span className="text-text-secondary">New Deal</span>
      </nav>

      <h1 className="mt-3 text-2xl font-semibold text-navy">New Deal</h1>

      <div className="mt-8">
        <NewDealForm taxonomy={taxonomy} />
      </div>
    </AppShell>
  );
}
