import Link from "next/link";
import { redirect } from "next/navigation";

import AppShell from "@/components/AppShell";
import CommitmentProjectionScreen from "@/components/portfolio/CommitmentProjectionScreen";
import { getHostSession } from "@/lib/authServer";
import { getTaDefaults, getTaProjection } from "@/lib/api";

export const metadata = {
  title: "Commitment projection · 2nd Act Capital",
};

// TA Model Sprint 3 — one commitment's projected cash flows. Host-aware
// session gate, same pattern as every other portfolio page.
export default async function CommitmentProjectionPage({ params, searchParams }) {
  const { commitmentId } = await params;
  const sp = (await searchParams) || {};
  const strategyKey = typeof sp.strategy_key === "string" ? sp.strategy_key : undefined;

  const session = await getHostSession();
  if (!session) {
    redirect(`/auth/login?returnTo=/portfolio/commitments/${commitmentId}`);
  }

  let projection = null;
  let error = null;
  try {
    projection = await getTaProjection(commitmentId, { strategyKey });
  } catch (e) {
    error = { status: e.status, message: e.message };
  }

  // Only fetched when the real, existing strategy vocabulary is actually
  // needed (no active override yet) — never hardcoded (CLAUDE.md Rule 1).
  let strategyKeys = [];
  if (error?.status === 422) {
    try {
      const defaults = await getTaDefaults();
      strategyKeys = Object.keys(defaults?.["modeling.ta.strategy_defaults"] || {});
    } catch {
      // The screen falls back to showing the raw 422 detail.
    }
  }

  return (
    <AppShell user={session.user}>
      <div className="mx-auto max-w-[1200px]">
        <header className="mb-5 flex items-baseline justify-between">
          <div>
            <p className="text-[11px] font-bold uppercase tracking-[0.22em] text-[var(--2a-gold)]">
              Portfolio
            </p>
            <h1
              className="mt-1 text-2xl font-semibold text-[var(--2a-navy)]"
              style={{ fontFamily: "Spectral, Georgia, serif" }}
            >
              Commitment projection
            </h1>
            <p className="mt-1 text-sm text-[var(--2a-text-secondary)]">
              Projected capital calls, distributions and NAV — computed live
              on every visit, never saved.
            </p>
          </div>
          <Link href="/portfolio/commitments" className="text-sm text-[var(--2a-gold)] hover:underline">
            ← Commitments
          </Link>
        </header>

        <CommitmentProjectionScreen
          commitmentId={commitmentId}
          initialProjection={projection}
          initialError={error}
          strategyKeys={strategyKeys}
        />
      </div>
    </AppShell>
  );
}
