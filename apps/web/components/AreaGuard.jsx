import { redirect } from "next/navigation";
import { getHostSession } from "@/lib/authServer";
import AppShell from "@/components/AppShell";
import { getMe } from "@/lib/api";
import { canOpenArea } from "@/lib/menuVisibility";

/**
 * BUGFIX2 — the server-side refusal for a whole area (a layout wraps its pages
 * in it). The decision is lib/menuVisibility.mjs `canOpenArea` (AREA_GATES), the
 * same definition the sidebar renders from; nothing is decided here. A lost
 * /users/me envelope fails CLOSED (canOpenArea(null, …) is false). The page's
 * own API gate stays the real gate — this keeps a member who pastes a staff URL
 * from seeing the page's chrome, title or controls.
 */
export default async function AreaGuard({ area, children }) {
  const session = await getHostSession();
  if (!session) redirect(`/auth/login?returnTo=${area}`);

  let me = null;
  try {
    me = await getMe();
  } catch {
    me = null;
  }
  if (canOpenArea(me, area)) return children;

  return (
    <AppShell user={session.user}>
      <div className="mt-6 rounded-[6px] border border-[#ece8dd] bg-white p-10 text-center text-sm text-text-muted">
        You do not have access to this area.
      </div>
    </AppShell>
  );
}
