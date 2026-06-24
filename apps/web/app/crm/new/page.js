import { redirect } from "next/navigation";
import { auth0 } from "@/lib/auth0";
import AppShell from "@/components/AppShell";
import NewEntityForm from "@/components/crm/NewEntityForm";

export default async function NewEntityPage() {
  const session = await auth0.getSession();
  if (!session) {
    redirect("/auth/login?returnTo=/crm/new");
  }

  return (
    <AppShell user={session.user}>
      <nav className="text-sm text-text-muted">
        <a href="/crm" className="hover:text-navy">
          CRM
        </a>
        <span className="mx-2">›</span>
        <span className="text-text-secondary">New Entity</span>
      </nav>

      <h1 className="mt-3 text-2xl font-semibold text-navy">New Entity</h1>

      <div className="mt-8">
        <NewEntityForm />
      </div>
    </AppShell>
  );
}
