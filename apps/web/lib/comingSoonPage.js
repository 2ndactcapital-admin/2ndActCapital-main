import { redirect } from "next/navigation";

import AppShell from "@/components/AppShell";
import ComingSoon from "@/components/ComingSoon";
import { getHostSession } from "@/lib/authServer";
import { labelFor } from "@/lib/menuVisibility.mjs";

// BUGFIX1 — one renderer for every `comingSoon` menu entry's URL. Host-aware
// session check, the same as every other page.
export async function renderComingSoon(href) {
  const session = await getHostSession();
  if (!session) {
    redirect(`/auth/login?returnTo=${encodeURIComponent(href)}`);
  }
  return (
    <AppShell user={session.user}>
      <ComingSoon label={labelFor(href)} />
    </AppShell>
  );
}
