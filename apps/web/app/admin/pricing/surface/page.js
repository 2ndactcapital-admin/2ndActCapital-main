import { redirect } from "next/navigation";
import { auth0 } from "@/lib/auth0";
import AppShell from "@/components/AppShell";
import SurfaceCalibrator from "@/components/admin/SurfaceCalibrator";

// Sprint 31 — SSVI volatility surface viewer.
//
// Server component only for the session check; the calibration itself is
// user-triggered and slow (15-40s), so it belongs in a client component that
// can show which phase it is in rather than a server-side await.
//
// Super Admin is enforced SERVER-SIDE by FastAPI. The nav entry is gated too,
// but a hidden link is not a permission — a non-super-admin who types this URL
// gets a typed 403 and the "not permitted" state inside the Calibrator.
//
// `.js`, not `.tsx`: apps/web has no TypeScript anywhere.
export default async function SurfacePage() {
  const session = await auth0.getSession();
  if (!session) {
    redirect("/auth/login?returnTo=/admin/pricing/surface");
  }

  return (
    <AppShell user={session.user}>
      <div>
        <h1 className="text-3xl font-semibold text-navy">Volatility Surface</h1>
        <p className="mt-1 max-w-3xl text-sm text-text-muted">
          Calibrate an arbitrage-free SSVI surface from live listed index
          options. Three parameters are fitted globally across every maturity;
          the fit is rejected outright if it cannot reproduce the market inside
          tolerance. Nothing is stored — this is a read of the market as of now.
        </p>
      </div>

      <SurfaceCalibrator />
    </AppShell>
  );
}
