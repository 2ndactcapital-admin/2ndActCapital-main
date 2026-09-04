import { forwardToApi } from "@/lib/apiForward";

// The real, frequency-aware minimum-realized-periods requirement (TA Model
// Sprint 2, Task 2) — proxies to services.ta_calibrate.minimum_realized_periods
// itself so the settings screen shows the true floor as an admin edits
// periods_per_year (e.g. 12 quarters, not a flat 3), never a value
// re-derived in the browser. Open read, same convention as GET .../defaults.
export async function GET(request) {
  const periodsPerYear = new URL(request.url).searchParams.get(
    "periods_per_year",
  );
  return forwardToApi("/api/v1/modeling/ta/calibration-floor", {
    searchParams: { periods_per_year: periodsPerYear },
  });
}
