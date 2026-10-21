import { redirect } from "next/navigation";
import { getHostSession } from "@/lib/authServer";
import AppShell from "@/components/AppShell";
import { getMe } from "@/lib/api";
import { GROUPS, landingDecision } from "@/lib/menuVisibility";

/**
 * A master page's own route (navregroup), e.g. /admin/people. After the same
 * host-aware session check every page uses, it redirects on the server to the
 * first tab the caller can see. With no visible tab it renders the standard
 * refusal panel — it never redirects back to itself or to another landing, so
 * it cannot loop. Which tab, and the refusal decision, come from
 * lib/menuVisibility.mjs (landingDecision); nothing is decided here.
 */
export default async function GroupLanding({ groupId }) {
  const group = GROUPS.find((g) => g.id === groupId);
  const session = await getHostSession();
  if (!session) redirect(`/auth/login?returnTo=${group?.landing ?? "/dashboard"}`);

  let me = null;
  try {
    me = await getMe();
  } catch {
    // Treated like any other lost envelope: strict and Super Admin tabs fail
    // closed, and the tab page's own API remains the real gate.
  }

  const decision = landingDecision(me, groupId);
  if (decision.kind === "redirect") redirect(decision.href);

  return (
    <AppShell user={session.user}>
      <div>
        <h1 className="text-3xl font-semibold text-navy">{group?.label}</h1>
      </div>
      <div className="mt-6 rounded-lg border border-border bg-bg-card p-10 text-center text-sm text-text-muted">
        You do not have access to this area.
      </div>
    </AppShell>
  );
}
