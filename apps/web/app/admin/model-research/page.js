import { redirect } from "next/navigation";

import AppShell from "@/components/AppShell";
import ModelResearchGrid from "@/components/admin/ModelResearchGrid";
import { getHostSession } from "@/lib/authServer";

export const dynamic = "force-dynamic";

// modelresearch.structural — read-only research grid of every model LiteLLM
// prices. Super Admin or manage_org_settings. The API is the gate (403 for a
// member); the grid renders nothing but the refusal when it is refused.
export default async function ModelResearchPage() {
  const session = await getHostSession();
  if (!session) {
    redirect("/auth/login?returnTo=/admin/model-research");
  }

  return (
    <AppShell user={session.user}>
      <div>
        <h1 className="text-3xl font-semibold text-navy">Model Research</h1>
        <p className="mt-1 text-sm text-text-muted">
          Every model LiteLLM prices, with what each costs, what it can do, and
          whether it runs on this platform today. Read-only.
        </p>
      </div>
      <ModelResearchGrid />
    </AppShell>
  );
}
