import { redirect } from "next/navigation";
import { getHostSession } from "@/lib/authServer";
import AppShell from "@/components/AppShell";
import { getMe } from "@/lib/api";
import { SECTIONS, visibleAdminGroups } from "@/lib/menuVisibility";

export const metadata = {
  title: "Admin — 2nd Act Capital",
};

// navregroup — a grouped overview: one card per master page the caller can see
// (its description, then its visible tabs as links), under the same section
// headings as the sidebar. Groups, descriptions, tabs, labels and gates all come
// from lib/menuVisibility.mjs — the sidebar renders from the same definition,
// so the two can no longer drift (superadminmenu fixed exactly that drift once).
export default async function AdminIndexPage() {
  const session = await getHostSession();
  if (!session) redirect("/auth/login?returnTo=/admin");

  let me = null;
  try {
    me = await getMe();
  } catch {
    // If /users/me fails we render an empty state rather than crashing.
  }

  const groups = visibleAdminGroups(me);
  const sections = SECTIONS.map((s) => ({
    ...s,
    groups: groups.filter((g) => g.section === s.id),
  })).filter((s) => s.groups.length > 0);

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
        sections.map((section) => (
          <section key={section.id} className="mt-8">
            <h2 className="text-sm font-semibold uppercase tracking-wider text-text-muted">
              {section.header}
            </h2>
            <div className="mt-3 grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
              {section.groups.map((g) => (
                <div
                  key={g.id}
                  data-group={g.id}
                  className="rounded-md border bg-bg-card p-5"
                  style={{ borderColor: "#ece8dd" }}
                >
                  <a href={g.tabs[0].href} className="text-base font-semibold text-navy hover:underline">
                    {g.label}
                  </a>
                  <p className="mt-1 text-sm text-text-muted">{g.description}</p>
                  <ul className="mt-3 space-y-1 text-sm">
                    {g.tabs.map((t) => (
                      <li key={t.href}>
                        <a href={t.href} className="text-gold hover:underline">
                          {t.label}
                        </a>
                      </li>
                    ))}
                  </ul>
                </div>
              ))}
            </div>
          </section>
        ))
      )}
    </AppShell>
  );
}
