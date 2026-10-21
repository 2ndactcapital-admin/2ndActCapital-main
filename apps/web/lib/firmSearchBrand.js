import { resolveTenant } from "@/lib/tenant";
import { brandName } from "@/lib/theme";
import { loadTheme } from "@/lib/themeServer";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

// BUGFIX1 (FIRM-01) — whose brand /firm-search wears. On a tenant's own host
// (2ndactcapital.com, <slug>.hollisworks.com) it is that tenant's
// `brand.name` from its org branding settings; only on the platform's
// marketing apex is it the Hollisworks platform org's own brand. It used to be
// "Find your firm — Hollisworks" everywhere, including the 2nd Act site.
const PLATFORM_SLUG = "hollisworks";

export async function firmSearchBrand() {
  const tenant = await resolveTenant();
  let settings = {};
  if (tenant.marketing) {
    try {
      const res = await fetch(
        `${API_BASE}/api/v1/theme/public?slug=${encodeURIComponent(PLATFORM_SLUG)}`,
        { cache: "no-store" },
      );
      if (res.ok) settings = (await res.json()).settings || {};
    } catch {
      // API down — fall through to the org name below.
    }
  } else {
    settings = (await loadTheme()).settings || {};
  }
  const name = brandName(settings) || tenant.org_name || "";
  return {
    name,
    supportEmail: settings["footer.support_email"] || null,
    marketing: Boolean(tenant.marketing),
  };
}
