import { redirect } from "next/navigation";

import AppShell from "@/components/AppShell";
import SecuritiesGrid from "@/components/portfolio/SecuritiesGrid";
import { getHostSession } from "@/lib/authServer";

export const metadata = {
  title: "Securities & Assets · 2nd Act Capital",
};

// Host-aware session check (lib/authServer), the same gate every other page
// uses — a session held on one tenant's host must not satisfy another's.
//
// Deliberately NOT a permission check. `view_portfolio` and `manage_portfolio`
// are enforced by FastAPI on every request the grid makes, and a second copy of
// that rule here would be one that could disagree with the real one. What this
// page owes the user is the session gate; what the API owes them is the answer.
export default async function SecuritiesPage() {
  const session = await getHostSession();
  if (!session) {
    redirect("/auth/login?returnTo=/portfolio/securities");
  }

  return (
    <AppShell user={session.user}>
      <div className="mx-auto max-w-[1600px]">
        <header className="mb-5">
          <p className="text-[11px] font-bold uppercase tracking-[0.22em] text-[var(--2a-gold)]">
            Portfolio
          </p>
          <h1
            className="mt-1 text-2xl font-semibold text-[var(--2a-navy)]"
            style={{ fontFamily: "Spectral, Georgia, serif" }}
          >
            Securities &amp; Assets
          </h1>
          <p className="mt-1 text-sm text-[var(--2a-text-secondary)]">
            Every instrument this firm holds, joined to the platform security
            master where one exists. Your asset records are yours to edit;
            identifiers and price history are shared across every tenant and are
            shown read-only.
          </p>
        </header>

        <SecuritiesGrid />
      </div>
    </AppShell>
  );
}
