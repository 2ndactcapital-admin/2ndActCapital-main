import { redirect } from "next/navigation";

import AppShell from "@/components/AppShell";
import TransactionsGrid from "@/components/portfolio/TransactionsGrid";
import { getHostSession } from "@/lib/authServer";

export const metadata = {
  title: "Transactions · 2nd Act Capital",
};

// Host-aware session check (lib/authServer), the same gate every other page
// uses — a session held on one tenant's host must not satisfy another's.
export default async function TransactionsPage() {
  const session = await getHostSession();
  if (!session) {
    redirect("/auth/login?returnTo=/portfolio/transactions");
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
            Transactions
          </h1>
          {/* BUGFIX3 — the sentence comes from the API (scope): a member never reads "across the firm" */}
        </header>

        <TransactionsGrid />
      </div>
    </AppShell>
  );
}
