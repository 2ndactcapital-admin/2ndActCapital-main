import { redirect } from "next/navigation";
import { auth0 } from "@/lib/auth0";
import AppShell from "@/components/AppShell";
import TradingAuthorityManager from "@/components/admin/TradingAuthorityManager";
import {
  getAdminUsers,
  getTradingAuthorityGrants,
  listEntities,
} from "@/lib/api";

// SOC Phase 5 admin screen: assign a member's per-entity trading-authority tier
// (inquiry | limited | full). Super Admin only. This only POPULATES the grant
// data the maker-checker + tier enforcement engine reads — it does not itself
// change any money-movement endpoint's behavior.
export default async function TradingAuthorityPage() {
  const session = await auth0.getSession();
  if (!session) {
    redirect("/auth/login?returnTo=/admin/trading-authority");
  }

  let grants = [];
  let tiers = ["inquiry", "limited", "full"];
  let users = [];
  let entities = [];
  let error = null;
  try {
    const [data, u, e] = await Promise.all([
      getTradingAuthorityGrants(),
      getAdminUsers({ limit: 200 }),
      listEntities({ limit: 200 }),
    ]);
    grants = data.grants || [];
    tiers = data.tiers || tiers;
    users = u || [];
    entities = e || [];
  } catch (e) {
    error = e.status === 403 ? "forbidden" : e.message;
  }

  return (
    <AppShell user={session.user}>
      <div>
        <h1 className="text-3xl font-semibold text-navy">Trading Authority</h1>
        <p className="mt-1 text-sm text-text-muted">
          Assign each member&rsquo;s money-movement authority per account
        </p>
      </div>

      {error === "forbidden" ? (
        <div className="mt-6 rounded-lg border border-border bg-bg-card p-10 text-center text-sm text-text-muted">
          You do not have permission to manage trading authority.
        </div>
      ) : error ? (
        <div className="mt-6 rounded-lg border border-border bg-bg-card p-10 text-center text-sm text-[#9B2335]">
          Could not load trading-authority data: {error}
        </div>
      ) : (
        <TradingAuthorityManager
          initialGrants={grants}
          users={users}
          entities={entities}
          tiers={tiers}
        />
      )}
    </AppShell>
  );
}
