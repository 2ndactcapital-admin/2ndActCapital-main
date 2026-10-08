import { redirect } from "next/navigation";

import AppShell from "@/components/AppShell";
import MarketIndicators from "@/components/market/MarketIndicators";
import { getHostSession } from "@/lib/authServer";

export const metadata = {
  title: "Market indicators · 2nd Act Capital",
};

// Host-aware session check (lib/authServer), the same gate every other page
// uses. The market API admits any valid session, so no permission is checked
// here; the page renders its controls only inside the catalog's envelope.
export default async function MarketPage() {
  const session = await getHostSession();
  if (!session) {
    redirect("/auth/login?returnTo=/market");
  }

  return (
    <AppShell user={session.user}>
      <div className="mx-auto min-w-0 max-w-[1600px]">
        <header className="mb-5">
          <p className="text-[11px] font-bold uppercase tracking-[0.22em] text-[var(--2a-gold)]">
            Markets
          </p>
          <h1
            className="mt-1 text-2xl font-semibold text-[var(--2a-navy)]"
            style={{ fontFamily: "Spectral, Georgia, serif" }}
          >
            Market indicators
          </h1>
          <p className="mt-1 text-sm text-[var(--2a-text-secondary)]">
            Economic and market series side by side, from a common starting point.
          </p>
        </header>

        <MarketIndicators />
      </div>
    </AppShell>
  );
}
