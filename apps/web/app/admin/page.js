import { redirect } from "next/navigation";
import { getHostSession } from "@/lib/authServer";
import AppShell from "@/components/AppShell";
import { getMe } from "@/lib/api";
import { visibleAdminSections } from "@/lib/menuVisibility";

export const metadata = {
  title: "Admin — 2nd Act Capital",
};

// The admin sections and their gates now live in the SINGLE pure module
// lib/menuVisibility.mjs, which the sidebar uses too. Previously this page kept
// its own independent copy of both the section list and the gate logic, and the
// two drifted: Note Terms Review and Volatility Surface were in the sidebar's
// super-admin block but missing here, so the /admin index showed a strictly
// smaller menu than the sidebar it claims to mirror. Neither copy had a Super
// Admin bypass. Both problems are fixed at the source now.
//
// The descriptions stay here — presentation copy specific to this landing page.
const SECTION_DESCRIPTIONS = {
  "/admin/users": "Manage member access and roles.",
  "/admin/staff-visibility": "Staff teams and per-entity assignments.",
  "/admin/profiles": "Permission profiles for members.",
  "/admin/permission-sets": "Reusable bundles of permissions.",
  "/admin/workflows": "Governance and approval workflows.",
  "/admin/settings": "White-label and organization settings.",
  "/admin/modeling/ta": "TA model per-strategy defaults and projection settings.",
  "/admin/restricted-access": "Restrict accounts and manage allow-lists.",
  "/admin/trading-authority": "Per-entity trading-authority tiers.",
  "/admin/pricing/note-terms-queue":
    "Structured-note terms review queue and STP policy.",
  "/admin/pricing/surface": "SSVI volatility surface viewer.",
  "/admin/platform": "Platform-wide settings across all orgs.",
};

export default async function AdminIndexPage() {
  const session = await getHostSession();
  if (!session) redirect("/auth/login?returnTo=/admin");

  let me = null;
  try {
    me = await getMe();
  } catch {
    // If /users/me fails we render an empty state rather than crashing.
  }

  const sections = visibleAdminSections(me);

  return (
    <AppShell user={session.user}>
      <div>
        <h1 className="text-3xl font-semibold text-navy">Admin</h1>
        <p className="mt-1 text-sm text-text-muted">
          Administration areas available to you.
        </p>
      </div>

      {sections.length === 0 ? (
        <div className="mt-6 rounded-lg border border-border bg-bg-card p-10 text-center text-sm text-text-muted">
          You do not have access to any administration areas.
        </div>
      ) : (
        <div className="mt-6 grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {sections.map((s) => (
            <a
              key={s.href}
              href={s.href}
              className="block rounded-md border border-border bg-bg-card p-5 transition-colors hover:border-gold"
              style={{ boxShadow: "0 1px 3px rgba(0,0,0,0.06)" }}
            >
              <div className="text-base font-semibold text-navy">{s.label}</div>
              <p className="mt-1 text-sm text-text-muted">
                {SECTION_DESCRIPTIONS[s.href]}
              </p>
            </a>
          ))}
        </div>
      )}
    </AppShell>
  );
}
