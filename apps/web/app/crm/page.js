import { redirect } from "next/navigation";
import { auth0 } from "@/lib/auth0";
import AppShell from "@/components/AppShell";
import EntityTable from "@/components/crm/EntityTable";
import { fetchAPI } from "@/lib/api";
import { FILTER_TABS, STATUS_FILTERS } from "@/lib/entityTypes";

export default async function CrmPage({ searchParams }) {
  const session = await auth0.getSession();
  if (!session) {
    redirect("/auth/login?returnTo=/crm");
  }

  const params = (await searchParams) || {};
  const q = typeof params.q === "string" ? params.q : "";
  const type = typeof params.type === "string" ? params.type : "";
  const status = typeof params.status === "string" ? params.status : "";

  let entities = [];
  let loadError = null;
  try {
    entities = await fetchAPI("/api/v1/entities", {
      searchParams: {
        search: q || undefined,
        type: type || undefined,
        status: status || undefined,
      },
    });
  } catch (error) {
    loadError = error.message;
  }

  function buildHref({ nextType = type, nextStatus = status } = {}) {
    const sp = new URLSearchParams();
    if (q) sp.set("q", q);
    if (nextType) sp.set("type", nextType);
    if (nextStatus) sp.set("status", nextStatus);
    const qs = sp.toString();
    return qs ? `/crm?${qs}` : "/crm";
  }

  return (
    <AppShell user={session.user}>
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-semibold text-navy">Entities</h1>
        <a
          href="/crm/new"
          className="rounded-md bg-navy px-4 py-2 text-sm font-medium text-bg-app transition-opacity hover:opacity-90"
        >
          New Entity
        </a>
      </div>

      {/* Search */}
      <form action="/crm" method="get" className="mt-6">
        {type && <input type="hidden" name="type" value={type} />}
        {status && <input type="hidden" name="status" value={status} />}
        <input
          type="text"
          name="q"
          defaultValue={q}
          placeholder="Search by name…"
          className="w-full max-w-md rounded-md border border-border bg-bg-card px-3 py-2 text-sm text-text-primary outline-none focus:ring-2 focus:ring-navy"
        />
      </form>

      {/* Status filter pills */}
      <div className="mt-4 flex flex-wrap gap-2">
        {STATUS_FILTERS.map((pill) => {
          const active = pill.value === status;
          return (
            <a
              key={pill.value || "all-status"}
              href={buildHref({ nextStatus: pill.value })}
              className={`rounded-full px-3 py-1 text-sm font-medium transition-colors ${
                active
                  ? "bg-gold text-navy"
                  : "border border-border bg-bg-card text-text-secondary hover:bg-border"
              }`}
            >
              {pill.label}
            </a>
          );
        })}
      </div>

      {/* Type filter tabs */}
      <div className="mt-3 flex flex-wrap gap-2">
        {FILTER_TABS.map((tab) => {
          const active = tab.value === type;
          return (
            <a
              key={tab.value || "all"}
              href={buildHref({ nextType: tab.value })}
              className={`rounded-full px-3 py-1 text-sm font-medium transition-colors ${
                active
                  ? "bg-navy text-bg-app"
                  : "border border-border bg-bg-card text-text-secondary hover:bg-border"
              }`}
            >
              {tab.label}
            </a>
          );
        })}
      </div>

      {/* Results */}
      <div className="mt-6">
        {loadError ? (
          <div className="rounded-lg border border-border bg-bg-card p-8 text-center text-sm text-text-muted">
            Could not load entities: {loadError}
          </div>
        ) : entities.length === 0 ? (
          <div className="rounded-lg border border-border bg-bg-card p-12 text-center">
            <p className="text-sm font-medium text-text-secondary">
              No entities found
            </p>
            <p className="mt-1 text-sm text-text-muted">
              {q || type || status
                ? "Try adjusting your search or filters."
                : "Create your first entity to get started."}
            </p>
          </div>
        ) : (
          <EntityTable entities={entities} />
        )}
      </div>
    </AppShell>
  );
}
