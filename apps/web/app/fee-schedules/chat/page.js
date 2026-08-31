import { redirect } from "next/navigation";

import AppShell from "@/components/AppShell";
import FeeChatWorkbench from "@/components/fee/FeeChatWorkbench";
import { getHostSession } from "@/lib/authServer";

export const metadata = {
  title: "Fee schedule assistant · 2nd Act Capital",
};

// Host-aware session check (lib/authServer), the same gate every other page
// uses — a session held on one tenant's host must not satisfy another's.
export default async function FeeChatPage() {
  const session = await getHostSession();
  if (!session) {
    redirect("/auth/login?returnTo=/fee-schedules/chat");
  }

  return (
    <AppShell user={session.user}>
      <div className="mx-auto max-w-[1000px]">
        <header className="mb-5">
          <p className="text-[11px] font-bold uppercase tracking-[0.22em] text-[var(--2a-gold)]">
            Billing
          </p>
          <h1
            className="mt-1 text-2xl font-semibold text-[var(--2a-navy)]"
            style={{ fontFamily: "Spectral, Georgia, serif" }}
          >
            Fee schedule assistant
          </h1>
          <p className="mt-1 max-w-[70ch] text-sm text-[var(--2a-text-secondary)]">
            Describe an arrangement in your own words. The assistant proposes a
            schedule; the validator and the billing engine decide whether it
            holds and what it charges. Anything you did not state is left
            unresolved rather than assumed.
          </p>
        </header>

        <FeeChatWorkbench />
      </div>
    </AppShell>
  );
}
