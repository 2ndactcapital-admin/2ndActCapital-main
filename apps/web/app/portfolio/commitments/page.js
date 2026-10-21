import { redirect } from "next/navigation";

import AppShell from "@/components/AppShell";
import CommitmentLookupForm from "@/components/portfolio/CommitmentLookupForm";
import CommitmentsList from "@/components/portfolio/CommitmentsList";
import { fetchAPI } from "@/lib/api";
import { getHostSession } from "@/lib/authServer";

export const metadata = {
  title: "Commitments · 2nd Act Capital",
};

// BUGFIX1 (PORT-02): the caller's visible commitments are listed first (GET
// /portfolio/commitments — view_portfolio, filtered to the entities the caller
// can see); each row opens the existing projection. The id lookup stays as a
// secondary control.
export default async function CommitmentsPage() {
  const session = await getHostSession();
  if (!session) {
    redirect("/auth/login?returnTo=/portfolio/commitments");
  }

  let rows = [];
  let listError = null;
  try {
    rows = (await fetchAPI("/api/v1/portfolio/commitments"))?.rows || [];
  } catch (error) {
    listError = error.message;
  }

  return (
    <AppShell user={session.user}>
      <div className="mx-auto max-w-[960px]">
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
            Your commitments, with what has been called and what remains. Open
            one to see its projected cash flows.
          </p>
        </header>

        <div
          className="mb-6 rounded-lg border bg-white p-6"
          style={{ borderColor: "#ece8dd" }}
        >
          <CommitmentsList rows={rows} error={listError} />
        </div>

        <h2 className="mb-2 text-xs font-semibold uppercase tracking-wide text-[var(--2a-text-muted)]">
          Open a commitment by id
        </h2>

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
