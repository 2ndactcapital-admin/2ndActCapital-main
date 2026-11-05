import { redirect } from "next/navigation";
import { getHostSession } from "@/lib/authServer";
import AppShell from "@/components/AppShell";
import NewEntityForm from "@/components/crm/NewEntityForm";
import { fetchAPI } from "@/lib/api";

export default async function NewEntityPage() {
  const session = await getHostSession();
  if (!session) {
    redirect("/auth/login?returnTo=/crm/new");
  }
  // BUGFIX3 (MUSE-OLIVO-M) — the form renders only when the API's own rule for POST /entities says so
  let allowed = false;
  try {
    allowed = (await fetchAPI("/api/v1/crm/vocabularies"))?.permissions?.can?.new_entity === true;
  } catch {
    allowed = false;
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
        {allowed ? <NewEntityForm /> : (
          <p className="text-sm text-text-muted">Creating an entity needs the manage members permission.</p>
        )}
      </div>
    </AppShell>
  );
}
