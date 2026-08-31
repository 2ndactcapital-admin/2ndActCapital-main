import { redirect } from "next/navigation";

import AppShell from "@/components/AppShell";
import ProfitabilityDashboard from "@/components/profitability/ProfitabilityDashboard";
import { getHostSession } from "@/lib/authServer";

export const metadata = {
  title: "Profitability · 2nd Act Capital",
};

// Host-aware session check (lib/authServer), the same gate every other page
// uses — a session held on one tenant's host must not satisfy another's.
export default async function ProfitabilityPage() {
  const session = await getHostSession();
  if (!session) {
    redirect("/auth/login?returnTo=/profitability");
  }

  return (
    <AppShell user={session.user}>
      <div className="mx-auto max-w-[1200px]">
        <header className="mb-5">
          <p className="text-[11px] font-bold uppercase tracking-[0.22em] text-[var(--2a-gold)]">
            Firm
          </p>
          <h1
            className="mt-1 text-2xl font-semibold text-[var(--2a-navy)]"
            style={{ fontFamily: "Spectral, Georgia, serif" }}
          >
            Profitability
          </h1>
          <p className="mt-1 text-sm text-[var(--2a-text-secondary)]">
            Revenue against cost, by account, household, billing group, advisor
            or product. Contribution margin is shown before allocated overhead
            as well as after it.
          </p>
        </header>

        <ProfitabilityDashboard />
      </div>
    </AppShell>
  );
}
