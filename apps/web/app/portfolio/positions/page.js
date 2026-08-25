import { redirect } from "next/navigation";

import AppShell from "@/components/AppShell";
import PositionsGrid from "@/components/portfolio/PositionsGrid";
import { getHostSession } from "@/lib/authServer";

export const metadata = {
  title: "Positions · 2nd Act Capital",
};

// Host-aware session check (lib/authServer), the same gate every other page
// uses — a session held on one tenant's host must not satisfy another's.
export default async function PositionsPage() {
  const session = await getHostSession();
  if (!session) {
    redirect("/auth/login?returnTo=/portfolio/positions");
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
            Positions
          </h1>
          <p className="mt-1 text-sm text-[var(--2a-text-secondary)]">
            Every holding across the firm. Select a row to see its valuation,
            transactions and source documents.
          </p>
        </header>

        <PositionsGrid />
      </div>
    </AppShell>
  );
}
