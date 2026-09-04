import Link from "next/link";
import { redirect } from "next/navigation";
import { getHostSession } from "@/lib/authServer";
import AppShell from "@/components/AppShell";
import { getTaDefaults } from "@/lib/api";
import TaSettingsScreen from "@/components/admin/TaSettingsScreen";

// TA Model — admin settings (TA Model Sprint 2). Edits modeling.ta.* — the 8
// strategy default parameter sets plus the 3 platform-level TA settings
// (projection horizon, default periods per year, calibration minimum).
//
// Reads are open to any authenticated org member (matches org_settings' own
// read-open convention, and every member with a projection on screen needs to
// understand what defaults produced it); the screen's write controls render
// only when the real `permissions.can_write` envelope says so — no client
// default, no `?? true`.
export default async function TaModelSettingsPage() {
  const session = await getHostSession();
  if (!session) {
    redirect("/auth/login?returnTo=/admin/modeling/ta");
  }

  let envelope = null;
  let error = null;
  try {
    envelope = await getTaDefaults();
  } catch (e) {
    error = e.status === 403 ? "forbidden" : e.message;
  }

  return (
    <AppShell user={session.user}>
      <div className="flex items-baseline justify-between">
        <div>
          <h1 className="text-3xl font-semibold text-navy">
            TA model defaults
          </h1>
          <p className="mt-1 text-sm text-text-muted">
            Per-strategy J-curve parameters and platform-level projection
            settings — what every commitment projects against until it is
            calibrated or overridden individually.
          </p>
        </div>
        <Link href="/admin" className="text-sm text-gold hover:underline">
          ← Admin
        </Link>
      </div>

      {error === "forbidden" ? (
        <div className="mt-6 rounded-lg border border-border bg-bg-card p-10 text-center text-sm text-text-muted">
          You do not have permission to view TA model settings.
        </div>
      ) : error ? (
        <div className="mt-6 rounded-lg border border-border bg-bg-card p-10 text-center text-sm text-gold">
          Could not load TA model settings: {error}
        </div>
      ) : (
        <div className="mt-6">
          <TaSettingsScreen initialEnvelope={envelope} />
        </div>
      )}
    </AppShell>
  );
}
