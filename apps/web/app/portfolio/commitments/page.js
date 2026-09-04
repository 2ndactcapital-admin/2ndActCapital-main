import { redirect } from "next/navigation";

import AppShell from "@/components/AppShell";
import CommitmentLookupForm from "@/components/portfolio/CommitmentLookupForm";
import { getHostSession } from "@/lib/authServer";

export const metadata = {
  title: "Commitments · 2nd Act Capital",
};

// TA Model Sprint 3, Task 1a: there is no general commitments list endpoint
// in this app yet (see CommitmentLookupForm's own note) — this is a minimal
// entry point into a real commitment's real projection by id, not a full
// commitments list/grid.
export default async function CommitmentsPage() {
  const session = await getHostSession();
  if (!session) {
    redirect("/auth/login?returnTo=/portfolio/commitments");
  }

  return (
    <AppShell user={session.user}>
      <div className="mx-auto max-w-[720px]">
        <header className="mb-5">
          <p className="text-[11px] font-bold uppercase tracking-[0.22em] text-[var(--2a-gold)]">
            Portfolio
          </p>
          <h1
            className="mt-1 text-2xl font-semibold text-[var(--2a-navy)]"
            style={{ fontFamily: "Spectral, Georgia, serif" }}
          >
            Commitments
          </h1>
          <p className="mt-1 text-sm text-[var(--2a-text-secondary)]">
            Open a commitment&rsquo;s projected cash flows by id.
          </p>
        </header>

        <div
          className="rounded-lg border bg-white p-6"
          style={{ borderColor: "#ece8dd", boxShadow: "0 1px 3px rgba(0,0,0,0.06)" }}
        >
          <CommitmentLookupForm />
        </div>
      </div>
    </AppShell>
  );
}
